"""Feature-021 KB 抽取强制门 — 公共门控查询.

由 :mod:`src.api.routers.tasks` 在排队 KB 抽取前调用，
也由 :mod:`src.services.kb_extraction_pipeline.step_executors.download_video`
在 DAG 第一步执行前调用，实现"两点对接"：

  router 层 (FR-010): 拦截"未跑过清洗就提 KB"，立即 ``CURATION_REQUIRED``
  DAG 层  (FR-008/9): 加载 ``effective_decision='accepted'`` 集合 +
                      ``accepted_duration_ratio==0`` 触发 ``LOW_QUALITY_SKIP``

bypass 开关：``settings.kb_extraction_bypass_curation_gate=True`` 时
两点都直通（路由不拦、DAG 读全量）；命中后 DAG 在 ``output_summary``
落 ``curation_bypass=true`` 留痕。

返回值约定：

- :func:`evaluate_curation_gate`：
    - ``ok``                  通过门 1 + 门 2，正常执行
    - ``low_quality_skip``    通过门 1，accepted_ratio==0 ⇒ DAG 应短路
    - ``low_quality_warn``    accepted_ratio ∈ (0, 0.3) ⇒ DAG 正常执行 + warning
    - ``required``            无 success 清洗作业 ⇒ router 应抛 CURATION_REQUIRED
    - ``bypassed``            bypass=True ⇒ 不查清洗、按全量走
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.video_curation_job import VideoCurationJob


GateDecision = Literal["ok", "low_quality_skip", "low_quality_warn", "required", "bypassed"]


@dataclass(slots=True)
class GateResult:
    """门控查询结果，路由 / DAG 层解读后采取相应动作。"""

    decision: GateDecision
    curation_job_id: UUID | None = None
    curation_rubric_version: str | None = None
    accepted_duration_ratio: float | None = None
    accepted_segment_count: int | None = None
    rejected_segment_count: int | None = None
    uncertain_segment_count: int | None = None
    total_segment_count: int | None = None


async def evaluate_curation_gate(
    session: AsyncSession,
    *,
    cos_object_key: str,
) -> GateResult:
    """查询该 ``cos_object_key`` 的最近一次 success 清洗作业并产出门控决策.

    顺序：
    1. bypass=True ⇒ 立即返回 ``bypassed``（不查 DB）
    2. 取最近 success 的 ``video_curation_jobs`` 行（按 ``completed_at DESC``）
    3. 不存在 ⇒ ``required``
    4. ``accepted_duration_ratio == 0`` ⇒ ``low_quality_skip``
    5. ``accepted_duration_ratio ∈ (0, low_quality_threshold)`` ⇒ ``low_quality_warn``
    6. 其它 ⇒ ``ok``

    ``low_quality_threshold`` 从 ``rubric.low_quality_ratio`` 读，但为减少
    跨包依赖，这里固定使用 0.3（与 v1.yaml + spec FR-009 一致）；未来阈值需
    随 rubric 联动时再注入 rubric_loader。
    """
    settings = get_settings()
    if settings.kb_extraction_bypass_curation_gate:
        return GateResult(decision="bypassed")

    stmt = (
        select(VideoCurationJob)
        .where(
            VideoCurationJob.cos_object_key == cos_object_key,
            VideoCurationJob.status == "success",
        )
        .order_by(VideoCurationJob.completed_at.desc())
        .limit(1)
    )
    job = (await session.execute(stmt)).scalar_one_or_none()
    if job is None:
        return GateResult(decision="required")

    ratio = float(job.accepted_duration_ratio or 0.0)
    if ratio <= 0.0:
        decision: GateDecision = "low_quality_skip"
    elif ratio < 0.3:
        decision = "low_quality_warn"
    else:
        decision = "ok"

    return GateResult(
        decision=decision,
        curation_job_id=job.id,
        curation_rubric_version=job.curation_rubric_version,
        accepted_duration_ratio=ratio,
        accepted_segment_count=job.accepted_segment_count,
        rejected_segment_count=job.rejected_segment_count,
        uncertain_segment_count=job.uncertain_segment_count,
        total_segment_count=job.total_segment_count,
    )


__all__ = ["GateDecision", "GateResult", "evaluate_curation_gate"]
