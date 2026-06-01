"""Feature-023 — TechClassifier V2 单元测试（不依赖真实 DB / LLM）.

T024:
  - test_keyword_match_hits_dictionary
  - test_llm_fallback_returns_dictionary_action
  - test_llm_returns_invalid_action_falls_back_to_unclassified
  - test_low_confidence_falls_back_to_unclassified
  - test_unclassified_keeps_categories_null
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.services.action_dictionary_service import (
    ActionDictionaryService,
    ActionEntry,
)
from src.services.tech_classifier import (
    ClassificationResultV2,
    TechClassifier,
)


pytestmark = pytest.mark.asyncio


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_rules_file(tmp_path: Path) -> Path:
    """构造测试用规则文件：rule key 为 action 名，value 为关键词列表."""
    rules = {
        "高吊弧圈球": ["高吊", "高调"],
        "拧": ["拧拉", "台内拧"],
        "削球": ["削球", "削"],
    }
    p = tmp_path / "rules.json"
    p.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    return p


def _build_action_dict_mock() -> ActionDictionaryService:
    """构造 mock ActionDictionaryService，含 3 个动作（其中"高吊弧圈球"跨手部重名）."""
    entries = [
        ActionEntry("横拍", "反胶", "正手·进攻", "高吊弧圈球"),
        ActionEntry("横拍", "反胶", "反手·进攻", "高吊弧圈球"),
        ActionEntry("横拍", "反胶", "反手·进攻", "拧"),
        ActionEntry("横拍", "反胶", "正手·防御", "削球"),
        ActionEntry("横拍", "反胶", "反手·防御", "削球"),
    ]

    class _MockDict(ActionDictionaryService):
        def __init__(self):
            super().__init__(session_factory=lambda: None)
            self._cache = entries
            self._action_index = {
                (e.category_l1, e.category_l2, e.category_l3, e.action): e
                for e in entries
            }
            self._by_action = {}
            for e in entries:
                self._by_action.setdefault(e.action, []).append(e)

        async def load_all(self, *, force=False):
            return self._cache

    return _MockDict()


@pytest.fixture
def llm_mock_dict_action():
    """LLM 返回有效字典 action（高置信度）."""
    mock = MagicMock()
    mock.chat.return_value = (
        json.dumps(
            {
                "category_l1": "横拍",
                "category_l2": "反胶",
                "category_l3": "正手·进攻",
                "action": "高吊弧圈球",
                "confidence": 0.85,
                "reason": "test",
            }
        ),
        100,  # tokens
    )
    return mock


@pytest.fixture
def llm_mock_invalid_action():
    """LLM 返回非字典 action（应降级 unclassified）."""
    mock = MagicMock()
    mock.chat.return_value = (
        json.dumps(
            {
                "category_l1": "横拍",
                "category_l2": "反胶",
                "category_l3": "正手·进攻",
                "action": "不存在的动作名",
                "confidence": 0.9,
                "reason": "test",
            }
        ),
        50,
    )
    return mock


@pytest.fixture
def llm_mock_low_conf():
    """LLM 返回字典 action 但置信度 < 0.5（应降级 unclassified）."""
    mock = MagicMock()
    mock.chat.return_value = (
        json.dumps(
            {
                "category_l1": "横拍",
                "category_l2": "反胶",
                "category_l3": "反手·进攻",
                "action": "拧",
                "confidence": 0.3,
                "reason": "test",
            }
        ),
        50,
    )
    return mock


# ── 测试用例 ──────────────────────────────────────────────────────────


async def test_keyword_match_hits_dictionary(tmp_rules_file: Path) -> None:
    """关键词匹配「拧拉」（唯一手部）→ 反手·进攻·拧."""
    classifier = TechClassifier(
        rules_path=str(tmp_rules_file),
        action_dict=_build_action_dict_mock(),
        llm_client=None,
    )
    result = await classifier.classify(
        "01_反手拧拉_台内挑.mp4", "孙浩泓课程"
    )
    assert isinstance(result, ClassificationResultV2)
    assert result.action == "拧"
    assert result.category_l1 == "横拍"
    assert result.category_l3 == "反手·进攻"
    assert result.classification_source == "rule"
    assert result.confidence == 1.0


async def test_keyword_match_disambiguates_by_filename(
    tmp_rules_file: Path,
) -> None:
    """跨手部重名「高吊弧圈球」根据文件名「正手」消歧."""
    classifier = TechClassifier(
        rules_path=str(tmp_rules_file),
        action_dict=_build_action_dict_mock(),
        llm_client=None,
    )
    result = await classifier.classify(
        "10_正手高吊弧圈球.mp4", "孙浩泓课程"
    )
    assert result.action == "高吊弧圈球"
    assert result.category_l3 == "正手·进攻"
    assert result.classification_source == "rule"


async def test_keyword_ambiguous_falls_back_to_llm(
    tmp_rules_file: Path,
    llm_mock_dict_action,
) -> None:
    """关键词匹配但跨手部重名且无文件名消歧线索 → fall through 到 LLM."""
    classifier = TechClassifier(
        rules_path=str(tmp_rules_file),
        action_dict=_build_action_dict_mock(),
        llm_client=llm_mock_dict_action,
    )
    # 文件名仅含「高吊」无「正手/反手」消歧字
    result = await classifier.classify("高吊弧圈球解析.mp4", "课程系列")
    assert result.classification_source == "llm"
    assert result.action == "高吊弧圈球"


async def test_llm_fallback_returns_dictionary_action(
    tmp_rules_file: Path,
    llm_mock_dict_action,
) -> None:
    """关键词无命中 → LLM 兜底 → 字典内的 action."""
    classifier = TechClassifier(
        rules_path=str(tmp_rules_file),
        action_dict=_build_action_dict_mock(),
        llm_client=llm_mock_dict_action,
    )
    result = await classifier.classify("无关文件名.mp4", "课程")
    assert result.classification_source == "llm"
    assert result.action == "高吊弧圈球"
    assert result.category_l1 == "横拍"
    assert result.confidence == 0.85


async def test_llm_returns_invalid_action_falls_back_to_unclassified(
    tmp_rules_file: Path,
    llm_mock_invalid_action,
) -> None:
    """LLM 返回字典外 action → 降级 unclassified."""
    classifier = TechClassifier(
        rules_path=str(tmp_rules_file),
        action_dict=_build_action_dict_mock(),
        llm_client=llm_mock_invalid_action,
    )
    result = await classifier.classify("无关.mp4", "课程")
    assert result.action == "unclassified"
    assert result.classification_source == "llm"
    assert result.confidence == 0.0


async def test_low_confidence_falls_back_to_unclassified(
    tmp_rules_file: Path,
    llm_mock_low_conf,
) -> None:
    """LLM 返回字典内 action 但 confidence < 0.5 → 降级 unclassified."""
    classifier = TechClassifier(
        rules_path=str(tmp_rules_file),
        action_dict=_build_action_dict_mock(),
        llm_client=llm_mock_low_conf,
    )
    result = await classifier.classify("无关.mp4", "课程")
    assert result.action == "unclassified"
    assert result.classification_source == "llm"
    assert result.confidence == 0.3


async def test_unclassified_keeps_categories_null(
    tmp_rules_file: Path,
) -> None:
    """无 LLM 客户端 + 无规则匹配 → unclassified；四级 l1/l2/l3 全为 None."""
    classifier = TechClassifier(
        rules_path=str(tmp_rules_file),
        action_dict=_build_action_dict_mock(),
        llm_client=None,
    )
    result = await classifier.classify("xxx.mp4", "课程")
    assert result.is_unclassified
    assert result.action == "unclassified"
    assert result.category_l1 is None
    assert result.category_l2 is None
    assert result.category_l3 is None
