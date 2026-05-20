"""Feature-021 基准回归脚本.

对照人工标注样本集计算清洗算法的关键指标，输出 JSON baseline 到
``specs/021-video-content-curation/benchmark/v{rubric_version}_baseline.json``。

用途：
- 上线前 / 规范升级（v1 → v2）后跑一次，对照 SC-001/002/003 三项指标达标
- 监控算法精度回归（precision/recall 显著下降时 fail）
- 量化 LLM token 节省（清洗前 vs 清洗后）

输入样本格式（``tests/data/curation_samples_v1/manifest.jsonl``，每行一个 JSON）：

    {
      "sample_id": "S001",
      "cos_object_key": "charhuang/x/y.mp4",
      "tech_category": "forehand_topspin",
      "coach_name": "张继科",
      "segments": [
        {
          "segment_index": 0,
          "start_ms": 0,
          "end_ms": 60000,
          "transcript_text": "来给大家做一个示范，注意看动作要领",
          "human_label": "accepted"   // accepted | rejected | uncertain
        },
        ...
      ]
    }

样本视频本身**不在仓库中**——manifest 只存指针 + 人工标注，节省存储且避免大文件
被 git 追踪。CI 跑 benchmark 时可指定 ``--manifest`` 路径到挂载卷。

输出（baseline JSON 结构见 ``--output`` 文件）：

    {
      "rubric_version": "v1",
      "sample_count": 30,
      "segment_count": 600,
      "metrics": {
        "precision_accepted": 0.91,
        "recall_rejected": 0.87,
        "f1_accepted": 0.89,
        "f1_rejected": 0.88,
        "llm_invocation_rate": 0.18,
        "uncertain_rate": 0.04,
        "llm_token_reduction_pct_estimate": 0.34
      },
      "sc_check": {
        "sc_001_recall_rejected_ge_0_85": true,
        "sc_001_precision_accepted_ge_0_85": true,
        "sc_002_token_reduction_ge_0_30": true
      },
      "ran_at": "2026-05-19T10:00:00+08:00",
      "rubric_path": "src/config/curation_rubric/v1.yaml"
    }

注意：
- SC-003（同 tech_category 多视频术语重叠率提升）需要"清洗前 vs 清洗后" KB 抽取
  的实际产物比对，无法在本脚本内单独算 —— 需在 staging 环境跑两次完整链路后比对，
  此脚本只产出 SC-001/002 + 算法层指标
- LLM token reduction 是**估算**：清洗后送给 KB 抽取的总文本量 / 清洗前总文本量。
  实测值待 staging 环境 KB 抽取 token 计数。

用法：
    python3 scripts/run_curation_benchmark.py \
        --manifest tests/data/curation_samples_v1/manifest.jsonl \
        --rubric-version v1 \
        --output specs/021-video-content-curation/benchmark/v1_baseline.json \
        [--no-llm]   # 跳过 LLM 兜底（纯规则路；模糊段全 uncertain）
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Path setup so the script runs without `pip install -e .`
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.services.curation.decision_engine import decide  # noqa: E402
from src.services.curation.rubric_loader import load as load_rubric  # noqa: E402
from src.utils.time_utils import now_cst  # noqa: E402

logger = logging.getLogger("curation_benchmark")


@dataclass
class _SegmentScore:
    sample_id: str
    segment_index: int
    human: str
    auto: str
    validity_score: float
    decision_source: str  # rule | llm
    transcript_chars: int = 0


@dataclass
class _Metrics:
    """聚合统计；最终序列化到 baseline JSON.``metrics`` 与 ``sc_check`` 字段."""

    sample_count: int = 0
    segment_count: int = 0
    by_human: dict[str, int] = field(default_factory=dict)  # accepted/rejected/uncertain → count
    by_auto: dict[str, int] = field(default_factory=dict)
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)  # human → auto → count
    llm_invocation_count: int = 0
    transcript_chars_total: int = 0
    transcript_chars_accepted: int = 0


def _add_to_confusion(metrics: _Metrics, human: str, auto: str) -> None:
    metrics.confusion.setdefault(human, {}).setdefault(auto, 0)
    metrics.confusion[human][auto] += 1


def _compute_precision_recall(metrics: _Metrics, label: str) -> tuple[float, float, float]:
    """对单个 label 计算 (precision, recall, f1).

    label = 'accepted'：precision = 真 accepted / 自动判 accepted 的总数；
                       recall    = 真 accepted 中被正确识别为 accepted 的占比.
    label = 'rejected' 同理.
    """
    tp = metrics.confusion.get(label, {}).get(label, 0)
    fp = sum(
        metrics.confusion.get(h, {}).get(label, 0)
        for h in metrics.confusion
        if h != label
    )
    fn = sum(
        metrics.confusion.get(label, {}).get(a, 0)
        for a in metrics.confusion.get(label, {})
        if a != label
    )
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (
        (2 * precision * recall) / (precision + recall)
        if (precision + recall) > 0 else 0.0
    )
    return round(precision, 4), round(recall, 4), round(f1, 4)


def _process_sample(
    sample: dict, rubric, *, with_llm: bool, mock_llm_client=None,
) -> list[_SegmentScore]:
    """对单个样本的所有分段跑 decide() 并收集打分."""
    out: list[_SegmentScore] = []
    sid = sample["sample_id"]
    tech_category = sample["tech_category"]
    coach_name = sample.get("coach_name")
    for seg in sample["segments"]:
        text = seg.get("transcript_text") or ""
        human = seg["human_label"]
        # 估算分段时长（人工标注里直接给 start_ms/end_ms）
        duration_s = max(0, (seg["end_ms"] - seg["start_ms"]) / 1000.0)
        result = decide(
            segment_text=text,
            rubric=rubric,
            tech_category=tech_category,
            coach_name=coach_name,
            segment_duration_seconds=duration_s,
            llm_client=mock_llm_client if with_llm else None,
        )
        out.append(_SegmentScore(
            sample_id=sid,
            segment_index=seg["segment_index"],
            human=human,
            auto=result.decision,
            validity_score=result.validity_score,
            decision_source=result.decision_source,
            transcript_chars=len(text),
        ))
    return out


def _aggregate(scores: list[_SegmentScore], rubric_version: str) -> dict[str, Any]:
    metrics = _Metrics()
    sample_ids: set[str] = set()
    for s in scores:
        sample_ids.add(s.sample_id)
        metrics.segment_count += 1
        metrics.by_human[s.human] = metrics.by_human.get(s.human, 0) + 1
        metrics.by_auto[s.auto] = metrics.by_auto.get(s.auto, 0) + 1
        _add_to_confusion(metrics, s.human, s.auto)
        if s.decision_source == "llm":
            metrics.llm_invocation_count += 1
        metrics.transcript_chars_total += s.transcript_chars
        if s.auto == "accepted":
            metrics.transcript_chars_accepted += s.transcript_chars
    metrics.sample_count = len(sample_ids)

    p_acc, r_acc, f1_acc = _compute_precision_recall(metrics, "accepted")
    p_rej, r_rej, f1_rej = _compute_precision_recall(metrics, "rejected")

    llm_rate = (
        metrics.llm_invocation_count / metrics.segment_count
        if metrics.segment_count else 0.0
    )
    uncertain_rate = (
        metrics.by_auto.get("uncertain", 0) / metrics.segment_count
        if metrics.segment_count else 0.0
    )
    token_reduction = (
        1.0 - (metrics.transcript_chars_accepted / metrics.transcript_chars_total)
        if metrics.transcript_chars_total else 0.0
    )

    metrics_block = {
        "precision_accepted": p_acc,
        "recall_accepted": r_acc,
        "f1_accepted": f1_acc,
        "precision_rejected": p_rej,
        "recall_rejected": r_rej,
        "f1_rejected": f1_rej,
        "llm_invocation_rate": round(llm_rate, 4),
        "uncertain_rate": round(uncertain_rate, 4),
        "llm_token_reduction_pct_estimate": round(token_reduction, 4),
    }
    sc_check = {
        # spec SC-001：召回率 / 精确率 ≥ 0.85
        "sc_001_recall_rejected_ge_0_85": r_rej >= 0.85,
        "sc_001_precision_accepted_ge_0_85": p_acc >= 0.85,
        # spec SC-002：清洗后 token 量下降 ≥ 30%（估算）
        "sc_002_token_reduction_ge_0_30": token_reduction >= 0.30,
    }

    return {
        "rubric_version": rubric_version,
        "sample_count": metrics.sample_count,
        "segment_count": metrics.segment_count,
        "by_human_label": metrics.by_human,
        "by_auto_decision": metrics.by_auto,
        "confusion_matrix": metrics.confusion,
        "metrics": metrics_block,
        "sc_check": sc_check,
        "ran_at": now_cst().isoformat(),
        "rubric_path": f"src/config/curation_rubric/{rubric_version}.yaml",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--manifest", required=True,
        help="Path to JSONL manifest file with human-annotated samples",
    )
    parser.add_argument(
        "--rubric-version", default=None,
        help="Override rubric version (default: latest)",
    )
    parser.add_argument(
        "--output", required=True,
        help="Where to write the baseline JSON",
    )
    parser.add_argument(
        "--no-llm", action="store_true",
        help="Skip LLM fallback path (pure rule + uncertain on ambiguous)",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    rubric = load_rubric(args.rubric_version) if args.rubric_version else load_rubric()
    logger.info("loaded rubric: version=%s", rubric.version)

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        logger.error("manifest not found: %s", manifest_path)
        return 2

    samples: list[dict] = []
    for line_num, raw in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            samples.append(json.loads(line))
        except json.JSONDecodeError as exc:
            logger.error("manifest line %d: invalid JSON — %s", line_num, exc)
            return 3

    if not samples:
        logger.error("manifest is empty after filter; cannot build baseline")
        return 4

    logger.info("loaded %d sample videos from manifest", len(samples))

    # 此脚本不真的调 LLM（避免外部 API 依赖）；--no-llm 时模糊区间一律 uncertain。
    # CI 上线前在 staging 跑一次完整链路（含 LLM）补全 SC-002 真实 token 节省值。
    all_scores: list[_SegmentScore] = []
    for sample in samples:
        all_scores.extend(_process_sample(
            sample, rubric, with_llm=not args.no_llm, mock_llm_client=None,
        ))

    baseline = _aggregate(all_scores, rubric.version)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(baseline, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    logger.info("baseline written: %s", output_path)
    logger.info(
        "metrics: precision_accepted=%.3f recall_rejected=%.3f "
        "llm_rate=%.3f token_reduction=%.3f",
        baseline["metrics"]["precision_accepted"],
        baseline["metrics"]["recall_rejected"],
        baseline["metrics"]["llm_invocation_rate"],
        baseline["metrics"]["llm_token_reduction_pct_estimate"],
    )

    # SC 检查未通过时退出码 1，CI 可据此 fail
    sc = baseline["sc_check"]
    if not all(sc.values()):
        failed = [k for k, v in sc.items() if not v]
        logger.warning("SC check failed: %s", failed)
        return 1
    logger.info("all SC checks PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
