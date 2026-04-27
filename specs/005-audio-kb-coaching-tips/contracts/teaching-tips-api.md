# API 契约: Teaching Tips 教学建议

## 端点列表

### 1. POST /tasks/{task_id}/extract-tips
重新（或首次）对已完成的 expert_video 任务触发教学建议提炼。

**请求**
```
POST /api/v1/tasks/{task_id}/extract-tips
Content-Type: application/json
```

**响应 202**
```json
{
  "task_id": "uuid",
  "status": "extracting",
  "message": "教学建议提炼已触发，将在30秒内完成"
}
```

**错误**
- `404 TASK_NOT_FOUND`：任务不存在
- `400 TASK_NOT_READY`：任务未完成（status != success）——Feature-017 对齐章程统一 400
- `409 NO_AUDIO_TRANSCRIPT`：该任务无音频转录记录
- `422 WRONG_TASK_TYPE`：非 expert_video 任务

---

### 2. GET /teaching-tips
查询教学建议列表，支持过滤。

**请求**
```
GET /api/v1/teaching-tips?action_type=forehand_topspin&tech_phase=contact&source_type=human
```

**查询参数**
| 参数 | 类型 | 说明 |
|------|------|------|
| action_type | string | 可选，过滤动作类型 |
| tech_phase | string | 可选，过滤技术阶段 |
| source_type | string | 可选，'auto' 或 'human' |
| task_id | uuid | 可选，过滤来源任务 |

**响应 200**
```json
{
  "total": 5,
  "items": [
    {
      "id": "uuid",
      "task_id": "uuid",
      "action_type": "forehand_topspin",
      "tech_phase": "contact",
      "tip_text": "击球瞬间手腕要有爆发性摩擦，不是推送",
      "confidence": 0.92,
      "source_type": "auto",
      "original_text": null,
      "created_at": "2026-04-20T14:00:00Z",
      "updated_at": "2026-04-20T14:00:00Z"
    }
  ]
}
```

---

### 3. PATCH /teaching-tips/{id}
人工编辑或删除教学建议。编辑后 source_type 变更为 'human'，原内容保存到 original_text。

**请求**
```
PATCH /api/v1/teaching-tips/{id}
Content-Type: application/json

{
  "tip_text": "修改后的建议文字",
  "tech_phase": "contact"
}
```

**响应 200**：返回更新后的完整 TeachingTip 对象（同 GET items 结构）

**错误**
- `404 TIP_NOT_FOUND`：条目不存在

---

### 4. DELETE /teaching-tips/{id}
删除教学建议条目（物理删除）。

**响应 204**：No Content

---

## CoachingAdvice 响应变更

`GET /tasks/{task_id}/result` 中 `coaching_advice` 条目新增字段：

```json
{
  "advice_id": "uuid",
  "dimension": "elbow_angle",
  "deviation_description": "肘部角度偏低 15°",
  "improvement_target": "将肘部角度控制在 110°～145° 范围内",
  "improvement_method": "练习正手攻球时注意保持肘部抬起...",
  "teaching_tips": [
    {
      "tip_text": "击球时肘部不要夹紧，保持自然张开",
      "tech_phase": "contact",
      "source_type": "human"
    }
  ],
  "impact_score": 0.85,
  "reliability_level": "high",
  "reliability_note": null
}
```

新增字段 `teaching_tips`（数组，可为空）：按 action_type 匹配，最多 3 条，human 优先。
