"""Feature-023 — TechClassifier V2 端到端集成测试.

T025: 使用真实 tech_actions 字典数据（asyncpg 直连读取） + mock LLM，
端到端验证「文件名 → 四级输出」一致性，同时绕过 SQLAlchemy 跨 event loop 问题.

⚠️ 前置条件：alembic 已 upgrade 到 0022 + tech_actions 字典 56 行已 seed.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import asyncpg
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

_DSN = "postgresql://postgres:password@localhost:5432/coaching_db"


# ── Fixtures ──────────────────────────────────────────────────────────────


async def _load_dict_via_asyncpg() -> list[ActionEntry]:
    """直连 asyncpg 读取 tech_actions 字典（避开 SQLAlchemy 跨 loop 问题）."""
    conn = await asyncpg.connect(_DSN)
    try:
        rows = await conn.fetch(
            "SELECT category_l1, category_l2, category_l3, action FROM tech_actions"
        )
    finally:
        await conn.close()
    return [
        ActionEntry(
            category_l1=r["category_l1"],
            category_l2=r["category_l2"],
            category_l3=r["category_l3"],
            action=r["action"],
        )
        for r in rows
    ]


@pytest.fixture
async def real_action_dict() -> ActionDictionaryService:
    """从真实 DB 加载 56 行字典，注入 ActionDictionaryService 内部缓存."""
    entries = await _load_dict_via_asyncpg()

    class _PreloadedDict(ActionDictionaryService):
        def __init__(self, prelo):
            super().__init__(session_factory=lambda: None)
            self._cache = prelo
            self._action_index = {
                (e.category_l1, e.category_l2, e.category_l3, e.action): e
                for e in prelo
            }
            self._by_action = {}
            for e in prelo:
                self._by_action.setdefault(e.action, []).append(e)

        async def load_all(self, *, force=False):
            return self._cache

    return _PreloadedDict(entries)


@pytest.fixture
def rules_file() -> Path:
    """构造测试用规则：rule key 与字典 action 名严格对齐."""
    rules = {
        "高吊弧圈球": ["高吊"],
        "前冲弧圈球": ["前冲"],
        "拧": ["拧拉", "台内拧"],
        "削球": ["削球"],
    }
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    )
    json.dump(rules, f, ensure_ascii=False)
    f.close()
    return Path(f.name)


@pytest.fixture
def llm_returns_unclassified():
    """LLM 返回低置信度场景 → 降级 unclassified."""
    mock = MagicMock()
    mock.chat.return_value = (
        json.dumps(
            {
                "category_l1": "横拍",
                "category_l2": "反胶",
                "category_l3": "正手·进攻",
                "action": "高吊弧圈球",
                "confidence": 0.0,
                "reason": "test fallback",
            }
        ),
        50,
    )
    return mock


# ── 测试用例：5 个动作样本端到端 ──────────────────────────────────────


@pytest.mark.parametrize(
    "filename,expected_action,expected_l3",
    [
        ("01_正手高吊弧圈球.mp4", "高吊弧圈球", "正手·进攻"),
        ("02_反手高吊弧圈球.mp4", "高吊弧圈球", "反手·进攻"),
        ("03_正手前冲弧圈球.mp4", "前冲弧圈球", "正手·进攻"),
        ("04_反手台内拧拉.mp4", "拧", "反手·进攻"),
        ("05_反手削球.mp4", "削球", "反手·防御"),
    ],
)
async def test_keyword_classification_against_real_dictionary(
    filename: str,
    expected_action: str,
    expected_l3: str,
    real_action_dict: ActionDictionaryService,
    rules_file: Path,
) -> None:
    """5 个动作样本通过 keyword 匹配命中真实字典."""
    classifier = TechClassifier(
        rules_path=str(rules_file),
        action_dict=real_action_dict,
        llm_client=None,
    )
    result = await classifier.classify(filename, "测试课程")
    assert isinstance(result, ClassificationResultV2)
    assert (
        result.action == expected_action
    ), f"{filename} 期望 {expected_action}，实际 {result.action}"
    assert result.category_l3 == expected_l3
    assert result.classification_source == "rule"
    assert result.confidence == 1.0


async def test_unclassifiable_filename_falls_back_to_unclassified(
    real_action_dict: ActionDictionaryService,
    rules_file: Path,
    llm_returns_unclassified,
) -> None:
    """无法识别的文件名 → LLM 返回低置信度 → unclassified."""
    classifier = TechClassifier(
        rules_path=str(rules_file),
        action_dict=real_action_dict,
        llm_client=llm_returns_unclassified,
    )
    result = await classifier.classify(
        "abc_xyz_no_keyword_match.mp4", "未知课程"
    )
    assert result.action == "unclassified"
    assert result.category_l1 is None
    assert result.category_l3 is None


async def test_dictionary_loaded_56_rows() -> None:
    """sanity check：真实数据库字典 56 行齐全（asyncpg 直连）."""
    entries = await _load_dict_via_asyncpg()
    assert len(entries) == 56
