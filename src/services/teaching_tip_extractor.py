"""TeachingTipExtractor — LLM-based teaching tip extraction from audio transcripts.

Flow:
  1. If transcript is empty → return []
  2. Concatenate sentences → call LLM to judge if technical coaching content exists
  3. If is_technical=False → return [] with reason logged
  4. Call LLM to extract tips grouped by tech_phase → return list[TeachingTipData]
  5. On any LLM error / timeout / JSON parse failure → log warning, return []

LLM backend priority (configured via .env):
  1. Venus Proxy  (venus_token + venus_base_url)  — raw HTTP, no openai SDK
  2. OpenAI-compatible (openai_api_key + base_url) — openai SDK (DeepSeek / OpenAI)
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from src.services.llm_client import LlmClient, LlmError

logger = logging.getLogger(__name__)

# Valid tech_phase values
VALID_TECH_PHASES = frozenset(
    ["preparation", "contact", "follow_through", "footwork", "general"]
)

# Prompt for judging whether transcript has technical coaching content
_IS_TECHNICAL_PROMPT = """你是一位乒乓球技术分析助手。请判断以下转录文本是否包含技术性教学内容（如动作要领、技术要点、纠错指导等），而不是纯示范或无关内容。

转录文本：
{transcript}

请以 JSON 格式回答：
{{"is_technical": true或false, "reason": "一句话说明原因"}}

只输出 JSON，不要其他内容。"""

# Prompt for extracting teaching tips by tech_phase
_EXTRACT_TIPS_PROMPT = """你是一位乒乓球技术要点提炼专家。请从以下教练讲解转录文本中，提炼该技术的核心教学建议条目。

转录文本（动作类型: {action_type}）：
{transcript}

要求：
1. 按技术阶段分组，每个阶段可有多条
2. tech_phase 只能是以下值之一：preparation（引拍/准备）、contact（击球瞬间）、follow_through（随挥/收拍）、footwork（步法/移动）、general（通用）
3. tip_text 为完整的指导性建议句子，中文，非专业运动员能理解
4. confidence 为你对该要点准确性的置信度（0.0-1.0）
5. 只提炼明确的技术指导，不要臆造内容
6. 如果某阶段内容不足，可以不包含该阶段

请以 JSON 格式回答（纯 JSON，不要 markdown 代码块）：
{{"tips": [{{"tech_phase": "...", "tip_text": "...", "confidence": 0.0}}]}}"""


@dataclass
class TeachingTipData:
    """Extracted teaching tip ready to be persisted to teaching_tips table."""
    task_id: uuid.UUID
    action_type: str
    tech_phase: str
    tip_text: str
    confidence: float
    source_type: str = "auto"


class TeachingTipExtractor:
    """Extracts structured teaching tips from audio transcript using LLM."""

    def __init__(self, llm_client: LlmClient) -> None:
        self._client = llm_client

    @classmethod
    def from_settings(cls) -> "TeachingTipExtractor":
        """Create extractor from application settings."""
        return cls(llm_client=LlmClient.from_settings())

    def extract(
        self,
        sentences: list[dict],
        action_type: str,
        task_id: uuid.UUID,
    ) -> list[TeachingTipData]:
        """Extract teaching tips from transcript sentences.

        Args:
            sentences: List of sentence dicts with 'text', 'start', 'end', 'confidence'
            action_type: The action type label (e.g. 'forehand_topspin')
            task_id: Source expert video task UUID

        Returns:
            List of TeachingTipData, empty list on failure or no technical content.
        """
        if not sentences:
            return []

        transcript_text = " ".join(
            s["text"] for s in sentences if s.get("text", "").strip()
        )
        if not transcript_text.strip():
            return []

        start_time = time.monotonic()
        try:
            # Step 1: judge if technical coaching content exists
            is_technical, judge_tokens = self._judge_is_technical(transcript_text)
            if not is_technical:
                logger.info(
                    "teaching_tip_extractor no_technical_content task_id=%s action_type=%s",
                    task_id,
                    action_type,
                )
                return []

            # Step 2: extract tips grouped by tech_phase
            tips_data, extract_tokens = self._extract_tips(transcript_text, action_type)

            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            result = [
                TeachingTipData(
                    task_id=task_id,
                    action_type=action_type,
                    tech_phase=t["tech_phase"],
                    tip_text=t["tip_text"],
                    confidence=float(t.get("confidence", 0.8)),
                )
                for t in tips_data
                if t.get("tech_phase") in VALID_TECH_PHASES and t.get("tip_text")
            ]

            logger.info(
                "teaching_tip_extractor done task_id=%s action_type=%s "
                "backend=%s tip_count=%d elapsed_ms=%d "
                "judge_tokens=%d extract_tokens=%d",
                task_id,
                action_type,
                self._client._backend,
                len(result),
                elapsed_ms,
                judge_tokens,
                extract_tokens,
            )
            return result

        except LlmError as exc:
            logger.warning(
                "teaching_tip_extractor llm_error task_id=%s action_type=%s error=%s",
                task_id,
                action_type,
                exc,
            )
            return []
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning(
                "teaching_tip_extractor parse_error task_id=%s action_type=%s error=%s",
                task_id,
                action_type,
                exc,
            )
            return []

    def _judge_is_technical(self, transcript: str) -> tuple[bool, int]:
        """Ask LLM whether the transcript contains technical coaching content."""
        prompt = _IS_TECHNICAL_PROMPT.format(transcript=transcript[:3000])
        content, tokens = self._client.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            json_mode=True,
        )
        data = json.loads(content or "{}")
        return bool(data.get("is_technical", False)), tokens

    def _extract_tips(self, transcript: str, action_type: str) -> tuple[list[dict], int]:
        """Ask LLM to extract tips grouped by tech_phase."""
        prompt = _EXTRACT_TIPS_PROMPT.format(
            transcript=transcript[:4000],
            action_type=action_type,
        )
        content, tokens = self._client.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            json_mode=True,
        )
        data = json.loads(content or '{"tips": []}')
        tips = data.get("tips", [])
        if not isinstance(tips, list):
            tips = []
        return tips, tokens
