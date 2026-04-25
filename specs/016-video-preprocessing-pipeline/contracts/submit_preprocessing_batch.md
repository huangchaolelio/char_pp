# 契约: POST /api/v1/tasks/preprocessing/batch

**方法**: POST
**路径**: `/api/v1/tasks/preprocessing/batch`
**路由**: `src/api/routers/tasks.py`
**通道**: `preprocessing`
**Celery 任务**: `src.workers.preprocessing_task.preprocess_video`（批量内循环提交）

## 用途

批量提交多条预处理任务。延续 Feature-013 批量提交语义，单批 ≤ `BATCH_MAX_SIZE`（默认 100）。

## 请求

### Body Schema

```json
{
  "items": [
    {
      "cos_object_key": "string",
      "force": "boolean (optional, default=false)",
      "idempotency_key": "string (optional)"
    }
  ]
}
```

### 字段说明

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| `items` | array | ✅ | 提交项列表，1 ≤ len ≤ `BATCH_MAX_SIZE` |
| `items[].cos_object_key` | string | ✅ | 同单条接口 |
| `items[].force` | bool | ❌ | 同单条接口 |
| `items[].idempotency_key` | string | ❌ | 同单条接口 |

## 响应

### 200 OK

```json
{
  "submitted": 3,
  "reused": 1,
  "failed": 1,
  "results": [
    {
      "cos_object_key": "...",
      "job_id": "uuid",
      "status": "running",
      "reused": false
    },
    {
      "cos_object_key": "...",
      "job_id": "uuid",
      "status": "success",
      "reused": true
    },
    {
      "cos_object_key": "...",
      "job_id": null,
      "status": null,
      "reused": false,
      "error_code": "COS_KEY_NOT_CLASSIFIED",
      "error_message": "cos_object_key ... not found"
    }
  ]
}
```

**重要**: 批量内单条失败不影响其他条目，失败条目在 `results[]` 中 `job_id=null` 且带 `error_code`。

### 400 Bad Request

```json
{
  "detail": {
    "error_code": "BATCH_TOO_LARGE",
    "message": "batch size 150 exceeds limit 100"
  }
}
```

## 契约测试点

- **C1**: 提交 5 个有效 items → 200，`submitted=5`
- **C2**: items 超过 `BATCH_MAX_SIZE` → 400 `BATCH_TOO_LARGE`
- **C3**: items 部分 cos_key 无效 → 200，`failed=N`，失败条目在 `results[]` 中
- **C4**: 空 items 数组 → 422
- **C5**: 并发上限达成，部分进 queue、部分 running → 都返回 `status='running'`，不区分排队状态（通道内部调度）
