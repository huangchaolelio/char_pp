#!/usr/bin/env python3
"""CLI utility to reset Feature-013 task-pipeline data (R5 / T048).

Usage:
    python reset_task_pipeline.py [--dry-run] [--confirm]

Behaviour:
  * Reads ``ADMIN_RESET_TOKEN`` from the environment (``.env`` loaded by
    ``src.config.Settings``).
  * Requires ``--confirm`` to actually run the destructive reset; without
    it the script refuses (safe default for operators).
  * ``--dry-run`` prints the pre-delete counts and exits without mutation.
  * Exits non-zero on any failure (missing token, DB error).

Example:
    # See what would be deleted
    python specs/013-task-pipeline-redesign/scripts/reset_task_pipeline.py --dry-run

    # Actually do it
    python specs/013-task-pipeline-redesign/scripts/reset_task_pipeline.py --confirm
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Make ``src`` importable when running this script directly.
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def _run(dry_run: bool) -> int:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from src.config import get_settings
    from src.services.task_reset_service import TaskResetService

    settings = get_settings()
    if not settings.admin_reset_token:
        print(
            "ERROR: ADMIN_RESET_TOKEN is not set in the environment/.env — aborting.",
            file=sys.stderr,
        )
        return 2

    engine = create_async_engine(settings.database_url, pool_size=1, max_overflow=0)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            svc = TaskResetService()
            report = await svc.reset(session=session, dry_run=dry_run)
    finally:
        await engine.dispose()

    payload = {
        "reset_at": report.reset_at.isoformat(),
        "dry_run": report.dry_run,
        "duration_ms": report.duration_ms,
        "deleted_counts": report.deleted_counts,
        "preserved_counts": report.preserved_counts,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reset Feature-013 task-pipeline data (destructive)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report pre-delete counts; make no changes.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required for a real reset; without it, the script refuses to run.",
    )
    args = parser.parse_args(argv)

    if not args.dry_run and not args.confirm:
        print(
            "Refusing to run without --confirm (or use --dry-run to preview).",
            file=sys.stderr,
        )
        return 1

    return asyncio.run(_run(dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
