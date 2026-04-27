"""Feature-017 — 已下线接口哨兵路由（章程 v1.4.0 「已下线接口台账」段落）.

**作用**：为 ``specs/017-api-standardization/contracts/retirement-ledger.md`` 中登记的
每一条下线接口保留「方法+路径」哨兵路由，统一抛
:class:`~src.api.errors.AppException`(:attr:`~src.api.errors.ErrorCode.ENDPOINT_RETIRED`,
details={successor, migration_note})，由全局异常处理器渲染为 HTTP 404 + 错误信封.

**为什么不物理删除**：
- 物理删除后 FastAPI 默认 404 为 ``{"detail":"Not Found"}``，与业务下线无法区分
- 哨兵路由可在 ``error.details.successor`` 中指出替代路径，对调用方更友好
- 便于审计「某条路径何时下线 / 去向何处」

**变更约束**：
- 已下线条目只可追加，不可删除或改名
- 新增下线接口时，同步更新 ``contracts/retirement-ledger.md``
- 禁止复用已下线路径作为新接口路径
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from fastapi import APIRouter

from src.api.errors import AppException, ErrorCode


# ── 台账条目数据结构 ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class RetiredEndpoint:
    """一条下线接口的台账记录.

    Attributes:
        method: HTTP 方法，大写 — ``GET``/``POST``/``PATCH``/``DELETE``/``PUT``
        path: 完整旧路径（含 ``/api/v1`` 前缀）
        successor: 替代路径，单个字符串或（两步迁移场景）字符串列表
        migration_note: 语义差异说明
    """

    method: str
    path: str
    successor: str | tuple[str, ...]
    migration_note: str


# ── 台账（单一事实来源；仅追加） ──────────────────────────────────────────
RETIREMENT_LEDGER: tuple[RetiredEndpoint, ...] = (
    RetiredEndpoint(
        method="POST",
        path="/api/v1/tasks/expert-video",
        successor=(
            "/api/v1/tasks/classification",
            "/api/v1/tasks/kb-extraction",
        ),
        migration_note="原一次调用改为两次独立提交：先分类再提取 KB",
    ),
    RetiredEndpoint(
        method="POST",
        path="/api/v1/tasks/athlete-video",
        successor="/api/v1/tasks/diagnosis",
        migration_note="路径改名，请求/响应字段不变",
    ),
    RetiredEndpoint(
        method="GET",
        path="/api/v1/videos/classifications",
        successor="/api/v1/classifications",
        migration_note="资源前缀变更：videos/classifications → classifications",
    ),
    RetiredEndpoint(
        method="POST",
        path="/api/v1/videos/classifications/refresh",
        successor="/api/v1/classifications/scan",
        migration_note="接口改名：refresh → scan；行为一致",
    ),
    RetiredEndpoint(
        method="PATCH",
        path="/api/v1/videos/classifications/{cos_object_key:path}",
        successor="/api/v1/classifications/{id}",
        migration_note="路径参数由 COS 对象键改为分类记录 ID",
    ),
    RetiredEndpoint(
        method="POST",
        path="/api/v1/videos/classifications/batch-submit",
        successor="/api/v1/tasks/kb-extraction/batch",
        migration_note="批量提交合并到任务通道的批量入口",
    ),
    RetiredEndpoint(
        method="POST",
        path="/api/v1/diagnosis",
        successor="/api/v1/tasks/diagnosis",
        migration_note="同步 60s 改为异步提交，需轮询 GET /tasks/{task_id}",
    ),
)


# ── 哨兵处理器工厂 ────────────────────────────────────────────────────────
def _retired_handler_factory(endpoint: RetiredEndpoint) -> Callable:
    """为单条下线端点生成统一的 ENDPOINT_RETIRED handler.

    handler 接受任意路径/查询/请求体参数（FastAPI 会把未声明的请求体当作 body 忽略），
    总是同步抛 :class:`AppException`，由全局处理器渲染为错误信封.
    """
    # successor 在 dataclass 中可能是 tuple，序列化为 list 供 JSON 兼容
    successor_value: str | list[str]
    if isinstance(endpoint.successor, tuple):
        successor_value = list(endpoint.successor)
    else:
        successor_value = endpoint.successor

    async def _handler() -> None:
        raise AppException(
            ErrorCode.ENDPOINT_RETIRED,
            details={
                "successor": successor_value,
                "migration_note": endpoint.migration_note,
            },
        )

    _handler.__name__ = f"retired_{endpoint.method.lower()}_{endpoint.path}"
    _handler.__doc__ = (
        f"[RETIRED] {endpoint.method} {endpoint.path} — 请改用 {successor_value}"
    )
    return _handler


# ── 路由装配 ──────────────────────────────────────────────────────────────
def build_retired_router() -> APIRouter:
    """构造并返回包含所有哨兵路由的 ``APIRouter``.

    返回的 router 应以 **空 prefix** 挂载（因为 ``RETIREMENT_LEDGER.path`` 已含 ``/api/v1``），
    避免与 ``include_router(prefix="/api/v1")`` 叠加导致双重前缀.

    Returns:
        已为 :data:`RETIREMENT_LEDGER` 中每条记录注册了 handler 的 router.
    """
    router = APIRouter(tags=["_retired"])
    for endpoint in RETIREMENT_LEDGER:
        handler = _retired_handler_factory(endpoint)
        router.add_api_route(
            endpoint.path,
            handler,
            methods=[endpoint.method],
            # 哨兵路由返回错误信封，不需要 response_model
            include_in_schema=False,
        )
    return router


__all__ = [
    "RetiredEndpoint",
    "RETIREMENT_LEDGER",
    "build_retired_router",
]
