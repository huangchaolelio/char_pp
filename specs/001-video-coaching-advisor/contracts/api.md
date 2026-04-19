# API 契约: 视频教学分析与专业指导建议

**功能分支**: `001-video-coaching-advisor`
**日期**: 2026-04-17
**API 风格**: REST / JSON
**基础路径**: `/api/v1`
**异步模式**: 提交任务返回 `task_id`，通过轮询获取结果

---

## 通用约定

### 请求头

```
Content-Type: application/json
Accept: application/json
```

### 通用响应结构

```json
// 成功
{ "data": { ... }, "meta": { "task_id": "...", "version": "1.0" } }

// 错误
{ "error": { "code": "ERROR_CODE", "message": "描述", "details": {} } }
```

### 错误码

| code | HTTP 状态 | 含义 |
|------|-----------|------|
| `VIDEO_QUALITY_REJECTED` | 422 | 视频质量不足（帧率/分辨率/遮挡） |
| `ACTION_TYPE_NOT_SUPPORTED` | 422 | 动作类型不在知识库覆盖范围 |
| `NO_MOTION_DETECTED` | 422 | 视频中未检测到有效运动动作 |
| `COS_OBJECT_NOT_FOUND` | 404 | COS 对象不存在或无访问权限（专家视频） |
| `COS_DOWNLOAD_FAILED` | 502 | COS 下载失败（网络或权限问题） |
| `TASK_NOT_FOUND` | 404 | 任务 ID 不存在或已删除 |
| `KNOWLEDGE_BASE_NOT_READY` | 409 | 无 active 状态知识库版本 |
| `TASK_TIMEOUT` | 408 | 分析任务超时（>8 分钟） |
| `VALIDATION_ERROR` | 400 | 请求参数校验失败 |
| `INTERNAL_ERROR` | 500 | 服务内部错误 |

---

## 接口一览

| 端点 | 方法 | 描述 |
|------|------|------|
| `/tasks/expert-video` | POST | 提交专家教练视频，触发知识提取任务 |
| `/tasks/athlete-video` | POST | 提交运动员视频，触发偏差分析任务 |
| `/tasks/{task_id}` | GET | 查询任务状态 |
| `/tasks/{task_id}/result` | GET | 获取任务完整结果 |
| `/tasks/{task_id}` | DELETE | 用户主动删除任务及关联数据 |
| `/knowledge-base/versions` | GET | 列出知识库版本 |
| `/knowledge-base/{version}` | GET | 获取指定版本知识库详情 |
| `/knowledge-base/{version}/approve` | POST | 专家审核通过知识库版本 |

---

## 接口详情

### POST /tasks/expert-video

提交专业教练教学视频分析任务。视频须预先上传至腾讯云 COS，调用方传入 COS Object Key，
服务端自行从 COS 下载处理。

**请求**（application/json）:

```json
{
  "cos_object_key": "coach-videos/forehand_lesson_001.mp4",
  "notes": "正手拉球教学示范"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `cos_object_key` | string | ✅ | COS 中的对象路径，如 `coach-videos/xxx.mp4` |
| `notes` | string | ❌ | 视频备注说明 |

**响应 202**:

```json
{
  "data": {
    "task_id": "550e8400-e29b-41d4-a716-446655440000",
    "status": "pending",
    "cos_object_key": "coach-videos/forehand_lesson_001.mp4",
    "estimated_completion_seconds": 300
  }
}
```

**拒绝响应 404**（COS 对象不存在）:

```json
{
  "error": {
    "code": "COS_OBJECT_NOT_FOUND",
    "message": "指定的 COS 对象不存在或无访问权限",
    "details": {
      "cos_object_key": "coach-videos/forehand_lesson_001.mp4"
    }
  }
}
```

**拒绝响应 422**（下载后视频质量不足）:

```json
{
  "error": {
    "code": "VIDEO_QUALITY_REJECTED",
    "message": "视频质量不足，无法进行可靠分析",
    "details": {
      "fps": 10,
      "min_required_fps": 15,
      "resolution": "320x240",
      "min_required_resolution": "854x480"
    }
  }
}
```

---

### POST /tasks/athlete-video

提交业余运动员打球视频，与知识库对比触发偏差分析。

**请求**（multipart/form-data）:

```
video: <文件二进制>                          # 必填
knowledge_base_version: "1.0.0"             # 可选，默认使用 active 版本
target_person_index: 0                       # 可选，多人场景指定目标对象（0-based）
```

**响应 202**:

```json
{
  "data": {
    "task_id": "660e8400-e29b-41d4-a716-446655440001",
    "status": "pending",
    "knowledge_base_version": "1.0.0",
    "estimated_completion_seconds": 300
  }
}
```

---

### GET /tasks/{task_id}

查询任务当前状态。

**响应 200**:

```json
{
  "data": {
    "task_id": "660e8400-e29b-41d4-a716-446655440001",
    "task_type": "athlete_video",
    "status": "success",
    "created_at": "2026-04-17T10:00:00Z",
    "started_at": "2026-04-17T10:00:05Z",
    "completed_at": "2026-04-17T10:03:42Z",
    "video_duration_seconds": 180.5,
    "video_fps": 30.0,
    "video_resolution": "1920x1080"
  }
}
```

**status 枚举值**: `pending` / `processing` / `success` / `failed` / `rejected`

---

### GET /tasks/{task_id}/result

获取任务完整分析结果（仅 status=success 时有内容）。

**响应 200（athlete_video 任务）**:

```json
{
  "data": {
    "task_id": "660e8400-...",
    "knowledge_base_version": "1.0.0",
    "motion_analyses": [
      {
        "analysis_id": "770e8400-...",
        "action_type": "forehand_topspin",
        "segment_start_ms": 1200,
        "segment_end_ms": 2800,
        "overall_confidence": 0.85,
        "is_low_confidence": false,
        "deviation_report": [
          {
            "deviation_id": "880e8400-...",
            "dimension": "elbow_angle",
            "measured_value": 142.5,
            "ideal_value": 110.0,
            "deviation_value": 32.5,
            "deviation_direction": "above",
            "confidence": 0.85,
            "is_low_confidence": false,
            "is_stable_deviation": true,
            "impact_score": 0.76
          }
        ],
        "coaching_advice": [
          {
            "advice_id": "990e8400-...",
            "dimension": "elbow_angle",
            "deviation_description": "正手拉球肘部角度偏大 32.5°",
            "improvement_target": "击球时肘部角度控制在 90°~130° 范围内，理想值 110°",
            "improvement_method": "击球前有意识地内收手肘，保持前臂与球台平行；可对镜练习感受肘部位置",
            "impact_score": 0.76,
            "reliability_level": "high",
            "reliability_note": null
          }
        ]
      }
    ],
    "summary": {
      "total_actions_detected": 5,
      "actions_analyzed": 4,
      "actions_low_confidence": 1,
      "total_deviations": 8,
      "stable_deviations": 3,
      "top_advice_dimension": "elbow_angle"
    }
  }
}
```

**响应 200（expert_video 任务）**:

```json
{
  "data": {
    "task_id": "550e8400-...",
    "knowledge_base_version_draft": "1.1.0",
    "extracted_points_count": 12,
    "extracted_points": [
      {
        "action_type": "forehand_topspin",
        "dimension": "elbow_angle",
        "param_min": 90.0,
        "param_max": 130.0,
        "param_ideal": 110.0,
        "unit": "°",
        "extraction_confidence": 0.91
      }
    ],
    "pending_approval": true
  }
}
```

---

### DELETE /tasks/{task_id}

用户主动删除任务及全部关联数据（软删除，24 小时内物理清除）。

**响应 200**:

```json
{
  "data": {
    "task_id": "660e8400-...",
    "deleted_at": "2026-04-17T15:00:00Z",
    "message": "任务及关联数据已标记删除，将在 24 小时内物理清除"
  }
}
```

---

### GET /knowledge-base/versions

列出所有知识库版本。

**响应 200**:

```json
{
  "data": {
    "versions": [
      {
        "version": "1.0.0",
        "status": "active",
        "action_types_covered": ["forehand_topspin", "backhand_push"],
        "point_count": 16,
        "approved_at": "2026-04-17T09:00:00Z"
      }
    ]
  }
}
```

---

### POST /knowledge-base/{version}/approve

专家人工审核通过指定版本，将其设为 active（同时归档当前 active 版本）。

**请求**:

```json
{
  "approved_by": "张教练",
  "notes": "经审核，所有技术要点准确，批准上线"
}
```

**响应 200**:

```json
{
  "data": {
    "version": "1.1.0",
    "status": "active",
    "approved_by": "张教练",
    "approved_at": "2026-04-17T10:00:00Z",
    "previous_active_version": "1.0.0"
  }
}
```
