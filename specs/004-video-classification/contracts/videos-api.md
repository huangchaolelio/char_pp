# API 契约: 视频分类端点

**功能**: 004-video-classification
**Base URL**: `/api/v1`
**日期**: 2026-04-20

---

## GET /videos/classifications

查询视频分类列表，支持过滤。

**Query Parameters**:

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| coach_name | string | 否 | 按教练过滤，如"孙浩泓" |
| tech_category | string | 否 | 按技术大类过滤，如"正手技术" |
| tech_detail | string | 否 | 按技术细分过滤，如"正手劈长" |
| action_type | string | 否 | 按 ActionType 枚举值过滤 |
| video_type | string | 否 | tutorial \| training |
| manually_overridden | bool | 否 | true=仅人工修正；false=仅自动分类 |

**Response 200**:
```json
{
  "total": 120,
  "items": [
    {
      "cos_object_key": "charhuang/tt_video/.../第06节正手攻球.mp4",
      "filename": "第06节正手攻球.mp4",
      "coach_name": "孙浩泓",
      "tech_category": "正手技术",
      "tech_sub_category": "正手攻球",
      "tech_detail": "正手攻球",
      "video_type": "tutorial",
      "action_type": "forehand_attack",
      "classification_confidence": 1.0,
      "manually_overridden": false,
      "classified_at": "2026-04-20T10:00:00Z"
    }
  ]
}
```

---

## POST /videos/classifications/refresh

全量重扫描 COS，对所有视频重新运行分类规则。不覆盖 `manually_overridden=true` 的记录。

**Request**: 无 body

**Response 200**:
```json
{
  "total": 120,
  "updated": 118,
  "skipped_manual": 2
}
```

**幂等性**: 多次调用结果相同（非 manually_overridden 记录会被覆盖更新）。

---

## PATCH /videos/classifications/{cos_object_key}

人工修正单个视频的分类。`cos_object_key` 需 URL 编码。

**Path Parameters**:
- `cos_object_key`: COS 完整路径（URL 编码）

**Request Body**:
```json
{
  "tech_category": "正手技术",
  "tech_sub_category": "正手攻球",
  "tech_detail": "正手攻球",
  "action_type": "forehand_attack",
  "video_type": "tutorial",
  "override_reason": "标题歧义，手动确认为正手攻球"
}
```
所有字段均可选，仅传需要修改的字段。

**Response 200**: 返回更新后的完整 `VideoClassificationResponse`（同 GET 返回结构）

**Response 404**: 视频不存在于分类表中

**副作用**: 自动设置 `manually_overridden=true`，后续 refresh 不覆盖此记录。

---

## POST /videos/classifications/batch-submit

按分类批量提交知识库提取任务。

**Request Body**:
```json
{
  "tech_detail": "正手劈长",
  "enable_audio_analysis": true,
  "audio_language": "zh"
}
```

过滤条件（至少提供一个）：
- `tech_detail`: 按技术细分过滤
- `tech_category`: 按技术大类过滤
- `action_type`: 按 ActionType 过滤

**Response 202**:
```json
{
  "submitted": 2,
  "task_ids": [
    "550e8400-e29b-41d4-a716-446655440000",
    "550e8400-e29b-41d4-a716-446655440001"
  ]
}
```

**Response 400**: 未提供任何过滤条件

**内部行为**: 对每个匹配视频调用 `process_expert_video.delay()`，`action_type_hint` 直接使用 `video_classifications.action_type`。

---

## 错误响应格式

所有错误遵循现有项目约定：
```json
{
  "code": "CLASSIFICATION_NOT_FOUND",
  "message": "视频分类记录不存在",
  "details": {"cos_object_key": "..."}
}
```
