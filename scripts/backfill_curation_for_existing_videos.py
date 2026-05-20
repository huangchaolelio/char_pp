"""Feature-021 — 老库存视频清洗回填脚本.

目的：在 Feature-021 上线后，对所有"已分类 + 已预处理 + 尚未跑过 success
清洗"的旧视频补跑清洗任务。否则它们后续 KB 抽取会被 router 层 Gate 1
（``CURATION_REQUIRED``）拒绝。

策略（dry-run 友好）：
- 通过 ``coach_video_classifications`` 找出符合条件的视频
- 通过 ``video_curation_jobs`` 排除已有 success 行（跨版本均算）
- 调 ``submit_curation`` 入队（受 channel capacity 限流；满时等待）
- 默认 ``--dry-run`` 只打印将提交的列表；显式 ``--apply`` 才真正提交

支持过滤：
- ``--tech-category forehand_topspin`` 只回填某一类
- ``--coach-name 张继科`` 只回填某教练
- ``--limit N`` 回填前 N 条（按 created_at ASC）

用法：
    # 1. dry-run，看会回填多少条
    python3 scripts/backfill_curation_for_existing_videos.py --dry-run

    # 2. 真跑，限定 100 条避免一次入队太多
    python3 scripts/backfill_curation_for_existing_videos.py --apply --limit 100

    # 3. 仅某类技术 / 某教练
    python3 scripts/backfill_curation_for_existing_videos.py \
        --apply --tech-category forehand_topspin

注意：
- 本脚本只入队任务；实际清洗由 Celery default queue worker 异步处理
- 被入队的视频立即写入 ``video_curation_jobs.status='pending'``，重启 / 中断
  后重跑本脚本会自然短路（既有 pending 行不会重复入队，由 service 的
  幂等短路兜住——见 ``submit_curation`` Q1 决议）
- 真实跑前建议先 ``--dry-run`` 确认范围 + 跑 ``GET /api/v1/task-channels``
  确认 default 通道剩余容量
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Path setup so the script runs without `pip install -e .`
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from src.api.errors import AppException  # noqa: E402
from src.config import get_settings  # noqa: E402
from src.models.coach_video_classification import CoachVideoClassification  # noqa: E402
from src.models.video_curation_job import VideoCurationJob  # noqa: E402

logger = logging.getLogger("backfill_curation")


async def _build_session_factory():
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url, pool_size=2, max_overflow=2, pool_pre_ping=True,
    )
    return async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )


async def _find_targets(
    session: AsyncSession,
    *,
    tech_category: str | None,
    coach_name: str | None,
    limit: int | None,
) -> list[CoachVideoClassification]:
    """枚举"已分类 + 已预处理 + 无 success 清洗"的视频."""
    # Subquery: cos_object_keys with at least one success curation job
    success_sub = (
        select(VideoCurationJob.cos_object_key)
        .where(VideoCurationJob.status == "success")
    )

    stmt = (
        select(CoachVideoClassification)
        .where(
            CoachVideoClassification.preprocessed.is_(True),
            CoachVideoClassification.tech_category != "unclassified",
            CoachVideoClassification.tech_category.isnot(None),
            CoachVideoClassification.cos_object_key.notin_(success_sub),
        )
        .order_by(CoachVideoClassification.created_at.asc())
    )
    if tech_category:
        stmt = stmt.where(CoachVideoClassification.tech_category == tech_category)
    if coach_name:
        stmt = stmt.where(CoachVideoClassification.coach_name == coach_name)
    if limit:
        stmt = stmt.limit(limit)

    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


async def _apply_one(
    session: AsyncSession,
    row: CoachVideoClassification,
    *,
    rubric_version: str | None,
    force: bool,
) -> tuple[str, str | None]:
    """提交单条；返回 (outcome_kind, error_code)。

    outcome_kind: ``queued`` / ``short_circuit`` / ``rejected``
    """
    from src.services.curation.curation_service import submit_curation

    try:
        out = await submit_curation(
            session,
            classification_id=row.id,
            rubric_version=rubric_version,
            force=force,
        )
        if out.idempotent_short_circuit:
            return ("short_circuit", None)
        return ("queued", None)
    except AppException as exc:
        return ("rejected", exc.code.value)
    except Exception as exc:  # noqa: BLE001
        logger.exception("submit_curation crashed for id=%s", row.id)
        return ("rejected", f"INTERNAL_ERROR:{type(exc).__name__}")


async def _amain(args) -> int:
    factory = await _build_session_factory()

    async with factory() as session:
        targets = await _find_targets(
            session,
            tech_category=args.tech_category,
            coach_name=args.coach_name,
            limit=args.limit,
        )
        logger.info("found %d candidate videos to backfill", len(targets))
        if not targets:
            return 0

        if args.dry_run:
            for row in targets:
                logger.info(
                    "DRY-RUN would submit: id=%s coach=%s tech_category=%s cos=%s",
                    row.id, row.coach_name, row.tech_category, row.cos_object_key,
                )
            logger.info("DRY-RUN complete; pass --apply to actually submit")
            return 0

        # 真跑
        queued = 0
        short_circuited = 0
        rejected = 0
        rejection_summary: dict[str, int] = {}

        for i, row in enumerate(targets, 1):
            kind, err = await _apply_one(
                session, row,
                rubric_version=args.rubric_version,
                force=args.force,
            )
            if kind == "queued":
                queued += 1
            elif kind == "short_circuit":
                short_circuited += 1
            else:
                rejected += 1
                if err:
                    rejection_summary[err] = rejection_summary.get(err, 0) + 1

            # 进度日志：每 10 条或最后一条
            if i % 10 == 0 or i == len(targets):
                logger.info(
                    "progress: %d/%d (queued=%d short=%d rejected=%d)",
                    i, len(targets), queued, short_circuited, rejected,
                )

        logger.info(
            "DONE: total=%d queued=%d short_circuited=%d rejected=%d",
            len(targets), queued, short_circuited, rejected,
        )
        if rejection_summary:
            logger.warning("rejection breakdown: %s", rejection_summary)

        return 0 if rejected == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually submit curation tasks (default: dry-run)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print plan only; do not submit (default behavior)",
    )
    parser.add_argument(
        "--tech-category", default=None,
        help="Limit to one tech_category (e.g. 'forehand_topspin')",
    )
    parser.add_argument(
        "--coach-name", default=None,
        help="Limit to one coach_name (e.g. '张继科')",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Max number of videos to process (per ASC created_at)",
    )
    parser.add_argument(
        "--rubric-version", default=None,
        help="Override rubric version (default: latest)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Pass force=true to submit_curation (rare; default false)",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    if not args.apply and not args.dry_run:
        # default to dry-run
        args.dry_run = True

    if args.apply and args.dry_run:
        print("--apply and --dry-run are mutually exclusive", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
