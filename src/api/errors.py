"""Feature-017 — API 统一错误处理基础设施（章程 v1.4.0 原则 IX）.

包含：
- :class:`ErrorCode`: 集中错误码枚举（39 个，禁止在业务代码中使用裸字符串）
- :data:`ERROR_STATUS_MAP`: 错误码 → HTTP 状态单一事实来源
- :data:`ERROR_DEFAULT_MESSAGE`: 错误码 → 默认面向开发者消息
- :class:`AppException`: 路由/服务层统一业务异常基类
- :func:`build_error_response`: 将 :class:`AppException` 渲染为 :class:`ErrorEnvelope` JSON 响应
- 三个全局异常处理器工厂 :func:`register_exception_handlers`，由 ``src/api/main.py`` 调用一次完成注册

严格参照 ``specs/017-api-standardization/data-model.md`` §2–§3 与
``specs/017-api-standardization/contracts/error-codes.md``。
"""

from __future__ import annotations

import logging
from enum import Enum
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.api.schemas.envelope import ErrorBody, ErrorEnvelope

logger = logging.getLogger(__name__)


# ── 错误码枚举（39 个） ────────────────────────────────────────────────────
class ErrorCode(str, Enum):
    """API 统一错误码枚举. 每个值绑定一个默认 HTTP 状态（见 ERROR_STATUS_MAP）."""

    # ── 通用（7） ─────────────────────────────────────────────────────────
    VALIDATION_FAILED = "VALIDATION_FAILED"
    INVALID_ENUM_VALUE = "INVALID_ENUM_VALUE"
    INVALID_PAGE_SIZE = "INVALID_PAGE_SIZE"
    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    ENDPOINT_RETIRED = "ENDPOINT_RETIRED"
    INTERNAL_ERROR = "INTERNAL_ERROR"

    # ── 认证（2） ─────────────────────────────────────────────────────────
    ADMIN_TOKEN_NOT_CONFIGURED = "ADMIN_TOKEN_NOT_CONFIGURED"
    ADMIN_TOKEN_INVALID = "ADMIN_TOKEN_INVALID"

    # ── 资源不存在（6） ───────────────────────────────────────────────────
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    COACH_NOT_FOUND = "COACH_NOT_FOUND"
    TIP_NOT_FOUND = "TIP_NOT_FOUND"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    KB_VERSION_NOT_FOUND = "KB_VERSION_NOT_FOUND"
    COS_OBJECT_NOT_FOUND = "COS_OBJECT_NOT_FOUND"

    # ── 状态 / 业务约束（18） ─────────────────────────────────────────────
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
    PREPROCESSING_JOB_NOT_FOUND = "PREPROCESSING_JOB_NOT_FOUND"

    # ── 容量 / 队列（2） ──────────────────────────────────────────────────
    CHANNEL_QUEUE_FULL = "CHANNEL_QUEUE_FULL"
    CHANNEL_DISABLED = "CHANNEL_DISABLED"

    # ── 上游依赖（4） ─────────────────────────────────────────────────────
    LLM_UPSTREAM_FAILED = "LLM_UPSTREAM_FAILED"
    COS_UPSTREAM_FAILED = "COS_UPSTREAM_FAILED"
    DB_UPSTREAM_FAILED = "DB_UPSTREAM_FAILED"
    WHISPER_UPSTREAM_FAILED = "WHISPER_UPSTREAM_FAILED"

    # ── Feature-018 业务阶段/步骤 + 优化杠杆（3） ─────────────────
    INVALID_PHASE_STEP_COMBO = "INVALID_PHASE_STEP_COMBO"
    PHASE_STEP_UNMAPPED = "PHASE_STEP_UNMAPPED"
    OPTIMIZATION_LEVERS_YAML_INVALID = "OPTIMIZATION_LEVERS_YAML_INVALID"

    # ── Feature-019 KB per-category 生命周期（4） ────────────────
    KB_CONFLICT_UNRESOLVED = "KB_CONFLICT_UNRESOLVED"
    KB_EMPTY_POINTS = "KB_EMPTY_POINTS"
    NO_ACTIVE_KB_FOR_CATEGORY = "NO_ACTIVE_KB_FOR_CATEGORY"
    STANDARD_ALREADY_UP_TO_DATE = "STANDARD_ALREADY_UP_TO_DATE"

# ── 错误码 → HTTP 状态（单一事实来源） ────────────────────────────────────
ERROR_STATUS_MAP: dict[ErrorCode, HTTPStatus] = {
    # 通用
    ErrorCode.VALIDATION_FAILED: HTTPStatus.UNPROCESSABLE_ENTITY,  # 422
    ErrorCode.INVALID_ENUM_VALUE: HTTPStatus.BAD_REQUEST,          # 400
    ErrorCode.INVALID_PAGE_SIZE: HTTPStatus.BAD_REQUEST,
    ErrorCode.INVALID_INPUT: HTTPStatus.BAD_REQUEST,
    ErrorCode.NOT_FOUND: HTTPStatus.NOT_FOUND,                     # 404
    ErrorCode.ENDPOINT_RETIRED: HTTPStatus.NOT_FOUND,              # 404（澄清决策 Q3）
    ErrorCode.INTERNAL_ERROR: HTTPStatus.INTERNAL_SERVER_ERROR,    # 500

    # 认证
    ErrorCode.ADMIN_TOKEN_NOT_CONFIGURED: HTTPStatus.INTERNAL_SERVER_ERROR,
    ErrorCode.ADMIN_TOKEN_INVALID: HTTPStatus.UNAUTHORIZED,        # 401

    # 资源不存在
    ErrorCode.TASK_NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.COACH_NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.TIP_NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.JOB_NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.KB_VERSION_NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.COS_OBJECT_NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.PREPROCESSING_JOB_NOT_FOUND: HTTPStatus.NOT_FOUND,

    # 状态 / 业务约束
    ErrorCode.TASK_NOT_READY: HTTPStatus.BAD_REQUEST,                 # 400（Feature-017：业务状态校验统一 400，对齐 JOB_NOT_FAILED）
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
    ErrorCode.INTERMEDIATE_EXPIRED: HTTPStatus.GONE,               # 410

    # 容量
    ErrorCode.CHANNEL_QUEUE_FULL: HTTPStatus.SERVICE_UNAVAILABLE,  # 503
    ErrorCode.CHANNEL_DISABLED: HTTPStatus.SERVICE_UNAVAILABLE,

    # 上游依赖
    ErrorCode.LLM_UPSTREAM_FAILED: HTTPStatus.BAD_GATEWAY,         # 502
    ErrorCode.COS_UPSTREAM_FAILED: HTTPStatus.BAD_GATEWAY,
    ErrorCode.DB_UPSTREAM_FAILED: HTTPStatus.BAD_GATEWAY,
    ErrorCode.WHISPER_UPSTREAM_FAILED: HTTPStatus.BAD_GATEWAY,

    # Feature-018（业务阶段/步骤 + 优化杠杆）
    ErrorCode.INVALID_PHASE_STEP_COMBO: HTTPStatus.BAD_REQUEST,          # 400
    ErrorCode.PHASE_STEP_UNMAPPED: HTTPStatus.INTERNAL_SERVER_ERROR,     # 500
    ErrorCode.OPTIMIZATION_LEVERS_YAML_INVALID: HTTPStatus.INTERNAL_SERVER_ERROR,  # 500

    # Feature-019（KB per-category 生命周期）
    ErrorCode.KB_CONFLICT_UNRESOLVED: HTTPStatus.CONFLICT,               # 409
    ErrorCode.KB_EMPTY_POINTS: HTTPStatus.CONFLICT,                      # 409
    ErrorCode.NO_ACTIVE_KB_FOR_CATEGORY: HTTPStatus.CONFLICT,            # 409
    ErrorCode.STANDARD_ALREADY_UP_TO_DATE: HTTPStatus.CONFLICT,          # 409
}


# ── 错误码 → 默认消息 ─────────────────────────────────────────────────────
ERROR_DEFAULT_MESSAGE: dict[ErrorCode, str] = {
    # 通用
    ErrorCode.VALIDATION_FAILED: "请求参数校验失败",
    ErrorCode.INVALID_ENUM_VALUE: "枚举参数取值非法",
    ErrorCode.INVALID_PAGE_SIZE: "page_size 超出允许范围",
    ErrorCode.INVALID_INPUT: "输入参数非法",
    ErrorCode.NOT_FOUND: "资源不存在",
    ErrorCode.ENDPOINT_RETIRED: "该接口已下线，请调用替代接口",
    ErrorCode.INTERNAL_ERROR: "服务器内部错误，请稍后重试",

    # 认证
    ErrorCode.ADMIN_TOKEN_NOT_CONFIGURED: "管理员令牌未配置",
    ErrorCode.ADMIN_TOKEN_INVALID: "管理员令牌无效",

    # 资源不存在
    ErrorCode.TASK_NOT_FOUND: "任务不存在",
    ErrorCode.COACH_NOT_FOUND: "教练不存在",
    ErrorCode.TIP_NOT_FOUND: "教学建议不存在",
    ErrorCode.JOB_NOT_FOUND: "KB 提取作业不存在",
    ErrorCode.KB_VERSION_NOT_FOUND: "知识库版本不存在",
    ErrorCode.COS_OBJECT_NOT_FOUND: "COS 对象不存在",
    ErrorCode.PREPROCESSING_JOB_NOT_FOUND: "视频预处理作业不存在",

    # 状态 / 业务约束
    ErrorCode.TASK_NOT_READY: "任务尚未就绪",
    ErrorCode.COACH_INACTIVE: "教练已停用，无法关联",
    ErrorCode.COACH_ALREADY_INACTIVE: "教练已处于停用状态",
    ErrorCode.COACH_NAME_CONFLICT: "教练名称冲突",
    ErrorCode.JOB_NOT_FAILED: "作业非失败状态",
    ErrorCode.INVALID_STATUS: "作业状态非法",
    ErrorCode.INVALID_ACTION_TYPE: "动作类型非法",
    ErrorCode.WRONG_TASK_TYPE: "任务类型不匹配",
    ErrorCode.KB_VERSION_NOT_DRAFT: "知识库版本非草稿",
    ErrorCode.CONFLICT_UNRESOLVED: "存在未解决的知识库冲突",
    ErrorCode.CLASSIFICATION_REQUIRED: "必须先完成分类",
    ErrorCode.COS_KEY_NOT_CLASSIFIED: "COS 对象未分类",
    ErrorCode.BATCH_TOO_LARGE: "批量提交超出上限",
    ErrorCode.VIDEO_TOO_LONG: "视频时长超限",
    ErrorCode.MISSING_VIDEO: "缺少视频文件",
    ErrorCode.NO_AUDIO_TRANSCRIPT: "无音频转写结果",
    ErrorCode.INTERMEDIATE_EXPIRED: "中间结果已过期",

    # 容量
    ErrorCode.CHANNEL_QUEUE_FULL: "任务通道队列已满",
    ErrorCode.CHANNEL_DISABLED: "任务通道已停用",

    # 上游
    ErrorCode.LLM_UPSTREAM_FAILED: "LLM 服务调用失败",
    ErrorCode.COS_UPSTREAM_FAILED: "COS 存储服务失败",
    ErrorCode.DB_UPSTREAM_FAILED: "数据库操作失败",
    ErrorCode.WHISPER_UPSTREAM_FAILED: "语音转写服务失败",

    # Feature-018（业务阶段/步骤 + 优化杠杆）
    ErrorCode.INVALID_PHASE_STEP_COMBO: "业务阶段/步骤与任务类型不匹配",
    ErrorCode.PHASE_STEP_UNMAPPED: "业务阶段/步骤派生失败（内部错误）",
    ErrorCode.OPTIMIZATION_LEVERS_YAML_INVALID: "优化杠杆台账配置加载失败",

    # Feature-019（KB per-category 生命周期）
    ErrorCode.KB_CONFLICT_UNRESOLVED: "知识库存在未解决的冲突点",
    ErrorCode.KB_EMPTY_POINTS: "知识库为空，无法批准",
    ErrorCode.NO_ACTIVE_KB_FOR_CATEGORY: "该技术类别无已激活的知识库",
    ErrorCode.STANDARD_ALREADY_UP_TO_DATE: "标准已是最新，无需重建",
}


# ── 业务异常基类 ──────────────────────────────────────────────────────────
class AppException(Exception):
    """路由/服务层统一抛出的业务异常. 由全局异常处理器转为 :class:`ErrorEnvelope`.

    Args:
        code: 必填，来自 :class:`ErrorCode` 的枚举值
        message: 可选，覆盖 ``ERROR_DEFAULT_MESSAGE.get(code)`` 的默认消息
        details: 可选，结构化上下文（RetiredErrorDetails / ValidationErrorDetails 等 dump 后的 dict）
    """

    def __init__(
        self,
        code: ErrorCode,
        *,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message or ERROR_DEFAULT_MESSAGE.get(code, code.value)
        self.details = details
        super().__init__(f"{code.value}: {self.message}")


# ── 响应构造 ──────────────────────────────────────────────────────────────
def build_error_response(exc: AppException) -> JSONResponse:
    """将 :class:`AppException` 渲染为统一错误信封 JSON 响应."""
    status = ERROR_STATUS_MAP.get(exc.code, HTTPStatus.INTERNAL_SERVER_ERROR)
    envelope = ErrorEnvelope(
        success=False,
        error=ErrorBody(
            code=exc.code.value,
            message=exc.message,
            details=exc.details,
        ),
    )
    return JSONResponse(
        status_code=int(status),
        content=envelope.model_dump(mode="json"),
    )


# ── 异常处理器注册 ────────────────────────────────────────────────────────
def register_exception_handlers(app: FastAPI) -> None:
    """在 FastAPI app 上注册三个统一异常处理器.

    仅应在 ``src/api/main.py::create_app`` 中调用一次.
    """

    @app.exception_handler(AppException)
    async def _app_exception_handler(request: Request, exc: AppException) -> JSONResponse:  # noqa: ARG001
        # 非 5xx 级错误使用 warning；5xx 使用 error 便于运维告警聚焦
        status = ERROR_STATUS_MAP.get(exc.code, HTTPStatus.INTERNAL_SERVER_ERROR)
        if int(status) >= 500:
            logger.error(
                "AppException %s: %s (path=%s details=%s)",
                exc.code.value, exc.message, request.url.path, exc.details,
            )
        else:
            logger.info(
                "AppException %s: %s (path=%s)",
                exc.code.value, exc.message, request.url.path,
            )
        return build_error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        request: Request, exc: RequestValidationError,  # noqa: ARG001
    ) -> JSONResponse:
        # 只取第一个错误作为 primary；完整列表塞入 details.errors 供调试
        errors = exc.errors()
        primary = errors[0] if errors else {}
        loc = primary.get("loc") or ()
        # loc 形如 ("query", "page_size") 或 ("body", "coach", "name")
        field = ".".join(str(x) for x in loc if x not in ("body", "query", "path"))
        raw_value = primary.get("input")
        # value 仅接受 str/int/None（ValidationErrorDetails 约束）；复杂对象转字符串
        value: str | int | None
        if isinstance(raw_value, (str, int)) or raw_value is None:
            value = raw_value
        else:
            value = str(raw_value)
        details: dict[str, Any] = {
            "field": field or None,
            "value": value,
        }
        wrapped = AppException(
            ErrorCode.VALIDATION_FAILED,
            message=primary.get("msg", ERROR_DEFAULT_MESSAGE[ErrorCode.VALIDATION_FAILED]),
            details=details,
        )
        return build_error_response(wrapped)

    @app.exception_handler(Exception)
    async def _internal_error_handler(request: Request, exc: Exception) -> JSONResponse:  # noqa: ARG001
        logger.exception("Unhandled exception at %s", request.url.path, exc_info=exc)
        wrapped = AppException(ErrorCode.INTERNAL_ERROR)
        return build_error_response(wrapped)


__all__ = [
    "ErrorCode",
    "ERROR_STATUS_MAP",
    "ERROR_DEFAULT_MESSAGE",
    "AppException",
    "build_error_response",
    "register_exception_handlers",
]
