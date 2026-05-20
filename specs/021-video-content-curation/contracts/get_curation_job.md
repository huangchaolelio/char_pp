# Contract — `GET /api/v1/curation-jobs/{id}`

查询单个清洗作业的视频级摘要 + 逐分段判定。Feature-021。

---

## 请求

```http
GET /api/v1/curation-jobs/9001 HTTP/1.1
```

| 路径参数 | 类型 | 说明 |
|---------|-----|------|
| `id` | int | `video_curation_jobs.id` |

| Query 参数 | 类型 | 说明 |
|-----------|-----|------|
| `include_segments` | bool | 默认 true。false 时 `segments` 字段为空数组 |

---

## 响应

### 成功

```json
{
  "success": true,
  "data": {
    "job_id": 9001,
    "cos_object_key": "charhuang/tt_video/.../coach_a/forehand_topspin.mp4",
    "coach_video_classification_id": 1234,
    "preprocessing_job_id": 5678,
    "curation_rubric_version": "v1",
    "status": "success",
    "error_code": null,
    "error_message": null,
    "summary": {
      "total_segment_count": 20,
      "accepted_segment_count": 14,
      "rejected_segment_count": 5,
      "uncertain_segment_count": 1,
      "total_duration_seconds": 3600.0,
      "accepted_duration_seconds": 2520.0,
      "accepted_duration_ratio": 0.7,
      "low_quality": false,
      "audio_unavailable": false,
      "short_video": false,
      "has_overrides": true,
      "kb_stale_after_override": false
    },
    "submitted_at": "2026-05-18T10:00:00+08:00",
    "started_at":   "2026-05-18T10:00:05+08:00",
    "completed_at": "2026-05-18T10:00:30+08:00",
    "segments": [
      {
        "segment_index": 0,
        "segment_start_ms": 0,
        "segment_end_ms": 180000,
        "auto_decision": "accepted",
        "validity_score": 0.85,
        "rejection_reason": null,
        "decision_source": "rule",
        "dim_breakdown": {
          "tech_keyword":    {"score": 0.85, "weight": 0.35, "matched": ["收小臂","重心转移"]},
          "non_teaching":    {"score": 1.0,  "weight": 0.25, "matched": []},
          "coach_dominance": {"score": 0.92, "weight": 0.20, "dominance_ratio": 0.78},
          "topic_relevance": {"score": 0.70, "weight": 0.15, "matched_keywords": ["弧圈"]},
          "duration_floor":  {"score": 1.0,  "weight": 0.05, "duration_seconds": 180}
        },
        "override_decision": null,
        "override_user": null,
        "override_reason": null,
        "overridden_at": null,
        "effective_decision": "accepted"
      }
      // ... 19 more segments
    ]
  }
}
```

### 错误

| HTTP | code | 触发场景 |
|------|------|---------|
| 404 | `RESOURCE_NOT_FOUND` | `id` 不存在 |
| 422 | `VALIDATION_FAILED` | `id` 非整数 |

---

## 行为契约

1. **只读**：本接口不写任何表
2. **`has_overrides` 派生**：service 层 `EXISTS (SELECT 1 FROM video_curation_segment_results WHERE job_id=$1 AND override_decision IS NOT NULL)`
3. **`kb_stale_after_override` 来源**：`coach_video_classifications.kb_stale_after_override`（service 同步维护，参见 data-model.md § 4 覆盖路径）
4. **大作业分段裁剪**：作业 segment 数 > 200 时（理论极限）`include_segments=true` 仍全量返回（不分页）— 单作业上限不会到这个量级，YAGNI

---

## 合约测试用例（`tests/contract/test_get_curation_job.py`）

1. ✅ 已 success 的作业 ⇒ 200 + 完整 summary + segments
2. ✅ `include_segments=false` ⇒ 200 + segments 数组为空
3. ✅ 含覆盖记录的作业 ⇒ `has_overrides=true`、`segments[i].override_decision` 非空
4. ✅ status=running 的作业 ⇒ 200 + summary 字段为 null（仅 status / submitted_at 有值）
5. ❌ id 不存在 ⇒ 404
6. ❌ id 非整数 ⇒ 422
