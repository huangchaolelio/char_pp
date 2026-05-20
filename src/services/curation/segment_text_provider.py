"""Feature-021 分段文本切片：把整段 transcript 按 ms 范围切到分段文本.

输入是与 ``AudioTranscript.sentences`` 同 schema 的 ``list[dict]``（每条形如
``{"start": float_seconds, "end": float_seconds, "text": str, "confidence": float}``）；
输出是覆盖到 ``[start_ms, end_ms]`` 的拼接文本（空格分隔）。

落库 / 转写本身不在本模块——本模块只做"切片对齐"。该解耦让单元测试可纯
内存运行，不依赖 Whisper 模型加载或 DB 查询。

实际数据来源（由 :mod:`curation_service` 编排时决定）：

1. **优先**：若 ``audio_transcripts`` 表中已存在与该视频 ``preprocessing_job_id``
   关联的转写行，直接读其 ``sentences`` JSONB
2. **兜底**：若不存在 transcript，调用 :class:`SpeechRecognizer` 现转
   （沿用 F-014 ``audio_transcription`` 执行器的同款封装）
3. **降级**：若视频整体 ``has_audio=false``，所有分段返回空文本，
   ``tech_keyword`` / ``non_teaching`` 维度得分按规范配置降级
"""

from __future__ import annotations

import logging
from typing import Iterable, Sequence

logger = logging.getLogger(__name__)


def extract_segment_text(
    sentences: Sequence[dict],
    *,
    segment_start_ms: int,
    segment_end_ms: int,
) -> str:
    """从 transcript 的 sentences 列表中提取覆盖 ``[start_ms, end_ms]`` 的文本.

    Args:
        sentences: ``[{"start": s, "end": s, "text": str, ...}]`` 列表，
            ``start/end`` 单位为秒（与 Whisper / AudioTranscript 一致）；
            非 dict 条目静默跳过
        segment_start_ms: 分段起点（毫秒，含）
        segment_end_ms: 分段终点（毫秒，不含）

    Returns:
        ``" ".join(matched_texts)``——空格分隔的拼接文本；无命中时返回 ``""``。
        命中规则：句子区间与分段区间存在任意交叉（``sent.end_ms > start_ms`` 且
        ``sent.start_ms < end_ms``）。

    Raises:
        ValueError: 若 ``segment_end_ms <= segment_start_ms``。
    """
    if segment_end_ms <= segment_start_ms:
        raise ValueError(
            f"segment_end_ms ({segment_end_ms}) must be > segment_start_ms "
            f"({segment_start_ms})"
        )

    pieces: list[str] = []
    for sent in sentences:
        if not isinstance(sent, dict):
            continue
        start = sent.get("start")
        end = sent.get("end")
        text = sent.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        sent_start_ms = int(start * 1000)
        sent_end_ms = int(end * 1000)
        if sent_end_ms <= segment_start_ms or sent_start_ms >= segment_end_ms:
            continue
        pieces.append(text.strip())

    return " ".join(pieces)


def iter_segment_texts(
    sentences: Sequence[dict],
    *,
    segment_ranges_ms: Iterable[tuple[int, int]],
) -> list[str]:
    """对一批连续分段同时切片，复用一次 sentences 遍历。

    供 :mod:`curation_service` 在主路径调用，O(n_sentences * n_segments)
    可接受（通常 n<200）。
    """
    return [
        extract_segment_text(sentences, segment_start_ms=s, segment_end_ms=e)
        for s, e in segment_ranges_ms
    ]


__all__ = ["extract_segment_text", "iter_segment_texts"]
