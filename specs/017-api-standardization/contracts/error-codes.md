# 错误码清单

**版本**: v1.0（随 Feature-017 首发）
**权威来源**: `src/api/errors.py` 中的 `ErrorCode` 枚举与 `ERROR_STATUS_MAP`
**同步规则**: 本文件与代码中的枚举严格 1:1 对应；CI 校验新增/删除错误码时必须同步更新本文件。

## 通用错误（跨资源共用）

| 错误码 | HTTP | 默认消息 | 触发场景 | details 结构 |
|---|---|---|---|---|
| `VALIDATION_FAILED` | 422 | 请求参数校验失败 | Pydantic 请求体/查询参数校验失败 | `ValidationErrorDetails` |
| `INVALID_ENUM_VALUE` | 400 | 枚举参数取值非法 | 如 `tech_category` 非 `TECH_CATEGORIES` 中的值 | `ValidationErrorDetails`（带 `allowed`） |
| `INVALID_PAGE_SIZE` | 400 | page_size 超出允许范围 | `page_size > 100` 或 `≤ 0` | `ValidationErrorDetails`（`value`, `allowed:{min:1,max:100}`） |
| `INVALID_INPUT` | 400 | 输入参数非法 | 业务规则校验失败（非 Pydantic schema 层面） | `{message}` |
| `NOT_FOUND` | 404 | 资源不存在 | FastAPI 未匹配路由（与 ENDPOINT_RETIRED 区分） | `null` |
| `ENDPOINT_RETIRED` | 404 | 该接口已下线，请调用替代接口 | 调用已下线的哨兵路由 | `RetiredErrorDetails` |
| `INTERNAL_ERROR` | 500 | 服务器内部错误，请稍后重试 | 全局异常 handler 兜底 | `null`（禁泄露栈） |

## 认证/授权

| 错误码 | HTTP | 默认消息 | 触发场景 |
|---|---|---|---|
| `ADMIN_TOKEN_NOT_CONFIGURED` | 500 | 管理员令牌未配置 | `admin.py` 路由调用但 `.env` 中无 `ADMIN_TOKEN` |
| `ADMIN_TOKEN_INVALID` | 401 | 管理员令牌无效 | 请求头 token 与配置不符 |

## 资源不存在（404 族）

| 错误码 | HTTP | 默认消息 | 触发资源 |
|---|---|---|---|
| `TASK_NOT_FOUND` | 404 | 任务不存在 | `analysis_tasks` |
| `COACH_NOT_FOUND` | 404 | 教练不存在 | `coaches` |
| `TIP_NOT_FOUND` | 404 | 教学建议不存在 | `teaching_tips` |
| `JOB_NOT_FOUND` | 404 | KB 提取作业不存在 | `extraction_jobs` |
| `KB_VERSION_NOT_FOUND` | 404 | 知识库版本不存在 | `knowledge_base_versions` |
| `COS_OBJECT_NOT_FOUND` | 404 | COS 对象不存在 | COS 对象键未找到 |
| `PREPROCESSING_JOB_NOT_FOUND` | 404 | 视频预处理作业不存在 | `preprocessing_jobs`（Feature-016 预处理作业指令 GET /api/v1/video-preprocessing/{job_id}） |

## 状态冲突 / 业务约束

| 错误码 | HTTP | 默认消息 | 触发场景 |
|---|---|---|---|
| `TASK_NOT_READY` | 400 | 任务尚未就绪 | 任务未到可读取结果的状态 |
| `COACH_INACTIVE` | 400 | 教练已停用，无法关联 | 关联已停用教练 |
| `COACH_ALREADY_INACTIVE` | 409 | 教练已处于停用状态 | 重复停用 |
| `COACH_NAME_CONFLICT` | 409 | 教练名称冲突 | 创建/更新时 name 重复 |
| `JOB_NOT_FAILED` | 400 | 作业非失败状态 | 仅失败作业可 rerun |
| `INVALID_STATUS` | 400 | 作业状态非法 | 状态转换非法 |
| `INVALID_ACTION_TYPE` | 400 | 动作类型非法 | 任务提交动作不在允许集合 |
| `WRONG_TASK_TYPE` | 400 | 任务类型不匹配 | 接口期望某类型任务但实际为另一种 |
| `KB_VERSION_NOT_DRAFT` | 400 | 知识库版本非草稿 | 仅 draft 版本可审批 |
| `CONFLICT_UNRESOLVED` | 409 | 存在未解决的知识库冲突 | 审批前必须清空冲突 |
| `CLASSIFICATION_REQUIRED` | 400 | 必须先完成分类 | KB 提取前置依赖 |
| `COS_KEY_NOT_CLASSIFIED` | 400 | COS 对象未分类 | 批量提交中有未分类项 |
| `BATCH_TOO_LARGE` | 400 | 批量提交超出上限 | 单次 > 50 条 |
| `VIDEO_TOO_LONG` | 400 | 视频时长超限 | 超出 Feature-016 约束 |
| `MISSING_VIDEO` | 400 | 缺少视频文件 | 必须上传但未提供 |
| `NO_AUDIO_TRANSCRIPT` | 400 | 无音频转写结果 | 教学建议需音频文本 |
| `INTERMEDIATE_EXPIRED` | 410 | 中间结果已过期 | Feature-014 KB 提取中间产物被清理 |

## 容量 / 队列

| 错误码 | HTTP | 默认消息 | 触发场景 |
|---|---|---|---|
| `CHANNEL_QUEUE_FULL` | 503 | 任务通道队列已满 | `task_channel_configs` 容量满 |
| `CHANNEL_DISABLED` | 503 | 任务通道已停用 | 通道禁用 |

## 上游依赖失败

| 错误码 | HTTP | 默认消息 | 触发场景 | details |
|---|---|---|---|---|
| `LLM_UPSTREAM_FAILED` | 502 | LLM 服务调用失败 | Venus Proxy + OpenAI 双双失败 | `UpstreamErrorDetails` |
| `COS_UPSTREAM_FAILED` | 502 | COS 存储服务失败 | COS SDK 异常、上传失败（替代旧的 `UPLOAD_FAILED`） | `UpstreamErrorDetails` |
| `DB_UPSTREAM_FAILED` | 502 | 数据库操作失败 | SQLAlchemy 抛出未预期异常 | `UpstreamErrorDetails` |
| `WHISPER_UPSTREAM_FAILED` | 502 | 语音转写服务失败 | Whisper 推理超时/崩溃 | `UpstreamErrorDetails` |

## 统计

- **通用**: 7
- **认证**: 2
- **资源不存在**: 7（含 Feature-017 新增 `PREPROCESSING_JOB_NOT_FOUND`）
- **状态/约束**: 17
- **容量**: 2
- **上游**: 4
- **合计**: **39 个错误码**

## 变更流程

1. 新增错误码必须同时更新：`src/api/errors.py::ErrorCode`、`ERROR_STATUS_MAP`、`ERROR_DEFAULT_MESSAGE`、本文件
2. CI 脚本 `scripts/lint_error_codes.py`（tasks.md 中有对应任务）会校验三方同步
3. 已发布的错误码**禁止改名或更换 HTTP 状态**（会破坏客户端 SDK 契约），只允许新增
