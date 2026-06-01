"""Feature-023 — TechClassifier V2 准确率评估脚本（T072 骨架）.

用途:
  - 读取 `data/eval/tech_classification_v2_eval.csv` 评估集
  - 调用 `TechClassifier.from_settings().classify(filename, course_series)`
  - 比对人工标注的 `expected_action`，输出 top-1 准确率 + 混淆矩阵

CSV 列约定（T071 评估集需提供）:
  - filename: 视频文件名（含扩展名）
  - course_series: COS 目录名 / 课程系列
  - expected_l1: 横拍|直拍
  - expected_l2: 反胶|长胶|生胶|正胶
  - expected_l3: 正手·进攻|反手·进攻|...
  - expected_action: 高吊弧圈球|前冲弧圈球|...

输出:
  - JSON 报告（top-1 / 三级一致率 / 混淆矩阵）打印到 stdout
  - 如附加 --output PATH，则写入文件
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


def _hash_eval_set(rows: list[dict]) -> str:
    """对评估集计算稳定 hash（剔除时间戳）."""
    canon = json.dumps(
        sorted(
            (r.get("filename", ""), r.get("expected_action", ""))
            for r in rows
        ),
        ensure_ascii=False,
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


async def _run(eval_csv: Path) -> dict[str, Any]:
    if not eval_csv.exists():
        raise SystemExit(
            f"评估集文件不存在: {eval_csv}\n"
            f"请按 T071 提示创建（≥ 100 条人工标注样本，覆盖 44 个 action）"
        )

    with open(eval_csv, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit("评估集为空")

    # Lazy import to avoid circular issues
    from src.services.tech_classifier import TechClassifier

    classifier = TechClassifier.from_settings()

    total = len(rows)
    top1_action_correct = 0
    l3_correct = 0
    l1_correct = 0
    confusion: dict[str, Counter] = defaultdict(Counter)  # expected → predicted
    sample_errors: list[dict] = []

    for r in rows:
        result = await classifier.classify(
            r.get("filename", ""), r.get("course_series", "")
        )
        exp_action = (r.get("expected_action") or "").strip()
        pred_action = result.action or "unclassified"

        if exp_action == pred_action:
            top1_action_correct += 1
        else:
            if len(sample_errors) < 20:
                sample_errors.append(
                    {
                        "filename": r.get("filename"),
                        "expected": exp_action,
                        "predicted": pred_action,
                        "source": result.classification_source,
                        "confidence": result.confidence,
                    }
                )
        confusion[exp_action][pred_action] += 1

        exp_l3 = (r.get("expected_l3") or "").strip()
        if exp_l3 and exp_l3 == (result.category_l3 or ""):
            l3_correct += 1
        exp_l1 = (r.get("expected_l1") or "").strip()
        if exp_l1 and exp_l1 == (result.category_l1 or ""):
            l1_correct += 1

    report = {
        "evaluated_at": datetime.utcnow().isoformat() + "Z",
        "model_version": "TechClassifierV2",
        "feature_version": "023",
        "eval_set": str(eval_csv),
        "eval_set_hash": _hash_eval_set(rows),
        "total_samples": total,
        "metrics": {
            "top1_action_accuracy": round(top1_action_correct / total, 4),
            "l3_accuracy": round(l3_correct / total, 4),
            "l1_accuracy": round(l1_correct / total, 4),
        },
        "sc_002_target": 0.85,
        "sc_002_passed": (top1_action_correct / total) >= 0.85,
        "confusion_matrix_top10_errors": [
            {"expected": exp, "predicted": pred, "count": cnt}
            for exp, preds in confusion.items()
            for pred, cnt in preds.most_common(3)
            if exp != pred
        ][:10],
        "sample_errors": sample_errors,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--eval-csv",
        type=Path,
        default=Path("data/eval/tech_classification_v2_eval.csv"),
        help="评估集 CSV 路径（默认 data/eval/tech_classification_v2_eval.csv）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="JSON 报告输出路径（默认仅打印 stdout）",
    )
    args = parser.parse_args()

    report = asyncio.run(_run(args.eval_csv))
    payload = json.dumps(report, ensure_ascii=False, indent=2)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(f"报告已写入: {args.output}")
    print(payload)
    sys.exit(0 if report["sc_002_passed"] else 1)


if __name__ == "__main__":
    main()
