"""Feature-017 — API 统一错误处理基础设施（章程 v1.4.0 原则 IX）.

包含：
- :class:`ErrorCode`: 集中错误码枚举（45 个，禁止在业务代码中使用裸字符串）
- :data:`ERROR_STATUS_MAP`: 错误码 → HTTP 状态单一事实来源
- :data:`ERROR_DEFAULT_MESSAGE`: 错误码 → 默认面向开发者消息
- :class:`AppException`: 路由/服务层统一业务异常基类
- :func:`build_error_response`: 将 :class:`AppException` 渲染为 :class:`ErrorEnvelope` JSON 响应
- 三个全局异常处理器工厂 :func:`register_exception_handlers`，由 ``src/api/main.py`` 调用一次完成注册

严格参照 ``specs/017-api-standardization/data-model.md`` §2–§3 与
``specs/017-api-standardization/contracts/error-codes.md``；Feature-020 新增 6 个 `ATHLETE_*` / `STANDARD_NOT_AVAILABLE` 错误码。
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

    # ── 通用（6） ────────────────────────────────────────────────────
    VALIDATION_FAILED = "VALIDATION_FAILED"
    INVALID_ENUM_VALUE = "INVALID_ENUM_VALUE"
    INVALID_PAGE_SIZE = "INVALID_PAGE_SIZE"
    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
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

    # ── Feature-019 KB per-action 生命周期（Feature-023 重命名：per-category → per-action） ────────────
    KB_CONFLICT_UNRESOLVED = "KB_CONFLICT_UNRESOLVED"
    KB_EMPTY_POINTS = "KB_EMPTY_POINTS"
    NO_ACTIVE_KB_FOR_ACTION = "NO_ACTIVE_KB_FOR_ACTION"  # Feature-023: replaces NO_ACTIVE_KB_FOR_CATEGORY
    STANDARD_ALREADY_UP_TO_DATE = "STANDARD_ALREADY_UP_TO_DATE"

    # ── Feature-020 运动员推理流水线（5；Feature-023 物理删除 STANDARD_NOT_AVAILABLE） ─────────────
    ATHLETE_ROOT_UNREADABLE = "ATHLETE_ROOT_UNREADABLE"
    ATHLETE_DIRECTORY_MAP_MISSING = "ATHLETE_DIRECTORY_MAP_MISSING"
    ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND = "ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND"
    ATHLETE_VIDEO_NOT_PREPROCESSED = "ATHLETE_VIDEO_NOT_PREPROCESSED"
    ATHLETE_VIDEO_POSE_UNUSABLE = "ATHLETE_VIDEO_POSE_UNUSABLE"

    # ── Feature-023 技术分类体系重构（4） ─────────────
    # 见 specs/023-tech-classification-rebuild/contracts/error-codes.md
    ACTION_NOT_FOUND = "ACTION_NOT_FOUND"                              # 404 — action 不在字典内
    ACTION_DICTIONARY_VIOLATION = "ACTION_DICTIONARY_VIOLATION"        # 400 — 提交体的 action 字段不在 tech_actions 字典
    STANDARD_NOT_AVAILABLE_FOR_ACTION = "STANDARD_NOT_AVAILABLE_FOR_ACTION"  # 503 — 该 action 暂无 active 标准（替换旧 STANDARD_NOT_AVAILABLE）

    # ── Feature-021 视频内容清洗（7） ─────────────────
    # 清洗强制门 + 业务结果型错误码（其中 LOW_QUALITY_SKIP / CURATION_LLM_UNAVAILABLE
    # 是"业务结果"而非异常：写入 extraction_jobs.error_code 或 segment.rejection_reason，
    # 不抛 AppException —— 见 contracts/error-codes.md "业务结果型错误码处理约定"）
    CURATION_REQUIRED = "CURATION_REQUIRED"
    LOW_QUALITY_SKIP = "LOW_QUALITY_SKIP"
    RUBRIC_INVALID = "RUBRIC_INVALID"
    RUBRIC_VERSION_NOT_FOUND = "RUBRIC_VERSION_NOT_FOUND"
    CURATION_TIMEOUT = "CURATION_TIMEOUT"
    CURATION_LLM_UNAVAILABLE = "CURATION_LLM_UNAVAILABLE"
    CURATION_RUBRIC_MISMATCH = "CURATION_RUBRIC_MISMATCH"

    # ── Feature-022 内容审核工作台（8） ─────────────────
    # 审核门 3 个 + 决策提交 4 个 + 开关切换 1 个；详见 contracts/error-codes.md
    CONTENT_NOT_REVIEWED = "CONTENT_NOT_REVIEWED"          # KB 抽取拦截：review_state=pending_review
    CONTENT_REVIEW_REJECTED = "CONTENT_REVIEW_REJECTED"    # KB 抽取拦截：review_state=rejected
    CONTENT_REVIEW_STALE = "CONTENT_REVIEW_STALE"          # KB 抽取拦截：review_state=stale
    REVIEW_VERSION_CONFLICT = "REVIEW_VERSION_CONFLICT"    # EP-3 决策提交：乐观锁冲突
    REVIEW_NOT_PENDING = "REVIEW_NOT_PENDING"              # EP-3 决策提交：状态机不允许
    INVALID_REVIEWER_IDENTITY = "INVALID_REVIEWER_IDENTITY"  # EP-3 提交：header vs body 不一致
    REJECTED_REQUIRES_REASON = "REJECTED_REQUIRES_REASON"  # EP-3 提交：rejected 缺 reason_code
    REVIEW_GATE_INVALID_STATE = "REVIEW_GATE_INVALID_STATE"  # EP-5b 开关切换：请求体不合法

# ── 错误码 → HTTP 状态（单一事实来源） ───────────────────────────────────
ERROR_STATUS_MAP: dict[ErrorCode, HTTPStatus] = {
    # 通用
    ErrorCode.VALIDATION_FAILED: HTTPStatus.UNPROCESSABLE_ENTITY,  # 422
    ErrorCode.INVALID_ENUM_VALUE: HTTPStatus.BAD_REQUEST,          # 400
    ErrorCode.INVALID_PAGE_SIZE: HTTPStatus.BAD_REQUEST,
    ErrorCode.INVALID_INPUT: HTTPStatus.BAD_REQUEST,
    ErrorCode.NOT_FOUND: HTTPStatus.NOT_FOUND,                     # 404
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

    # Feature-019（KB per-action 生命周期；Feature-023 重命名）
    ErrorCode.KB_CONFLICT_UNRESOLVED: HTTPStatus.CONFLICT,               # 409
    ErrorCode.KB_EMPTY_POINTS: HTTPStatus.CONFLICT,                      # 409
    ErrorCode.NO_ACTIVE_KB_FOR_ACTION: HTTPStatus.CONFLICT,              # 409 — Feature-023 替换 NO_ACTIVE_KB_FOR_CATEGORY
    ErrorCode.STANDARD_ALREADY_UP_TO_DATE: HTTPStatus.CONFLICT,          # 409

    # Feature-020（运动员推理流水线；Feature-023 删除 STANDARD_NOT_AVAILABLE）
    ErrorCode.ATHLETE_ROOT_UNREADABLE: HTTPStatus.BAD_GATEWAY,                       # 502
    ErrorCode.ATHLETE_DIRECTORY_MAP_MISSING: HTTPStatus.INTERNAL_SERVER_ERROR,       # 500
    ErrorCode.ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND: HTTPStatus.NOT_FOUND,          # 404
    ErrorCode.ATHLETE_VIDEO_NOT_PREPROCESSED: HTTPStatus.CONFLICT,                   # 409
    ErrorCode.ATHLETE_VIDEO_POSE_UNUSABLE: HTTPStatus.UNPROCESSABLE_ENTITY,          # 422

    # Feature-023（技术分类体系重构）
    ErrorCode.ACTION_NOT_FOUND: HTTPStatus.NOT_FOUND,                                # 404
    ErrorCode.ACTION_DICTIONARY_VIOLATION: HTTPStatus.BAD_REQUEST,                   # 400
    ErrorCode.STANDARD_NOT_AVAILABLE_FOR_ACTION: HTTPStatus.SERVICE_UNAVAILABLE,     # 503

    # Feature-021（视频内容清洗）
    # LOW_QUALITY_SKIP / CURATION_LLM_UNAVAILABLE 是"业务结果型"标记，不会通过
    # AppException 路径返回给 HTTP 客户端 —— 它们写入 extraction_jobs.error_code
    # / video_curation_segment_results.rejection_reason 字段，由任务监控接口读取。
    # 但根据章程 IX 的"集中错误码"约束，仍须登记到 ERROR_STATUS_MAP；映射到 409
    # 表"该资源处于无法继续处理的业务状态"，与 KB_CONFLICT_UNRESOLVED 同语义档位。
    ErrorCode.CURATION_REQUIRED: HTTPStatus.CONFLICT,                                # 409
    ErrorCode.LOW_QUALITY_SKIP: HTTPStatus.CONFLICT,                                 # 409（业务结果，不直接返回客户端）
    ErrorCode.RUBRIC_INVALID: HTTPStatus.UNPROCESSABLE_ENTITY,                       # 422
    ErrorCode.RUBRIC_VERSION_NOT_FOUND: HTTPStatus.NOT_FOUND,                        # 404
    ErrorCode.CURATION_TIMEOUT: HTTPStatus.INTERNAL_SERVER_ERROR,                    # 500
    ErrorCode.CURATION_LLM_UNAVAILABLE: HTTPStatus.CONFLICT,                         # 409（业务结果，不直接返回客户端）
    ErrorCode.CURATION_RUBRIC_MISMATCH: HTTPStatus.CONFLICT,                         # 409

    # Feature-022（内容审核工作台）
    # 审核门 3 个都是 409（设计上与 CURATION_REQUIRED 同档位：资源状态不允许推进）
    # 决策提交 4 个：乐观锁冲突 + 状态机不合法 = 409；body 校验不过 = 400
    # 开关切换 1 个：请求体不合法 = 400
    ErrorCode.CONTENT_NOT_REVIEWED: HTTPStatus.CONFLICT,                             # 409
    ErrorCode.CONTENT_REVIEW_REJECTED: HTTPStatus.CONFLICT,                          # 409
    ErrorCode.CONTENT_REVIEW_STALE: HTTPStatus.CONFLICT,                             # 409
    ErrorCode.REVIEW_VERSION_CONFLICT: HTTPStatus.CONFLICT,                          # 409
    ErrorCode.REVIEW_NOT_PENDING: HTTPStatus.CONFLICT,                               # 409
    ErrorCode.INVALID_REVIEWER_IDENTITY: HTTPStatus.BAD_REQUEST,                     # 400
    ErrorCode.REJECTED_REQUIRES_REASON: HTTPStatus.BAD_REQUEST,                      # 400
    ErrorCode.REVIEW_GATE_INVALID_STATE: HTTPStatus.BAD_REQUEST,                     # 400
}


# ── 错误码 → 默认消息 ─────────────────────────────────────────────────────
ERROR_DEFAULT_MESSAGE: dict[ErrorCode, str] = {
    # 通用
    ErrorCode.VALIDATION_FAILED: "请求参数校验失败",
    ErrorCode.INVALID_ENUM_VALUE: "枚举参数取值非法",
    ErrorCode.INVALID_PAGE_SIZE: "page_size 超出允许范围",
    ErrorCode.INVALID_INPUT: "输入参数非法",
    ErrorCode.NOT_FOUND: "资源不存在",
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

    # Feature-019（KB per-action 生命周期；Feature-023 重命名）
    ErrorCode.KB_CONFLICT_UNRESOLVED: "知识库存在未解决的冲突点",
    ErrorCode.KB_EMPTY_POINTS: "知识库为空，无法批准",
    ErrorCode.NO_ACTIVE_KB_FOR_ACTION: "该动作无已激活的知识库",
    ErrorCode.STANDARD_ALREADY_UP_TO_DATE: "标准已是最新，无需重建",

    # Feature-020（运动员推理流水线；Feature-023 删除 STANDARD_NOT_AVAILABLE）
    ErrorCode.ATHLETE_ROOT_UNREADABLE: "运动员视频根路径不可读或凭证无效",
    ErrorCode.ATHLETE_DIRECTORY_MAP_MISSING: "运动员目录映射配置文件缺失",
    ErrorCode.ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND: "运动员素材记录不存在",
    ErrorCode.ATHLETE_VIDEO_NOT_PREPROCESSED: "运动员视频尚未完成预处理，不能直接诊断",
    ErrorCode.ATHLETE_VIDEO_POSE_UNUSABLE: "运动员视频姿态提取全程无可用关键点",

    # Feature-023（技术分类体系重构）
    ErrorCode.ACTION_NOT_FOUND: "动作不存在",
    ErrorCode.ACTION_DICTIONARY_VIOLATION: "action 不在字典内",
    ErrorCode.STANDARD_NOT_AVAILABLE_FOR_ACTION: "该动作暂无 active 技术标准",

    # Feature-021（视频内容清洗）
    ErrorCode.CURATION_REQUIRED: "视频尚未完成内容清洗，请先提交清洗任务",
    ErrorCode.LOW_QUALITY_SKIP: "视频清洗判定为低质量，KB 抽取已业务跳过",
    ErrorCode.RUBRIC_INVALID: "清洗规范文件 schema 校验失败",
    ErrorCode.RUBRIC_VERSION_NOT_FOUND: "指定的清洗规范版本不存在",
    ErrorCode.CURATION_TIMEOUT: "清洗任务执行超时已被孤儿回收",
    ErrorCode.CURATION_LLM_UNAVAILABLE: "LLM 不可用，模糊分段已落 uncertain",
    ErrorCode.CURATION_RUBRIC_MISMATCH: "本次提交的规范版本与既有 success 作业不一致；如需新建请使用 force=true",

    # Feature-022（内容审核工作台）
    ErrorCode.CONTENT_NOT_REVIEWED: "视频尚未完成内容审核，请先在审核工作台提交决策",
    ErrorCode.CONTENT_REVIEW_REJECTED: "视频内容审核未通过，不能进入训练阶段",
    ErrorCode.CONTENT_REVIEW_STALE: "视频已被重新清洗，原审核决策已失效，请重新审核",
    ErrorCode.REVIEW_VERSION_CONFLICT: "审核版本冲突，请刷新后重试",
    ErrorCode.REVIEW_NOT_PENDING: "当前审核状态不允许提交决策",
    ErrorCode.INVALID_REVIEWER_IDENTITY: "请求头 X-Reviewer-Id 与请求体 reviewer_id 不一致",
    ErrorCode.REJECTED_REQUIRES_REASON: "拒绝决策必须提供 reason_code",
    ErrorCode.REVIEW_GATE_INVALID_STATE: "审核门开关切换请求不合法",
}


# ── 业务异常基类 ──────────────────────────────────────────────────────────
class AppException(Exception):
    """路由/服务层统一抛出的业务异常. 由全局异常处理器转为 :class:`ErrorEnvelope`.

    Args:
        code: 必填，来自 :class:`ErrorCode` 的枚举值
        message: 可选，覆盖 ``ERROR_DEFAULT_MESSAGE.get(code)`` 的默认消息
        details: 可选，结构化上下文（ValidationErrorDetails / UpstreamErrorDetails 等 dump 后的 dict）
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
