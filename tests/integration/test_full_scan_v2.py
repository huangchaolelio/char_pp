"""Feature-023 — Full COS scan V2 集成测试.

T035:
  - test_scan_creates_four_level_records
  - test_scan_reuses_existing_preprocessing
  - test_scan_idempotent_via_cos_object_key

策略：mock COS client 返回 20 条样本 mp4 对象键，驱动 CosClassificationScanner 走
完整 scan_full / scan_incremental 路径，断言 4 级字段写入 + cos_object_key
upsert 幂等 + 预处理产物反查复用.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

from src.services.action_dictionary_service import (
    ActionDictionaryService,
    ActionEntry,
)
from src.services.cos_classification_scanner import (
    CosClassificationScanner,
    ScanStats,
)
from src.services.tech_classifier import TechClassifier


pytestmark = pytest.mark.asyncio

_DSN = "postgresql://postgres:password@localhost:5432/coaching_db"


# ── Fixtures ──────────────────────────────────────────────────────────────


async def _truncate_business_tables() -> None:
    """每个测试前清空相关业务表（避免 cos_object_key 唯一冲突干扰）."""
    conn = await asyncpg.connect(_DSN)
    try:
        await conn.execute("TRUNCATE TABLE coach_video_classifications CASCADE")
        await conn.execute("TRUNCATE TABLE coaches CASCADE")
        await conn.execute("TRUNCATE TABLE video_preprocessing_jobs CASCADE")
    finally:
        await conn.close()


async def _count(sql: str, *args) -> int:
    conn = await asyncpg.connect(_DSN)
    try:
        return int(await conn.fetchval(sql, *args) or 0)
    finally:
        await conn.close()


async def _load_dict_entries() -> list[ActionEntry]:
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


def _build_action_dict(entries: list[ActionEntry]) -> ActionDictionaryService:
    """构造 preloaded ActionDictionaryService（绕开 SQLAlchemy session)."""

    class _Pre(ActionDictionaryService):
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

    return _Pre(entries)


def _build_scanner_with_classifier(
    cos_root: str,
    coach_map: dict[str, str],
    classifier: TechClassifier,
) -> CosClassificationScanner:
    return CosClassificationScanner(
        coach_map=coach_map,
        cos_root_prefix=cos_root,
        tech_classifier=classifier,
    )


@pytest.fixture
def cos_root() -> str:
    return "test/coach_videos/"


@pytest.fixture
def coach_map() -> dict[str, str]:
    return {
        "孙浩泓课程": "孙浩泓",
        "小孙课程": "小孙",
    }


@pytest.fixture
def rules_file(tmp_path: Path) -> Path:
    rules = {
        "高吊弧圈球": ["高吊"],
        "前冲弧圈球": ["前冲"],
        "拧": ["拧拉", "台内拧"],
        "削球": ["削球"],
        "搓球": ["搓球"],
    }
    p = tmp_path / "rules.json"
    p.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture
async def classifier(rules_file: Path) -> TechClassifier:
    entries = await _load_dict_entries()
    return TechClassifier(
        rules_path=str(rules_file),
        action_dict=_build_action_dict(entries),
        llm_client=None,
    )


def _mock_cos_objects(cos_root: str) -> list[dict]:
    """构造 20 条样本 cos object dicts（含正/反手 + 跨教练）."""
    objs = []
    for i, fname in enumerate(
        [
            "01_正手高吊弧圈球.mp4",
            "02_反手高吊弧圈球.mp4",
            "03_正手前冲弧圈球.mp4",
            "04_反手台内拧拉.mp4",
            "05_反手削球.mp4",
            "06_反手搓球.mp4",
            "07_正手前冲.mp4",
            "08_正手高吊.mp4",
            "09_反手高吊.mp4",
            "10_反手拧拉.mp4",
        ]
    ):
        for coach_dir in ("孙浩泓课程", "小孙课程"):
            objs.append(
                {
                    "Key": f"{cos_root}{coach_dir}/{fname}",
                    "Size": 1024 * 1024,
                }
            )
    return objs


# ── 测试用例 ──────────────────────────────────────────────────────────


async def test_scan_creates_four_level_records(
    cos_root: str,
    coach_map: dict[str, str],
    classifier: TechClassifier,
) -> None:
    """全量扫描后，coach_video_classifications 必须填充 4 级字段."""
    await _truncate_business_tables()

    scanner = _build_scanner_with_classifier(cos_root, coach_map, classifier)

    # mock COS lister 返回 20 条对象
    fake_objects = _mock_cos_objects(cos_root)
    with patch.object(scanner, "_list_all_mp4s", return_value=fake_objects):
        # 使用 SQLAlchemy session（scanner 内部业务表写入）— 通过自建 sessionmaker
        from sqlalchemy.ext.asyncio import (
            AsyncSession,
            async_sessionmaker,
            create_async_engine,
        )

        engine = create_async_engine(
            _DSN.replace("postgresql://", "postgresql+asyncpg://"), pool_pre_ping=False
        )
        SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with SessionFactory() as session:
                stats: ScanStats = await scanner.scan_full(session)
        finally:
            await engine.dispose()

    assert stats.errors == 0, f"errors: {stats.error_detail}"
    assert stats.scanned == 20, f"scanned={stats.scanned}"
    assert stats.inserted == 20

    # 断言 4 级字段填充率 100%
    classified_cnt = await _count(
        "SELECT count(*) FROM coach_video_classifications "
        "WHERE action IS NOT NULL AND action != 'unclassified'"
    )
    total_cnt = await _count("SELECT count(*) FROM coach_video_classifications")
    assert total_cnt == 20
    assert classified_cnt == 20, f"覆盖率 {classified_cnt}/{total_cnt}"

    # 抽样检查具体一条记录的四级字段
    conn = await asyncpg.connect(_DSN)
    try:
        row = await conn.fetchrow(
            "SELECT category_l1, category_l2, category_l3, action "
            "FROM coach_video_classifications "
            "WHERE filename = '01_正手高吊弧圈球.mp4' LIMIT 1"
        )
    finally:
        await conn.close()
    assert row is not None
    assert row["action"] == "高吊弧圈球"
    assert row["category_l3"] == "正手·进攻"
    assert row["category_l1"] == "横拍"


async def test_scan_idempotent_via_cos_object_key(
    cos_root: str,
    coach_map: dict[str, str],
    classifier: TechClassifier,
) -> None:
    """全量扫描两次后，记录数不重复（cos_object_key upsert 幂等）."""
    await _truncate_business_tables()
    scanner = _build_scanner_with_classifier(cos_root, coach_map, classifier)
    fake_objects = _mock_cos_objects(cos_root)

    from sqlalchemy.ext.asyncio import (
        async_sessionmaker,
        create_async_engine,
    )

    engine = create_async_engine(
        _DSN.replace("postgresql://", "postgresql+asyncpg://"), pool_pre_ping=False
    )
    SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        with patch.object(scanner, "_list_all_mp4s", return_value=fake_objects):
            async with SessionFactory() as session:
                stats1 = await scanner.scan_full(session)
            async with SessionFactory() as session:
                stats2 = await scanner.scan_full(session)
    finally:
        await engine.dispose()

    assert stats1.inserted == 20
    # 第二次：同 cos_object_key 全部 update 而非 insert
    assert stats2.inserted == 0
    assert stats2.updated == 20

    # 总记录数仍是 20（无重复）
    total = await _count("SELECT count(*) FROM coach_video_classifications")
    assert total == 20


async def test_scan_reuses_existing_preprocessing(
    cos_root: str,
    coach_map: dict[str, str],
    classifier: TechClassifier,
) -> None:
    """增量扫描时，若 cos_object_key 已有 video_preprocessing_jobs.success，则
    新建分类记录的 preprocessed=True（FR-008 反查复用）.
    """
    await _truncate_business_tables()

    # 预先插入一条 video_preprocessing_jobs.success 记录
    target_key = f"{cos_root}孙浩泓课程/01_正手高吊弧圈球.mp4"
    conn = await asyncpg.connect(_DSN)
    try:
        await conn.execute(
            """
            INSERT INTO video_preprocessing_jobs
                (id, cos_object_key, status, business_phase, business_step,
                 started_at, created_at, updated_at)
            VALUES
                (gen_random_uuid(), $1, 'success', 'CONTENT_PREP', 'preprocess_video',
                 timezone('Asia/Shanghai', now()),
                 timezone('Asia/Shanghai', now()),
                 timezone('Asia/Shanghai', now()))
            """,
            target_key,
        )
    finally:
        await conn.close()

    scanner = _build_scanner_with_classifier(cos_root, coach_map, classifier)
    fake_objects = _mock_cos_objects(cos_root)

    from sqlalchemy.ext.asyncio import (
        async_sessionmaker,
        create_async_engine,
    )

    engine = create_async_engine(
        _DSN.replace("postgresql://", "postgresql+asyncpg://"), pool_pre_ping=False
    )
    SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        with patch.object(scanner, "_list_all_mp4s", return_value=fake_objects):
            async with SessionFactory() as session:
                stats = await scanner.scan_incremental(session)
    finally:
        await engine.dispose()

    assert stats.inserted == 20
    # 抽样检查：目标 key 的 preprocessed=True
    preprocessed_flag = await _count(
        "SELECT count(*) FROM coach_video_classifications "
        "WHERE cos_object_key = $1 AND preprocessed = true",
        target_key,
    )
    assert preprocessed_flag == 1, "FR-008 预处理反查复用未生效"
