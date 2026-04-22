# API 契约: 教练管理与校准接口

**功能**: 006-multi-coach-kb | **日期**: 2026-04-21
**基础路径**: `/api/v1`

---

## 教练管理 API

### POST /coaches — 创建教练

**请求体**:
```json
{
  "name": "张教练",      // 必填，全局唯一
  "bio": "国家队主教练"   // 可选
}
```

**响应 201**:
```json
{
  "id": "uuid",
  "name": "张教练",
  "bio": "国家队主教练",
  "is_active": true,
  "created_at": "2026-04-21T10:00:00Z"
}
```

**响应 409**（姓名冲突）:
```json
{"detail": "Coach with name '张教练' already exists"}
```

---

### GET /coaches — 查询教练列表

**查询参数**:
- `include_inactive` (bool, default=false): 是否包含软删除的教练

**响应 200**:
```json
[
  {"id": "uuid", "name": "张教练", "bio": "...", "is_active": true, "created_at": "..."},
  ...
]
```

---

### GET /coaches/{coach_id} — 查询单个教练

**响应 200**: 同上单条
**响应 404**: `{"detail": "Coach not found"}`

---

### PATCH /coaches/{coach_id} — 修改教练信息

**请求体**（所有字段可选）:
```json
{
  "name": "新名字",   // 可选，修改姓名
  "bio": "新简介"     // 可选，修改简介
}
```

**响应 200**: 返回更新后的完整教练对象
**响应 404**: 教练不存在
**响应 409**: 新姓名已被占用

---

### DELETE /coaches/{coach_id} — 软删除教练

**响应 204**: 无内容（软删除成功，is_active 置为 false）
**响应 404**: 教练不存在
**响应 409**: 教练已是软删除状态

---

## 任务教练关联 API

### PATCH /tasks/{task_id}/coach — 为任务指定教练

**请求体**:
```json
{
  "coach_id": "uuid"  // 传 null 可解除关联
}
```

**响应 200**:
```json
{
  "task_id": "uuid",
  "coach_id": "uuid",
  "coach_name": "张教练"
}
```

**响应 404**: 任务或教练不存在
**响应 422**: coach_id 对应的教练已软删除（is_active=false）

---

## 校准对比 API

### GET /calibration/tech-points — 技术参数多教练对比

**查询参数**:
- `action_type` (str, **必填**): 如 `forehand_topspin`
- `dimension` (str, **必填**): 如 `elbow_angle`

**响应 200**:
```json
{
  "action_type": "forehand_topspin",
  "dimension": "elbow_angle",
  "coaches": [
    {
      "coach_id": "uuid-A",
      "coach_name": "张教练",
      "param_min": 85.0,
      "param_ideal": 95.0,
      "param_max": 110.0,
      "unit": "°",
      "extraction_confidence": 0.92,
      "source_count": 3
    },
    {
      "coach_id": "uuid-B",
      "coach_name": "李教练",
      "param_min": 80.0,
      "param_ideal": 90.0,
      "param_max": 105.0,
      "unit": "°",
      "extraction_confidence": 0.88,
      "source_count": 1
    }
  ]
}
```

**响应 200（无数据）**: `{"action_type": "...", "dimension": "...", "coaches": []}`
**响应 422**: 缺少必填参数

---

### GET /calibration/teaching-tips — 教学建议多教练对比

**查询参数**:
- `action_type` (str, **必填**): 如 `forehand_topspin`
- `tech_phase` (str, **必填**): `preparation` | `contact` | `follow_through` | `footwork` | `general`

**响应 200**:
```json
{
  "action_type": "forehand_topspin",
  "tech_phase": "contact",
  "coaches": [
    {
      "coach_id": "uuid-A",
      "coach_name": "张教练",
      "tips": [
        "击球时肘部保持90度弯曲，发力点集中在前臂",
        "接触球的瞬间手腕快速内旋"
      ]
    },
    {
      "coach_id": "uuid-B",
      "coach_name": "李教练",
      "tips": [
        "接触球瞬间保持稳定，不要提前发力"
      ]
    }
  ]
}
```

**响应 200（无数据）**: `{"action_type": "...", "tech_phase": "...", "coaches": []}`
**响应 422**: 缺少必填参数或 tech_phase 值不合法

---

## 现有接口扩展

### GET /teaching-tips（扩展 coach_id 过滤）

新增可选查询参数：
- `coach_id` (UUID, optional): 过滤特定教练的建议；不传则返回所有教练

**过滤行为**:
- `coach_id` 有值 → 只返回关联任务 `coach_id` 匹配的建议
- `coach_id` 为空 → 返回全部建议（含无教练关联的历史数据）
- 不存在的 `coach_id` → 返回空列表（不报错）

每条返回数据新增 `coach_id` 和 `coach_name` 字段（历史数据返回 null）。
