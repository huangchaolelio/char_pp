# API 契约: 全量任务查询接口

**功能**: 012-task-query-all
**日期**: 2026-04-23
**路由前缀**: `/api/v1/tasks`

---

## 新增端点

### GET /api/v1/tasks

获取全量任务列表，支持分页、筛选和排序。

**Query Parameters**:

| 参数 | 类型 | 必填 | 默认值 | 约束 | 说明 |
|------|------|------|--------|------|------|
| page | integer | 否 | 1 | ≥ 1 | 页码（从 1 开始） |
| page_size | integer | 否 | 20 | 1–200，超出截断为 200 | 每页条数 |
| sort_by | string | 否 | created_at | created_at / completed_at | 排序字段 |
| order | string | 否 | desc | asc / desc | 排序方向 |
| status | string | 否 | — | 见 TaskStatus 枚举 | 按状态筛选 |
| task_type | string | 否 | — | expert_video / athlete_video | 按任务类型筛选 |
| coach_id | UUID | 否 | — | 有效 UUID | 按教练 ID 筛选 |
| created_after | datetime | 否 | — | ISO 8601 | 创建时间下界（含） |
| created_before | datetime | 否 | — | ISO 8601 | 创建时间上界（含） |

**Response 200**:

```json
{
  "items": [
    {
      "task_id": "550e8400-e29b-41d4-a716-446655440000",
      "task_type": "expert_video",
      "status": "success",
      "video_filename": "coach_forehand.mp4",
      "video_storage_uri": "cos://bucket/path/coach_forehand.mp4",
      "video_duration_seconds": 120.5,
      "progress_pct": 100.0,
      "error_message": null,
      "knowledge_base_version": "v1.2",
      "coach_id": "660e8400-e29b-41d4-a716-446655440001",
      "coach_name": "张教练",
      "created_at": "2026-04-20T10:00:00Z",
      "started_at": "2026-04-20T10:00:05Z",
      "completed_at": "2026-04-20T10:02:30Z"
    }
  ],
  "total": 1523,
  "page": 1,
  "page_size": 20,
  "total_pages": 77
}
```

**Error Responses**:

| 状态码 | 条件 | 响应体示例 |
|--------|------|-----------|
| 400 | 非法枚举值（如 status=unknown） | `{"detail": "Invalid status value: 'unknown'. Valid values: pending, processing, success, partial_success, failed, rejected"}` |
| 400 | page < 1 或 page_size < 1 | `{"detail": "page must be >= 1"}` |
| 422 | 参数类型错误（如 page=abc） | FastAPI 标准 422 校验错误 |

---

## 修改的现有端点

### GET /api/v1/tasks/{task_id}

在现有 `TaskStatusResponse` 基础上，**新增** `summary` 字段。

**Path Parameter**: `task_id` (UUID, 必填)

**Response 200（新增字段）**:

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "task_type": "expert_video",
  "status": "success",
  "... (现有字段不变) ...",
  "summary": {
    "tech_point_count": 42,
    "has_transcript": true,
    "semantic_segment_count": 8,
    "motion_analysis_count": 0,
    "deviation_count": 0,
    "advice_count": 0
  }
}
```

对于运动员视频任务（athlete_video），`summary` 示例：

```json
{
  "summary": {
    "tech_point_count": 0,
    "has_transcript": false,
    "semantic_segment_count": 0,
    "motion_analysis_count": 5,
    "deviation_count": 12,
    "advice_count": 12
  }
}
```

**注**: `summary` 字段始终填充（非 null），具体数值依任务类型和处理状态而定。对于未完成或失败的任务，各统计字段均为 0。

**Error Responses**（保持现有行为不变）:

| 状态码 | 条件 |
|--------|------|
| 404 | 任务不存在或已软删除 |

---

## TaskStatus 枚举值

| 值 | 含义 |
|----|------|
| pending | 待处理 |
| processing | 处理中 |
| success | 完全成功 |
| partial_success | 部分成功（如音频回退） |
| failed | 失败 |
| rejected | 被拒绝（验证不通过） |

## TaskType 枚举值

| 值 | 含义 |
|----|------|
| expert_video | 教练视频（知识提取） |
| athlete_video | 运动员视频（偏差分析） |
