"""Knowledge base version management service.

Responsibilities:
  - Create a new draft version (auto-increments minor version)
  - Add ExpertTechPoints to a draft version
  - Approve a version: set it to active, archive the previous active version
  - Enforce single-active-version constraint
  - Query versions and active version
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.expert_tech_point import ActionType, ExpertTechPoint
from src.models.tech_knowledge_base import KBStatus, TechKnowledgeBase
from src.services.tech_extractor import ExtractionResult

logger = logging.getLogger(__name__)


class KnowledgeBaseError(Exception):
    pass


class NoActiveVersionError(KnowledgeBaseError):
    """Raised when an operation requires an active KB but none exists."""


class VersionNotFoundError(KnowledgeBaseError):
    def __init__(self, version: str) -> None:
        super().__init__(f"Knowledge base version not found: {version}")
        self.version = version


class VersionNotDraftError(KnowledgeBaseError):
    def __init__(self, version: str, status: str) -> None:
        super().__init__(f"Version {version} is {status}, expected draft")
        self.version = version


class ConflictUnresolvedError(KnowledgeBaseError):
    """Raised when a KB version has unresolved visual/audio parameter conflicts."""

    def __init__(self, version: str, conflict_count: int) -> None:
        super().__init__(
            f"KB version {version} has {conflict_count} unresolved conflict(s) — "
            "resolve or override before approving"
        )
        self.version = version
        self.conflict_count = conflict_count


# ── Version helpers ───────────────────────────────────────────────────────────

def _next_minor_version(current: str) -> str:
    """Increment the minor component of a semver string, e.g. '1.0.0' → '1.1.0'."""
    parts = current.split(".")
    if len(parts) != 3:
        raise ValueError(f"Invalid semver: {current}")
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
    return f"{major}.{minor + 1}.0"


async def _latest_version(session: AsyncSession) -> Optional[TechKnowledgeBase]:
    """Return the most recently created knowledge base version."""
    result = await session.execute(
        select(TechKnowledgeBase).order_by(TechKnowledgeBase.created_at.desc()).limit(1)
    )
    return result.scalar_one_or_none()


# ── Public API ────────────────────────────────────────────────────────────────

async def create_draft_version(
    session: AsyncSession,
    action_types: list[str],
    notes: Optional[str] = None,
) -> TechKnowledgeBase:
    """Create a new draft knowledge base version.

    Auto-generates the version string by incrementing the minor version of the
    latest existing version. If no version exists, starts at '1.0.0'.

    Returns:
        The newly created TechKnowledgeBase record (status=draft).
    """
    latest = await _latest_version(session)
    new_version = _next_minor_version(latest.version) if latest else "1.0.0"

    kb = TechKnowledgeBase(
        version=new_version,
        action_types_covered=action_types,
        point_count=0,
        status=KBStatus.draft,
        notes=notes,
    )
    session.add(kb)
    await session.flush()  # get PK without committing
    logger.info("Created draft KB version %s", new_version)
    return kb


async def add_tech_points(
    session: AsyncSession,
    kb_version: str,
    source_task_id: uuid.UUID,
    extraction_results: list[ExtractionResult],
) -> int:
    """Add extracted tech points to a draft KB version.

    Args:
        kb_version: The draft version string to add points to.
        source_task_id: The AnalysisTask.id of the expert video that produced these points.
        extraction_results: List of ExtractionResult from tech_extractor.

    Returns:
        Number of TechPoints actually inserted.

    Raises:
        VersionNotFoundError: if the version doesn't exist.
        VersionNotDraftError: if the version is not in draft status.
    """
    kb = await session.get(TechKnowledgeBase, kb_version)
    if kb is None:
        raise VersionNotFoundError(kb_version)
    if kb.status != KBStatus.draft:
        raise VersionNotDraftError(kb_version, kb.status.value)

    # Aggregate dimensions across multiple segments: keep the entry with the
    # highest extraction_confidence for each (action_type, dimension) pair.
    # This avoids UniqueViolationError on uq_expert_point_version_action_dim.
    best: dict[tuple[str, str], object] = {}  # (action_type, dimension) → dim
    for result in extraction_results:
        if result.action_type not in ("forehand_topspin", "backhand_push"):
            logger.debug("Skipping unknown action type: %s", result.action_type)
            continue
        for dim in result.dimensions:
            key = (result.action_type, dim.dimension)
            existing = best.get(key)
            if existing is None or dim.extraction_confidence > existing.extraction_confidence:
                best[key] = dim
            # Keep a reference to the action_type string alongside the dim
            dim._action_type_str = result.action_type  # type: ignore[attr-defined]

    inserted = 0
    for (action_type_str, _), dim in best.items():
        action_type = ActionType(action_type_str)
        point = ExpertTechPoint(
            knowledge_base_version=kb_version,
            action_type=action_type,
            dimension=dim.dimension,
            param_min=dim.param_min,
            param_max=dim.param_max,
            param_ideal=dim.param_ideal,
            unit=dim.unit,
            extraction_confidence=dim.extraction_confidence,
            source_video_id=source_task_id,
        )
        session.add(point)
        inserted += 1

    # Update point count
    kb.point_count = kb.point_count + inserted
    await session.flush()
    logger.info("Added %d tech points to KB version %s", inserted, kb_version)
    return inserted


async def approve_version(
    session: AsyncSession,
    version: str,
    approved_by: str,
    notes: Optional[str] = None,
) -> tuple[TechKnowledgeBase, Optional[str]]:
    """Approve a draft version: set it active and archive the current active version.

    Enforces the single-active-version constraint atomically.

    Returns:
        (newly_active_kb, previous_active_version_str | Optional[str])

    Raises:
        VersionNotFoundError / VersionNotDraftError as appropriate.
    """
    kb = await session.get(TechKnowledgeBase, version)
    if kb is None:
        raise VersionNotFoundError(version)
    if kb.status != KBStatus.draft:
        raise VersionNotDraftError(version, kb.status.value)

    # Feature 002: block approval if any tech points have unresolved conflicts
    conflict_result = await session.execute(
        select(ExpertTechPoint).where(
            ExpertTechPoint.knowledge_base_version == version,
            ExpertTechPoint.conflict_flag.is_(True),
        )
    )
    conflict_points = conflict_result.scalars().all()
    if conflict_points:
        raise ConflictUnresolvedError(version, len(conflict_points))

    # Archive any currently active version
    result = await session.execute(
        select(TechKnowledgeBase).where(TechKnowledgeBase.status == KBStatus.active)
    )
    previous_active: Optional[TechKnowledgeBase] = result.scalar_one_or_none()
    previous_version_str: Optional[str] = None

    if previous_active is not None:
        previous_version_str = previous_active.version
        previous_active.status = KBStatus.archived
        logger.info("Archived KB version %s", previous_version_str)

    # Activate the new version
    kb.status = KBStatus.active
    kb.approved_by = approved_by
    kb.approved_at = datetime.now(tz=timezone.utc)
    if notes:
        kb.notes = notes

    await session.flush()
    logger.info("Activated KB version %s (approved by %s)", version, approved_by)
    return kb, previous_version_str


async def get_active_version(session: AsyncSession) -> Optional[TechKnowledgeBase]:
    """Return the currently active knowledge base version, or None if none exists."""
    result = await session.execute(
        select(TechKnowledgeBase).where(TechKnowledgeBase.status == KBStatus.active)
    )
    return result.scalar_one_or_none()


async def get_version(session: AsyncSession, version: str) -> TechKnowledgeBase:
    """Fetch a specific version by string.

    Raises:
        VersionNotFoundError if not found.
    """
    kb = await session.get(TechKnowledgeBase, version)
    if kb is None:
        raise VersionNotFoundError(version)
    return kb


async def list_versions(session: AsyncSession) -> list[TechKnowledgeBase]:
    """Return all knowledge base versions ordered by creation time descending."""
    result = await session.execute(
        select(TechKnowledgeBase).order_by(TechKnowledgeBase.created_at.desc())
    )
    return list(result.scalars().all())


async def get_tech_points(
    session: AsyncSession,
    version: str,
    action_type: Optional[str] = None,
) -> list[ExpertTechPoint]:
    """Return all ExpertTechPoints for a given KB version, optionally filtered by action_type."""
    stmt = select(ExpertTechPoint).where(
        ExpertTechPoint.knowledge_base_version == version
    )
    if action_type:
        stmt = stmt.where(ExpertTechPoint.action_type == ActionType(action_type))
    result = await session.execute(stmt)
    return list(result.scalars().all())
