# 契约: POST /api/v1/tasks/preprocessing

**方法**: POST
**路径**: `/api/v1/tasks/preprocessing`
**路由**: `src/api/routers/tasks.py`
**通道**: `preprocessing`
**Celery 任务**: `src.workers.preprocessing_task.preprocess_video`

## 用途

提交单条视频预处理任务。从 COS 下载原视频 → probe → 转码 → 分段 → 并发上传 → 更新映射表。

## 请求

### Headers

```
Content-Type: application/json
```

### Body Schema

```json
{
  "cos_object_key": "string (required)",
  "force": "boolean (optional, default=false)",
  "idempotency_key": "string (optional, UUID recommended)"
}
```

### 字段说明

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| `cos_object_key` | string | ✅ | 原视频 COS key，必须已存在于 `coach_video_classifications` 表 |
| `force` | bool | ❌ | `true` 时即使已有 `status='success'` 的 job 也重新预处理（旧 job → superseded，旧 COS 对象删除）|
| `idempotency_key` | string | ❌ | Feature-013 幂等提交 key；同 key 重复提交返回同 job_id |

## 响应

### 200 OK — 幂等命中（force=false 且已有 success job）

```json
{
  "job_id": "uuid",
  "status": "success",
  "reused": true,
  "cos_object_key": "charhuang/tt_video/.../video.mp4",
  "segment_count": 4,
  "has_audio": true,
  "started_at": "2026-04-25T10:00:00+08:00",
  "completed_at": "2026-04-25T10:08:32+08:00"
}
```

### 202 Accepted — 新任务已入队

```json
{
  "job_id": "uuid",
  "status": "running",
  "reused": false,
  "cos_object_key": "charhuang/tt_video/.../video.mp4",
  "started_at": "2026-04-25T10:10:00+08:00"
}
```

### 400 Bad Request

```json
{
  "detail": {
    "error_code": "COS_KEY_NOT_CLASSIFIED",
    "message": "cos_object_key not found in coach_video_classifications"
  }
}
```

错误码：
- `COS_KEY_NOT_CLASSIFIED`: 原视频未在 Feature-008 分类表内
- `BATCH_TOO_LARGE`: （仅 batch 接口）超过 `batch_max_size`
- `CHANNEL_QUEUE_FULL`: `preprocessing` 通道队列已满（沿用 F-013 错误）

### 422 Unprocessable Entity

Pydantic 校验失败（cos_object_key 为空等）。

## 示例

### 请求

```bash
curl -X POST http://localhost:8080/api/v1/tasks/preprocessing \
  -H 'Content-Type: application/json' \
  -d '{
    "cos_object_key": "charhuang/tt_video/乒乓球合集【较新】/张继科/正手攻球.mp4",
    "force": false
  }'
```

### 响应（新任务）

```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef0123456789",
  "status": "running",
  "reused": false,
  "cos_object_key": "charhuang/tt_video/乒乓球合集【较新】/张继科/正手攻球.mp4",
  "started_at": "2026-04-25T10:10:00+08:00"
}
```

## 契约测试点

- **C1**: 有效 cos_object_key → 202（新 job）或 200（幂等命中）
- **C2**: cos_object_key 不在 classifications → 400 `COS_KEY_NOT_CLASSIFIED`
- **C3**: force=false 且已有 success → 返回同 job_id，reused=true
- **C4**: force=true 且已有 success → 新 job_id，旧 job 变 superseded，旧 COS 对象被删除
- **C5**: 同 idempotency_key 重复提交 → 返回同 job_id（Feature-013 幂等语义）
- **C6**: 通道队列满 → 400 `CHANNEL_QUEUE_FULL`
- **C7**: 空 body 或缺字段 → 422
