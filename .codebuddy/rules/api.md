---
alwaysApply: false
paths: src/api/**/*.py
---

# API 设计规范（对齐章程 v2.0.0 / Feature-017）

## 基础约定

- 版本前缀统一：`/api/v1/`；由 `app.include_router(<module>.router, prefix="/api/v1")` 单点拼接，路由文件内 `APIRouter(prefix="/<resource>")` 只声明资源段
- 每个路由文件对应一个资源（coaches, tasks, classifications...），禁止混搭不同资源
- 资源段使用 kebab-case 复数名词；动作型子路径使用 kebab-case 动词（如 `/refresh`、`/approve`、`/scan`）
- 资源 ID 路径段统一使用 `{resource_id}` 形式（如 `{task_id}`、`{coach_id}`、`{tip_id}`），禁止无命名的 `{id}`
- 分页参数统一：`page`（从 1 开始，默认 1）+ `page_size`（默认 20，最大 100）；越界返回 400 + `INVALID_PAGE_SIZE`，禁止静默截断；禁止 `limit`/`offset`/`skip`/`take`
- 枚举型查询参数（如 `tech_category`、`status`、`task_type`）在服务端统一按小写下划线归一化；非法值返回 400 + `INVALID_ENUM_VALUE`，`details` 含合法取值列表

## 响应体统一信封（v2.0.0 强制）

所有 `/api/v1/**` 接口的响应体必须匹配下列两种互斥信封之一，顶层 `success` 布尔位作为判别式。禁止裸对象 / 裸数组 / 其他形态。

**成功信封**（`success=true`，不得出现 `error`）：

```json
{
  "success": true,
  "data": <业务载荷>,
  "meta": { "page": 1, "page_size": 20, "total": 42 }
}
```

- `data`：单对象 / 列表 / `null`；由路由的 `response_model` 泛型决定
- `meta`：仅列表/分页接口非空；非列表接口为 `null` 或省略

**错误信封**（`success=false`，不得出现 `data`/`meta`）：

```json
{
  "success": false,
  "error": {
    "code": "TASK_NOT_FOUND",
    "message": "任务不存在",
    "details": { "resource_id": "..." }
  }
}
```

**实现约定**：

- 成功响应必须通过 `src/api/schemas/envelope.py::SuccessEnvelope[T]` 泛型模型构造
- 非分页接口用 `ok(data)` 构造器，分页接口用 `page(items, page=, page_size=, total=)` 构造器
- 禁止路由层手写 `return {"success": True, "data": ...}` 字典

## 错误响应（v2.0.0 强制）

服务层与路由层统一抛 `AppException(code, message=None, details=None)`（定义于 `src/api/errors.py`），由全局异常处理器转为上述错误信封。**禁止直接抛 `HTTPException` 或返回错误字典**。

映射规则：

| 场景 | HTTP | ErrorCode |
|------|------|-----------|
| Pydantic 请求体/查询参数校验失败 | 422 | `VALIDATION_FAILED` |
| 资源不存在 | 404 | 资源专属（`TASK_NOT_FOUND` / `COACH_NOT_FOUND` / …） |
| 业务状态/约束冲突 | 400 \| 409 | 场景专属（`COACH_INACTIVE` / `KB_VERSION_NOT_DRAFT` / …） |
| 通道容量 / 禁用 | 503 | `CHANNEL_QUEUE_FULL` / `CHANNEL_DISABLED` |
| 上游依赖失败 | 502 | `LLM_/COS_/DB_/WHISPER_UPSTREAM_FAILED` |
| 未预期异常 | 500 | `INTERNAL_ERROR`（含 `logging.exception`，不泄露栈）|

**错误码集中化**：`ErrorCode` 枚举 + `ERROR_STATUS_MAP`（code→HTTP）+ `ERROR_DEFAULT_MESSAGE`（code→默认消息）统一定义于 `src/api/errors.py`，作为单一事实来源。禁止在业务代码中使用裸字符串错误码（CI 扫描阻断）。已发布的错误码禁止改名或更换 HTTP 状态（只允许新增）。

## 接口下线

接口下线采用**直接物理删除**策略（章程 v2.0.0 原则 IV + IX）：

- 直接删除路由代码、契约文件与合约测试，不保留哨兵路由或台账文件
- 客户端调用已下线路径将收到 FastAPI 默认 404 `NOT_FOUND`
- 迁移说明在 Feature changelog / `spec.md`「业务阶段映射」中一次性简述替代路径
## 分层职责

- 路由层（`src/api/routers/`）只做参数校验 + 响应组装；业务逻辑一律归 `src/services/`
- 每条新增/变更接口必须先在 `specs/<feature>/contracts/` 下提供契约，再在 `tests/contract/` 下创建合约测试，最后才写实现（TDD Red-Green 前置）

## 现有路由模块（Feature-017 完成后的保留清单，8 个）

| 文件 | 前缀 | 说明 |
|------|------|------|
| `tasks.py` | `/api/v1/tasks` | 任务提交、查询、删除（含全量分页 Feature-012 / 异步诊断入口 Feature-011 / 异步 KB 提取入口 Feature-014） |
| `knowledge_base.py` | `/api/v1/knowledge-base` | 知识库版本管理 |
| `classifications.py` | `/api/v1/classifications` | COS 扫描分类（Feature-008；原 `videos.py` 已并入） |
| `coaches.py` | `/api/v1/coaches` | 教练 CRUD |
| `standards.py` | `/api/v1/standards` | 技术标准查询与构建（Feature-010） |
| `teaching_tips.py` | `/api/v1/teaching-tips` | 教学建议（Feature-005） |
| `calibration.py` | `/api/v1/calibration` | 多教练知识库对比（Feature-006） |
| `extraction_jobs.py` | `/api/v1/extraction-jobs` | KB 提取作业 DAG 状态查询与重跑（Feature-014） |

> **已下线模块**：`videos.py`（并入 `classifications.py`）、`diagnosis.py`（同步诊断并入 `tasks.py` 异步诊断通道）；下线采用直接物理删除策略（v2.0.0），不保留哨兵路由。
