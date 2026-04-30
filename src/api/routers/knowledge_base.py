"""Knowledge base router — US1 implementations (T028–T029).

Feature-017: 响应体统一迁移至 ``SuccessEnvelope``；``HTTPException``
改为 ``AppException``（章程 v1.4.0 原则 IX）。原 ``KnowledgeBaseVersionsResponse``
（``{versions: [...]}`` 包装）由 ``SuccessEnvelope[list[KnowledgeBaseVersionItem]]`` 替代。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import AppException, ErrorCode
from src.api.schemas.envelope import SuccessEnvelope, ok, page as page_envelope
from src.api.schemas.knowledge_base import (
    ApproveRequest,
    ApproveResponse,
    KnowledgeBaseDetailResponse,
    KnowledgeBaseVersionItem,
    TechPointDetail,
)
from src.db.session import get_db
from src.models.extraction_job import ExtractionJob
from src.services import knowledge_base_svc
from src.services.knowledge_base_svc import (
    ConflictUnresolvedError,
    VersionNotDraftError,
    VersionNotFoundError,
)

router = APIRouter(tags=["knowledge-base"])


# ── GET /knowledge-base/versions ─────────────────────────────────────────────

@router.get(
    "/knowledge-base/versions",
    response_model=SuccessEnvelope[list[KnowledgeBaseVersionItem]],
)
async def list_kb_versions(
    page_num: int = Query(1, ge=1, alias="page"),
    page_size: int = Query(20, ge=1, le=100),
    business_phase: str | None = Query(
        None,
        description="Feature-018: 按业务阶段过滤（knowledge_base 恒为 STANDARDIZATION）",
    ),
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[list[KnowledgeBaseVersionItem]]:
    """List all knowledge base versions, newest first.

    Feature-017 阶段 5 T054：统一 ``page/page_size`` 分页参数（默认 20、最大 100）。
    知识库版本数量通常较少，服务层 ``list_versions`` 返回全量后路由层切片。
    """
    # Feature-018: knowledge_base 恒为 STANDARDIZATION 阶段；仅做参数一致性校验
    from src.api.phase_params import parse_business_phase
    from src.models.analysis_task import BusinessPhase
    phase_enum = parse_business_phase(business_phase, field="business_phase")
    if phase_enum is not None and phase_enum != BusinessPhase.STANDARDIZATION:
        raise AppException(
            ErrorCode.INVALID_PHASE_STEP_COMBO,
            message="knowledge_base 仅存在于 STANDARDIZATION 阶段",
            details={"conflict": "phase_resource_mismatch", "phase": phase_enum.value, "resource": "knowledge_base"},
        )

    versions = await knowledge_base_svc.list_versions(db)
    total = len(versions)
    offset = (page_num - 1) * page_size
    sliced = versions[offset : offset + page_size]

    # 批量查询切片内所有 extraction_job，一次性获得每个 KB 版本对应的原始视频动作类型
    job_ids = [kb.extraction_job_id for kb in sliced if kb.extraction_job_id is not None]
    job_tech_map: dict[str, str] = {}
    if job_ids:
        rows = await db.execute(
            select(ExtractionJob.id, ExtractionJob.tech_category).where(
                ExtractionJob.id.in_(job_ids)
            )
        )
        for job_id, tech_category in rows.all():
            job_tech_map[str(job_id)] = tech_category

    items = [
        KnowledgeBaseVersionItem(
            version=kb.version,
            status=kb.status.value,
            action_types_covered=kb.action_types_covered,
            point_count=kb.point_count,
            approved_at=kb.approved_at,
            job_id=str(kb.extraction_job_id) if kb.extraction_job_id else None,
            tech_category=job_tech_map.get(str(kb.extraction_job_id)) if kb.extraction_job_id else None,
        )
        for kb in sliced
    ]
    return page_envelope(items, page=page_num, page_size=page_size, total=total)


# ── GET /knowledge-base/{version} ────────────────────────────────────────────

@router.get(
    "/knowledge-base/{version}",
    response_model=SuccessEnvelope[KnowledgeBaseDetailResponse],
)
async def get_kb_version(
    version: str,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[KnowledgeBaseDetailResponse]:
    """Return full detail for a specific knowledge base version, including all tech points."""
    try:
        kb = await knowledge_base_svc.get_version(db, version)
    except VersionNotFoundError:
        raise AppException(
            ErrorCode.KB_VERSION_NOT_FOUND,
            message=f"知识库版本 {version} 不存在",
            details={"version": version},
        )

    points = await knowledge_base_svc.get_tech_points(db, version)

    # 回溯关联的 extraction_job，取出原始视频动作类型 tech_category
    tech_category: str | None = None
    if kb.extraction_job_id is not None:
        job = await db.get(ExtractionJob, kb.extraction_job_id)
        if job is not None:
            tech_category = job.tech_category

    return ok(KnowledgeBaseDetailResponse(
        version=kb.version,
        status=kb.status.value,
        action_types_covered=kb.action_types_covered,
        point_count=kb.point_count,
        tech_points=[
            TechPointDetail(
                action_type=p.action_type.value,
                dimension=p.dimension,
                param_min=p.param_min,
                param_max=p.param_max,
                param_ideal=p.param_ideal,
                unit=p.unit,
                extraction_confidence=p.extraction_confidence,
            )
            for p in points
        ],
        approved_by=kb.approved_by,
        approved_at=kb.approved_at,
        created_at=kb.created_at,
        notes=kb.notes,
        job_id=str(kb.extraction_job_id) if kb.extraction_job_id else None,
        tech_category=tech_category,
    ))


# ── POST /knowledge-base/{version}/approve ───────────────────────────────────

@router.post(
    "/knowledge-base/{version}/approve",
    response_model=SuccessEnvelope[ApproveResponse],
)
async def approve_kb_version(
    version: str,
    body: ApproveRequest,
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[ApproveResponse]:
    """Approve a draft KB version: activates it and archives the current active version."""
    try:
        async with db.begin():
            kb, previous = await knowledge_base_svc.approve_version(
                db, version, body.approved_by, body.notes
            )
    except VersionNotFoundError:
        raise AppException(
            ErrorCode.KB_VERSION_NOT_FOUND,
            message=f"知识库版本 {version} 不存在",
            details={"version": version},
        )
    except VersionNotDraftError as exc:
        raise AppException(
            ErrorCode.KB_VERSION_NOT_DRAFT,
            message=f"版本 {version} 当前状态为 {exc.args[0]}，只有 draft 版本可审核通过",
            details={"version": version, "current_status": str(exc.args[0])},
        )
    except ConflictUnresolvedError as exc:
        raise AppException(
            ErrorCode.CONFLICT_UNRESOLVED,
            message=(
                f"版本 {version} 存在 {exc.conflict_count} 个未解决的视觉/音频参数冲突，"
                "请先解决冲突或覆盖后再审核通过"
            ),
            details={"version": version, "conflict_count": exc.conflict_count},
        )

    return ok(ApproveResponse(
        version=kb.version,
        status=kb.status.value,
        approved_by=kb.approved_by,
        approved_at=kb.approved_at,
        previous_active_version=previous,
    ))
