"""Feature-021 教练主导率启发式判定.

任务目标：判断"分段对应的转录文本"是否以**目标教练**为讲解主导，输出
``[0, 1]`` 区间的 dominance ratio。

启发式（无 ASR 说话人分离能力，仅用关键词与文本特征）：

1. **教练姓名命中**：分段文本中包含目标教练姓名 → 大概率是介绍 / 旁白
   而非教练本人讲解；扣分（"老师 / 教练"等通用称谓不扣分）
2. **第一人称频次**：``"我"`` / ``"咱们"`` 等高频出现 → 教练本人讲解，加分
3. **第二人称频次**：``"你 / 你们"`` 高频 → 教练对学员讲解，加分
4. **采访 / 旁白特征词**：``"接受采访" / "记者" / "颁奖"`` → 强降分
5. **空文本兜底**：返回中性值 ``0.5``（不可判定）

返回值与 :class:`CurationRubric.rules.coach_dominance` 配置约定一致：
高于 ``min_dominance_ratio`` 视为该维度满分；否则线性归一到 [0, 1]。
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


# ── 强减分关键词（出现即重罚） ────────────────────────────────────────────
_ANTI_DOMINANCE_KEYWORDS = (
    "接受采访",
    "记者",
    "颁奖",
    "抽签",
    "解说员",
    "评论员",
    "现场观众",
    "本场比赛",
)

# ── 自我讲解特征（第一人称 + 教学口吻） ─────────────────────────────────
_FIRST_PERSON_PATTERN = re.compile(r"我|咱们|我们")
_SECOND_PERSON_PATTERN = re.compile(r"你|你们|大家")
_TEACHING_VERB_PATTERN = re.compile(
    r"看|来|做|放|抬|压|蹬|转|拉|推|拨|挥|发|接|跟着|注意"
)


def estimate_dominance_ratio(
    *,
    segment_text: str,
    target_coach_name: str | None,
) -> float:
    """估算分段中目标教练的讲解主导率.

    Args:
        segment_text: 分段对应的转录文本（已由 segment_text_provider 切片）
        target_coach_name: 目标教练姓名；可为 ``None`` 时跳过姓名扣分维度

    Returns:
        ``[0.0, 1.0]`` 的 dominance ratio；空文本返回 ``0.5`` 表中性兜底
    """
    text = (segment_text or "").strip()
    if not text:
        return 0.5

    text_len = len(text)

    # 1) 强减分：采访 / 旁白特征词
    for kw in _ANTI_DOMINANCE_KEYWORDS:
        if kw in text:
            return 0.0

    # 2) 教练姓名出现频次（第三人称介绍信号；仅当姓名非通用词时扣）
    name_penalty = 0.0
    if target_coach_name and target_coach_name.strip():
        name = target_coach_name.strip()
        if 2 <= len(name) <= 10:  # 过滤过短/过长的潜在脏数据
            name_count = text.count(name)
            # 出现 >=3 次 → 介绍/旁白概率高
            if name_count >= 3:
                name_penalty = min(0.4, name_count * 0.1)

    # 3) 第一/二人称密度（教学口吻特征）
    first_count = len(_FIRST_PERSON_PATTERN.findall(text))
    second_count = len(_SECOND_PERSON_PATTERN.findall(text))
    teach_count = len(_TEACHING_VERB_PATTERN.findall(text))

    # 归一到"每 100 字符的命中数"
    chars_per_100 = max(text_len, 1) / 100.0
    first_density = first_count / chars_per_100
    second_density = second_count / chars_per_100
    teach_density = teach_count / chars_per_100

    # 加权：教学动词与第二人称权重高（直接反映教学行为）
    teaching_signal = min(1.0, (
        first_density * 0.10
        + second_density * 0.15
        + teach_density * 0.20
    ))

    # 基线 0.5（中性）+ 教学信号 - 姓名扣分
    score = 0.5 + teaching_signal - name_penalty
    return max(0.0, min(1.0, score))


__all__ = ["estimate_dominance_ratio"]
