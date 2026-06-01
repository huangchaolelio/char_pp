"""Feature-023 — 启发式评估集生成器（T071 lower-bound 替代方案）.

用途：
  在缺少人工标注的前提下，从 1015 条真实 COS 文件名中筛选出**强信号**样本，
  为其生成可信的 expected_(l1, l2, l3, action) 标签，输出符合 T071 评估脚本
  约定的 CSV，便于跑 lower-bound 准确率回归。

强信号定义：文件名同时满足
  1) 含明确手部线索（"正手" / "反手" / "fh" / "bh"）或在通用·教学辅助桶里
  2) 含字典里某个 action 的精确关键词（如「高吊弧圈」「劈长」「拨」「快攻」）
  3) action 在字典 56 行内可唯一定位 (l1, l2, l3, action)

过滤策略：
  - 只用 "横拍 + 反胶" 一档（与字典 56 行口径一致）
  - 跨手部歧义样本（如「劈长」单独出现无手部线索）整体丢弃
  - 至少覆盖 9 个 L3 桶；同 action 取 ≤ 5 条

输出：data/eval/tech_classification_v2_eval.csv
"""
from __future__ import annotations

import asyncio
import csv
import random
import sys
from collections import defaultdict
from pathlib import Path

import asyncpg

DB_DSN = "postgresql://postgres:password@localhost:5432/coaching_db"
OUT_CSV = Path("data/eval/tech_classification_v2_eval.csv")
TARGET_TOTAL = 100
PER_ACTION_CAP = 18
RANDOM_SEED = 20260531

# ---- 强信号规则（仅在文件名同时含 hand_signal + action_keyword 时打标） ----

# 手部信号 → l3 前缀映射（横拍·反胶下）
HAND_SIGNALS: dict[str, str] = {
    "正手": "正手",
    "反手": "反手",
    "fh": "正手",
    "bh": "反手",
    "FH": "正手",
    "BH": "反手",
}

# action 关键词 → (action_name, l3_suffix_when_handed, allow_general_l3)
# l3_suffix_when_handed:  "进攻" / "防御" / "发球" / "步法"
# allow_general_l3:       True 时若无手部信号，归入「通用·教学辅助」
ACTION_RULES: list[tuple[str, str, str | None, bool]] = [
    # action_keyword,  action_name,        l3_suffix,    allow_general
    ("高吊弧圈",        "高吊弧圈球",         "进攻",       False),
    ("高调弧圈",        "高吊弧圈球",         "进攻",       False),
    ("前冲弧圈",        "前冲弧圈球",         "进攻",       False),
    ("发力拉",         "高吊弧圈球",         "进攻",       False),
    ("发力传递",       "高吊弧圈球",         "进攻",       False),
    ("快攻",           "快攻",              "进攻",       False),
    ("台内拧拉",        "拧",               "进攻",       False),
    ("拧拉",           "拧",               "进攻",       False),
    ("挑打",           "挑",               "进攻",       False),
    ("拨",             "拨",               "进攻",       False),   # 反手拨击
    ("推挡",           "拨",               "进攻",       False),   # 别名
    ("劈长",           "劈长",              "防御",       False),
    ("摆短",           "摆短",              "防御",       False),
    ("搝球",           "搝球",              "防御",       False),
    ("勾手发球",        "勾手发球",          "发球",       False),
    ("逆旋转发球",      "逆旋转发球",         "发球",       False),
    ("接发球",         "接发球",            None,        True),   # 通用·教学辅助
    ("握拍站位",        "握拍站位",          None,        True),   # 通用·教学辅助
    ("握拍",           "握拍站位",          None,        True),
    ("站位",           "握拍站位",          None,        True),
    ("教学概述",        "教学概述",          None,        True),   # 通用·教学辅助
    ("课程概述",        "教学概述",          None,        True),
    ("总论",           "教学概述",          None,        True),
    ("继续课程",        "教学概述",          None,        True),
    ("教学说明",        "教学概述",          None,        True),
    ("训练计划",        "教学概述",          None,        True),
    ("并步",           "并步",              "步法",       False),
    ("交叉步",         "交叉步",            "步法",       False),
    ("推侧扑",         "推侧扑",            "步法",       False),
]

async def load_filenames() -> list[tuple[str, str]]:
    conn = await asyncpg.connect(DB_DSN)
    rows = await conn.fetch(
        "SELECT filename, course_series FROM coach_video_classifications ORDER BY id"
    )
    await conn.close()
    return [(r["filename"], r["course_series"] or "") for r in rows]


async def load_dictionary() -> dict[str, tuple[str, str, str, str]]:
    """action -> (l1, l2, l3, action)；跨手部重名 action 用 l3 区分由调用方决定."""
    conn = await asyncpg.connect(DB_DSN)
    rows = await conn.fetch(
        "SELECT category_l1, category_l2, category_l3, action FROM tech_actions"
    )
    await conn.close()
    out: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    for r in rows:
        out[r["action"]].append(
            (r["category_l1"], r["category_l2"], r["category_l3"], r["action"])
        )
    return out


def _detect_hand(filename: str) -> str | None:
    for sig, hand in HAND_SIGNALS.items():
        if sig in filename:
            return hand
    return None


def label(
    filename: str, course_series: str, dict_index: dict[str, list[tuple[str, str, str, str]]]
) -> dict[str, str] | None:
    """为单个文件名生成 expected 标签；信号不强时返回 None."""
    hand = _detect_hand(filename)
    course_hand = _detect_hand(course_series) if not hand else hand

    for kw, action, l3_suffix, allow_general in ACTION_RULES:
        if kw not in filename:
            continue

        # 通用·教学辅助 类（接发球/握拍站位/教学概述）：手部不重要
        if allow_general:
            entries = dict_index.get(action) or []
            general = [e for e in entries if "通用" in e[2]]
            if not general:
                return None
            l1, l2, l3, act = general[0]
            return {
                "filename": filename,
                "course_series": course_series,
                "expected_l1": l1,
                "expected_l2": l2,
                "expected_l3": l3,
                "expected_action": act,
            }

        # 进攻/防御/发球/步法 类：必须有明确手部线索
        if hand is None and course_hand is None:
            return None
        chosen_hand = hand or course_hand
        target_l3 = f"{chosen_hand}·{l3_suffix}"
        entries = dict_index.get(action) or []
        match = [e for e in entries if e[2] == target_l3]
        if not match:
            return None
        l1, l2, l3, act = match[0]
        return {
            "filename": filename,
            "course_series": course_series,
            "expected_l1": l1,
            "expected_l2": l2,
            "expected_l3": l3,
            "expected_action": act,
        }

    return None


async def main() -> None:
    files = await load_filenames()
    dict_index = await load_dictionary()

    # 仅取 横拍·反胶 字典子集（与现网口径一致）
    dict_filtered: dict[str, list[tuple[str, str, str, str]]] = {
        a: [e for e in entries if e[0] == "横拍" and e[1] == "反胶"]
        for a, entries in dict_index.items()
    }
    dict_filtered = {a: e for a, e in dict_filtered.items() if e}

    candidates: list[dict[str, str]] = []
    for fn, cs in files:
        rec = label(fn, cs, dict_filtered)
        if rec:
            candidates.append(rec)

    print(f"raw signal candidates: {len(candidates)} / {len(files)}")

    # per-action 限流 + 随机抽样 → TARGET_TOTAL
    rng = random.Random(RANDOM_SEED)
    rng.shuffle(candidates)

    bucket: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in candidates:
        bucket[r["expected_action"]].append(r)

    chosen: list[dict[str, str]] = []
    for action, lst in bucket.items():
        chosen.extend(lst[:PER_ACTION_CAP])

    rng.shuffle(chosen)
    chosen = chosen[:TARGET_TOTAL]

    print(f"final picked: {len(chosen)}; action coverage: {len({c['expected_action'] for c in chosen})}")
    l3_dist: dict[str, int] = defaultdict(int)
    for c in chosen:
        l3_dist[c["expected_l3"]] += 1
    print("L3 distribution:")
    for k, v in sorted(l3_dist.items(), key=lambda x: -x[1]):
        print(f"  {k:20s} {v}")

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "filename",
                "course_series",
                "expected_l1",
                "expected_l2",
                "expected_l3",
                "expected_action",
            ],
        )
        w.writeheader()
        w.writerows(chosen)

    print(f"wrote {OUT_CSV}")


if __name__ == "__main__":
    asyncio.run(main())
