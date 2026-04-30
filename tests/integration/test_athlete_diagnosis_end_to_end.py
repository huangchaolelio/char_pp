"""Feature-020 · T037 · US3 端到端集测.

验证目标:
    直接调用 ``DiagnosisService.diagnose_athlete_by_classification_id`` 走完:
      1. 读 ``athlete_video_classifications`` 行得 cos_object_key + tech_category
      2. 进入 ``self.diagnose(...)`` 写 ``diagnosis_reports`` 行时带齐三要素
      3. 回写 ``athlete_video_classifications.last_diagnosis_report_id``

    断言:
      - ``diagnosis_reports`` 行的 ``cos_object_key`` / ``preprocessing_job_id``
        / ``standard_version`` / ``source='athlete_pipeline'`` 四字段全部非空且正确
      - 按 ``cos_object_key`` 反查报告 ≤ 1 次 SQL 即可命中（SC-005）
      - ``athlete_video_classifications.last_diagnosis_report_id`` 被回写为新
        DiagnosisReport.id

策略: 不走 Celery / 不跑 pose / scorer / advice 真实管线；把
    :meth:`DiagnosisService.diagnose` 替换为一个"快速成功"存根——直接构造
    :class:`DiagnosisReport` 行写库并返回 ``DiagnosisReportData``，完全模拟
    诊断链路最终落库的副作用。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings
from src.db import session as session_module
from src.models.athlete import Athlete
from src.models.athlete_video_classification import AthleteVideoClassification
from src.models.diagnosis_report import DiagnosisReport
from src.models.tech_standard import (
    SourceQuality,
    StandardStatus,
    TechStandard,
)
from src.models.video_preprocessing_job import VideoPreprocessingJob
from src.services.diagnosis_service import DiagnosisReportData, DiagnosisService


_COS_KEY = "charhuang/tt_video/athletes/__t037_athlete/正手攻球_e2e.mp4"
_ATHLETE_NAME = "__t037_athlete"
_TECH = "forehand_attack"


@pytest_asyncio.fixture
async def session_factory():
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


async def _cleanup(factory):
    """删除本测试残留 seed（幂等）."""
    async with factory() as session:
        await session.execute(
            delete(DiagnosisReport).where(DiagnosisReport.video_path == _COS_KEY)
        )
        await session.execute(
            delete(AthleteVideoClassification).where(
                AthleteVideoClassification.cos_object_key == _COS_KEY
            )
        )
        await session.execute(
            delete(VideoPreprocessingJob).where(
                VideoPreprocessingJob.cos_object_key == _COS_KEY
            )
        )
        await session.execute(delete(Athlete).where(Athlete.name == _ATHLETE_NAME))
        # tech_standard 保留复用（不同 feature 共用）；不清
        await session.commit()


async def _seed(factory) -> tuple[uuid.UUID, uuid.UUID, int]:
    """Seed 返回 (classification_id, preprocessing_job_id, standard_id)."""
    async with factory() as session:
        # 1. athletes
        ath = Athlete(name=_ATHLETE_NAME, bio=_ATHLETE_NAME, created_via="athlete_scan")
        session.add(ath)
        await session.flush()

        # 2. video_preprocessing_jobs · success 一行
        vpj = VideoPreprocessingJob(
            cos_object_key=_COS_KEY,
            status="success",
            business_phase="TRAINING",
            business_step="preprocess_video",
        )
        session.add(vpj)
        await session.flush()

        # 3. athlete_video_classifications
        clf = AthleteVideoClassification(
            cos_object_key=_COS_KEY,
            athlete_id=ath.id,
            athlete_name=_ATHLETE_NAME,
            name_source="fallback",
            tech_category=_TECH,
            classification_source="rule",
            classification_confidence=1.0,
            preprocessed=True,
            preprocessing_job_id=vpj.id,
        )
        session.add(clf)
        await session.flush()

        # 4. tech_standards — 确保 active 版存在（若已存在则复用）
        existing = (
            await session.execute(
                select(TechStandard).where(
                    TechStandard.tech_category == _TECH,
                    TechStandard.status == StandardStatus.active,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            std = TechStandard(
                tech_category=_TECH,
                version=1,
                status=StandardStatus.active,
                source_quality=SourceQuality.low,
                built_from_expert_count=1,
            )
            session.add(std)
            await session.flush()
            standard_id, standard_version = std.id, std.version
        else:
            standard_id, standard_version = existing.id, existing.version

        await session.commit()
        return clf.id, vpj.id, standard_id


def _fake_diagnose_factory(expected_cos: str, vpj_id: uuid.UUID, std_id: int, std_ver: int):
    """构造 DiagnosisService.diagnose 的快速成功替身.

    - 只构造必需的 DiagnosisReport 字段（含三要素锚点）落库；
    - 不写 DiagnosisDimensionResult / CoachingAdvice（本测试仅验证 US3 报告层锚点）；
    - 返回 DiagnosisReportData 数据类给上层消费。
    """

    async def _fake(
        self,
        *,
        tech_category: str,
        video_path: str,
        athlete_cos_object_key: str | None = None,
        athlete_preprocessing_job_id: uuid.UUID | None = None,
        athlete_source: str | None = None,
    ) -> DiagnosisReportData:
        assert video_path == expected_cos, (
            f"fake diagnose 收到意外 video_path={video_path!r}"
        )
        assert athlete_cos_object_key == expected_cos
        assert athlete_preprocessing_job_id == vpj_id
        assert athlete_source == "athlete_pipeline"

        report = DiagnosisReport(
            tech_category=tech_category,
            standard_id=std_id,
            standard_version=std_ver,
            video_path=video_path,
            overall_score=85.0,
            strengths_summary=json.dumps(["strong_stance"], ensure_ascii=False),
            cos_object_key=athlete_cos_object_key,
            preprocessing_job_id=athlete_preprocessing_job_id,
            source=athlete_source or "legacy",
        )
        self._session.add(report)
        await self._session.flush()

        return DiagnosisReportData(
            report_id=report.id,
            tech_category=tech_category,
            standard_id=std_id,
            standard_version=std_ver,
            overall_score=85.0,
            strengths=["strong_stance"],
            dimensions=[],
            created_at=datetime.utcnow(),
        )

    return _fake


@pytest.mark.integration
@pytest.mark.asyncio
class TestAthleteDiagnosisEndToEnd:

    async def test_diagnosis_persists_three_anchors_and_reverse_lookup(
        self, session_factory,
    ):
        # ── Arrange seed ────────────────────────────────────────────────
        await _cleanup(session_factory)
        clf_id, vpj_id, std_id = await _seed(session_factory)
        async with session_factory() as session:
            std_row = (
                await session.execute(
                    select(TechStandard).where(TechStandard.id == std_id)
                )
            ).scalar_one()
            std_ver = std_row.version

        # ── Act ────────────────────────────────────────────────────────
        task_id = uuid.uuid4()
        async with session_factory() as session:
            with patch.object(
                DiagnosisService,
                "diagnose",
                _fake_diagnose_factory(_COS_KEY, vpj_id, std_id, std_ver),
            ):
                svc = DiagnosisService(session=session)
                summary = await svc.diagnose_athlete_by_classification_id(
                    session, task_id, clf_id
                )

        # ── Assert summary payload ─────────────────────────────────────
        assert summary["tech_category"] == _TECH
        assert summary["standard_version"] == std_ver
        assert summary["athlete_video_classification_id"] == str(clf_id)
        assert summary["overall_score"] == 85.0

        # ── Assert DB state ────────────────────────────────────────────
        async with session_factory() as session:
            # 1) 报告三要素锚点齐全
            reports = (
                (
                    await session.execute(
                        select(DiagnosisReport).where(
                            DiagnosisReport.cos_object_key == _COS_KEY
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(reports) == 1, "按 cos_object_key 反查应恰好 1 条（SC-005）"
            report = reports[0]
            assert report.cos_object_key == _COS_KEY
            assert report.preprocessing_job_id == vpj_id
            assert report.standard_version == std_ver
            assert report.source == "athlete_pipeline"

            # 2) avc.last_diagnosis_report_id 已回写
            updated_clf = (
                await session.execute(
                    select(AthleteVideoClassification).where(
                        AthleteVideoClassification.id == clf_id
                    )
                )
            ).scalar_one()
            assert updated_clf.last_diagnosis_report_id == report.id

            # 3) SC-005：按 preprocessing_job_id 单索引反查命中 1 行
            cnt = int(
                (
                    await session.execute(
                        select(func.count()).select_from(DiagnosisReport).where(
                            DiagnosisReport.preprocessing_job_id == vpj_id
                        )
                    )
                ).scalar_one()
            )
            assert cnt == 1

        # ── Cleanup ────────────────────────────────────────────────────
        await _cleanup(session_factory)
