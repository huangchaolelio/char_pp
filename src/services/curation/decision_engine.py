"""Feature-021 决策引擎 — 规则路 + LLM 兜底两层骨架.

调用入口：
    from src.services.curation.decision_engine import decide

    result = decide(
        segment_text=...,
        rubric=loaded_rubric,
        tech_category="forehand_topspin",
        coach_name="张继科",
        segment_duration_seconds=180.0,
        llm_client=optional_llm_client,
    )

返回 :class:`DecisionResult`，包含 ``decision`` / ``validity_score`` /
``rejection_reason`` / ``decision_source`` / ``dim_breakdown``，由
``curation_service`` 写入 ``video_curation_segment_results`` 行。

算法骨架（specs/021-video-content-curation/research.md § R3）：

1. **第 1 层规则路**（5 维加权）：
   - tech_keyword       (0.35): 教学关键词命中
   - non_teaching       (0.25): 非教学关键词排除（命中重罚）
   - coach_dominance    (0.20): 启发式判教练主导率
   - topic_relevance    (0.15): 与目标 tech_category 关键词的重叠
   - duration_floor     (0.05): 单分段最短时长硬约束（<min → 0 分）

   规则路 ``validity_score = sum(dim_score * weight)``；
   - 得分 ≥ ``threshold_accept`` (0.7) → ``accepted``
   - 得分 ≤ ``threshold_reject`` (0.3) → ``rejected``
   - 落入模糊区间 ``(threshold_reject, threshold_accept)`` → 进第 2 层

2. **第 2 层 LLM 兜底**：
   - 仅对模糊区间分段调用一次 LLM（Venus 优先 → OpenAI fallback）
   - LLM 返回 JSON ``{decision, validity_score, rejection_reason, rationale}``
   - LLM 不可用 / JSON 解析失败 / 超时 → 落 ``rubric.llm_unavailable_decision``
     （默认 ``"uncertain"``），不阻断整个作业
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.services.curation import error_codes as curation_codes
from src.services.curation.coach_dominance_detector import estimate_dominance_ratio
from src.services.curation.rubric_loader import CurationRubric

logger = logging.getLogger(__name__)


# ── 类型 ────────────────────────────────────────────────────────────────


@dataclass
class DecisionResult:
    """单分段决策结果（service 层直接写入 ORM 行）.

    字段语义见 :class:`VideoCurationSegmentResult`：

    - ``decision``: ``accepted | rejected | uncertain``
    - ``validity_score``: ``[0, 1]``
    - ``rejection_reason``: 仅在 ``decision != accepted`` 时填；命名 snake_case
    - ``decision_source``: ``rule | llm``
    - ``dim_breakdown``: 5 维各自分数 + 命中关键词，写入 JSONB
    """

    decision: str
    validity_score: float
    rejection_reason: str | None
    decision_source: str
    dim_breakdown: dict[str, Any] = field(default_factory=dict)


# ── 内部辅助：关键词加载（缓存） ────────────────────────────────────────


_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_keywords_from_path(rel_path: str) -> list[str]:
    """加载 ``rules.tech_keyword.keywords_ref`` / ``topic_relevance.keywords_ref``
    指向的 JSON 文件，返回扁平关键词列表（兼容两种结构）。"""
    full = _REPO_ROOT / rel_path
    if not full.exists():
        logger.warning("keywords_ref file missing: %s", full)
        return []
    try:
        data = json.loads(full.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("keywords_ref load failed (%s): %s", full, exc)
        return []

    # 结构 A：{"keywords": [...]}
    if isinstance(data, dict) and isinstance(data.get("keywords"), list):
        return [k for k in data["keywords"] if isinstance(k, str)]
    # 结构 B：{"forehand_topspin": [...], "forehand_attack": [...], ...}
    if isinstance(data, dict):
        out: list[str] = []
        for v in data.values():
            if isinstance(v, list):
                out.extend(k for k in v if isinstance(k, str))
        return out
    if isinstance(data, list):
        return [k for k in data if isinstance(k, str)]
    return []


def _load_topic_keywords(rel_path: str, tech_category: str) -> list[str]:
    """从 ``tech_classification_rules.json`` 形结构中只取目标 tech_category 的关键词。"""
    full = _REPO_ROOT / rel_path
    if not full.exists():
        return []
    try:
        data = json.loads(full.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict):
        v = data.get(tech_category)
        if isinstance(v, list):
            return [k for k in v if isinstance(k, str)]
    return []


# ── 维度计算 ────────────────────────────────────────────────────────────


def _score_tech_keyword(text: str, keywords: list[str]) -> tuple[float, list[str]]:
    """``tech_keyword`` 维度：命中越多分数越高，>=3 个达满。"""
    matched = [kw for kw in keywords if kw and kw in text]
    if not matched:
        return 0.0, []
    return min(1.0, len(matched) / 3.0), matched


def _score_non_teaching(
    text: str, keywords: dict[str, list[str]]
) -> tuple[float, list[str]]:
    """``non_teaching`` 维度：命中即重罚——返回 ``1 - min(命中数/3, 1.0)``。

    分数 1.0 表"完全干净，零命中"，分数 0.0 表"高密度命中"。
    """
    match_kws = keywords.get("match", [])
    matched = [kw for kw in match_kws if kw and kw in text]
    if not matched:
        return 1.0, []
    penalty = min(1.0, len(matched) / 3.0)
    return 1.0 - penalty, matched


def _score_coach_dominance(
    text: str, target_coach_name: str | None, min_ratio: float
) -> tuple[float, float]:
    """``coach_dominance`` 维度：启发式 dominance ratio 与 ``min_ratio`` 的归一。

    - dominance >= min_ratio → 1.0（不扣分）
    - dominance <  min_ratio → 线性归一到 [0, 1]
    """
    ratio = estimate_dominance_ratio(
        segment_text=text, target_coach_name=target_coach_name
    )
    if ratio >= min_ratio:
        return 1.0, ratio
    # 线性映射 [0, min_ratio) → [0, 1)
    if min_ratio <= 0:
        return 1.0, ratio
    return max(0.0, ratio / min_ratio), ratio


def _score_topic_relevance(
    text: str, topic_keywords: list[str]
) -> tuple[float, list[str]]:
    """``topic_relevance`` 维度：本段是否与目标 tech_category 主题相关。"""
    if not topic_keywords:
        # 无目标关键词列表时给中性满分，避免误伤已分类视频
        return 0.7, []
    matched = [kw for kw in topic_keywords if kw and kw in text]
    if not matched:
        return 0.0, []
    return min(1.0, len(matched) / 2.0), matched


def _score_duration_floor(
    duration_seconds: float, min_seconds: int
) -> float:
    """``duration_floor`` 维度：硬约束——< min_seconds 直接 0 分；否则 1 分。"""
    if duration_seconds <= 0:
        return 0.0
    return 1.0 if duration_seconds >= min_seconds else 0.0


# ── 主入口 ──────────────────────────────────────────────────────────────


def decide(
    *,
    segment_text: str,
    rubric: CurationRubric,
    tech_category: str,
    coach_name: str | None,
    segment_duration_seconds: float,
    llm_client: Any | None = None,
) -> DecisionResult:
    """对单分段做决策；若规则路得分模糊则调 LLM 兜底.

    Args:
        segment_text: 分段对应转录文本（可空）
        rubric: 已加载并校验的 :class:`CurationRubric`
        tech_category: 目标技术类别（``coach_video_classifications.tech_category``）
        coach_name: 目标教练姓名；可 None
        segment_duration_seconds: 该分段时长（秒）
        llm_client: 可选 :class:`LlmClient` 实例；为 None 时模糊区间一律落
            ``rubric.llm_unavailable_decision`` (默认 ``uncertain``)

    Returns:
        :class:`DecisionResult`
    """
    rules = rubric.data["rules"]

    # 1) tech_keyword 维度
    tk_cfg = rules["tech_keyword"]
    tk_score: float = 0.0
    tk_matched: list[str] = []
    if tk_cfg.get("enabled", True):
        tk_keywords = _load_keywords_from_path(tk_cfg["keywords_ref"])
        tk_score, tk_matched = _score_tech_keyword(segment_text, tk_keywords)
    tk_weight = float(tk_cfg.get("weight", 0.35))

    # 2) non_teaching 维度
    nt_cfg = rules["non_teaching"]
    nt_score: float = 1.0
    nt_matched: list[str] = []
    if nt_cfg.get("enabled", True):
        nt_score, nt_matched = _score_non_teaching(
            segment_text, nt_cfg.get("keywords", {})
        )
    nt_weight = float(nt_cfg.get("weight", 0.25))

    # 3) coach_dominance 维度
    cd_cfg = rules["coach_dominance"]
    cd_score: float = 1.0
    cd_ratio: float = 1.0
    if cd_cfg.get("enabled", True):
        cd_score, cd_ratio = _score_coach_dominance(
            segment_text, coach_name, float(cd_cfg.get("min_dominance_ratio", 0.6))
        )
    cd_weight = float(cd_cfg.get("weight", 0.20))

    # 4) topic_relevance 维度
    tr_cfg = rules["topic_relevance"]
    tr_score: float = 0.7
    tr_matched: list[str] = []
    if tr_cfg.get("enabled", True):
        tr_keywords = _load_topic_keywords(tr_cfg["keywords_ref"], tech_category)
        tr_score, tr_matched = _score_topic_relevance(segment_text, tr_keywords)
    tr_weight = float(tr_cfg.get("weight", 0.15))

    # 5) duration_floor 维度
    df_cfg = rules["duration_floor"]
    df_score: float = 1.0
    if df_cfg.get("enabled", True):
        df_score = _score_duration_floor(
            segment_duration_seconds, rubric.min_segment_seconds
        )
    df_weight = float(df_cfg.get("weight", 0.05))

    # 加权合成
    validity_score = (
        tk_score * tk_weight
        + nt_score * nt_weight
        + cd_score * cd_weight
        + tr_score * tr_weight
        + df_score * df_weight
    )
    validity_score = max(0.0, min(1.0, validity_score))

    dim_breakdown: dict[str, Any] = {
        "tech_keyword": {
            "score": round(tk_score, 4),
            "weight": tk_weight,
            "matched": tk_matched,
        },
        "non_teaching": {
            "score": round(nt_score, 4),
            "weight": nt_weight,
            "matched": nt_matched,
        },
        "coach_dominance": {
            "score": round(cd_score, 4),
            "weight": cd_weight,
            "dominance_ratio": round(cd_ratio, 4),
        },
        "topic_relevance": {
            "score": round(tr_score, 4),
            "weight": tr_weight,
            "matched_keywords": tr_matched,
        },
        "duration_floor": {
            "score": round(df_score, 4),
            "weight": df_weight,
            "duration_seconds": round(segment_duration_seconds, 2),
        },
    }

    # 决策分流
    threshold_accept = rubric.threshold_accept
    threshold_reject = rubric.threshold_reject

    if validity_score >= threshold_accept:
        return DecisionResult(
            decision="accepted",
            validity_score=round(validity_score, 4),
            rejection_reason=None,
            decision_source="rule",
            dim_breakdown=dim_breakdown,
        )
    if validity_score <= threshold_reject:
        return DecisionResult(
            decision="rejected",
            validity_score=round(validity_score, 4),
            rejection_reason=_pick_rejection_reason(
                tk_score, nt_score, cd_score, tr_score, df_score
            ),
            decision_source="rule",
            dim_breakdown=dim_breakdown,
        )

    # 模糊区间 → LLM 兜底
    if not rubric.llm_enabled or llm_client is None:
        return DecisionResult(
            decision=rubric.llm_unavailable_decision,
            validity_score=round(validity_score, 4),
            rejection_reason=curation_codes.CURATION_LLM_UNAVAILABLE.lower(),
            decision_source="rule",
            dim_breakdown=dim_breakdown,
        )

    return _llm_fallback_decide(
        segment_text=segment_text,
        rubric=rubric,
        tech_category=tech_category,
        coach_name=coach_name,
        rule_score=validity_score,
        dim_breakdown=dim_breakdown,
        llm_client=llm_client,
    )


def _pick_rejection_reason(
    tk: float, nt: float, cd: float, tr: float, df: float
) -> str:
    """从 5 维得分中选最低维度作为 rejection_reason，便于审计。"""
    candidates = [
        ("non_teaching_content", nt),
        ("too_short", df),
        ("off_topic", tr),
        ("other_speaker", cd),
        ("no_tech_terms", tk),
    ]
    candidates.sort(key=lambda x: x[1])
    return candidates[0][0]


def _llm_fallback_decide(
    *,
    segment_text: str,
    rubric: CurationRubric,
    tech_category: str,
    coach_name: str | None,
    rule_score: float,
    dim_breakdown: dict[str, Any],
    llm_client: Any,
) -> DecisionResult:
    """模糊区间 LLM 兜底；任何失败都落入 ``llm_unavailable_decision``。"""
    try:
        prompt_template_path = _REPO_ROOT / rubric.llm_prompt_template_path
        if prompt_template_path.exists():
            template = prompt_template_path.read_text(encoding="utf-8")
        else:
            logger.warning(
                "llm_fallback prompt_template missing: %s", prompt_template_path
            )
            template = ""

        # 拼装 user message — 用模板中已声明的占位符（容忍模板缺失）
        dim_summary_lines = []
        for dim_name, info in dim_breakdown.items():
            score = info.get("score", 0.0)
            dim_summary_lines.append(f"- {dim_name}: {score}")
        dim_summary = "\n".join(dim_summary_lines)

        user_msg = (
            f"分段转录：{segment_text or '(无文本)'}\n\n"
            f"目标 tech_category: {tech_category}\n"
            f"目标教练姓名: {coach_name or '未指定'}\n"
            f"规则路 validity_score: {round(rule_score, 4)}（处于模糊区间）\n"
            f"5 维细分:\n{dim_summary}\n\n"
            "请按 system prompt 给出 JSON 决策。"
        )

        messages = [
            {"role": "system", "content": template or "你是教学视频审核员"},
            {"role": "user", "content": user_msg},
        ]

        response_text, _tokens = llm_client.chat(
            messages=messages,
            temperature=0.0,
            json_mode=True,
        )
        payload = json.loads(response_text)
    except Exception as exc:  # noqa: BLE001
        logger.info("decision_engine LLM fallback failed: %s", exc)
        return DecisionResult(
            decision=rubric.llm_unavailable_decision,
            validity_score=round(rule_score, 4),
            rejection_reason=curation_codes.CURATION_LLM_UNAVAILABLE.lower(),
            decision_source="llm",
            dim_breakdown=dim_breakdown,
        )

    decision = str(payload.get("decision") or "").lower()
    if decision not in ("accepted", "rejected", "uncertain"):
        return DecisionResult(
            decision="uncertain",
            validity_score=round(rule_score, 4),
            rejection_reason="llm_response_invalid",
            decision_source="llm",
            dim_breakdown=dim_breakdown,
        )

    score_raw = payload.get("validity_score")
    try:
        llm_score = float(score_raw)
    except (TypeError, ValueError):
        llm_score = rule_score
    llm_score = max(0.0, min(1.0, llm_score))

    rejection_reason = payload.get("rejection_reason")
    if decision == "accepted":
        rejection_reason = None
    elif not isinstance(rejection_reason, str) or not rejection_reason:
        rejection_reason = "llm_unspecified"

    return DecisionResult(
        decision=decision,
        validity_score=round(llm_score, 4),
        rejection_reason=rejection_reason,
        decision_source="llm",
        dim_breakdown=dim_breakdown,
    )


__all__ = ["DecisionResult", "decide"]
