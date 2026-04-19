"""Knowledge base router — US1 implementations (T028–T029)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas.knowledge_base import (
    ApproveRequest,
    ApproveResponse,
    KnowledgeBaseDetailResponse,
    KnowledgeBaseVersionItem,
    KnowledgeBaseVersionsResponse,
    TechPointDetail,
)
from src.db.session import get_db
from src.services import knowledge_base_svc
from src.services.knowledge_base_svc import VersionNotDraftError, VersionNotFoundError

router = APIRouter(tags=["knowledge-base"])


# ── GET /knowledge-base/versions ─────────────────────────────────────────────

@router.get("/knowledge-base/versions", response_model=KnowledgeBaseVersionsResponse)
async def list_kb_versions(
    db: AsyncSession = Depends(get_db),
) -> KnowledgeBaseVersionsResponse:
    """List all knowledge base versions, newest first."""
    versions = await knowledge_base_svc.list_versions(db)
    return KnowledgeBaseVersionsResponse(
        versions=[
            KnowledgeBaseVersionItem(
                version=kb.version,
                status=kb.status.value,
                action_types_covered=kb.action_types_covered,
                point_count=kb.point_count,
                approved_at=kb.approved_at,
            )
            for kb in versions
        ]
    )


# ── GET /knowledge-base/{version} ────────────────────────────────────────────

@router.get("/knowledge-base/{version}", response_model=KnowledgeBaseDetailResponse)
async def get_kb_version(
    version: str,
    db: AsyncSession = Depends(get_db),
) -> KnowledgeBaseDetailResponse:
    """Return full detail for a specific knowledge base version, including all tech points."""
    try:
        kb = await knowledge_base_svc.get_version(db, version)
    except VersionNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "KB_VERSION_NOT_FOUND",
                "message": f"知识库版本 {version} 不存在",
                "details": {"version": version},
            },
        )

    points = await knowledge_base_svc.get_tech_points(db, version)

    return KnowledgeBaseDetailResponse(
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
    )


# ── POST /knowledge-base/{version}/approve ───────────────────────────────────

@router.post("/knowledge-base/{version}/approve", response_model=ApproveResponse)
async def approve_kb_version(
    version: str,
    body: ApproveRequest,
    db: AsyncSession = Depends(get_db),
) -> ApproveResponse:
    """Approve a draft KB version: activates it and archives the current active version."""
    try:
        async with db.begin():
            kb, previous = await knowledge_base_svc.approve_version(
                db, version, body.approved_by, body.notes
            )
    except VersionNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "KB_VERSION_NOT_FOUND",
                "message": f"知识库版本 {version} 不存在",
                "details": {"version": version},
            },
        )
    except VersionNotDraftError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "KB_VERSION_NOT_DRAFT",
                "message": f"版本 {version} 当前状态为 {exc.args[0]}，只有 draft 版本可审核通过",
                "details": {"version": version},
            },
        )

    return ApproveResponse(
        version=kb.version,
        status=kb.status.value,
        approved_by=kb.approved_by,
        approved_at=kb.approved_at,
        previous_active_version=previous,
    )
