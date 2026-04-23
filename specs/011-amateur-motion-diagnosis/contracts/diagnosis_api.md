# Feature 011 API 契约

业余选手动作诊断接口的完整请求/响应规范与错误目录。

---

## 接口概览

| 属性 | 值 |
|------|----|
| 方法 | POST |
| 路径 | `/api/v1/diagnosis` |
| Content-Type | `application/json` |
| 认证 | —（当前版本无认证要求） |

---

## 请求

### 请求体

```json
{
  "tech_category": "forehand_topspin",
  "video_path": "cos://bucket/path/to/video.mp4"
}
```

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `tech_category` | string | 是 | 动作类型，必须为下方枚举值之一 |
| `video_path` | string | 是 | 视频路径，COS URL 或绝对本地路径 |

### `tech_category` 合法枚举值

| 枚举值 | 含义 |
|--------|------|
| `forehand_topspin` | 正手上旋 |
| `backhand_topspin` | 反手上旋 |
| `forehand_push` | 正手推挡 |
| `backhand_push` | 反手推挡 |
| `forehand_flick` | 正手拨球 |
| `backhand_flick` | 反手拨球 |
| `forehand_loop_underspin` | 正手拉下旋 |
| `backhand_loop_underspin` | 反手拉下旋 |
| `forehand_smash` | 正手扣杀 |
| `backhand_smash` | 反手扣杀 |
| `forehand_block` | 正手挡球 |
| `backhand_block` | 反手挡球 |

### `video_path` 格式规则

- COS URL 格式：`cos://bucket-name/object/key.mp4`
- 本地路径格式：绝对路径，如 `/data/videos/session1.mp4`
- 不接受相对路径

---

## 成功响应

### HTTP 200

```json
{
  "report_id": "550e8400-e29b-41d4-a716-446655440000",
  "tech_category": "forehand_topspin",
  "standard_id": 1,
  "standard_version": 2,
  "overall_score": 85.0,
  "strengths": ["elbow_angle", "contact_timing"],
  "dimensions": [
    {
      "dimension": "elbow_angle",
      "measured_value": 92.3,
      "ideal_value": 95.0,
      "standard_min": 85.0,
      "standard_max": 105.0,
      "unit": "°",
      "score": 100.0,
      "deviation_level": "ok",
      "deviation_direction": "none",
      "improvement_advice": null
    },
    {
      "dimension": "swing_trajectory",
      "measured_value": 0.45,
      "ideal_value": 0.65,
      "standard_min": 0.55,
      "standard_max": 0.80,
      "unit": "ratio",
      "score": 42.0,
      "deviation_level": "significant",
      "deviation_direction": "below",
      "improvement_advice": "您的挥拍轨迹偏短（0.45），理想值为 0.65。建议增大引拍幅度..."
    }
  ],
  "created_at": "2026-04-23T10:00:00Z"
}
```

### 响应字段说明

**顶层字段**

| 字段 | 类型 | 说明 |
|------|------|------|
| `report_id` | string (UUID) | 诊断报告唯一 ID |
| `tech_category` | string | 与请求一致的动作类型 |
| `standard_id` | integer | 本次诊断所用技术标准的数据库 ID |
| `standard_version` | integer | 诊断时标准的版本号快照 |
| `overall_score` | float | 所有维度得分的均值，范围 [0, 100] |
| `strengths` | array\<string\> | 偏差等级为 ok 的维度名称列表；无优势项时为空数组 |
| `dimensions` | array\<DimensionResult\> | 每个评估维度的详细结果 |
| `created_at` | string (ISO 8601) | 报告创建时间，UTC 时区 |

**DimensionResult 字段**

| 字段 | 类型 | 说明 |
|------|------|------|
| `dimension` | string | 维度标识，如 `elbow_angle`、`swing_trajectory` |
| `measured_value` | float | 从视频提取的实测值 |
| `ideal_value` | float | 标准理想值 |
| `standard_min` | float | 标准下限 |
| `standard_max` | float | 标准上限 |
| `unit` | string \| null | 物理单位（`°`、`ratio` 等）；无量纲时为 null |
| `score` | float | 该维度得分，范围 [0, 100] |
| `deviation_level` | string | `"ok"` / `"slight"` / `"significant"` |
| `deviation_direction` | string \| null | `"above"` / `"below"` / `"none"`；ok 时为 `"none"` |
| `improvement_advice` | string \| null | LLM 生成的改进建议；deviation_level 为 ok 时为 null |

---

## 错误响应

### 错误结构

所有错误均通过 HTTP 状态码区分，响应体 `detail` 字段承载错误信息。

**业务错误**（404、400、500）`detail` 为对象：

```json
{
  "detail": {
    "error": "<error_code>",
    "detail": "<human-readable message>"
  }
}
```

**验证错误**（422）`detail` 为数组（FastAPI/Pydantic 标准格式）：

```json
{
  "detail": [
    {
      "loc": ["body", "<field_name>"],
      "msg": "<validation message>",
      "type": "<error_type>"
    }
  ]
}
```

---

### 错误目录

| HTTP 状态码 | error_code | 触发条件 | 示例响应 |
|-------------|-----------|---------|---------|
| 422 | — | `tech_category` 不是合法枚举值，或请求体缺少必填字段 | 见下方 422 示例 |
| 404 | `standard_not_found` | 指定 `tech_category` 没有处于 active 状态的技术标准 | 见下方 404 示例 |
| 400 | `extraction_failed` | 视频中未检测到有效动作片段，特征提取失败 | 见下方 400 示例 |
| 500 | `internal_error` | 其他未预期错误 | 见下方 500 示例 |

---

**422 — 请求参数无效**

```json
{
  "detail": [
    {
      "loc": ["body", "tech_category"],
      "msg": "value is not a valid enumeration member; permitted: 'forehand_topspin', ...",
      "type": "value_error.enum"
    }
  ]
}
```

**404 — 无活跃技术标准**

```json
{
  "detail": {
    "error": "standard_not_found",
    "detail": "No active standard for tech_category: forehand_topspin"
  }
}
```

**400 — 视频特征提取失败**

```json
{
  "detail": {
    "error": "extraction_failed",
    "detail": "No valid action segments detected in video"
  }
}
```

**500 — 服务内部错误**

```json
{
  "detail": {
    "error": "internal_error",
    "detail": "An unexpected error occurred during diagnosis."
  }
}
```

---

## 字段级验证规则

| 字段 | 规则 |
|------|------|
| `tech_category` | 必填；值必须在 12 个 ActionType 枚举值中，否则返回 422 |
| `video_path` | 必填；非空字符串；服务端不验证文件是否存在，提取失败时返回 400 |
| `overall_score` (响应) | 始终为 [0.0, 100.0]，由评分算法保证 |
| `score` (响应) | 始终为 [0.0, 100.0]，由评分算法保证 |
| `deviation_level` (响应) | 仅为 `ok` / `slight` / `significant` 之一 |
| `deviation_direction` (响应) | `ok` → `none`；`slight`/`significant` → `above` 或 `below` |
| `improvement_advice` (响应) | `deviation_level` 为 `ok` 时必须为 null；其余情况由 LLM 生成 |
