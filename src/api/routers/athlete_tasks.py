"""Feature-020 · athlete_tasks router.

集中 US2 / US3 的 4 个 POST 端点，避免 `tasks.py` 膨胀：
  POST /api/v1/tasks/athlete-preprocessing           (US2 单条)
  POST /api/v1/tasks/athlete-preprocessing/batch     (US2 批量)
  POST /api/v1/tasks/athlete-diagnosis               (US3 单条)
  POST /api/v1/tasks/athlete-diagnosis/batch         (US3 批量)

路由层仅做请求 → service DTO 的参数转换 + 响应封装；业务逻辑在
:mod:`src.services.athlete_submission_service`。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import AppException, ErrorCode
from src.api.schemas.athlete_classification import (
    AthleteBatchRejectedItem as _RespRejected,
    AthleteBatchSubmitResponse,
    AthleteBatchSubmittedItem as _RespSubmitted,
    AthleteDiagnosisBatchRequest,
    AthleteDiagnosisSubmitRequest,
    AthleteDiagnosisSubmitResponse,
    AthletePreprocessingBatchRequest,
    AthletePreprocessingSubmitRequest,
    AthletePreprocessingSubmitResponse,
)
from src.api.schemas.envelope import SuccessEnvelope, ok
from src.db.session import get_db
from src.services.athlete_submission_service import (
    AthleteBatchOutcome,
    AthleteDiagnosisOutcome,
    AthletePreprocessingOutcome,
    submit_athlete_diagnosis,
    submit_athlete_diagnosis_batch,
    submit_athlete_preprocessing,
    submit_athlete_preprocessing_batch,
)

router = APIRouter(tags=["athlete-tasks"])


# ── Helpers ──────────────────────────────────────────────────────────────


def _to_batch_response(out: AthleteBatchOutcome) -> AthleteBatchSubmitResponse:
    return AthleteBatchSubmitResponse(
        submitted=[
            _RespSubmitted(
                athlete_video_classification_id=s.athlete_video_classification_id,
                job_id=s.job_id,
                task_id=s.task_id,
                reused=s.reused,
            )
            for s in out.submitted
        ],
        rejected=[
            _RespRejected(
                athlete_video_classification_id=r.athlete_video_classification_id,
                error_code=r.error_code,
                message=r.message,
            )
            for r in out.rejected
        ],
    )


# ══════════════════════════════════════════════════════════════════════════
# US2 · PREPROCESSING
# ══════════════════════════════════════════════════════════════════════════


@router.post(
    "/tasks/athlete-preprocessing",
    status_code=200,
    response_model=SuccessEnvelope[AthletePreprocessingSubmitResponse],
    summary="提交运动员视频预处理（单条）",
)
async def submit_single_athlete_preprocessing(
    body: AthletePreprocessingSubmitRequest,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[AthletePreprocessingSubmitResponse]:
    out = await submit_athlete_preprocessing(
        db,
        classification_id=body.athlete_video_classification_id,
        force=body.force,
    )
    return ok(AthletePreprocessingSubmitResponse(
        job_id=out.job_id,
        athlete_video_classification_id=out.athlete_video_classification_id,
        cos_object_key=out.cos_object_key,
        status=out.status,
        reused=out.reused,
        segment_count=out.segment_count,
        has_audio=out.has_audio,
        started_at=out.started_at,
        completed_at=out.completed_at,
    ))


@router.post(
    "/tasks/athlete-preprocessing/batch",
    status_code=200,
    response_model=SuccessEnvelope[AthleteBatchSubmitResponse],
    summary="提交运动员视频预处理（批量）",
)
async def submit_batch_athlete_preprocessing(
    body: AthletePreprocessingBatchRequest,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[AthleteBatchSubmitResponse]:
    items = [
        (it.athlete_video_classification_id, it.force)
        for it in body.items
    ]
    out = await submit_athlete_preprocessing_batch(db, items=items)
    return ok(_to_batch_response(out))


# ══════════════════════════════════════════════════════════════════════════
# US3 · DIAGNOSIS
# ══════════════════════════════════════════════════════════════════════════


@router.post(
    "/tasks/athlete-diagnosis",
    status_code=200,
    response_model=SuccessEnvelope[AthleteDiagnosisSubmitResponse],
    summary="提交运动员诊断任务（单条）",
)
async def submit_single_athlete_diagnosis(
    body: AthleteDiagnosisSubmitRequest,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[AthleteDiagnosisSubmitResponse]:
    out = await submit_athlete_diagnosis(
        db,
        classification_id=body.athlete_video_classification_id,
        force=body.force,
    )
    return ok(AthleteDiagnosisSubmitResponse(
        task_id=out.task_id,
        athlete_video_classification_id=out.athlete_video_classification_id,
        tech_category=out.tech_category,
        status=out.status,
    ))


@router.post(
    "/tasks/athlete-diagnosis/batch",
    status_code=200,
    response_model=SuccessEnvelope[AthleteBatchSubmitResponse],
    summary="提交运动员诊断任务（批量）",
)
async def submit_batch_athlete_diagnosis(
    body: AthleteDiagnosisBatchRequest,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[AthleteBatchSubmitResponse]:
    items = [
        (it.athlete_video_classification_id, it.force)
        for it in body.items
    ]
    out = await submit_athlete_diagnosis_batch(db, items=items)
    return ok(_to_batch_response(out))
