# API Contracts: 教练视频技术分类数据库 (Feature 008)

**Router prefix**: `/api/v1/classifications`

---

## POST /api/v1/classifications/scan

触发全量/增量扫描任务（异步，返回 task_id）。

### Request

```json
{
  "scan_mode": "full | incremental"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `scan_mode` | string | 是 | `full`=全量扫描（upsert），`incremental`=仅处理新文件 |

### Response 202 Accepted

```json
{
  "task_id": "uuid",
  "scan_mode": "full",
  "status": "pending"
}
```

### Response 400 Bad Request

```json
{
  "detail": "invalid scan_mode: must be 'full' or 'incremental'"
}
```

---

## GET /api/v1/classifications/scan/{task_id}

查询扫描任务进度。

### Response 200 OK

```json
{
  "task_id": "uuid",
  "status": "pending | running | success | failed",
  "scanned": 120,
  "inserted": 85,
  "updated": 30,
  "skipped": 5,
  "errors": 0,
  "elapsed_s": 12.4,
  "error_detail": null
}
```

### Response 404 Not Found

```json
{
  "detail": "scan task not found"
}
```

---

## GET /api/v1/classifications

按条件查询分类记录列表。

### Query Parameters

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `coach_name` | string | - | 按教练姓名过滤 |
| `tech_category` | string | - | 按主技术类别 ID 过滤 |
| `kb_extracted` | boolean | - | `false`=仅未提取，`true`=仅已提取 |
| `classification_source` | string | - | `rule` \| `llm` \| `manual` |
| `limit` | int | 50 | 每页条数，最大 200 |
| `offset` | int | 0 | 跳过条数 |

### Response 200 OK

```json
{
  "total": 320,
  "items": [
    {
      "id": "uuid",
      "coach_name": "孙浩泓",
      "course_series": "小孙专业乒乓球—全套正反手体系课程_33节",
      "cos_object_key": "charhuang/tt_video/.../22_正手下旋拉球解析.mp4",
      "filename": "22_正手下旋拉球解析.mp4",
      "tech_category": "forehand_topspin_backspin",
      "tech_tags": [],
      "raw_tech_desc": "正手下旋拉球",
      "classification_source": "rule",
      "confidence": 1.0,
      "duration_s": null,
      "kb_extracted": false,
      "created_at": "2026-04-21T10:00:00Z",
      "updated_at": "2026-04-21T10:00:00Z"
    }
  ]
}
```

---

## GET /api/v1/classifications/summary

按教练统计各技术类别视频数量。

### Query Parameters

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `coach_name` | string | - | 指定教练，空则返回所有教练 |

### Response 200 OK

```json
{
  "coaches": [
    {
      "coach_name": "孙浩泓",
      "total_videos": 120,
      "kb_extracted": 45,
      "tech_breakdown": [
        {"tech_category": "forehand_attack", "label": "正手攻球", "count": 12, "kb_extracted": 8},
        {"tech_category": "forehand_topspin_backspin", "label": "正手拉下旋", "count": 6, "kb_extracted": 0}
      ]
    }
  ]
}
```

---

## PATCH /api/v1/classifications/{id}

人工修正单条记录的技术分类（来源标记为 `manual`）。

### Request

```json
{
  "tech_category": "forehand_topspin_backspin",
  "tech_tags": ["forehand_topspin"]
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `tech_category` | string | 是 | 新的主技术类别 ID（需为有效枚举值） |
| `tech_tags` | string[] | 否 | 副技术标签，默认保留原值 |

### Response 200 OK

```json
{
  "id": "uuid",
  "tech_category": "forehand_topspin_backspin",
  "tech_tags": ["forehand_topspin"],
  "classification_source": "manual",
  "confidence": 1.0,
  "updated_at": "2026-04-21T11:00:00Z"
}
```

### Response 400 Bad Request

```json
{
  "detail": "invalid tech_category: 'xyz' is not a valid TechCategory"
}
```

### Response 404 Not Found

```json
{
  "detail": "classification record not found"
}
```
