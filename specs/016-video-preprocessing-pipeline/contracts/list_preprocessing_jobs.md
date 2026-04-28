# 契约: GET /api/v1/video-preprocessing

**方法**: GET
**路径**: `/api/v1/video-preprocessing`
**路由**: `src/api/routers/video_preprocessing.py::list_preprocessing_jobs`
**Service**: `src/services/preprocessing_service.py::list_jobs`
**新增日期**: 2026-04-28（Feature-016 后续补强）

## 用途

分页 + 过滤查询视频预处理任务列表（`video_preprocessing_jobs` 表），供运维浏览、审计以及前端"预处理任务中心"页面使用。

预处理任务**独立于** `analysis_tasks`，因此**不会**出现在 `GET /api/v1/tasks` 列表中。本端点是查询所有预处理 job（running / success / failed / superseded）的唯一入口。

## 请求

### Query 参数

| 参数 | 类型 | 必填 | 默认 | 约束 | 说明 |
|------|------|------|------|------|------|
| `page` | int | ❌ | `1` | `≥ 1` | 页码（从 1 开始） |
| `page_size` | int | ❌ | `20` | `1 ≤ x ≤ 100` | 每页条数（章程 v1.4.0 硬上限 100） |
| `status` | string | ❌ | `null`（不过滤） | `running \| success \| failed \| superseded` | 按任务状态精确匹配 |
| `cos_object_key` | string | ❌ | `null`（不过滤） | 长度 ≤ 1024 | 按原视频 COS key 精确匹配 |
| `sort_by` | string | ❌ | `started_at` | `started_at \| completed_at \| created_at` | 排序字段 |
| `order` | string | ❌ | `desc` | `asc \| desc` | 排序方向，null 值按 `NULLS LAST` 规则排在最后 |

### 请求示例

```bash
# 默认：最近提交的 20 条（按 started_at 降序）
GET /api/v1/video-preprocessing

# 只看失败的
GET /api/v1/video-preprocessing?status=failed&page_size=50

# 追踪某条视频的所有历史 job（含 superseded）
GET /api/v1/video-preprocessing?cos_object_key=charhuang/tt_video/zhang/forehand.mp4

# 按完成时间倒序（failed / running 排在最后）
GET /api/v1/video-preprocessing?sort_by=completed_at&order=desc
```

## 响应

### 200 OK — 成功（分页信封）

```json
{
  "success": true,
  "data": [
    {
      "job_id": "a1b2c3d4-e5f6-7890-abcd-ef0123456789",
      "cos_object_key": "charhuang/tt_video/zhang/forehand.mp4",
      "status": "success",
      "force": false,
      "started_at": "2026-04-25T10:00:00+08:00",
      "completed_at": "2026-04-25T10:08:32+08:00",
      "duration_ms": 600000,
      "segment_count": 4,
      "has_audio": true,
      "error_message": null
    },
    {
      "job_id": "...",
      "cos_object_key": "charhuang/tt_video/wang/backhand.mp4",
      "status": "failed",
      "force": false,
      "started_at": "2026-04-25T09:30:00+08:00",
      "completed_at": "2026-04-25T09:30:08+08:00",
      "duration_ms": null,
      "segment_count": null,
      "has_audio": false,
      "error_message": "VIDEO_QUALITY_REJECTED: fps=12.5 below minimum 15"
    }
  ],
  "meta": {
    "page": 1,
    "page_size": 20,
    "total": 142
  }
}
```

### 响应字段（`data[]` 项 — `PreprocessingJobListItem`）

| 字段 | 类型 | 可空 | 说明 |
|------|------|------|------|
| `job_id` | UUID | ❌ | 预处理任务 id；调用 `GET /api/v1/video-preprocessing/{job_id}` 查看完整详情 |
| `cos_object_key` | string | ❌ | 原视频 COS 对象键 |
| `status` | string | ❌ | `running` / `success` / `failed` / `superseded` |
| `force` | bool | ❌ | 是否为 `force=true` 强制重跑任务 |
| `started_at` | datetime | ❌ | 任务启动时间（ISO 8601 带时区） |
| `completed_at` | datetime | ✅ | 任务结束时间；running 时为 `null` |
| `duration_ms` | int | ✅ | 视频时长毫秒；仅 success 任务有值 |
| `segment_count` | int | ✅ | 分段数；仅 success 任务有值 |
| `has_audio` | bool | ❌ | 原视频是否含音轨 |
| `error_message` | string | ✅ | 失败原因（含结构化前缀）；仅 failed 任务有值 |

> **与 GET /{job_id} 的区别**：列表响应**不**包含 `segments` / `original_meta` / `target_standard` / `audio`，减少单页载荷大小。如需完整元数据，请按 `job_id` 钻取详情端点。

### 400 Bad Request — 非法枚举参数

`status` / `sort_by` / `order` 取值不在白名单内：

```json
{
  "success": false,
  "error": {
    "code": "INVALID_ENUM_VALUE",
    "message": "status='bogus' 非法",
    "details": {
      "field": "status",
      "allowed_values": ["running", "success", "failed", "superseded"]
    }
  }
}
```

### 422 Unprocessable Entity — 参数硬约束越界

`page < 1` / `page_size < 1` / `page_size > 100` / `cos_object_key` 长度 > 1024：

```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "请求参数校验失败",
    "details": { "...pydantic 结构化错误..." }
  }
}
```

> **设计说明**：分页越界用 `VALIDATION_FAILED`（422）而非 `INVALID_PAGE_SIZE`（400），与 `GET /api/v1/tasks`（Feature-017 T054）保持一致 —— 由 `Query(le=100, ge=1)` 在 FastAPI 层硬约束。

## 契约测试点（`tests/contract/test_preprocessing_api.py::TestPreprocessingListContract`）

| ID | 用例 | 关键断言 |
|----|------|---------|
| **C1** | 默认分页 | 200 + `meta={page:1, page_size:20, total:N}`；service 被调用时参数为默认值 |
| **C2** | `?status=failed` 过滤 | status 透传到 `list_jobs`，响应成功 |
| **C3** | `?cos_object_key=...` 过滤 | cos_object_key 透传，精确匹配 |
| **C4** | `?page_size=500` 越界 | 422 `VALIDATION_FAILED`，service 不被调用 |
| **C5** | `?status=bogus` 非法枚举 | 400 `INVALID_ENUM_VALUE`，`details.field="status"`，`details.allowed_values` 含 4 个合法值 |
| **C6** | `?sort_by=random_col` 非法枚举 | 400 `INVALID_ENUM_VALUE`，`details.field="sort_by"` |

## 实施约束

- **分页信封**：必须通过 `src/api/schemas/envelope.py::page(items, page=, page_size=, total=)` 构造器返回，禁止手写字典。
- **排序**：`NULLS LAST`（无论 asc / desc），避免 running 任务的 `completed_at=null` 干扰 `sort_by=completed_at` 的首屏展示。
- **过滤组合**：`status` 与 `cos_object_key` 为 **AND** 关系；均为精确匹配，不做大小写归一化（COS key 本身大小写敏感）。
- **枚举白名单校验**：在路由层提前完成，非法值**不应**到达 service 层的数据库查询（节省一次空查询 + 提供含 `allowed_values` 的 details）。service 层保留 `ValueError` 兜底，防御非 HTTP 调用方（如内部脚本）传入非法参数。
- **默认上限**：`page_size=20` 与 `GET /api/v1/tasks` 保持一致；最大 100。
- **审计友好**：`status=superseded` 的 job 也会返回（它们是 force=true 重跑后的历史快照，审计场景需可见）。

## 相关端点

- `POST /api/v1/tasks/preprocessing` — 提交单个预处理任务
- `POST /api/v1/tasks/preprocessing/batch` — 批量提交
- `GET /api/v1/video-preprocessing/{job_id}` — 查询单个任务完整详情（含 segments / original_meta / target_standard / audio）
