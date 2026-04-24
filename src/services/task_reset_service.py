"""TaskResetService — admin-initiated data reset for Feature 013 (R5).

Clears all historical task-related rows while preserving "core assets"
(coaches, classifications, tech standards, published KB versions, skills,
reference videos). Guarded by ``confirmation_token`` that must equal
``settings.admin_reset_token``; enforcement lives in the admin router.

Deleted tables (TRUNCATE … CASCADE):
  * analysis_tasks
  * audio_transcripts
  * coaching_advice
  * teaching_tips
  * expert_tech_points
  * tech_semantic_segments
  * athlete_motion_analyses
  * diagnosis_reports
  * deviation_reports
  * skill_executions

Deleted rows (conditional DELETE):
  * tech_knowledge_bases WHERE status = 'draft'

Preserved tables (counts reported for audit):
  * coaches / coach_video_classifications / video_classifications
  * tech_standards / tech_knowledge_bases WHERE status != 'draft'
  * skills / reference_videos / reference_video_segments

Dry-run mode: computes the pre-delete counts and returns them as
``deleted_counts``, but performs no mutations (``duration_ms`` still
measured).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# Tables whose entire contents should be removed.  Order does not matter
# because a single TRUNCATE ... CASCADE statement covers them all.
_TRUNCATE_TABLES: tuple[str, ...] = (
    "analysis_tasks",
    "audio_transcripts",
    "coaching_advice",
    "teaching_tips",
    "expert_tech_points",
    "tech_semantic_segments",
    "athlete_motion_analyses",
    "diagnosis_reports",
    "deviation_reports",
    "skill_executions",
)

# Tables whose row-counts should be reported for audit after reset.
_PRESERVE_TABLES: tuple[str, ...] = (
    "coaches",
    "coach_video_classifications",
    "video_classifications",
    "tech_standards",
    "skills",
    "reference_videos",
    "reference_video_segments",
)


@dataclass(slots=True)
class ResetReportData:
    reset_at: datetime
    dry_run: bool
    deleted_counts: dict[str, int] = field(default_factory=dict)
    preserved_counts: dict[str, int] = field(default_factory=dict)
    duration_ms: int = 0


class TaskResetService:
    """Perform (or simulate) the Feature-013 task-pipeline data reset."""

    async def reset(
        self,
        session: AsyncSession,
        *,
        dry_run: bool = False,
    ) -> ResetReportData:
        """Execute the reset.

        Token validation is the caller's responsibility (admin router checks
        ``settings.admin_reset_token``). This method trusts the caller.
        """
        start = time.monotonic()

        deleted_counts: dict[str, int] = {}
        for table in _TRUNCATE_TABLES:
            count = await self._count_table(session, table)
            deleted_counts[table] = count

        draft_kb_count = await self._count_draft_kbs(session)
        deleted_counts["tech_knowledge_bases_draft"] = draft_kb_count

        preserved_counts: dict[str, int] = {}
        for table in _PRESERVE_TABLES:
            preserved_counts[table] = await self._count_table(session, table)
        preserved_counts["tech_knowledge_bases_published"] = (
            await self._count_published_kbs(session)
        )

        if not dry_run:
            await self._execute_reset(session)

        duration_ms = int((time.monotonic() - start) * 1000)
        report = ResetReportData(
            reset_at=datetime.now(timezone.utc),
            dry_run=dry_run,
            deleted_counts=deleted_counts,
            preserved_counts=preserved_counts,
            duration_ms=duration_ms,
        )
        logger.warning(
            "TaskResetService.reset: dry_run=%s duration_ms=%d deleted=%s preserved=%s",
            dry_run, duration_ms,
            {k: v for k, v in deleted_counts.items() if v},
            {k: v for k, v in preserved_counts.items() if v},
        )
        return report

    # -- helpers -----------------------------------------------------------

    async def _count_table(self, session: AsyncSession, table: str) -> int:
        # ``table`` is from our internal whitelist, never user input —
        # safe to format into the SQL.
        row = (
            await session.execute(text(f"SELECT COUNT(*) FROM {table}"))
        ).scalar_one()
        return int(row)

    async def _count_draft_kbs(self, session: AsyncSession) -> int:
        row = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM tech_knowledge_bases WHERE status = 'draft'"
                )
            )
        ).scalar_one()
        return int(row)

    async def _count_published_kbs(self, session: AsyncSession) -> int:
        row = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM tech_knowledge_bases WHERE status != 'draft'"
                )
            )
        ).scalar_one()
        return int(row)

    async def _execute_reset(self, session: AsyncSession) -> None:
        """Run the TRUNCATE + DELETE in a single transaction."""
        # Single TRUNCATE covers all tables + cascades to any dependant.
        truncate_list = ", ".join(_TRUNCATE_TABLES)
        await session.execute(
            text(f"TRUNCATE TABLE {truncate_list} RESTART IDENTITY CASCADE")
        )
        await session.execute(
            text("DELETE FROM tech_knowledge_bases WHERE status = 'draft'")
        )
        await session.commit()
