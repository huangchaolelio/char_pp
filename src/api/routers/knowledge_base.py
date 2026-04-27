"""Knowledge base router — US1 implementations (T028–T029).

Feature-017: 响应体统一迁移至 ``SuccessEnvelope``；``HTTPException``
改为 ``AppException``（章程 v1.4.0 原则 IX）。原 ``KnowledgeBaseVersionsResponse``
（``{versions: [...]}`` 包装）由 ``SuccessEnvelope[list[KnowledgeBaseVersionItem]]`` 替代。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import AppException, ErrorCode
from src.api.schemas.envelope import SuccessEnvelope, ok
from src.api.schemas.knowledge_base import (
    ApproveRequest,
    ApproveResponse,
    KnowledgeBaseDetailResponse,
    KnowledgeBaseVersionItem,
    TechPointDetail,
)
from src.db.session import get_db
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
    db: AsyncSession = Depends(get_db),
) -> SuccessEnvelope[list[KnowledgeBaseVersionItem]]:
    """List all knowledge base versions, newest first."""
    versions = await knowledge_base_svc.list_versions(db)
    return ok([
        KnowledgeBaseVersionItem(
            version=kb.version,
            status=kb.status.value,
            action_types_covered=kb.action_types_covered,
            point_count=kb.point_count,
            approved_at=kb.approved_at,
        )
        for kb in versions
    ])


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
