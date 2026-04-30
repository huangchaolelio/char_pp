"""Feature-020 · T052 · GET /api/v1/diagnosis-reports 合约测试.

覆盖 contracts/athlete_reports_list.md 的 8 个断言点:
  1. 默认分页 + 按 created_at 倒序
  2. 按 athlete_id 过滤：只返回该运动员报告
  3. 按 cos_object_key 过滤：返回该素材的所有历史版本
  4. 按 preprocessing_job_id 过滤：返回该 job 下的所有诊断报告
  5. source=athlete_pipeline 过滤：不返回 legacy 旧行（SC-006 侧边验证）
  6. source=invalid → 400 INVALID_ENUM_VALUE
  7. page_size=200 → 422 VALIDATION_FAILED
  8. 同一运动员 2 次诊断同一素材 → 返回 2 条，时间倒序
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.api.main import app
from src.config import get_settings
from src.db import session as session_module
from src.models.athlete import Athlete
from src.models.athlete_video_classification import AthleteVideoClassification
from src.models.diagnosis_report import DiagnosisReport
from src.models.tech_standard import SourceQuality, StandardStatus, TechStandard
from src.models.video_preprocessing_job import VideoPreprocessingJob
from tests.contract.conftest import assert_error_envelope, assert_success_envelope


_TAG = f"__t052_{uuid.uuid4().hex[:8]}"
_COS_A = f"charhuang/tt_video/athletes/{_TAG}_a/forehand.mp4"
_COS_B = f"charhuang/tt_video/athletes/{_TAG}_b/backhand.mp4"
_ATH_NAME_A = f"{_TAG}_a"
_ATH_NAME_B = f"{_TAG}_b"
_TECH = "forehand_attack"


@pytest_asyncio.fixture
async def session_factory():
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


@pytest_asyncio.fixture
async def client(session_factory):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _ensure_standard(session) -> tuple[int, int]:
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


async def _cleanup(factory):
    async with factory() as session:
        await session.execute(
            delete(DiagnosisReport).where(
                DiagnosisReport.cos_object_key.in_([_COS_A, _COS_B])
            )
        )
        await session.execute(
            delete(AthleteVideoClassification).where(
                AthleteVideoClassification.cos_object_key.in_([_COS_A, _COS_B])
            )
        )
        await session.execute(
            delete(VideoPreprocessingJob).where(
                VideoPreprocessingJob.cos_object_key.in_([_COS_A, _COS_B])
            )
        )
        await session.execute(
            delete(Athlete).where(Athlete.name.in_([_ATH_NAME_A, _ATH_NAME_B]))
        )
        await session.commit()


async def _seed_two_athletes_with_reports(factory) -> dict:
    """Seed:
       Athlete A (_ATH_NAME_A) — 2 份诊断（同 COS key，不同时间戳）
       Athlete B (_ATH_NAME_B) — 1 份诊断（不同 COS key）
       额外插入 1 份 source='legacy' 旧报告（对应 A）用于 source 过滤隔离验证
       => 共 4 条 DiagnosisReport，其中 athlete_pipeline=3 / legacy=1
    """
    async with factory() as session:
        std_id, std_ver = await _ensure_standard(session)

        # Athletes
        ath_a = Athlete(name=_ATH_NAME_A, bio=_ATH_NAME_A, created_via="athlete_scan")
        ath_b = Athlete(name=_ATH_NAME_B, bio=_ATH_NAME_B, created_via="athlete_scan")
        session.add_all([ath_a, ath_b])
        await session.flush()

        # VPJ
        vpj_a = VideoPreprocessingJob(
            cos_object_key=_COS_A,
            status="success",
            business_phase="TRAINING",
            business_step="preprocess_video",
        )
        vpj_b = VideoPreprocessingJob(
            cos_object_key=_COS_B,
            status="success",
            business_phase="TRAINING",
            business_step="preprocess_video",
        )
        session.add_all([vpj_a, vpj_b])
        await session.flush()

        # AVC
        avc_a = AthleteVideoClassification(
            cos_object_key=_COS_A,
            athlete_id=ath_a.id,
            athlete_name=_ATH_NAME_A,
            name_source="fallback",
            tech_category=_TECH,
            classification_source="rule",
            classification_confidence=1.0,
            preprocessed=True,
            preprocessing_job_id=vpj_a.id,
        )
        avc_b = AthleteVideoClassification(
            cos_object_key=_COS_B,
            athlete_id=ath_b.id,
            athlete_name=_ATH_NAME_B,
            name_source="fallback",
            tech_category=_TECH,
            classification_source="rule",
            classification_confidence=1.0,
            preprocessed=True,
            preprocessing_job_id=vpj_b.id,
        )
        session.add_all([avc_a, avc_b])
        await session.flush()

        # Reports —— 注意 created_at 由 server_default 赋值，为保证先后顺序，我们
        # 分两次 flush，让数据库的 now() 自然生成不同时间戳；同时先写 rep_a_old。
        now = datetime.now()

        rep_a_old = DiagnosisReport(
            tech_category=_TECH,
            standard_id=std_id,
            standard_version=std_ver,
            video_path=_COS_A,
            overall_score=70.0,
            strengths_summary="[]",
            cos_object_key=_COS_A,
            preprocessing_job_id=vpj_a.id,
            source="athlete_pipeline",
            created_at=now - timedelta(minutes=10),
        )
        session.add(rep_a_old)
        await session.flush()

        rep_a_new = DiagnosisReport(
            tech_category=_TECH,
            standard_id=std_id,
            standard_version=std_ver,
            video_path=_COS_A,
            overall_score=85.0,
            strengths_summary="[]",
            cos_object_key=_COS_A,
            preprocessing_job_id=vpj_a.id,
            source="athlete_pipeline",
            created_at=now - timedelta(minutes=1),
        )
        session.add(rep_a_new)
        await session.flush()

        rep_b = DiagnosisReport(
            tech_category=_TECH,
            standard_id=std_id,
            standard_version=std_ver,
            video_path=_COS_B,
            overall_score=75.0,
            strengths_summary="[]",
            cos_object_key=_COS_B,
            preprocessing_job_id=vpj_b.id,
            source="athlete_pipeline",
            created_at=now - timedelta(minutes=5),
        )
        session.add(rep_b)

        # Legacy 旧报告：cos_object_key 也指向 _COS_A 但 source='legacy'
        rep_a_legacy = DiagnosisReport(
            tech_category=_TECH,
            standard_id=std_id,
            standard_version=std_ver,
            video_path=_COS_A,
            overall_score=60.0,
            strengths_summary="[]",
            cos_object_key=_COS_A,
            preprocessing_job_id=vpj_a.id,
            source="legacy",
            created_at=now - timedelta(hours=1),
        )
        session.add(rep_a_legacy)

        await session.commit()

        return {
            "athlete_a_id": ath_a.id,
            "athlete_b_id": ath_b.id,
            "vpj_a_id": vpj_a.id,
            "vpj_b_id": vpj_b.id,
            "rep_a_old_id": rep_a_old.id,
            "rep_a_new_id": rep_a_new.id,
            "rep_b_id": rep_b.id,
            "rep_a_legacy_id": rep_a_legacy.id,
        }


def _ours_only(items: list[dict], seeded: dict) -> list[dict]:
    """过滤出本次 seed 的记录（按 id 白名单）."""
    ours_ids = {
        str(seeded["rep_a_old_id"]),
        str(seeded["rep_a_new_id"]),
        str(seeded["rep_b_id"]),
        str(seeded["rep_a_legacy_id"]),
    }
    return [it for it in items if it["id"] in ours_ids]


# ═══════════════════════════════════════════════════════════════════════
# 断言 1：默认分页 + 按 created_at 倒序
# ═══════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_default_lists_by_created_at_desc(session_factory, client):
    await _cleanup(session_factory)
    try:
        seeded = await _seed_two_athletes_with_reports(session_factory)

        resp = await client.get("/api/v1/diagnosis-reports?page_size=100")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        data = assert_success_envelope(body, expect_meta=True)
        assert body["meta"]["page"] == 1
        assert body["meta"]["page_size"] == 100
        assert isinstance(body["meta"]["total"], int)

        ours = _ours_only(data, seeded)
        # 4 条全部本 seed 的记录必须出现
        assert len(ours) == 4

        # 确认倒序：rep_a_new (最新) 在 rep_a_old 前
        order = [it["id"] for it in ours]
        assert order.index(str(seeded["rep_a_new_id"])) < order.index(
            str(seeded["rep_a_old_id"])
        )
        assert order.index(str(seeded["rep_a_old_id"])) < order.index(
            str(seeded["rep_a_legacy_id"])
        )
    finally:
        await _cleanup(session_factory)


# ═══════════════════════════════════════════════════════════════════════
# 断言 2：按 athlete_id 过滤
# ═══════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_filter_by_athlete_id(session_factory, client):
    await _cleanup(session_factory)
    try:
        seeded = await _seed_two_athletes_with_reports(session_factory)

        resp = await client.get(
            f"/api/v1/diagnosis-reports?athlete_id={seeded['athlete_a_id']}"
            f"&page_size=100"
        )
        assert resp.status_code == 200
        data = assert_success_envelope(resp.json())
        ours = _ours_only(data, seeded)
        # A 有 3 条（2 新 + 1 legacy，都基于 _COS_A）；B 的 1 条应被排除
        assert len(ours) == 3
        assert all(it["cos_object_key"] == _COS_A for it in ours)
        assert str(seeded["rep_b_id"]) not in {it["id"] for it in ours}
    finally:
        await _cleanup(session_factory)


# ═══════════════════════════════════════════════════════════════════════
# 断言 3：按 cos_object_key 过滤 — 返回所有版本
# ═══════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_filter_by_cos_object_key_returns_all_versions(
    session_factory, client
):
    await _cleanup(session_factory)
    try:
        seeded = await _seed_two_athletes_with_reports(session_factory)

        resp = await client.get(
            f"/api/v1/diagnosis-reports?cos_object_key={_COS_A}&page_size=100"
        )
        assert resp.status_code == 200
        data = assert_success_envelope(resp.json())
        ours = _ours_only(data, seeded)
        # _COS_A 共 3 条（rep_a_old + rep_a_new + rep_a_legacy）
        assert len(ours) == 3
    finally:
        await _cleanup(session_factory)


# ═══════════════════════════════════════════════════════════════════════
# 断言 4：按 preprocessing_job_id 过滤
# ═══════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_filter_by_preprocessing_job_id(session_factory, client):
    await _cleanup(session_factory)
    try:
        seeded = await _seed_two_athletes_with_reports(session_factory)

        resp = await client.get(
            f"/api/v1/diagnosis-reports?preprocessing_job_id={seeded['vpj_b_id']}"
            f"&page_size=100"
        )
        assert resp.status_code == 200
        data = assert_success_envelope(resp.json())
        ours = _ours_only(data, seeded)
        # vpj_b 只关联 rep_b 一条
        assert len(ours) == 1
        assert ours[0]["id"] == str(seeded["rep_b_id"])
    finally:
        await _cleanup(session_factory)


# ═══════════════════════════════════════════════════════════════════════
# 断言 5：source='athlete_pipeline' 过滤 — 排除 legacy
# ═══════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_source_athlete_pipeline_excludes_legacy(session_factory, client):
    await _cleanup(session_factory)
    try:
        seeded = await _seed_two_athletes_with_reports(session_factory)

        resp = await client.get(
            "/api/v1/diagnosis-reports?source=athlete_pipeline&page_size=100"
        )
        assert resp.status_code == 200
        data = assert_success_envelope(resp.json())
        ours = _ours_only(data, seeded)
        # 4 条里除掉 legacy 后剩 3 条
        assert len(ours) == 3
        assert all(it["source"] == "athlete_pipeline" for it in ours)
        assert str(seeded["rep_a_legacy_id"]) not in {it["id"] for it in ours}
    finally:
        await _cleanup(session_factory)


# ═══════════════════════════════════════════════════════════════════════
# 断言 6：source=invalid → 400 INVALID_ENUM_VALUE
# ═══════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_source_invalid_returns_400(session_factory, client):
    resp = await client.get("/api/v1/diagnosis-reports?source=invalid")
    assert resp.status_code == 400, resp.text
    err = assert_error_envelope(resp.json(), code="INVALID_ENUM_VALUE")
    assert err["details"]["field"] == "source"


# ═══════════════════════════════════════════════════════════════════════
# 断言 7：page_size=200 → 422 VALIDATION_FAILED
# ═══════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_page_size_too_large_returns_422(session_factory, client):
    resp = await client.get("/api/v1/diagnosis-reports?page_size=200")
    assert resp.status_code == 422, resp.text
    err = assert_error_envelope(resp.json(), code="VALIDATION_FAILED")


# ═══════════════════════════════════════════════════════════════════════
# 断言 8：同运动员 2 次诊断同素材 → 返回 2 条，时间倒序
# ═══════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_two_diagnoses_same_cos_key_returned_time_desc(
    session_factory, client
):
    await _cleanup(session_factory)
    try:
        seeded = await _seed_two_athletes_with_reports(session_factory)

        resp = await client.get(
            f"/api/v1/diagnosis-reports?cos_object_key={_COS_A}"
            f"&source=athlete_pipeline&page_size=100"
        )
        assert resp.status_code == 200
        data = assert_success_envelope(resp.json())
        ours = _ours_only(data, seeded)
        # 2 条（legacy 被 source 过滤掉）
        assert len(ours) == 2
        ids = [it["id"] for it in ours]
        # 时间倒序：new 在 old 前
        assert ids.index(str(seeded["rep_a_new_id"])) < ids.index(
            str(seeded["rep_a_old_id"])
        )
    finally:
        await _cleanup(session_factory)
