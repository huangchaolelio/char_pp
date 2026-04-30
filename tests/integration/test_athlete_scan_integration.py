"""Integration test for Feature-020 athlete scan end-to-end (T020).

Flow:
  mock COS list → CosAthleteScanner.scan_full → 断言 DB 行
  + 教练侧表不变（SC-006）
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings
from src.db import session as session_module
from src.models.athlete import Athlete
from src.models.athlete_video_classification import AthleteVideoClassification
from src.models.coach_video_classification import CoachVideoClassification


@dataclass
class _ClfResult:
    tech_category: str
    classification_source: str
    confidence: float


def _fake_cos_objects() -> list[dict]:
    """3 条运动员素材：2 个不同运动员 + 1 条重复运动员不同视频."""
    prefix = "charhuang/tt_video/athletes"
    return [
        {"Key": f"{prefix}/张三/正手攻球01.mp4", "Size": 1000},
        {"Key": f"{prefix}/张三/反手拉球01.mp4", "Size": 2000},
        {"Key": f"{prefix}/李四/发球01.mp4", "Size": 1500},
    ]


@pytest_asyncio.fixture
async def async_session_factory():
    """Per-test engine + 覆盖 src.db.session.AsyncSessionFactory，避免跨 event loop."""
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=False,
    )
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    orig_engine = session_module.engine
    orig_factory = session_module.AsyncSessionFactory
    session_module.engine = engine
    session_module.AsyncSessionFactory = factory
    try:
        yield factory
    finally:
        session_module.engine = orig_engine
        session_module.AsyncSessionFactory = orig_factory
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
class TestAthleteScanEndToEnd:

    async def test_full_scan_inserts_rows_isolated(self, async_session_factory):
        from src.services.cos_athlete_scanner import CosAthleteScanner

        # Seed 清理：删除此测试可能遗留的 seed 数据
        async with async_session_factory() as session:
            await session.execute(
                AthleteVideoClassification.__table__.delete().where(
                    AthleteVideoClassification.cos_object_key.like(
                        "charhuang/tt_video/athletes/%"
                    )
                )
            )
            await session.execute(
                Athlete.__table__.delete().where(Athlete.name.in_(["张三", "李四"]))
            )
            await session.commit()

        # 抓拍教练侧表基线行数
        async with async_session_factory() as session:
            coach_cnt_before = int(
                (await session.execute(
                    select(func.count()).select_from(CoachVideoClassification)
                )).scalar_one()
            )

        # Build scanner with in-memory athlete_map
        classifier = MagicMock()
        classifier.classify.return_value = _ClfResult(
            tech_category="forehand_attack",
            classification_source="rule",
            confidence=1.0,
        )
        scanner = CosAthleteScanner(
            athlete_map={"张三": "张三", "李四": "李四"},
            cos_root_prefix="charhuang/tt_video/athletes/",
            tech_classifier=classifier,
        )

        # Mock COS listing
        with patch.object(scanner, "_list_all_mp4s", return_value=_fake_cos_objects()):
            async with async_session_factory() as session:
                stats = await scanner.scan_full(session)

        assert stats.scanned == 3
        assert stats.inserted == 3
        assert stats.errors == 0

        async with async_session_factory() as session:
            ath_cnt = int(
                (await session.execute(
                    select(func.count()).select_from(Athlete).where(
                        Athlete.name.in_(["张三", "李四"])
                    )
                )).scalar_one()
            )
            avc_cnt = int(
                (await session.execute(
                    select(func.count()).select_from(AthleteVideoClassification).where(
                        AthleteVideoClassification.cos_object_key.like(
                            "charhuang/tt_video/athletes/%"
                        )
                    )
                )).scalar_one()
            )
            coach_cnt_after = int(
                (await session.execute(
                    select(func.count()).select_from(CoachVideoClassification)
                )).scalar_one()
            )

        # SC-006：运动员侧插入 2 个 athlete + 3 条素材；教练侧毫不受扰
        assert ath_cnt == 2, f"expected 2 athletes, got {ath_cnt}"
        assert avc_cnt == 3, f"expected 3 classifications, got {avc_cnt}"
        assert coach_cnt_after == coach_cnt_before, (
            f"SC-006 violated: coach table changed "
            f"{coach_cnt_before}→{coach_cnt_after}"
        )

        # Cleanup
        async with async_session_factory() as session:
            await session.execute(
                AthleteVideoClassification.__table__.delete().where(
                    AthleteVideoClassification.cos_object_key.like(
                        "charhuang/tt_video/athletes/%"
                    )
                )
            )
            await session.execute(
                Athlete.__table__.delete().where(Athlete.name.in_(["张三", "李四"]))
            )
            await session.commit()
