# API 变更契约: 音频增强型教练视频技术知识库提取

**分支**: `002-audio-enhanced-kb-extraction` | **日期**: 2026-04-19（最后更新: 2026-04-20）

## 变更原则

所有变更为**向后兼容增量扩展**：现有字段不删除、不重命名、不改变语义。新增字段均有默认值或可为 NULL，旧客户端忽略新字段不受影响。

---

## 1. POST /api/v1/tasks/expert-video

**变更类型**: 新增可选请求字段（向后兼容）

### 变更前请求体
```json
{
  "video_cos_key": "string",
  "notes": "string (optional)"
}
```

### 变更后请求体（新增字段）
```json
{
  "video_cos_key": "string",
  "notes": "string (optional)",
  "enable_audio_analysis": "boolean (optional, default: true)",
  "audio_language": "string (optional, default: 'zh', enum: ['zh', 'en', 'auto'])",
  "action_type_hint": "string (optional, default: null, enum: ['forehand_topspin', 'backhand_push', null])"
}
```

**说明**:
- `enable_audio_analysis`: false 时跳过音频提取，直接使用纯视觉模式
- `audio_language`: 指定音频语言；`auto` = Whisper 自动检测（精度略低）
- `action_type_hint`: 可选动作类型提示。**不传时**，系统将自动根据 `cos_object_key` 文件名中的关键词推断（含"正手"且不含"反手"→`forehand_topspin`；含"反手"且不含"正手"→`backhand_push`；其他→`null`）。**传入时**，以显式指定值为准。设置后，视觉分类不符的动作段被丢弃，音频来源要点自动继承该 action_type。

---

## 2. GET /api/v1/tasks/{task_id}

**变更类型**: 响应新增进度字段（向后兼容）

### 变更后响应体（新增字段）
```json
{
  "id": "uuid",
  "status": "pending | processing | success | failed | rejected",
  "task_type": "expert_video | athlete_video",
  "created_at": "ISO8601",
  "started_at": "ISO8601 | null",
  "completed_at": "ISO8601 | null",

  // 新增字段（仅 status=processing 时有值）
  "progress_pct": "float | null (0-100)",
  "processed_segments": "integer | null",
  "total_segments": "integer | null",

  // 新增字段（处理完成后）
  "audio_fallback_reason": "string | null"
}
```

---

## 3. GET /api/v1/tasks/{task_id}/result（专家视频任务）

**变更类型**: 响应新增音频来源字段和冲突信息（向后兼容）

### 变更后 ExpertTechPoint 结构（新增字段）
```json
{
  "id": "uuid",
  "action_type": "forehand_topspin | backhand_push | unknown",
  "dimension": "elbow_angle | swing_trajectory | contact_timing | weight_transfer",
  "param_min": "float",
  "param_max": "float",
  "param_ideal": "float",
  "unit": "string",
  "extraction_confidence": "float",
  "source_segment_start_ms": "integer",
  "source_segment_end_ms": "integer",

  // 新增字段
  "source_type": "visual | audio | visual+audio",
  "conflict_flag": "boolean",
  "conflict_detail": {
    "visual_value": "float | null",
    "audio_value": "float | null",
    "diff_pct": "float | null"
  } | null
}
```

### 变更后顶层响应（新增 conflicts 数组）
```json
{
  "task_id": "uuid",
  "knowledge_base_version": "string",
  "tech_points": [...],

  // 新增字段
  "audio_analysis": {
    "enabled": "boolean",
    "quality_flag": "ok | low_snr | unsupported_language | silent | null",
    "fallback_reason": "string | null",
    "transcript_sentence_count": "integer | null"
  },
  "conflicts": [
    {
      "tech_point_id": "uuid",
      "dimension": "string",
      "visual_value": "float",
      "audio_value": "float",
      "diff_pct": "float",
      "requires_review": true
    }
  ]
}
```

---

## 4. GET /api/v1/tasks/cos-videos（新弹端点）

**变更类型**: 新增端点（向后兼容，属 Feature-002 增强）

### 请求
```
GET /api/v1/tasks/cos-videos?action_type=forehand|backhand|all
```

**查询参数**:
- `action_type` （可选，默认 `all`）: 按动作类型过滤 COS 视频列表

### 响应体
```json
{
  "action_type_filter": "forehand | backhand | all",
  "total": "integer",
  "videos": [
    {
      "cos_object_key": "string",
      "filename": "string",
      "size_bytes": "integer",
      "action_type": "forehand | backhand | forehand+backhand | other"
    }
  ]
}
```

**说明**: 列出 COS 指定前缀下的所有视频对象，根据文件名关键词（`FOREHAND_VIDEO_KEYWORDS` / `BACKHAND_VIDEO_KEYWORDS`）进行分类。返回的 `cos_object_key` 可直接用于 `POST /tasks/expert-video` 提交。

---

## 5. 错误码扩展（新增）

在现有附录 B 错误码基础上新增：

| 错误码 | HTTP 状态 | 场景 |
|--------|-----------|------|
| `AUDIO_EXTRACTION_FAILED` | 422 | ffmpeg 无法提取音频轨道（视频无音频流），系统已回退视觉模式并写入 fallback_reason |
| `UNSUPPORTED_AUDIO_LANGUAGE` | 422 | 检测到音频语言不在支持列表，已回退视觉模式 |
| `INVALID_ACTION_TYPE` | 400 | `GET /tasks/cos-videos` 的 `action_type` 参数非法（非 `forehand`/`backhand`/`all`） |

**注**: 这两个错误码对应的场景不会导致任务失败（status=failed），而是降级为纯视觉模式，任务仍然成功完成，错误码仅在 `audio_analysis.quality_flag` 中体现。

---

## 不变部分（兼容性保证）

- `POST /tasks/athlete-video` 接口无变更（本功能仅影响专家视频 KB 提取）
- `GET /knowledge-base/{version}` 接口无变更（知识库查询结构不变，新增的 `source_type` 字段为可选展示）
- `POST /knowledge-base/{version}/approve` 接口无变更
- 所有现有错误码语义不变
