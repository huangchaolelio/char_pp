# Contract — `PATCH /api/v1/curation-jobs/{id}/segments/{segment_index}`

人工覆盖单个分段的清洗判定。Feature-021。

---

## 请求

```http
PATCH /api/v1/curation-jobs/9001/segments/3 HTTP/1.1
Content-Type: application/json

{
  "override_decision": "accepted",
  "override_reason": "教练在 0:30 - 1:30 段虽然没有命中关键词，但完整演示了高吊弧圈的还原动作",
  "override_user": "ops_alice"
}
```

| 字段 | 类型 | 必填 | 说明 |
|-----|-----|-----|------|
| `override_decision` | string | 是 | `accepted` 或 `rejected`；传 `null` ⇒ 取消覆盖 |
| `override_reason` | string | `override_decision != null` 时必填 | 长度 ≤ 1000 |
| `override_user` | string | 是 | 操作员标识；现阶段为字符串字段，未来接入鉴权时改读上下文 |

| 路径参数 | 类型 | 说明 |
|---------|-----|------|
| `id` | int | `video_curation_jobs.id` |
| `segment_index` | int | 必须存在于 `video_curation_segment_results` |

---

## 响应

### 成功

```json
{
  "success": true,
  "data": {
    "job_id": 9001,
    "segment_index": 3,
    "auto_decision": "rejected",
    "override_decision": "accepted",
    "override_user": "ops_alice",
    "override_reason": "...",
    "overridden_at": "2026-05-18T11:20:00+08:00",
    "effective_decision": "accepted",
    "summary_recomputed": {
      "accepted_segment_count": 15,
      "rejected_segment_count": 4,
      "accepted_duration_ratio": 0.75,
      "low_quality": false,
      "kb_stale_after_override": true
    }
  }
}
```

### 错误

| HTTP | code | 触发场景 |
|------|------|---------|
| 404 | `RESOURCE_NOT_FOUND` | `job_id` 或 `segment_index` 不存在 |
| 422 | `VALIDATION_FAILED` | `override_decision` 非枚举 / `override_reason` 缺失 / 超长 |
| 409 | `INVALID_STATE` | 作业 `status != 'success'`（清洗未完成不允许覆盖）|

---

## 行为契约

1. **事务原子**：覆盖 + 视频级摘要重算 + `coach_video_classifications` 字段同步在同一事务（参见 `data-model.md § 4` 覆盖路径）
2. **取消覆盖**：`override_decision=null` ⇒ 同步把 `override_user` / `override_reason` / `overridden_at` 全部清空，`effective_decision` 自动回退到 `auto_decision`；`kb_stale_after_override` 重新评估
3. **不级联触发 KB 重抽**（spec Q5 决议）：仅写 `kb_stale_after_override=true` 提示位；运营按需 `POST /extraction-jobs/{id}/rerun` 显式重抽
4. **审计**：所有覆盖动作必落 `overridden_at` 时间戳；`override_user` 字段是必填，约束保证审计可追溯
5. **重复幂等**：相同请求重复 PATCH 不报错 — `overridden_at` 字段每次刷新

---

## 合约测试用例（`tests/contract/test_override_curation_segment.py`）

1. ✅ 把 rejected 段覆盖为 accepted ⇒ 200，effective_decision=accepted，summary 重算
2. ✅ 把 accepted 段覆盖为 rejected ⇒ 200，effective_decision=rejected，summary 重算
3. ✅ 取消覆盖（override_decision=null）⇒ 200，effective_decision 回退
4. ✅ 该视频已有 KB 抽取作业 ⇒ kb_stale_after_override=true
5. ❌ override_decision='foo' ⇒ 422
6. ❌ override_reason 缺失 ⇒ 422
7. ❌ job_id 不存在 ⇒ 404
8. ❌ segment_index 不存在 ⇒ 404
9. ❌ 作业 status=running ⇒ 409 INVALID_STATE
