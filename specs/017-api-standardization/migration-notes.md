# 前端 / SDK 迁移清单（Feature-017 US1）

**适用范围**：所有调用 `/api/v1/**` 的前端、移动端、第三方 SDK 调用方
**生效时间**：Feature-017 合入日起（Big Bang，无兼容期，无废弃窗口）
**权威来源**：
- 路由层：`src/api/routers/*.py`
- 错误码：`src/api/errors.py::ErrorCode` + `ERROR_STATUS_MAP`
- 信封模型：`src/api/schemas/envelope.py::SuccessEnvelope` / `ErrorEnvelope`
- 下线台账：`specs/017-api-standardization/contracts/retirement-ledger.md`

---

## 1. 全局变更（影响所有接口）

### 1.1 响应体统一信封（最严重 · 破坏性）

所有 `/api/v1/**` 接口响应体结构变更：

**成功响应（原来：裸对象/裸数组）**：

```diff
- // 旧格式：直接返回业务对象
- { "task_id": "xxx", "status": "running", ... }

+ // 新格式：SuccessEnvelope[T] 统一信封
+ {
+   "success": true,
+   "data": { "task_id": "xxx", "status": "running", ... },
+   "meta": null                   // 非分页接口为 null
+ }
```

**错误响应（原来：FastAPI 默认 `{"detail": ...}`）**：

```diff
- // 旧格式：FastAPI 默认格式（或自定义的嵌套 detail.error）
- { "detail": { "code": "TASK_NOT_FOUND", "message": "...", "details": {...} } }
- { "detail": { "error": { "code": "TASK_NOT_FOUND", ... } } }

+ // 新格式：ErrorEnvelope 统一信封
+ {
+   "success": false,
+   "error": {
+     "code": "TASK_NOT_FOUND",
+     "message": "任务不存在",
+     "details": { "task_id": "..." }
+   }
+ }
```

**前端建议**：

```typescript
// 封装一层响应拦截器，统一抽取 data/error
interface SuccessEnvelope<T> {
  success: true;
  data: T;
  meta: { page: number; page_size: number; total: number } | null;
}
interface ErrorEnvelope {
  success: false;
  error: { code: string; message: string; details: Record<string, any> | null };
}
type Envelope<T> = SuccessEnvelope<T> | ErrorEnvelope;

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init);
  const body: Envelope<T> = await resp.json();
  if (!body.success) {
    throw new ApiError(body.error.code, body.error.message, body.error.details);
  }
  return body.data;
}
```

### 1.2 分页参数与元数据（破坏性）

**参数侧**：

- ✅ 继续支持：`page`（从 1 开始，默认 1）+ `page_size`（默认 20，**最大 100**）
- ❌ 已下线：`limit` / `offset` / `skip` / `take`（若之前用过，需改为 `page`/`page_size`）
- ⚠️ **`page_size` 上限从 200 降为 100**（`tasks.py` 受影响）

**响应侧**：

```diff
- // 旧格式：分页字段散落在顶层
- {
-   "items": [...],
-   "total": 42,
-   "page": 1,
-   "page_size": 20,
-   "total_pages": 3                // ❌ 已下线
- }

+ // 新格式：items→data，分页元数据收归 meta
+ {
+   "success": true,
+   "data": [...],                  // ← 原 items
+   "meta": {
+     "page": 1,
+     "page_size": 20,
+     "total": 42
+   }
+ }
```

**⚠️ `total_pages` 字段已下线**，前端自行计算：

```typescript
const totalPages = Math.ceil(meta.total / meta.page_size);
```

### 1.3 参数校验错误统一

Pydantic 请求体/查询参数校验失败统一返回 `422` + `VALIDATION_FAILED`；枚举值非法返回 `400` + `INVALID_ENUM_VALUE`（`details` 带 `allowed` 列表）。

```json
// 枚举非法示例
{
  "success": false,
  "error": {
    "code": "INVALID_ENUM_VALUE",
    "message": "Invalid status value: 'xxx'",
    "details": {
      "field": "status",
      "value": "xxx",
      "allowed": ["pending", "processing", "success", "failed", "rejected"]
    }
  }
}
```

---

## 2. 状态码变更（破坏性）

前端若按状态码分支处理，需按下表更新：

| 错误码 | 旧 HTTP | 新 HTTP | 影响接口 | 变更理由 |
|---|---|---|---|---|
| `TASK_NOT_READY` | 409 | **400** | `GET /tasks/{task_id}/result`、teaching-tips | 业务状态校验统一 400 |
| `JOB_NOT_FAILED` | 409 | **400** | `POST /extraction-jobs/{job_id}/rerun` | 同上 |
| `INTERMEDIATE_EXPIRED` | 409 | **410 GONE** | `POST /extraction-jobs/{job_id}/rerun` | 语义更准确：资源已被清理 |
| `CHANNEL_QUEUE_FULL` | 400 | **503** | 所有 POST `/tasks/*` 提交端点 | 章程归类为服务不可用 |
| `CHANNEL_DISABLED` | 400 | **503** | 同上 | 同上 |
| `INVALID_STATUS`（旧码） | 400 | **400（码名改为 `INVALID_ENUM_VALUE`）** | `GET /extraction-jobs?status=xxx` | 枚举校验统一 |

---

## 3. 端点级响应字段变化（按路由分组）

### 3.1 `/api/v1/tasks/*`（`tasks.py`，15 端点）

#### `GET /api/v1/tasks`（分页列表）

```diff
- {
-   "items": [...],
-   "total": 42, "page": 1, "page_size": 20,
-   "total_pages": 3
- }

+ {
+   "success": true,
+   "data": [...],                  // ← 原 items
+   "meta": { "page": 1, "page_size": 20, "total": 42 }
+ }
```

- ⚠️ `page_size` 上限从 200 降为 100
- ⚠️ `total_pages` 字段下线
- `sort_by` / `order` 非法值返回 `400 INVALID_ENUM_VALUE`（原 400 纯文本）

#### `GET /api/v1/tasks/{task_id}`（详情）

```diff
- { "task_id": "...", "status": "...", "summary": {...}, ... }

+ { "success": true, "data": { "task_id": "...", "status": "...", "summary": {...} }, "meta": null }
```

#### `GET /api/v1/tasks/{task_id}/result`（结果查询）

- 成功：`{ success: true, data: <ExpertResult | AthleteResult>, meta: null }`
- 任务未就绪：从 **409 → 400** + `TASK_NOT_READY`

#### `DELETE /api/v1/tasks/{task_id}`

```diff
- { "task_id": "...", "deleted_at": "...", "message": "..." }

+ { "success": true, "data": { "task_id": "...", "deleted_at": "...", "message": "..." }, "meta": null }
```

#### `POST /api/v1/tasks/classification`、`/kb-extraction`、`/diagnosis`（单提交 + 3 个 `/batch`）

- 成功响应业务字段（`accepted`、`rejected`、`items`、`channel`、`submitted_at`）**全部进入 `data`**：

```diff
- { "task_type": "kb_extraction", "accepted": 1, "rejected": 0, "items": [...], "channel": {...}, "submitted_at": "..." }

+ { "success": true,
+   "data": { "task_type": "kb_extraction", "accepted": 1, "rejected": 0, "items": [...], "channel": {...}, "submitted_at": "..." },
+   "meta": null }
```

- `CLASSIFICATION_REQUIRED` 错误：保持 `400`，但信封从 `{"detail":{"error":{...}}}` 改为根级 `{"success":false,"error":{...}}`
- `CHANNEL_DISABLED` / `CHANNEL_QUEUE_FULL`：**状态码 400 → 503**
- `BATCH_TOO_LARGE`：保持 `400`，信封对齐

#### `GET /api/v1/tasks/cos-videos`

- `action_type` 非法：`400 INVALID_ACTION_TYPE`（旧码）→ `400 INVALID_ENUM_VALUE`

#### `POST /api/v1/tasks/preprocessing`、`/preprocessing/batch`

- 成功响应字段（`job_id`、`status`、`reused`、`segment_count`、`has_audio` 等）进入 `data`
- `CHANNEL_QUEUE_FULL`：**400 → 503**
- `COS_KEY_NOT_CLASSIFIED` / `BATCH_TOO_LARGE`：保持 `400`，信封对齐

---

### 3.2 `/api/v1/extraction-jobs/*`（`extraction_jobs.py`，3 端点）

#### `GET /api/v1/extraction-jobs/{job_id}`、`GET /api/v1/extraction-jobs`

- 所有业务字段进入 `data`；列表版分页元数据进 `meta`
- `status` 查询参数非法：原 `INVALID_STATUS` → 新 `INVALID_ENUM_VALUE`

#### `POST /api/v1/extraction-jobs/{job_id}/rerun`

- 成功：`{ success: true, data: { job_id, status, reset_steps: [...] }, meta: null }`
- `JOB_NOT_FAILED`：**409 → 400**
- `INTERMEDIATE_EXPIRED`：**409 → 410 GONE**

---

### 3.3 其他 9 个路由（阶段 4 批次 A+B 覆盖）

以下路由的响应体均改为 `{ success, data, meta }` 信封形态，前端均需调整：

| 路由 | 主要端点 | 分页接口？ |
|---|---|---|
| `coaches.py` | 教练 CRUD + `PATCH /tasks/{task_id}/coach` | 列表：否（全量返回） |
| `classifications.py` | 分类扫描 + 列表 + 更新 | 列表：**是**（meta 存在） |
| `standards.py` | 技术标准查询 + 构建 | 列表：是 |
| `teaching_tips.py` | 教学建议 CRUD + 提取 | 列表：是 |
| `knowledge_base.py` | 知识库版本管理 + 审批 | 列表：是 |
| `calibration.py` | 多教练知识库对比 | 否 |
| `task_channels.py` | 通道快照查询 | 否 |
| `admin.py` | 通道配置 PATCH + 重置 | 否 |
| `video_preprocessing.py` | 预处理作业查询 | 否 |

所有 `204 No Content`（`DELETE /coaches/{id}`、`DELETE /teaching-tips/{tip_id}`）**保持无响应体**，不套信封。

---

## 4. 已下线接口（物理下线，哨兵路由 404）

详见 `specs/017-api-standardization/contracts/retirement-ledger.md`。前端若还有调用，会收到：

```http
HTTP/1.1 404 Not Found
{
  "success": false,
  "error": {
    "code": "ENDPOINT_RETIRED",
    "message": "该接口已下线，请调用替代接口",
    "details": { "successor": "...", "migration_note": "..." }
  }
}
```

**7 条下线路径**：

| 旧路径 | 替代 |
|---|---|
| `POST /api/v1/tasks/expert-video` | `POST /classification` + `POST /kb-extraction` |
| `POST /api/v1/tasks/athlete-video` | `POST /tasks/diagnosis` |
| `GET /api/v1/videos/classifications` | `GET /api/v1/classifications` |
| `POST /api/v1/videos/classifications/refresh` | `POST /api/v1/classifications/scan` |
| `PATCH /api/v1/videos/classifications/{cos_object_key}` | `PATCH /api/v1/classifications/{id}`（**参数改为记录 ID**） |
| `POST /api/v1/videos/classifications/batch-submit` | `POST /api/v1/tasks/kb-extraction/batch` |
| `POST /api/v1/diagnosis` | `POST /api/v1/tasks/diagnosis`（**同步 → 异步**） |

---

## 5. 迁移 checklist（前端/SDK）

合入前必须完成：

- [ ] **响应拦截器统一改造**：按 SuccessEnvelope/ErrorEnvelope 抽取 `data` / `error`
- [ ] **错误处理统一**：按 `error.code` 分支（不再按 HTTP 状态码硬编码判断业务类型）
- [ ] **分页字段重命名**：`items` → `data`、`total_pages` 自行计算、`page_size` 上限降到 100
- [ ] **状态码更新**：6 个错误码的 HTTP 码变更点（表 § 2）全部确认
- [ ] **下线接口清理**：7 条老路径全部从代码/文档中删除，按 successor 重写调用链
- [ ] **联调测试**：覆盖成功 + 失败（含 `ENDPOINT_RETIRED` 404）两类路径
- [ ] **监控告警调整**：`alert on 4xx` / `alert on 5xx` 相关阈值按新状态码分布复核

---

## 6. 参考测试样例（后端 tests/ 下已落地）

| 用途 | 文件 |
|---|---|
| 成功信封断言模式 | `tests/contract/test_task_list_api.py::test_task_list_response_structure` |
| 错误信封断言模式 | `tests/contract/test_api_contracts.py::TestErrorResponseFormat::test_error_response_has_code_and_message` |
| 分页 meta 断言 | `tests/integration/test_task_list.py::test_list_tasks_default_pagination` |
| `ENDPOINT_RETIRED` 哨兵 | `tests/contract/test_retirement_contract.py`（若存在） |
| 状态码变更覆盖 | `tests/integration/test_rerun_us4.py`（400/410） |

---

**版本**：v1.0（Feature-017 阶段 4 闭环后首发）
**维护**：后续 US3（命名规范）/ US4（schema 清理）/ US5（契约文档）若产生新的破坏性变更，需在本文件追加章节。