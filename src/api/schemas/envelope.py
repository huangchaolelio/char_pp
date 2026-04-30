"""Feature-017 — 统一 API 响应信封（章程 v1.4.0 原则 IX）.

落位参照 `specs/017-api-standardization/data-model.md` §1。
所有 `/api/v1/**` 接口响应体必须匹配 `SuccessEnvelope[T]` 或 `ErrorEnvelope` 之一。

使用方式::

    # 非分页接口
    @router.get("/coaches/{coach_id}", response_model=SuccessEnvelope[CoachOut])
    async def get_coach(...) -> SuccessEnvelope[CoachOut]:
        return ok(await service.get_coach(coach_id))

    # 分页接口
    @router.get("/tasks", response_model=SuccessEnvelope[list[TaskOut]])
    async def list_tasks(...) -> SuccessEnvelope[list[TaskOut]]:
        items, total = await service.list_tasks(page=p, page_size=ps)
        return page(items, page=p, page_size=ps, total=total)
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

DataT = TypeVar("DataT")


# ── 分页元信息 ─────────────────────────────────────────────────────────────
class PaginationMeta(BaseModel):
    """分页元信息. 非分页接口为 ``None``.

    约束：
    - ``page`` ≥ 1（页码从 1 开始）
    - 1 ≤ ``page_size`` ≤ 100
    - ``total`` ≥ 0（符合条件的全量记录数）
    """

    model_config = ConfigDict(extra="forbid")

    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100)
    total: int = Field(..., ge=0)


# ── 成功信封 ───────────────────────────────────────────────────────────────
class SuccessEnvelope(BaseModel, Generic[DataT]):
    """成功响应信封. 字段互斥——不得出现 ``error``.

    - ``success`` 永远为 ``True``
    - ``data`` 类型由泛型参数 ``DataT`` 决定（单对象 / 列表 / None / dict）
    - ``meta`` 仅对分页接口非空；非分页接口为 ``None``
    """

    model_config = ConfigDict(extra="forbid")

    success: bool = True
    data: DataT | None = None
    meta: PaginationMeta | None = None


# ── 错误详情（专用子类） ──────────────────────────────────────────
class ValidationErrorDetails(BaseModel):
    """``VALIDATION_FAILED`` / ``INVALID_PAGE_SIZE`` / ``INVALID_ENUM_VALUE`` 共用结构."""

    model_config = ConfigDict(extra="forbid")

    field: str | None = None
    value: str | int | None = None
    allowed: list[str] | None = None


class UpstreamErrorDetails(BaseModel):
    """``LLM_/COS_/DB_/WHISPER_UPSTREAM_FAILED`` 共用结构."""

    model_config = ConfigDict(extra="forbid")

    upstream: str
    upstream_code: str | None = None
    upstream_message: str | None = None


# ── 错误信封 ───────────────────────────────────────────────────────────────
class ErrorBody(BaseModel):
    """错误响应中的 ``error`` 字段. ``details`` 允许任意结构化上下文."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    # 不同错误码的 details 语义不同，此处接受任意 dict 或 None；
    # 专用子类（ValidationErrorDetails / UpstreamErrorDetails）在抛 AppException 时通过 `.model_dump()` 传入
    details: dict[str, Any] | None = None


class ErrorEnvelope(BaseModel):
    """错误响应信封. 字段互斥——不得出现 ``data`` / ``meta``."""

    model_config = ConfigDict(extra="forbid")

    success: bool = False
    error: ErrorBody


# ── 便捷构造器 ─────────────────────────────────────────────────────────────
def ok(
    data: DataT,
    meta: PaginationMeta | None = None,
) -> SuccessEnvelope[DataT]:
    """构造非分页成功响应. 路由层唯一推荐的成功出口之一."""
    return SuccessEnvelope[DataT](success=True, data=data, meta=meta)


def page(
    items: list[DataT],
    *,
    page: int,  # noqa: A002 — 与 OpenAPI / 用户规则保持字段名一致
    page_size: int,
    total: int,
) -> SuccessEnvelope[list[DataT]]:
    """构造分页成功响应. 路由层唯一推荐的列表/分页出口.

    Args:
        items: 当前页数据列表
        page: 当前页码（从 1 开始）
        page_size: 每页容量（1-100）
        total: 符合条件的全量记录数
    """
    return SuccessEnvelope[list[DataT]](
        success=True,
        data=items,
        meta=PaginationMeta(page=page, page_size=page_size, total=total),
    )


__all__ = [
    "DataT",
    "PaginationMeta",
    "SuccessEnvelope",
    "ErrorBody",
    "ErrorEnvelope",
    "ValidationErrorDetails",
    "UpstreamErrorDetails",
    "ok",
    "page",
]
