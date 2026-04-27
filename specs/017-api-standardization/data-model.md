# 数据模型: API 统一响应信封与错误码枚举

**日期**: 2026-04-27
**分支**: `017-api-standardization`
**注意**: 本 Feature 不引入任何数据库 schema 变更，因此本文件仅描述**代码侧 Pydantic/枚举模型**，不含 SQLAlchemy ORM。

## 1. ResponseEnvelope（统一响应信封）

**落位**: `src/api/schemas/envelope.py`（新增）

### 1.1 成功信封

```python
from typing import Generic, TypeVar
from pydantic import BaseModel, ConfigDict

DataT = TypeVar("DataT")


class PaginationMeta(BaseModel):
    """分页元信息。对非分页接口为 None。"""
    model_config = ConfigDict(extra="forbid")

    page: int          # 从 1 开始，默认 1
    page_size: int     # 默认 20，最大 100
    total: int         # 符合条件的全量记录数


class SuccessEnvelope(BaseModel, Generic[DataT]):
    """成功响应信封。字段互斥——不得出现 error。"""
    model_config = ConfigDict(extra="forbid")

    success: bool = True         # 永远为 True，FastAPI 的 response_model 会固定该值
    data: DataT | None           # 业务载荷；可为 None（204/空对象场景）
    meta: PaginationMeta | None = None  # 仅列表场景非空
```

**字段规则**:
- `success` 在成功响应中必为 `true`。不对外暴露为可变字段，由 helper `ok(data, meta=None)` 统一构造。
- `data` 类型由泛型 `DataT` 决定；`DataT` 可以是 `CoachOut`、`list[TaskOut]`、`None`、`dict[str, Any]`（兜底）等。
- `meta` 仅对列表接口非空；非列表接口应显式传 `meta=None`（等价于字段省略）。

### 1.2 错误信封

```python
class ErrorDetails(BaseModel):
    """错误详情。可携带任意结构化上下文；详见不同错误码的约定。"""
    model_config = ConfigDict(extra="allow")  # 允许任意 key，因不同错误码语义不同


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str          # 来自 ErrorCode 枚举的字符串值（如 "TASK_NOT_FOUND"）
    message: str       # 面向开发者的可读消息
    details: dict | None = None  # 可选上下文


class ErrorEnvelope(BaseModel):
    """错误响应信封。字段互斥——不得出现 data / meta。"""
    model_config = ConfigDict(extra="forbid")

    success: bool = False        # 永远为 False
    error: ErrorBody
```

### 1.3 专用 Details 子类（用于 `error.details` 的已知结构）

```python
class RetiredErrorDetails(BaseModel):
    """ENDPOINT_RETIRED 专用 details 结构。"""
    model_config = ConfigDict(extra="forbid")

    successor: str | list[str]   # 替代路径（单个或两步串联）
    migration_note: str | None = None  # 可选的语义差异说明


class ValidationErrorDetails(BaseModel):
    """VALIDATION_FAILED / INVALID_PAGE_SIZE / INVALID_ENUM_VALUE 共用结构。"""
    model_config = ConfigDict(extra="forbid")

    field: str | None = None
    value: str | int | None = None
    allowed: list[str] | None = None  # 用于 INVALID_ENUM_VALUE


class UpstreamErrorDetails(BaseModel):
    """LLM_/COS_/DB_/WHISPER_UPSTREAM_FAILED 共用结构。"""
    model_config = ConfigDict(extra="forbid")

    upstream: str                  # 例如 "venus-proxy"、"openai"、"cos-tencent"
    upstream_code: str | None = None
    upstream_message: str | None = None
```

### 1.4 便捷构造器

```python
def ok(data: DataT, meta: PaginationMeta | None = None) -> SuccessEnvelope[DataT]:
    return SuccessEnvelope[DataT](success=True, data=data, meta=meta)


def page(
    items: list[DataT],
    *,
    page: int,
    page_size: int,
    total: int,
) -> SuccessEnvelope[list[DataT]]:
    return SuccessEnvelope[list[DataT]](
        success=True,
        data=items,
        meta=PaginationMeta(page=page, page_size=page_size, total=total),
    )
```

## 2. ErrorCode（集中错误码枚举）

**落位**: `src/api/errors.py`（新增）

```python
from enum import Enum
from http import HTTPStatus


class ErrorCode(str, Enum):
    """API 统一错误码枚举。每个值绑定一个默认 HTTP 状态。"""
    # 通用
    VALIDATION_FAILED = "VALIDATION_FAILED"
    INVALID_ENUM_VALUE = "INVALID_ENUM_VALUE"
    INVALID_PAGE_SIZE = "INVALID_PAGE_SIZE"
    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    ENDPOINT_RETIRED = "ENDPOINT_RETIRED"
    INTERNAL_ERROR = "INTERNAL_ERROR"

    # 认证
    ADMIN_TOKEN_NOT_CONFIGURED = "ADMIN_TOKEN_NOT_CONFIGURED"
    ADMIN_TOKEN_INVALID = "ADMIN_TOKEN_INVALID"

    # 资源不存在（Feature-001~016 复用）
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    COACH_NOT_FOUND = "COACH_NOT_FOUND"
    TIP_NOT_FOUND = "TIP_NOT_FOUND"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    KB_VERSION_NOT_FOUND = "KB_VERSION_NOT_FOUND"
    COS_OBJECT_NOT_FOUND = "COS_OBJECT_NOT_FOUND"

    # 状态/业务约束
    TASK_NOT_READY = "TASK_NOT_READY"
    COACH_INACTIVE = "COACH_INACTIVE"
    COACH_ALREADY_INACTIVE = "COACH_ALREADY_INACTIVE"
    COACH_NAME_CONFLICT = "COACH_NAME_CONFLICT"
    JOB_NOT_FAILED = "JOB_NOT_FAILED"
    INVALID_STATUS = "INVALID_STATUS"
    INVALID_ACTION_TYPE = "INVALID_ACTION_TYPE"
    WRONG_TASK_TYPE = "WRONG_TASK_TYPE"
    KB_VERSION_NOT_DRAFT = "KB_VERSION_NOT_DRAFT"
    CONFLICT_UNRESOLVED = "CONFLICT_UNRESOLVED"
    CLASSIFICATION_REQUIRED = "CLASSIFICATION_REQUIRED"
    COS_KEY_NOT_CLASSIFIED = "COS_KEY_NOT_CLASSIFIED"
    BATCH_TOO_LARGE = "BATCH_TOO_LARGE"
    VIDEO_TOO_LONG = "VIDEO_TOO_LONG"
    MISSING_VIDEO = "MISSING_VIDEO"
    NO_AUDIO_TRANSCRIPT = "NO_AUDIO_TRANSCRIPT"
    INTERMEDIATE_EXPIRED = "INTERMEDIATE_EXPIRED"

    # 容量/队列
    CHANNEL_QUEUE_FULL = "CHANNEL_QUEUE_FULL"
    CHANNEL_DISABLED = "CHANNEL_DISABLED"

    # 上游依赖
    LLM_UPSTREAM_FAILED = "LLM_UPSTREAM_FAILED"
    COS_UPSTREAM_FAILED = "COS_UPSTREAM_FAILED"
    DB_UPSTREAM_FAILED = "DB_UPSTREAM_FAILED"
    WHISPER_UPSTREAM_FAILED = "WHISPER_UPSTREAM_FAILED"


# 错误码 → HTTP 状态 映射（单一事实来源）
ERROR_STATUS_MAP: dict[ErrorCode, HTTPStatus] = {
    ErrorCode.VALIDATION_FAILED: HTTPStatus.UNPROCESSABLE_ENTITY,        # 422
    ErrorCode.INVALID_ENUM_VALUE: HTTPStatus.BAD_REQUEST,                # 400
    ErrorCode.INVALID_PAGE_SIZE: HTTPStatus.BAD_REQUEST,
    ErrorCode.INVALID_INPUT: HTTPStatus.BAD_REQUEST,
    ErrorCode.NOT_FOUND: HTTPStatus.NOT_FOUND,                           # 404
    ErrorCode.ENDPOINT_RETIRED: HTTPStatus.NOT_FOUND,                    # 404（澄清决策 Q3）
    ErrorCode.INTERNAL_ERROR: HTTPStatus.INTERNAL_SERVER_ERROR,          # 500

    ErrorCode.ADMIN_TOKEN_NOT_CONFIGURED: HTTPStatus.INTERNAL_SERVER_ERROR,
    ErrorCode.ADMIN_TOKEN_INVALID: HTTPStatus.UNAUTHORIZED,              # 401

    ErrorCode.TASK_NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.COACH_NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.TIP_NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.JOB_NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.KB_VERSION_NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.COS_OBJECT_NOT_FOUND: HTTPStatus.NOT_FOUND,

    ErrorCode.TASK_NOT_READY: HTTPStatus.CONFLICT,                       # 409
    ErrorCode.COACH_INACTIVE: HTTPStatus.BAD_REQUEST,
    ErrorCode.COACH_ALREADY_INACTIVE: HTTPStatus.CONFLICT,
    ErrorCode.COACH_NAME_CONFLICT: HTTPStatus.CONFLICT,
    ErrorCode.JOB_NOT_FAILED: HTTPStatus.BAD_REQUEST,
    ErrorCode.INVALID_STATUS: HTTPStatus.BAD_REQUEST,
    ErrorCode.INVALID_ACTION_TYPE: HTTPStatus.BAD_REQUEST,
    ErrorCode.WRONG_TASK_TYPE: HTTPStatus.BAD_REQUEST,
    ErrorCode.KB_VERSION_NOT_DRAFT: HTTPStatus.BAD_REQUEST,
    ErrorCode.CONFLICT_UNRESOLVED: HTTPStatus.CONFLICT,
    ErrorCode.CLASSIFICATION_REQUIRED: HTTPStatus.BAD_REQUEST,
    ErrorCode.COS_KEY_NOT_CLASSIFIED: HTTPStatus.BAD_REQUEST,
    ErrorCode.BATCH_TOO_LARGE: HTTPStatus.BAD_REQUEST,
    ErrorCode.VIDEO_TOO_LONG: HTTPStatus.BAD_REQUEST,
    ErrorCode.MISSING_VIDEO: HTTPStatus.BAD_REQUEST,
    ErrorCode.NO_AUDIO_TRANSCRIPT: HTTPStatus.BAD_REQUEST,
    ErrorCode.INTERMEDIATE_EXPIRED: HTTPStatus.GONE,                     # 410

    ErrorCode.CHANNEL_QUEUE_FULL: HTTPStatus.SERVICE_UNAVAILABLE,        # 503
    ErrorCode.CHANNEL_DISABLED: HTTPStatus.SERVICE_UNAVAILABLE,

    ErrorCode.LLM_UPSTREAM_FAILED: HTTPStatus.BAD_GATEWAY,               # 502
    ErrorCode.COS_UPSTREAM_FAILED: HTTPStatus.BAD_GATEWAY,
    ErrorCode.DB_UPSTREAM_FAILED: HTTPStatus.BAD_GATEWAY,
    ErrorCode.WHISPER_UPSTREAM_FAILED: HTTPStatus.BAD_GATEWAY,
}


# 错误码 → 默认消息（可在抛出点通过 message 参数覆盖）
ERROR_DEFAULT_MESSAGE: dict[ErrorCode, str] = {
    ErrorCode.VALIDATION_FAILED: "请求参数校验失败",
    ErrorCode.ENDPOINT_RETIRED: "该接口已下线，请调用替代接口",
    ErrorCode.INTERNAL_ERROR: "服务器内部错误，请稍后重试",
    # ...（其余按原有错误消息保留；contracts/error-codes.md 给出完整表）
}
```

## 3. AppException（业务异常统一基类）

**落位**: `src/api/errors.py`

```python
class AppException(Exception):
    """路由/服务层统一抛出的业务异常。由全局异常处理器转为 ErrorEnvelope。"""

    def __init__(
        self,
        code: ErrorCode,
        *,
        message: str | None = None,
        details: dict | None = None,
    ) -> None:
        self.code = code
        self.message = message or ERROR_DEFAULT_MESSAGE.get(code, code.value)
        self.details = details
        super().__init__(f"{code.value}: {self.message}")
```

**使用约定**:
- 服务层与路由层**统一抛 `AppException`**，不再手写 `HTTPException(detail=...)`
- 未预期异常（`Exception` 兜底 handler）统一转 `AppException(ErrorCode.INTERNAL_ERROR)` + `logging.exception`
- `RequestValidationError`（Pydantic 校验失败）由专用 handler 转 `AppException(VALIDATION_FAILED, details={...})`

## 4. RetirementLedger（下线接口台账）

**落位**:
- Markdown 可读版: `specs/017-api-standardization/contracts/retirement-ledger.md`
- 代码运行时版: `src/api/routers/_retired.py`（Python 常量字典，单一事实来源由 contracts/ 同步）

### 4.1 运行时结构（Python 字典）

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class RetiredEndpoint:
    method: str              # "GET" | "POST" | "PATCH" | "DELETE"
    path: str                # 完整旧路径，如 "/api/v1/tasks/expert-video"
    successor: str | list[str]  # 替代路径
    migration_note: str      # 语义差异说明


RETIREMENT_LEDGER: tuple[RetiredEndpoint, ...] = (
    RetiredEndpoint(
        method="POST",
        path="/api/v1/tasks/expert-video",
        successor=[
            "/api/v1/tasks/classification",
            "/api/v1/tasks/kb-extraction",
        ],
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
        path="/api/v1/videos/classifications/{cos_object_key}",
        successor="/api/v1/classifications/{id}",
        migration_note="由 cos_object_key 路径参数改为 classification id",
    ),
    RetiredEndpoint(
        method="POST",
        path="/api/v1/videos/classifications/batch-submit",
        successor="/api/v1/tasks/kb-extraction/batch",
        migration_note="合并到任务通道的批量入口",
    ),
    RetiredEndpoint(
        method="POST",
        path="/api/v1/diagnosis",
        successor="/api/v1/tasks/diagnosis",
        migration_note="同步 60s 改为异步提交，需轮询 GET /tasks/{task_id}",
    ),
)
```

### 4.2 哨兵路由注册

`_retired.py` 遍历 `RETIREMENT_LEDGER`，用 `router.add_api_route()` 为每条旧路径+方法注册一个"只抛 `AppException(ENDPOINT_RETIRED, details=...)`" 的 handler：

```python
def _retired_handler_factory(endpoint: RetiredEndpoint):
    async def _handler():
        raise AppException(
            ErrorCode.ENDPOINT_RETIRED,
            details={
                "successor": endpoint.successor,
                "migration_note": endpoint.migration_note,
            },
        )
    return _handler
```

## 5. 现有 Schema 的改造原则

本 Feature **不删除**任何现有 `src/api/schemas/*.py` 中的业务字段定义（`CoachResponse`、`TaskStatusResponse`、`ExtractionJobDetail` 等），只把它们作为 `SuccessEnvelope[T]` 的 `T` 使用。

改造前：
```python
@router.get("/coaches/{coach_id}", response_model=CoachResponse)
async def get_coach(...): return coach_obj
```

改造后：
```python
@router.get("/coaches/{coach_id}", response_model=SuccessEnvelope[CoachResponse])
async def get_coach(...): return ok(coach_obj)
```

列表接口改造前（以 `TaskListResponse` 为例）：
```python
class TaskListResponse(BaseModel):
    data: list[TaskOut]
    total: int

@router.get("/tasks", response_model=TaskListResponse)
```

改造后：
```python
# 删除 TaskListResponse，改用泛型：
@router.get("/tasks", response_model=SuccessEnvelope[list[TaskOut]])
async def list_tasks(...):
    items, total = await service.list_tasks(...)
    return page(items, page=p, page_size=ps, total=total)
```

## 6. 不涉及数据库变更

本 Feature 明确**不动**以下数据模型（来自 `src/models/`）：
- `analysis_tasks`、`coach_video_classifications`、`video_classifications`、`coaches`
- `extraction_jobs`、`pipeline_steps`、`kb_conflicts`、`teaching_tips`
- `knowledge_base_versions`、`task_channel_configs`

Alembic 迁移**不新增**。当前最新迁移 `0013_kb_extraction_pipeline` 继续作为 head。

## 7. 验证关系矩阵（spec FR → 本文件锚点）

| 规范条款 | 本文件锚点 |
|---|---|
| FR-001（成功信封） | §1.1 SuccessEnvelope |
| FR-002（错误信封） | §1.2 ErrorEnvelope + §1.3 Details |
| FR-003（分页 meta） | §1.1 PaginationMeta + §1.4 `page()` 构造器 |
| FR-005（RetirementLedger） | §4.1 |
| FR-006（ENDPOINT_RETIRED 哨兵） | §4.2 |
| FR-015（错误码枚举） | §2 ErrorCode |
| FR-016（统一映射） | §2 ERROR_STATUS_MAP + §3 AppException |
| FR-017（INTERNAL_ERROR 兜底） | §3 使用约定 |
| FR-018（上游错误归类） | §2 `*_UPSTREAM_FAILED` 系列 + §1.3 UpstreamErrorDetails |
| FR-019（OpenAPI） | §5 response_model 泛型改造（FastAPI 自动展开） |
