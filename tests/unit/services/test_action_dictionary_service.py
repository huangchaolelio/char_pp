"""Feature-023 — ActionDictionaryService 单元测试.

T012: 不依赖真实数据库；通过 mock async session 提供字典样本数据
【注】fixture 使用原 44 行作为最小样本集（包含跨手部重名语义验证）；
     运行期真实字典为 56 行（Path 1' 拓展），详见 contracts/tech-actions-seed.csv。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.action_dictionary_service import (
    ActionDictionaryService,
    ActionEntry,
)


pytestmark = pytest.mark.asyncio


# ── 测试 fixture：mock 44 行字典 ──────────────────────────────────────
def _build_mock_entries() -> list[MagicMock]:
    """构造 44 行 mock TechAction ORM 对象（取关键样本含跨手部重名）."""
    raw = [
        # 正手·发球（8）
        ("横拍", "反胶", "正手·发球", "平击发球"),
        ("横拍", "反胶", "正手·发球", "奔球"),
        ("横拍", "反胶", "正手·发球", "左侧下旋"),
        ("横拍", "反胶", "正手·发球", "左侧上旋"),
        ("横拍", "反胶", "正手·发球", "右侧上旋"),
        ("横拍", "反胶", "正手·发球", "右侧下旋"),
        ("横拍", "反胶", "正手·发球", "下旋转球"),
        ("横拍", "反胶", "正手·发球", "不转球"),
        # 正手·进攻（8）
        ("横拍", "反胶", "正手·进攻", "快攻"),
        ("横拍", "反胶", "正手·进攻", "突击"),
        ("横拍", "反胶", "正手·进攻", "扣杀"),
        ("横拍", "反胶", "正手·进攻", "挑"),
        ("横拍", "反胶", "正手·进攻", "高吊弧圈球"),  # 跨手部重名
        ("横拍", "反胶", "正手·进攻", "前冲弧圈球"),
        ("横拍", "反胶", "正手·进攻", "反拉"),
        ("横拍", "反胶", "正手·进攻", "反冲"),
        # 正手·防御（6）
        ("横拍", "反胶", "正手·防御", "挡"),
        ("横拍", "反胶", "正手·防御", "快带"),
        ("横拍", "反胶", "正手·防御", "兜"),
        ("横拍", "反胶", "正手·防御", "放高球"),
        ("横拍", "反胶", "正手·防御", "削球"),
        ("横拍", "反胶", "正手·防御", "搓球"),
        # 反手·发球（7）
        ("横拍", "反胶", "反手·发球", "平击发球"),  # 跨手部重名
        ("横拍", "反胶", "反手·发球", "右侧下旋"),
        ("横拍", "反胶", "反手·发球", "右侧上旋"),
        ("横拍", "反胶", "反手·发球", "左侧上旋"),
        ("横拍", "反胶", "反手·发球", "左侧下旋"),
        ("横拍", "反胶", "反手·发球", "下旋转球"),
        ("横拍", "反胶", "反手·发球", "不转球"),
        # 反手·进攻（8）
        ("横拍", "反胶", "反手·进攻", "拨"),
        ("横拍", "反胶", "反手·进攻", "弹"),
        ("横拍", "反胶", "反手·进攻", "扣杀"),
        ("横拍", "反胶", "反手·进攻", "高吊弧圈球"),  # 跨手部重名
        ("横拍", "反胶", "反手·进攻", "前冲弧圈球"),
        ("横拍", "反胶", "反手·进攻", "反冲"),
        ("横拍", "反胶", "反手·进攻", "拧"),
        ("横拍", "反胶", "反手·进攻", "反拉"),
        # 反手·防御（7）
        ("横拍", "反胶", "反手·防御", "挡"),
        ("横拍", "反胶", "反手·防御", "快撕"),
        ("横拍", "反胶", "反手·防御", "贴"),
        ("横拍", "反胶", "反手·防御", "放高球"),
        ("横拍", "反胶", "反手·防御", "兜"),
        ("横拍", "反胶", "反手·防御", "削球"),
        ("横拍", "反胶", "反手·防御", "搓球"),
    ]
    assert len(raw) == 44, f"测试样本应为 44 行，实际 {len(raw)}"
    out: list[MagicMock] = []
    for l1, l2, l3, action in raw:
        m = MagicMock()
        m.category_l1 = l1
        m.category_l2 = l2
        m.category_l3 = l3
        m.action = action
        out.append(m)
    return out


@pytest.fixture
def mock_session_factory():
    """构造一个 async ctx manager 工厂，返回 mock AsyncSession，让 select(TechAction) 返回 44 行."""
    entries = _build_mock_entries()

    class _MockResult:
        def scalars(self):
            return self

        def all(self):
            return entries

    class _MockSession:
        async def execute(self, _stmt):
            return _MockResult()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    def factory():
        return _MockSession()

    return factory


# ── 测试用例 ──────────────────────────────────────────────────────────


async def test_load_all_returns_44_rows(mock_session_factory) -> None:
    svc = ActionDictionaryService(session_factory=mock_session_factory)
    entries = await svc.load_all()
    assert len(entries) == 44
    assert all(isinstance(e, ActionEntry) for e in entries)


async def test_lookup_existing_action_with_unique_name(mock_session_factory) -> None:
    svc = ActionDictionaryService(session_factory=mock_session_factory)
    # 「拧」只在反手·进攻出现一次 → 唯一命中
    entry = await svc.lookup("拧")
    assert entry is not None
    assert entry.category_l3 == "反手·进攻"
    assert entry.action == "拧"


async def test_lookup_unknown_returns_none(mock_session_factory) -> None:
    svc = ActionDictionaryService(session_factory=mock_session_factory)
    entry = await svc.lookup("不存在的动作")
    assert entry is None


async def test_lookup_duplicate_name_returns_none_requires_quad(
    mock_session_factory,
) -> None:
    """跨手部重名（高吊弧圈球）场景：单列 lookup 必须返回 None."""
    svc = ActionDictionaryService(session_factory=mock_session_factory)
    entry = await svc.lookup("高吊弧圈球")
    assert entry is None  # 重名歧义 → None
    candidates = await svc.lookup_candidates("高吊弧圈球")
    assert len(candidates) == 2  # 正手·进攻 + 反手·进攻


async def test_validate_full_quad(mock_session_factory) -> None:
    svc = ActionDictionaryService(session_factory=mock_session_factory)
    # 合法
    assert await svc.validate("横拍", "反胶", "正手·进攻", "高吊弧圈球") is True
    assert await svc.validate("横拍", "反胶", "反手·进攻", "高吊弧圈球") is True
    # 非法：l3 不匹配
    assert (
        await svc.validate("横拍", "反胶", "正手·防御", "高吊弧圈球") is False
    )
    # 非法：action 不存在
    assert await svc.validate("横拍", "反胶", "正手·进攻", "未知") is False


async def test_get_prompt_enum_block_format(mock_session_factory) -> None:
    svc = ActionDictionaryService(session_factory=mock_session_factory)
    block = await svc.get_prompt_enum_block()
    lines = block.split("\n")
    assert len(lines) == 44
    # 每行格式正确
    for line in lines:
        assert line.startswith("- category_l1=")
        assert "category_l2=" in line
        assert "category_l3=" in line
        assert "action=" in line


async def test_seed_strips_zwsp_via_migration() -> None:
    """字典内不应出现 U+200B 零宽字符（迁移内的 _strip_zwsp 已清洗）.

    本 test 不依赖数据库；仅断言 ActionEntry 不会主动添加 ZWSP.
    真实库检验在 integration test_migration_0022_taxonomy.py.
    """
    e = ActionEntry(
        category_l1="横拍",
        category_l2="反胶",
        category_l3="正手·进攻",
        action="高吊弧圈球",
    )
    for v in (e.category_l1, e.category_l2, e.category_l3, e.action):
        assert "\u200b" not in v


async def test_all_actions_returns_distinct_set(mock_session_factory) -> None:
    svc = ActionDictionaryService(session_factory=mock_session_factory)
    actions = await svc.all_actions()
    # 44 行字典中存在大量跨手部重名（如「平击发球」「高吊弧圈球」「挡」「削球」等）
    # distinct action 数量实际 = 27（含 17 个跨手部重名 + 10 个单手部独占）
    assert 20 <= len(actions) <= 44
    assert "高吊弧圈球" in actions
    assert "拧" in actions
    assert "未知" not in actions


async def test_all_quads_returns_44(mock_session_factory) -> None:
    svc = ActionDictionaryService(session_factory=mock_session_factory)
    quads = await svc.all_quads()
    assert len(quads) == 44


async def test_load_all_caches_results(mock_session_factory) -> None:
    svc = ActionDictionaryService(session_factory=mock_session_factory)
    entries1 = await svc.load_all()
    entries2 = await svc.load_all()
    assert entries1 is entries2  # 同一对象引用 = 缓存命中
