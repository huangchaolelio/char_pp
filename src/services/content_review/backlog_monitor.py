"""Feature-022 · T032 — 待审核积压监控.

提供 :func:`check_pending_backlog` 周期任务，扫描 ``coach_video_classifications``
中等待时长 > ``settings.review_pending_red_line_hours`` 的行；命中即写入 ERROR 级
结构化日志（不阻塞业务流程，纯监控与告警通知）。

配套调度: 由 housekeeping_task.cleanup_pending_backlog 包装为 Celery shared_task；
celery_app.beat_schedule 中以"每小时一次"频率触发，沿用 ``default`` 队列。

设计要点（章程原则 V「可观测性」）：
- **不抛异常 / 不阻塞**：积压告警是"提示运维"而非"业务校验"，任何 SQL 异常吃掉
- **结构化日志**：``metric=content_review_backlog_alert`` + 命中明细，便于 SRE 仪表盘
  按阶段聚合 + ``alert.severity=high`` 触发分级告警
- **采样而非详细打印**：仅打印 top-N 行的关键字段（id / pending_since / coach_name /
  tech_category），避免一次告警刷出几千行日志
- **同时埋 pending 总数 / p95 指标**：复用 :func:`review_service.record_pending_metrics`，
  让"积压告警 + 阶段健康度"一次跑里同时上报，节省 Beat 触发次数
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.coach_video_classification import CoachVideoClassification
from src.services.content_review.review_service import record_pending_metrics
from src.utils.time_utils import now_cst


logger = logging.getLogger(__name__)


_TOP_N_FOR_LOG = 10  # 命中告警时仅打印前 N 行明细，避免日志爆炸


async def check_pending_backlog(session: AsyncSession) -> dict:
    """周期扫描审核积压告警 + 上报 pending 健康度指标.

    流程：
      1. 调 :func:`record_pending_metrics` 采样总量 / p95（指标 2 + 4）
      2. 查 ``review_state='pending_review' AND pending_since < now() - red_line_hours``
      3. 命中行 > 0 ⇒ ERROR 级结构化日志 + 列出 top-N 明细
      4. 命中行 = 0 ⇒ DEBUG 级（健康日常状态不刷屏）

    Returns:
        ``{"pending_count": int, "backlog_count": int, "p95_seconds": float | None}``

    Side-effects:
        - 通过 logger 输出结构化日志（INFO 总量 / ERROR 告警）
        - 不修改任何 DB 行
    """
    settings = get_settings()
    red_line_hours = settings.review_pending_red_line_hours
    cutoff = now_cst() - timedelta(hours=red_line_hours)

    # ── 1. 总量与 p95 指标（指标 2 + 4） ───────────────────────────────
    metrics = await record_pending_metrics(session)
    pending_count = int(metrics["pending_count"])
    p95_seconds = metrics["pending_since_p95_seconds"]

    # ── 2. 命中红线的明细（仅查必要列，避免大对象） ─────────────────────
    backlog_stmt = (
        select(
            CoachVideoClassification.id,
            CoachVideoClassification.coach_name,
            CoachVideoClassification.action,
            CoachVideoClassification.pending_since,
        )
        .where(
            CoachVideoClassification.review_state == "pending_review",
            CoachVideoClassification.pending_since.is_not(None),
            CoachVideoClassification.pending_since < cutoff,
        )
        .order_by(CoachVideoClassification.pending_since.asc())
        .limit(_TOP_N_FOR_LOG)
    )
    backlog_rows = (await session.execute(backlog_stmt)).all()
    backlog_count_top_n = len(backlog_rows)

    # 为给运维一个"全量积压数"，再做一次 count 子查询（避免拉全部行）
    from sqlalchemy import func as _f

    full_count_stmt = (
        select(_f.count())
        .select_from(CoachVideoClassification)
        .where(
            CoachVideoClassification.review_state == "pending_review",
            CoachVideoClassification.pending_since.is_not(None),
            CoachVideoClassification.pending_since < cutoff,
        )
    )
    full_backlog_count = int(
        (await session.execute(full_count_stmt)).scalar_one() or 0
    )

    # ── 3. 命中 ⇒ ERROR 级告警；未命中 ⇒ DEBUG 级 ─────────────────────
    if full_backlog_count > 0:
        sample_ids = [str(r.id) for r in backlog_rows]
        logger.error(
            "review_backlog_alert: %d items pending > %dh "
            "(p95=%s, total_pending=%d)",
            full_backlog_count,
            red_line_hours,
            f"{p95_seconds:.1f}s" if p95_seconds is not None else "n/a",
            pending_count,
            extra={
                "metric": "content_review_backlog_alert",
                "phase": "CONTENT_PREP",
                "step": "content_review",
                "alert.severity": "high",
                "red_line_hours": red_line_hours,
                "backlog_count": full_backlog_count,
                "pending_count_total": pending_count,
                "p95_seconds": p95_seconds,
                "sample_ids": sample_ids,
                "sample_count": backlog_count_top_n,
            },
        )
    else:
        logger.debug(
            "review_backlog_check: no items beyond red line "
            "(red_line_hours=%d, pending_count=%d)",
            red_line_hours,
            pending_count,
        )

    return {
        "pending_count": pending_count,
        "backlog_count": full_backlog_count,
        "p95_seconds": p95_seconds,
    }


__all__ = ["check_pending_backlog"]
