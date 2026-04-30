"""Feature-020 · T066 合约测试 · GET /api/v1/tasks/{task_id} athlete_diagnosis 专属字段.

覆盖断言:
  1. 对 ``task_type='athlete_diagnosis'`` 任务，响应包含
     ``athlete_video_classification_id`` / ``tech_category`` 两字段（非 None）
  2. 若 ``status='success'``，``standard_version`` 字段填充为 int
  3. 若 ``status='pending'``（尚未诊断完成），``standard_version`` 为 None
  4. 对 ``task_type='kb_extraction'`` / 其他任务，三字段均为 None（隔离性）
  5. 信封遵循 SuccessEnvelope + data 含 schema 允许的三字段键

策略: 使用 FastAPI TestClient + 真实 DB，seed 完整 AVC/VPJ/Report/Task 链路。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

from src.api.main import app
from src.config import get_settings
from src.db import session as session_module
from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
from src.models.athlete import Athlete
from src.models.athlete_video_classification import AthleteVideoClassification
from src.models.diagnosis_report import DiagnosisReport
from src.models.tech_standard import SourceQuality, StandardStatus, TechStandard
from src.models.video_preprocessing_job import VideoPreprocessingJob
from tests.contract.conftest import assert_success_envelope


_COS_KEY_A = "charhuang/tt_video/athletes/__t066_a/正手攻球.mp4"
_COS_KEY_B = "charhuang/tt_video/athletes/__t066_b/反手推挡.mp4"
_ATH_NAMES = ["__t066_a", "__t066_b"]
_TECH = "forehand_attack"


@pytest_asyncio.fixture
async def session_factory():
    """Per-test engine + factory + 覆盖 src.db.session.AsyncSessionFactory
    避免 asyncpg 跨 event loop.
    """
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


async def _cleanup(factory) -> None:
    async with factory() as session:
        await session.execute(
            delete(DiagnosisReport).where(
                DiagnosisReport.cos_object_key.in_([_COS_KEY_A, _COS_KEY_B])
            )
        )
        await session.execute(
            delete(AnalysisTask).where(
                AnalysisTask.cos_object_key.in_([_COS_KEY_A, _COS_KEY_B])
            )
        )
        await session.execute(
            delete(AthleteVideoClassification).where(
                AthleteVideoClassification.cos_object_key.in_(
                    [_COS_KEY_A, _COS_KEY_B]
                )
            )
        )
        await session.execute(
            delete(VideoPreprocessingJob).where(
                VideoPreprocessingJob.cos_object_key.in_([_COS_KEY_A, _COS_KEY_B])
            )
        )
        await session.execute(delete(Athlete).where(Athlete.name.in_(_ATH_NAMES)))
        await session.commit()


async def _ensure_active_standard(session) -> tuple[int, int]:
    """获取或创建 forehand_attack 的 active standard；返回 (id, version)."""
    existing = (
        await session.execute(
            select(TechStandard).where(
                TechStandard.tech_category == _TECH,
                TechStandard.status == StandardStatus.active,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing.id, existing.version
    std = TechStandard(
        tech_category=_TECH,
        version=1,
        status=StandardStatus.active,
        source_quality=SourceQuality.low,
        built_from_expert_count=1,
    )
    session.add(std)
    await session.flush()
    return std.id, std.version


async def _seed_pending_task(factory) -> uuid.UUID:
    """Seed 一个 pending 状态的 athlete_diagnosis 任务.

    ——不建 DiagnosisReport（standard_version 必须为 None）
    """
    async with factory() as session:
        ath = Athlete(name=_ATH_NAMES[0], bio=_ATH_NAMES[0], created_via="athlete_scan")
        session.add(ath)
        await session.flush()

        vpj = VideoPreprocessingJob(
            cos_object_key=_COS_KEY_A,
            status="success",
            business_phase="TRAINING",
            business_step="preprocess_video",
        )
        session.add(vpj)
        await session.flush()

        avc = AthleteVideoClassification(
            cos_object_key=_COS_KEY_A,
            athlete_id=ath.id,
            athlete_name=_ATH_NAMES[0],
            name_source="fallback",
            tech_category=_TECH,
            classification_source="rule",
            classification_confidence=1.0,
            preprocessed=True,
            preprocessing_job_id=vpj.id,
        )
        session.add(avc)
        await session.flush()

        task = AnalysisTask(
            id=uuid.uuid4(),
            task_type=TaskType.athlete_diagnosis,
            video_filename=_COS_KEY_A.rsplit("/", 1)[-1],
            video_size_bytes=0,
            video_storage_uri=_COS_KEY_A,
            cos_object_key=_COS_KEY_A,
            status=TaskStatus.pending,
            submitted_via="single",
        )
        session.add(task)
        await session.commit()
        return task.id


async def _seed_success_task(factory) -> tuple[uuid.UUID, int]:
    """Seed 一个 success 状态的 athlete_diagnosis 任务 + 1 份 DiagnosisReport."""
    async with factory() as session:
        std_id, std_ver = await _ensure_active_standard(session)

        ath = Athlete(name=_ATH_NAMES[1], bio=_ATH_NAMES[1], created_via="athlete_scan")
        session.add(ath)
        await session.flush()

        vpj = VideoPreprocessingJob(
            cos_object_key=_COS_KEY_B,
            status="success",
            business_phase="TRAINING",
            business_step="preprocess_video",
        )
        session.add(vpj)
        await session.flush()

        avc = AthleteVideoClassification(
            cos_object_key=_COS_KEY_B,
            athlete_id=ath.id,
            athlete_name=_ATH_NAMES[1],
            name_source="fallback",
            tech_category=_TECH,
            classification_source="rule",
            classification_confidence=1.0,
            preprocessed=True,
            preprocessing_job_id=vpj.id,
        )
        session.add(avc)
        await session.flush()

        report = DiagnosisReport(
            tech_category=_TECH,
            standard_id=std_id,
            standard_version=std_ver,
            video_path=_COS_KEY_B,
            overall_score=80.0,
            strengths_summary="[]",
            cos_object_key=_COS_KEY_B,
            preprocessing_job_id=vpj.id,
            source="athlete_pipeline",
        )
        session.add(report)
        await session.flush()

        task = AnalysisTask(
            id=uuid.uuid4(),
            task_type=TaskType.athlete_diagnosis,
            video_filename=_COS_KEY_B.rsplit("/", 1)[-1],
            video_size_bytes=0,
            video_storage_uri=_COS_KEY_B,
            cos_object_key=_COS_KEY_B,
            status=TaskStatus.success,
            submitted_via="single",
        )
        session.add(task)
        await session.commit()
        return task.id, std_ver


@pytest_asyncio.fixture
async def client(session_factory):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.mark.asyncio
async def test_pending_athlete_task_returns_classification_and_category_without_version(
    session_factory, client,
):
    """Pending 诊断任务：分类 ID + tech_category 填充，standard_version 为 None."""
    await _cleanup(session_factory)
    try:
        task_id = await _seed_pending_task(session_factory)

        resp = await client.get(f"/api/v1/tasks/{task_id}")
        assert resp.status_code == 200, resp.text
        data = assert_success_envelope(resp.json())

        assert data["task_type"] == "athlete_diagnosis"
        assert data["status"] == "pending"
        # T066 三字段
        assert "athlete_video_classification_id" in data
        assert "tech_category" in data
        assert "standard_version" in data
        assert data["athlete_video_classification_id"] is not None
        assert data["tech_category"] == _TECH
        assert data["standard_version"] is None  # 仅 success 后才填
    finally:
        await _cleanup(session_factory)


@pytest.mark.asyncio
async def test_success_athlete_task_returns_all_three_athlete_fields(
    session_factory, client,
):
    """Success 诊断任务：三字段全部填充；standard_version 来自 DiagnosisReport."""
    await _cleanup(session_factory)
    try:
        task_id, expected_std_ver = await _seed_success_task(session_factory)

        resp = await client.get(f"/api/v1/tasks/{task_id}")
        assert resp.status_code == 200, resp.text
        data = assert_success_envelope(resp.json())

        assert data["task_type"] == "athlete_diagnosis"
        assert data["status"] == "success"
        assert data["athlete_video_classification_id"] is not None
        assert data["tech_category"] == _TECH
        assert data["standard_version"] == expected_std_ver
    finally:
        await _cleanup(session_factory)


@pytest.mark.asyncio
async def test_non_athlete_task_three_fields_are_none(session_factory, client):
    """其他 task_type（kb_extraction）：三字段均为 None（隔离性）."""
    await _cleanup(session_factory)
    try:
        async with session_factory() as session:
            task = AnalysisTask(
                id=uuid.uuid4(),
                task_type=TaskType.kb_extraction,
                video_filename="whatever.mp4",
                video_size_bytes=0,
                video_storage_uri="charhuang/tt_video/__t066_kb/whatever.mp4",
                cos_object_key="charhuang/tt_video/__t066_kb/whatever.mp4",
                status=TaskStatus.pending,
                submitted_via="single",
            )
            session.add(task)
            await session.commit()
            task_id = task.id

        try:
            resp = await client.get(f"/api/v1/tasks/{task_id}")
            assert resp.status_code == 200, resp.text
            data = assert_success_envelope(resp.json())

            assert data["task_type"] == "kb_extraction"
            assert data["athlete_video_classification_id"] is None
            assert data["tech_category"] is None
            assert data["standard_version"] is None
        finally:
            async with session_factory() as session:
                await session.execute(
                    delete(AnalysisTask).where(AnalysisTask.id == task_id)
                )
                await session.commit()
    finally:
        await _cleanup(session_factory)
