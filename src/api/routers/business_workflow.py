"""Feature-018 — ``GET /api/v1/business-workflow/overview`` 路由 (US1).

对齐:
- contracts/business-workflow-overview.yaml（OpenAPI 契约）
- data-model.md § 7（SuccessEnvelope + WorkflowOverviewMeta 组合策略）
- spec.md FR-005 / FR-006 / FR-007

路由层职责：参数校验 + response_model 落位；聚合逻辑全部委托给
``WorkflowOverviewService``（分层规则项目规则 2）。

**响应信封策略**：因 Feature-017 ``SuccessEnvelope.meta`` 当前类型为
``PaginationMeta | None``，本接口的 meta（含 generated_at / window_hours / degraded）
与分页不同构，故路由层手工构造 ``JSONResponse``（绕过 pydantic ``meta`` 严格校验），
并在 ``response_model`` 声明 ``SuccessEnvelope[WorkflowOverviewSnapshot]`` 保留
OpenAPI 文档 200 schema 可导出性。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas.business_workflow import WorkflowOverviewSnapshot
from src.api.schemas.envelope import SuccessEnvelope
from src.db.session import get_db
from src.services.business_workflow_service import WorkflowOverviewService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/business-workflow", tags=["business-workflow"])


_service = WorkflowOverviewService()


@router.get(
    "/overview",
    response_model=SuccessEnvelope[WorkflowOverviewSnapshot],
    summary="三阶段八步骤业务总览 (Feature-018)",
)
async def get_business_workflow_overview(
    window_hours: int = Query(
        default=24,
        ge=1,
        le=168,
        description="聚合窗口（小时），默认 24，允许 1–168；越界 422 VALIDATION_FAILED",
    ),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """按业务阶段 × 步骤聚合的当前状态总览。

    响应 meta 字段结构：
    - ``generated_at``: CST 时间戳（ISO 8601 with +08:00）
    - ``window_hours``: 本次聚合窗口，回填请求参数
    - ``degraded``: 是否降级模式（true = analysis_tasks 行数 > 100 万）
    - ``degraded_reason``: 降级时为 ``"row_count_exceeds_latency_budget"``，
      完整档为 None（响应 JSON 中省略）

    降级档下 ``data.*.steps.*.p50_seconds / p95_seconds`` 字段省略。
    """
    snapshot, meta = await _service.get_overview(db, window_hours=window_hours)

    # 手工构造信封以支持自定义 meta 结构（见模块 docstring）
    payload = {
        "success": True,
        "data": snapshot.model_dump(mode="json", exclude_none=True),
        "meta": meta.model_dump(mode="json", exclude_none=True),
    }
    return JSONResponse(content=payload, status_code=200)


__all__ = ["router"]
