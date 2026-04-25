# 契约: GET /api/v1/video-preprocessing/{job_id}

**方法**: GET
**路径**: `/api/v1/video-preprocessing/{job_id}`
**路由**: `src/api/routers/video_preprocessing.py`

## 用途

查询一个视频预处理任务的完整元数据（原视频 + 标准化参数 + 分段列表），供运维和审计使用。

## 请求

### Path 参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `job_id` | UUID | 预处理任务 id（从提交接口返回）|

## 响应

### 200 OK — 成功任务

```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef0123456789",
  "cos_object_key": "charhuang/tt_video/乒乓球合集【较新】/张继科/正手攻球.mp4",
  "status": "success",
  "force": false,
  "started_at": "2026-04-25T10:00:00+08:00",
  "completed_at": "2026-04-25T10:08:32+08:00",
  "duration_ms": 600000,
  "segment_count": 4,
  "has_audio": true,
  "error_message": null,

  "original_meta": {
    "fps": 25.0,
    "width": 1920,
    "height": 1080,
    "duration_ms": 600000,
    "codec": "h264",
    "size_bytes": 124518400,
    "has_audio": true
  },

  "target_standard": {
    "target_fps": 30,
    "target_short_side": 720,
    "segment_duration_s": 180
  },

  "audio": {
    "cos_object_key": "preprocessed/.../jobs/{job_id}/audio.wav",
    "size_bytes": 19200000
  },

  "segments": [
    {
      "segment_index": 0,
      "start_ms": 0,
      "end_ms": 180000,
      "cos_object_key": "preprocessed/.../jobs/{job_id}/seg_0000.mp4",
      "size_bytes": 22450000
    },
    {
      "segment_index": 1,
      "start_ms": 180000,
      "end_ms": 360000,
      "cos_object_key": "preprocessed/.../jobs/{job_id}/seg_0001.mp4",
      "size_bytes": 22100000
    },
    ...
  ]
}
```

### 200 OK — 失败任务

```json
{
  "job_id": "...",
  "cos_object_key": "...",
  "status": "failed",
  "error_message": "VIDEO_QUALITY_REJECTED: fps=12.5 below minimum 15",
  "started_at": "2026-04-25T10:00:00+08:00",
  "completed_at": "2026-04-25T10:00:08+08:00",
  "duration_ms": null,
  "segment_count": null,
  "has_audio": false,
  "original_meta": { "fps": 12.5, "width": 640, "height": 480, ... },
  "target_standard": null,
  "audio": null,
  "segments": []
}
```

### 200 OK — Running 任务

```json
{
  "job_id": "...",
  "status": "running",
  "started_at": "2026-04-25T10:10:00+08:00",
  "completed_at": null,
  "segments": []
}
```

### 404 Not Found

```json
{
  "detail": "video_preprocessing job not found"
}
```

## 契约测试点

- **C1**: 成功 job → 200，字段完整，segments 按 segment_index 升序
- **C2**: 失败 job → 200，error_message 有结构化前缀，target_standard / audio / segments 可为 null / 空数组
- **C3**: running job → 200，completed_at 为 null，segments 为空数组（尚未创建）
- **C4**: 不存在的 job_id → 404
- **C5**: 非 UUID 格式 job_id → 422
- **C6**: superseded job 也能查到（审计用途）→ 200，status='superseded'

## 实施约束

- `segments` 按 `segment_index` 升序返回
- 所有时间字段使用 ISO 8601 带时区格式
- `size_bytes` 使用整数（JSON number，不超过 `Number.MAX_SAFE_INTEGER`）
