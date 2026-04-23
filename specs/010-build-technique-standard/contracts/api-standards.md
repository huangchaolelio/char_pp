# API 契约: 技术标准服务

**路由前缀**: `/api/v1/standards`
**功能**: 010-build-technique-standard

---

## 端点列表

### POST `/api/v1/standards/build`

触发单项或全量技术标准构建。

**请求体**:
```json
{
  "tech_category": "forehand_topspin",  // 可选，指定单技术；省略则触发全量构建
}
```

**响应 202 Accepted**（异步任务）:
```json
{
  "task_id": "build-20260422-001",
  "mode": "single",                    // "single" | "batch"
  "tech_category": "forehand_topspin", // 单技术时返回，全量时省略
  "status": "queued"
}
```

**响应 422 Unprocessable Entity**（tech_category 不在 21 类中）:
```json
{
  "error": "invalid_tech_category",
  "detail": "forehand_xyz is not a valid tech category"
}
```

---

### GET `/api/v1/standards/build/{task_id}`

> **N/A — 同步实现决策 (T026)**
>
> POST /build 采用同步方式执行，结果直接包含在响应体中（无需轮询）。
> 因此 GET /build/{task_id} 端点不实现。
> 原始契约中的 `task_id` 字段仍保留在响应中（UUID格式），但仅作为幂等性标识符使用，不支持状态查询。

---

### GET `/api/v1/standards/{tech_category}`

查询指定技术类别的最新 active 标准。

**路径参数**: `tech_category` — 21 类技术 ID 之一

**响应 200 OK**:
```json
{
  "tech_category": "forehand_topspin",
  "standard_id": 42,
  "version": 3,
  "source_quality": "multi_source",    // "multi_source" | "single_source"
  "coach_count": 5,
  "point_count": 47,
  "built_at": "2026-04-22T10:30:00Z",
  "dimensions": [
    {
      "dimension": "elbow_angle_at_contact",
      "ideal": 110.5,
      "min": 95.0,
      "max": 125.0,
      "unit": "°",
      "sample_count": 12,
      "coach_count": 4
    }
  ]
}
```

**响应 404 Not Found**（无 active 标准）:
```json
{
  "error": "standard_not_found",
  "detail": "No active standard for tech_category: forehand_topspin"
}
```

---

### GET `/api/v1/standards`

查询所有技术类别的标准摘要列表。

**查询参数**:
- `source_quality`: 可选，过滤 `multi_source` 或 `single_source`

**响应 200 OK**:
```json
{
  "standards": [
    {
      "tech_category": "forehand_topspin",
      "standard_id": 42,
      "version": 3,
      "source_quality": "multi_source",
      "coach_count": 5,
      "dimension_count": 8,
      "built_at": "2026-04-22T10:30:00Z"
    }
  ],
  "total": 18,
  "missing_categories": ["penhold_reverse", "unclassified", "general"]
}
```

---

## 错误码规范

| 错误码 | HTTP 状态 | 说明 |
|--------|-----------|------|
| `invalid_tech_category` | 422 | tech_category 不在 21 类枚举中 |
| `standard_not_found` | 404 | 该技术类别无 active 标准 |
| `build_task_not_found` | 404 | task_id 不存在 |
| `insufficient_coaches` | — | 构建结果中跳过原因，coach_count < 2 |
| `no_valid_points` | — | 构建结果中跳过原因，无满足条件的技术点 |
