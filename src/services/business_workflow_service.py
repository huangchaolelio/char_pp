"""Feature-018 — 业务流程总览服务（US1）.

对齐:
- specs/018-workflow-standardization/spec.md FR-005 ~ FR-007
- data-model.md § 7（响应 DTO）+ § 10（索引）
- research.md R2（pg_class.reltuples 降级策略）、R6（?business_phase / ?task_type 矛盾校验）

核心职责:
1. ``WorkflowOverviewService.get_overview()``:
   - 读 ``pg_class.reltuples`` 判断是否降级
   - 完整档：按 ``(business_phase, business_step, status)`` GROUP BY 聚合计数 +
     子查询 percentile_cont 算耗时
   - 降级档：仅 GROUP BY 计数，省略百分位
   - 返回 ``(WorkflowOverviewSnapshot, WorkflowOverviewMeta)``

2. ``_validate_phase_step_task_type_combo()``:
   - 对外暴露的校验矩阵（FR-004/FR-017 调用）
   - 不合法 ⇒ ``AppException(INVALID_PHASE_STEP_COMBO)``
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import AppException, ErrorCode
from src.api.schemas.business_workflow import (
    PhaseSnapshot,
    StepSnapshot,
    WorkflowOverviewMeta,
    WorkflowOverviewSnapshot,
)
from src.models.analysis_task import BusinessPhase

logger = logging.getLogger(__name__)


# ── 阶段 → 步骤列表的静态映射（保证响应中每个阶段都展示全部已知步骤，
# 即使计数为 0 也不会 "消失"；未命中数据的步骤走零计数兜底） ──────────────
_PHASE_STEPS: dict[str, tuple[str, ...]] = {
    "TRAINING": (
        "scan_cos_videos",
        "preprocess_video",
        "classify_video",
        "extract_kb",
    ),
    "STANDARDIZATION": (
        "review_conflicts",
        "kb_version_activate",
        "build_standards",
    ),
    "INFERENCE": (
        "scan_athlete_videos",
        "preprocess_athlete_video",
        "diagnose_athlete",
    ),
}

# ── 降级阈值（按 FR-007 / Q3 决议）────────────────────────────────────────
_DEGRADATION_THRESHOLD = 1_000_000  # > 100 万 ⇒ 降级（省略 p50 / p95）


# ── (phase, step, task_type) 三元组矛盾校验矩阵 ──────────────────────────
# 以 (phase, step) 为键，列出兼容的 task_type 集合；给定显式 task_type 不在集合内 ⇒ 矛盾
_PHASE_STEP_TASK_TYPE_MATRIX: dict[tuple[str, str], set[str]] = {
    ("TRAINING", "scan_cos_videos"): {"video_classification"},
    ("TRAINING", "preprocess_video"): {"video_preprocessing"},
    ("TRAINING", "classify_video"): {"video_classification"},
    ("TRAINING", "extract_kb"): {"kb_extraction"},
    ("STANDARDIZATION", "review_conflicts"): set(),  # 非 analysis_tasks 业务；显式 task_type 一律冲突
    ("STANDARDIZATION", "kb_version_activate"): set(),
    ("STANDARDIZATION", "build_standards"): set(),
    ("INFERENCE", "scan_athlete_videos"): {"athlete_video_classification"},
    ("INFERENCE", "preprocess_athlete_video"): {"athlete_video_preprocessing"},
    ("INFERENCE", "diagnose_athlete"): {"athlete_diagnosis"},
}

# 单独按 phase 维度的允许 task_type 集合（phase 指定但 step 未指定时使用）
_PHASE_TASK_TYPES: dict[str, set[str]] = {
    "TRAINING": {"video_classification", "video_preprocessing", "kb_extraction"},
    "STANDARDIZATION": set(),
    "INFERENCE": {
        "athlete_diagnosis",
        "athlete_video_classification",
        "athlete_video_preprocessing",
    },
}


def _validate_phase_step_task_type_combo(
    phase: str | None,
    step: str | None,
    task_type: str | None,
) -> None:
    """校验 (phase, step, task_type) 三元组语义无冲突 (FR-017 + research R6).

    规则：
    - phase 与 task_type 同时指定 ⇒ task_type 必须在该 phase 允许集合内
    - phase + step + task_type 三者都指定 ⇒ task_type 必须在 (phase, step) 允许集合内
    - step 指定但无对应矩阵条目 ⇒ 由路由层 INVALID_ENUM_VALUE 前置拦截，这里不重复

    冲突时抛 ``AppException(INVALID_PHASE_STEP_COMBO, 400)``。
    """
    if task_type is None:
        return
    if phase is None and step is None:
        return

    if phase is not None and step is not None:
        allowed = _PHASE_STEP_TASK_TYPE_MATRIX.get((phase, step))
        if allowed is None or task_type not in allowed:
            raise AppException(
                ErrorCode.INVALID_PHASE_STEP_COMBO,
                message="business_phase / business_step / task_type 三者语义矛盾",
                details={
                    "conflict": "phase_step_task_type_mismatch",
                    "phase": phase,
                    "step": step,
                    "task_type": task_type,
                },
            )
        return

    if phase is not None:
        allowed = _PHASE_TASK_TYPES.get(phase, set())
        if task_type not in allowed:
            raise AppException(
                ErrorCode.INVALID_PHASE_STEP_COMBO,
                message="business_phase 与 task_type 语义矛盾",
                details={
                    "conflict": "phase_task_type_mismatch",
                    "phase": phase,
                    "task_type": task_type,
                },
            )


class WorkflowOverviewService:
    """三阶段八步骤总览聚合查询服务."""

    def __init__(self, *, degradation_threshold: int = _DEGRADATION_THRESHOLD) -> None:
        self._degradation_threshold = degradation_threshold

    async def _estimate_analysis_tasks_rows(self, session: AsyncSession) -> int:
        """读 pg_class.reltuples 做行数估算（避免 COUNT(*) 拉高 P95）."""
        row = await session.execute(
            text(
                "SELECT reltuples::bigint AS estimate "
                "FROM pg_class WHERE relname = 'analysis_tasks'"
            )
        )
        val = row.scalar_one_or_none()
        if val is None:
            return 0
        return max(0, int(val))

    async def get_overview(
        self,
        session: AsyncSession,
        window_hours: int = 24,
    ) -> tuple[WorkflowOverviewSnapshot, WorkflowOverviewMeta]:
        """聚合三阶段八步骤计数 + 耗时百分位。

        返回 (snapshot, meta)，路由层直接构造 SuccessEnvelope 落位。
        """
        if window_hours < 1 or window_hours > 168:
            raise AppException(
                ErrorCode.INVALID_ENUM_VALUE,
                message="window_hours 超出 [1, 168] 范围",
                details={"field": "window_hours", "value": str(window_hours), "allowed": ["1..168"]},
            )

        rows_estimate = await self._estimate_analysis_tasks_rows(session)
        degraded = rows_estimate > self._degradation_threshold
        degraded_reason: str | None = (
            "row_count_exceeds_latency_budget" if degraded else None
        )

        # ── 1) 四类计数聚合 ──────────────────────────────────────
        counts_map = await self._aggregate_counts(session)

        # ── 2) recent_24h_completed（按窗口） ───────────────────
        recent_map = await self._aggregate_recent_completed(session, window_hours)

        # ── 3) p50 / p95（完整档才计算） ─────────────────────────
        if not degraded:
            percentile_map = await self._aggregate_percentiles(session, window_hours)
        else:
            percentile_map = {}

        # ── 4) 组装三阶段 PhaseSnapshot ──────────────────────────
        snapshot = WorkflowOverviewSnapshot(
            TRAINING=self._build_phase_snapshot(
                "TRAINING", counts_map, recent_map, percentile_map, degraded
            ),
            STANDARDIZATION=self._build_phase_snapshot(
                "STANDARDIZATION", counts_map, recent_map, percentile_map, degraded
            ),
            INFERENCE=self._build_phase_snapshot(
                "INFERENCE", counts_map, recent_map, percentile_map, degraded
            ),
        )

        meta = WorkflowOverviewMeta(
            generated_at=datetime.now(tz=ZoneInfo("Asia/Shanghai")),
            window_hours=window_hours,
            degraded=degraded,
            degraded_reason=degraded_reason,  # type: ignore[arg-type]
        )
        return snapshot, meta

    # ─────────────────────────── 内部实现 ────────────────────────────────

    async def _aggregate_counts(
        self, session: AsyncSession
    ) -> dict[tuple[str, str], dict[str, int]]:
        """按 (phase, step, status) 三元组聚合全量计数.

        同时兼容 analysis_tasks（四种 task_type，有 deleted_at 软删）+ extraction_jobs
        （仅 TRAINING/extract_kb）+ video_preprocessing_jobs + tech_knowledge_bases
        （后两表按本 Feature 约束单 step 单 phase，从 analysis_tasks 主导即可）。

        **关键**：本接口的 "pending/processing/success/failed" 语义以 analysis_tasks
        为主线（运营关心的任务视角），其他表由独立接口暴露；因此这里仅查 analysis_tasks。
        """
        result: dict[tuple[str, str], dict[str, int]] = {}

        rows = await session.execute(
            text(
                """
                SELECT business_phase::text AS phase,
                       business_step AS step,
                       status::text AS status,
                       COUNT(*)::bigint AS cnt
                FROM analysis_tasks
                WHERE deleted_at IS NULL
                GROUP BY business_phase, business_step, status
                """
            )
        )
        for row in rows.mappings():
            key = (row["phase"], row["step"])
            bucket = result.setdefault(
                key, {"pending": 0, "processing": 0, "success": 0, "failed": 0}
            )
            status = row["status"]
            # 状态归一：partial_success → success；rejected → failed
            if status == "partial_success":
                bucket["success"] += int(row["cnt"])
            elif status == "rejected":
                bucket["failed"] += int(row["cnt"])
            elif status in bucket:
                bucket[status] += int(row["cnt"])
        return result

    async def _aggregate_recent_completed(
        self, session: AsyncSession, window_hours: int
    ) -> dict[tuple[str, str], int]:
        """按 (phase, step) 聚合窗口内已完成（success | partial_success）数量."""
        result: dict[tuple[str, str], int] = {}
        # 使用 make_interval 避免 asyncpg 整型参数与字符串拼接的类型冲突
        rows = await session.execute(
            text(
                """
                SELECT business_phase::text AS phase,
                       business_step AS step,
                       COUNT(*)::bigint AS cnt
                FROM analysis_tasks
                WHERE deleted_at IS NULL
                  AND status IN ('success', 'partial_success')
                  AND completed_at IS NOT NULL
                  AND completed_at >= (timezone('Asia/Shanghai', now()) - make_interval(hours => :hours))
                GROUP BY business_phase, business_step
                """
            ),
            {"hours": int(window_hours)},
        )
        for row in rows.mappings():
            result[(row["phase"], row["step"])] = int(row["cnt"])
        return result

    async def _aggregate_percentiles(
        self, session: AsyncSession, window_hours: int
    ) -> dict[tuple[str, str], tuple[float | None, float | None]]:
        """按 (phase, step) 聚合窗口内耗时 p50/p95 秒."""
        result: dict[tuple[str, str], tuple[float | None, float | None]] = {}
        rows = await session.execute(
            text(
                """
                SELECT business_phase::text AS phase,
                       business_step AS step,
                       percentile_cont(0.5) WITHIN GROUP (
                         ORDER BY EXTRACT(EPOCH FROM (completed_at - started_at))
                       ) AS p50,
                       percentile_cont(0.95) WITHIN GROUP (
                         ORDER BY EXTRACT(EPOCH FROM (completed_at - started_at))
                       ) AS p95
                FROM analysis_tasks
                WHERE deleted_at IS NULL
                  AND status IN ('success', 'partial_success')
                  AND started_at IS NOT NULL
                  AND completed_at IS NOT NULL
                  AND completed_at >= (timezone('Asia/Shanghai', now()) - make_interval(hours => :hours))
                GROUP BY business_phase, business_step
                """
            ),
            {"hours": int(window_hours)},
        )
        for row in rows.mappings():
            p50 = float(row["p50"]) if row["p50"] is not None else None
            p95 = float(row["p95"]) if row["p95"] is not None else None
            result[(row["phase"], row["step"])] = (p50, p95)
        return result

    def _build_phase_snapshot(
        self,
        phase: str,
        counts_map: dict[tuple[str, str], dict[str, int]],
        recent_map: dict[tuple[str, str], int],
        percentile_map: dict[tuple[str, str], tuple[float | None, float | None]],
        degraded: bool,
    ) -> PhaseSnapshot:
        steps: dict[str, StepSnapshot] = {}
        for step in _PHASE_STEPS[phase]:
            key = (phase, step)
            counts = counts_map.get(
                key, {"pending": 0, "processing": 0, "success": 0, "failed": 0}
            )
            recent = recent_map.get(key, 0)
            if degraded:
                p50, p95 = None, None
            else:
                p50, p95 = percentile_map.get(key, (None, None))
            snapshot_kwargs: dict[str, Any] = {
                "step": step,
                "pending": counts["pending"],
                "processing": counts["processing"],
                "success": counts["success"],
                "failed": counts["failed"],
                "recent_24h_completed": recent,
            }
            if not degraded:
                snapshot_kwargs["p50_seconds"] = p50
                snapshot_kwargs["p95_seconds"] = p95
            steps[step] = StepSnapshot(**snapshot_kwargs)
        return PhaseSnapshot(phase=phase, steps=steps)  # type: ignore[arg-type]


__all__ = [
    "WorkflowOverviewService",
    "_validate_phase_step_task_type_combo",
]
