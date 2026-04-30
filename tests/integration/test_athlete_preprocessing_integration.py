"""Integration test for Feature-020 US2 athlete preprocessing (T029).

Flow:
  seed 1 条 athlete_video_classifications → submit → mock F-016 orchestrator 为快速成功
  → 断言 preprocessed=true + preprocessing_job_id 被回写。

Mocks the preprocessing Celery dispatch so no real ffmpeg/COS happens.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings
from src.db import session as session_module
from src.models.athlete import Athlete
from src.models.athlete_video_classification import AthleteVideoClassification
from src.models.video_preprocessing_job import (
    PreprocessingJobStatus,
    VideoPreprocessingJob,
)


@pytest_asyncio.fixture
async def AsyncSessionFactory():
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
class TestAthletePreprocessingIntegration:

    async def test_submit_marks_preprocessed_on_success(self, AsyncSessionFactory):
        from src.services import preprocessing_service as _ps
        from src.services.athlete_submission_service import (
            submit_athlete_preprocessing,
        )

        # ── Seed: 创建 Athlete + AthleteVideoClassification 行 ──
        cos_key = f"charhuang/tt_video/athletes/IntegTest-{uuid.uuid4()}/forehand.mp4"
        async with AsyncSessionFactory() as session:
            athlete = Athlete(name=f"IntegAthlete-{uuid.uuid4().hex[:6]}", bio="test")
            session.add(athlete)
            await session.flush()
            avc = AthleteVideoClassification(
                cos_object_key=cos_key,
                athlete_id=athlete.id,
                athlete_name=athlete.name,
                name_source="map",
                tech_category="forehand_attack",
                classification_source="rule",
                classification_confidence=1.0,
                preprocessed=False,
            )
            session.add(avc)
            await session.commit()
            avc_id = avc.id

        # ── Mock F-016 底座 create_or_reuse 为快速成功 + chain 不入队 ──
        # 为满足 FK，先插入一条真实的 video_preprocessing_jobs 行
        from src.utils.time_utils import now_cst as _now

        async with AsyncSessionFactory() as session:
            fake_job = VideoPreprocessingJob(
                cos_object_key=cos_key,
                status=PreprocessingJobStatus.running.value,
                force=False,
                started_at=_now(),
                has_audio=False,
            )
            session.add(fake_job)
            await session.commit()
            fake_job_id = fake_job.id

        class _FakeOutcome:
            job_id = fake_job_id
            status = "running"
            reused = False
            cos_object_key = cos_key
            segment_count = None
            has_audio = None
            started_at = None
            completed_at = None

        async def _fake_create(session, **kwargs):
            return _FakeOutcome()

        with patch.object(_ps, "create_or_reuse", side_effect=_fake_create), \
             patch(
                 "src.services.athlete_submission_service._dispatch_preprocessing_chain"
             ) as mock_dispatch:
            async with AsyncSessionFactory() as session:
                out = await submit_athlete_preprocessing(
                    session, classification_id=avc_id, force=False,
                )

            assert out.job_id == fake_job_id
            assert out.reused is False
            mock_dispatch.assert_called_once()

        # ── 模拟 chain 完成：直接执行 mark_athlete_preprocessed ──
        async with AsyncSessionFactory() as session:
            await _ps.mark_athlete_preprocessed(
                session, cos_object_key=cos_key, preprocessing_job_id=fake_job_id,
            )
            await session.commit()

        # ── 断言：DB 中 preprocessed=true + preprocessing_job_id 已写 ──
        async with AsyncSessionFactory() as session:
            row = (
                await session.execute(
                    select(AthleteVideoClassification).where(
                        AthleteVideoClassification.id == avc_id
                    )
                )
            ).scalar_one()
            assert row.preprocessed is True, "preprocessed flag not flipped"
            assert row.preprocessing_job_id == fake_job_id

        # Cleanup
        async with AsyncSessionFactory() as session:
            await session.execute(
                AthleteVideoClassification.__table__.delete().where(
                    AthleteVideoClassification.id == avc_id
                )
            )
            await session.execute(
                VideoPreprocessingJob.__table__.delete().where(
                    VideoPreprocessingJob.id == fake_job_id
                )
            )
            await session.execute(
                Athlete.__table__.delete().where(Athlete.name == row.athlete_name)
            )
            await session.commit()
