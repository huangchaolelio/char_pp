"""Knowledge base version management service — Feature-019 per-category lifecycle.

**Breaking change vs Feature-018**:
  - 主键从单列 ``version VARCHAR`` 改为复合主键 ``(tech_category, version INTEGER)``
  - ``approve_version`` 签名从 ``(version: str)`` 改为 ``(tech_category: str, version: int)``
  - ``create_draft_version`` 签名从 ``(action_types: list[str])`` 改为
    ``(tech_category: str, extraction_job_id: UUID, point_count: int)``
  - ``action_types_covered`` 字段已删除（被主键 ``tech_category`` 替代）
  - approve 事务内自动联动 ``teaching_tips`` 批量归档/激活（通过 ``teaching_tip_svc``）

Responsibilities:
  - 按 tech_category 独立生命周期管理 KB 版本（draft → active → archived）
  - 每类别 ``MAX(version) + 1`` 自增
  - approve：单类别原子事务，partial unique index + advisory lock 双保险
  - 列表 / 详情 / 按 extraction_job_id 反查
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import AppException, ErrorCode
from src.models.expert_tech_point import ActionType, ExpertTechPoint
from src.models.tech_knowledge_base import KBStatus, TechKnowledgeBase
from src.utils.time_utils import now_cst

logger = logging.getLogger(__name__)


# ── 内部异常（仅本模块使用；路由层应直接抛 AppException） ───────────────────
class VersionNotFoundError(Exception):
    def __init__(self, tech_category: str, version: int) -> None:
        super().__init__(f"KB version not found: ({tech_category}, {version})")
        self.tech_category = tech_category
        self.version = version


class VersionNotDraftError(Exception):
    def __init__(self, tech_category: str, version: int, status: str) -> None:
        super().__init__(
            f"KB version ({tech_category}, {version}) is {status}, expected draft"
        )
        self.tech_category = tech_category
        self.version = version
        self.status = status


# ── Public API ────────────────────────────────────────────────────────────────


async def create_draft_version(
    session: AsyncSession,
    *,
    tech_category: str,
    extraction_job_id: uuid.UUID,
    point_count: int = 0,
    notes: str | None = None,
) -> TechKnowledgeBase:
    """Create a new draft KB record for a given tech_category.

    Implements research.md R2: ``MAX(version) + 1`` per-category auto-increment
    with optimistic-concurrency retry once on IntegrityError.
    """
    for attempt in (1, 2):
        next_v = (
            await session.execute(
                select(
                    func.coalesce(func.max(TechKnowledgeBase.version), 0) + 1
                ).where(TechKnowledgeBase.tech_category == tech_category)
            )
        ).scalar_one()

        kb = TechKnowledgeBase(
            tech_category=tech_category,
            version=int(next_v),
            status=KBStatus.draft,
            point_count=point_count,
            extraction_job_id=extraction_job_id,
            notes=notes,
            business_phase="STANDARDIZATION",     # type: ignore[arg-type]
            business_step="kb_version_activate",
        )
        session.add(kb)
        try:
            await session.flush()
            logger.info(
                "Created draft KB (tech_category=%s, version=%d)",
                tech_category,
                next_v,
            )
            return kb
        except IntegrityError:
            await session.rollback()
            if attempt == 2:
                raise
            logger.warning(
                "create_draft_version race on (%s, %d), retrying once",
                tech_category,
                next_v,
            )


async def approve_version(
    session: AsyncSession,
    tech_category: str,
    version: int,
    approved_by: str,
    notes: str | None = None,
) -> dict:
    """Approve a draft KB record → active; archive same-category previous active.

    事务流程（research.md R3）:
      1. pg_advisory_xact_lock(hashtext(tech_category)) 锁该类别命名空间
      2. 校验目标记录：存在 + status='draft' + point_count>0 + 无未解决冲突
      3. 归档同类别旧 active（UPDATE ... SET status='archived'）
      4. 激活目标记录（UPDATE ... SET status='active', approved_by, approved_at）
      5. 同事务内联动 teaching_tips（通过 teaching_tip_svc.relink_on_kb_approve）

    Returns: dict {
        "new_active": TechKnowledgeBase,
        "previous_active_version": int | None,
        "tips_updated": {"archived_count": N, "activated_count": M}
    }

    Raises AppException 直接（路由层可直通）:
      - KB_VERSION_NOT_FOUND / KB_VERSION_NOT_DRAFT / KB_EMPTY_POINTS / KB_CONFLICT_UNRESOLVED
    """
    # 延迟 import 避免循环依赖
    from src.services import teaching_tip_svc

    # ── Step 1: advisory lock on tech_category namespace ────────────────────
    await session.execute(
        func.pg_advisory_xact_lock(func.hashtext(tech_category))
    )

    # ── Step 2: 校验目标记录 ────────────────────────────────────────────
    kb = await session.get(TechKnowledgeBase, (tech_category, version))
    if kb is None:
        raise AppException(
            ErrorCode.KB_VERSION_NOT_FOUND,
            message=f"知识库版本不存在：({tech_category}, {version})",
            details={"tech_category": tech_category, "version": version},
        )
    if kb.status != KBStatus.draft:
        raise AppException(
            ErrorCode.KB_VERSION_NOT_DRAFT,
            message=f"版本 ({tech_category}, {version}) 非草稿状态（当前 {kb.status.value}）",
            details={
                "tech_category": tech_category,
                "version": version,
                "current_status": kb.status.value,
            },
        )
    if kb.point_count <= 0:
        raise AppException(
            ErrorCode.KB_EMPTY_POINTS,
            details={
                "tech_category": tech_category,
                "version": version,
                "point_count": kb.point_count,
            },
        )

    # 冲突检查：该 KB 下存在 conflict_flag=true 的 expert_tech_points → 拒绝
    conflict_count = (
        await session.execute(
            select(func.count(ExpertTechPoint.id)).where(
                ExpertTechPoint.kb_tech_category == tech_category,
                ExpertTechPoint.kb_version == version,
                ExpertTechPoint.conflict_flag.is_(True),
            )
        )
    ).scalar_one()
    if conflict_count > 0:
        raise AppException(
            ErrorCode.KB_CONFLICT_UNRESOLVED,
            details={
                "tech_category": tech_category,
                "version": version,
                "conflict_count": int(conflict_count),
            },
        )

    # ── Step 3: 查同类别旧 active 并归档 ──────────────────────────────────
    previous_active = (
        await session.execute(
            select(TechKnowledgeBase).where(
                TechKnowledgeBase.tech_category == tech_category,
                TechKnowledgeBase.status == KBStatus.active,
            )
        )
    ).scalar_one_or_none()

    previous_version: int | None = None
    if previous_active is not None:
        previous_version = previous_active.version
        previous_active.status = KBStatus.archived

    # ── Step 4: 激活目标 ─────────────────────────────────────────────────
    kb.status = KBStatus.active
    kb.approved_by = approved_by
    kb.approved_at = now_cst()
    if notes:
        kb.notes = notes

    await session.flush()

    # ── Step 5: 联动 teaching_tips（同事务）───────────────────────────────
    tips_stats = await teaching_tip_svc.relink_on_kb_approve(
        session,
        tech_category=tech_category,
        old_version=previous_version,
        new_version=version,
    )

    logger.info(
        "Approved KB (tech_category=%s, version=%d) by %s; previous=%s; tips %s",
        tech_category,
        version,
        approved_by,
        previous_version,
        tips_stats,
    )
    return {
        "new_active": kb,
        "previous_active_version": previous_version,
        "tips_updated": tips_stats,
    }


async def get_active_version(
    session: AsyncSession, tech_category: str
) -> TechKnowledgeBase | None:
    """Return the currently active KB for the given tech_category, or None."""
    result = await session.execute(
        select(TechKnowledgeBase).where(
            TechKnowledgeBase.tech_category == tech_category,
            TechKnowledgeBase.status == KBStatus.active,
        )
    )
    return result.scalar_one_or_none()


async def get_version(
    session: AsyncSession, tech_category: str, version: int
) -> TechKnowledgeBase | None:
    """Fetch a specific KB record by composite key. Returns None if not found."""
    return await session.get(TechKnowledgeBase, (tech_category, version))


async def list_versions(
    session: AsyncSession,
    *,
    tech_category: str | None = None,
    status: str | None = None,
    extraction_job_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[TechKnowledgeBase], int]:
    """List KB records with filters + pagination.

    Returns: (items, total) — 路由层用 page() 构造器封装 PaginationMeta。
    Order: tech_category ASC, version DESC（同类别最新版在前）
    """
    base_where = []
    if tech_category is not None:
        base_where.append(TechKnowledgeBase.tech_category == tech_category)
    if status is not None:
        base_where.append(TechKnowledgeBase.status == KBStatus(status))
    if extraction_job_id is not None:
        base_where.append(TechKnowledgeBase.extraction_job_id == extraction_job_id)

    # Count query
    count_stmt = select(func.count()).select_from(TechKnowledgeBase)
    if base_where:
        count_stmt = count_stmt.where(*base_where)
    total = (await session.execute(count_stmt)).scalar_one()

    # Data query
    offset = (page - 1) * page_size
    data_stmt = (
        select(TechKnowledgeBase)
        .order_by(
            TechKnowledgeBase.tech_category.asc(),
            TechKnowledgeBase.version.desc(),
        )
        .offset(offset)
        .limit(page_size)
    )
    if base_where:
        data_stmt = data_stmt.where(*base_where)
    items = list((await session.execute(data_stmt)).scalars().all())
    return items, int(total)


async def get_version_detail(
    session: AsyncSession, tech_category: str, version: int
) -> tuple[TechKnowledgeBase, dict] | None:
    """Fetch KB + aggregated ExpertTechPoint dimensions_summary.

    Returns: (kb, {"total_points": N, "dimensions": [...], "conflict_count": M})
    Returns None if KB not found.
    """
    kb = await get_version(session, tech_category, version)
    if kb is None:
        return None

    # 聚合 dimensions_summary
    result = await session.execute(
        select(
            func.count(ExpertTechPoint.id),
            func.count(ExpertTechPoint.id).filter(
                ExpertTechPoint.conflict_flag.is_(True)
            ),
        ).where(
            ExpertTechPoint.kb_tech_category == tech_category,
            ExpertTechPoint.kb_version == version,
        )
    )
    total_points, conflict_count = result.one()

    dims_result = await session.execute(
        select(ExpertTechPoint.dimension)
        .where(
            ExpertTechPoint.kb_tech_category == tech_category,
            ExpertTechPoint.kb_version == version,
        )
        .distinct()
    )
    dimensions = sorted(row[0] for row in dims_result.all())

    summary = {
        "total_points": int(total_points),
        "dimensions": dimensions,
        "conflict_count": int(conflict_count),
    }
    return kb, summary


async def get_tech_points(
    session: AsyncSession,
    tech_category: str,
    version: int,
) -> list[ExpertTechPoint]:
    """Return all ExpertTechPoints for a given (tech_category, version) KB record."""
    result = await session.execute(
        select(ExpertTechPoint).where(
            ExpertTechPoint.kb_tech_category == tech_category,
            ExpertTechPoint.kb_version == version,
        )
    )
    return list(result.scalars().all())


async def list_kbs_for_extraction_job(
    session: AsyncSession, extraction_job_id: uuid.UUID
) -> list[TechKnowledgeBase]:
    """Feature-019 US5 — 反向查询：某 extraction_job 产出的所有 KB 记录。"""
    result = await session.execute(
        select(TechKnowledgeBase)
        .where(TechKnowledgeBase.extraction_job_id == extraction_job_id)
        .order_by(TechKnowledgeBase.tech_category.asc())
    )
    return list(result.scalars().all())
