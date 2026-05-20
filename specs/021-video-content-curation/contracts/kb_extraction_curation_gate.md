# Contract 扩展 — `POST /api/v1/tasks/kb-extraction`（清洗强制门）

Feature-021 对既有 `POST /tasks/kb-extraction` 接口的**行为破坏性扩展**。本契约描述新行为；既有请求 / 响应字段保持兼容。

---

## 行为变更（FR-008 / FR-009 / FR-010）

KB 抽取在排队前增加 2 道前置门：

### 门 1：`CURATION_REQUIRED`（FR-010）

排队前必须存在该视频的 `video_curation_jobs.status='success'` 行（`force=false` 时取最新；`force=true` 时仍要求至少 1 行 success）。

```sql
SELECT 1 FROM video_curation_jobs
 WHERE coach_video_classification_id = $1
   AND status = 'success'
 ORDER BY completed_at DESC
 LIMIT 1
```

不满足 ⇒ 立即拒绝：

```http
HTTP/1.1 409 Conflict
{
  "success": false,
  "error": {
    "code": "CURATION_REQUIRED",
    "message": "Video has not been curated; submit POST /tasks/curation first.",
    "details": {"coach_video_classification_id": 1234}
  }
}
```

### 门 2：`LOW_QUALITY_SKIP`（FR-009）

通过门 1 后，读取该视频的 success 作业摘要：

- `accepted_duration_ratio == 0` ⇒ KB 抽取以**业务结果短路**完成（不入 LLM 路径），写入：
  ```
  extraction_jobs.status      = "success"          # 沿用约定
  extraction_jobs.error_code  = "LOW_QUALITY_SKIP"  # 业务结果信号
  extraction_jobs.output_summary = {
      "kb_items_count": 0,
      "segments_processed": 0,
      "segments_skipped_by_curation": <total>,
      "curation_job_id": <job_id>,
      "curation_rubric_version": "v1"
  }
  ```
  返回的 task 状态为 `success`，前端按 `extraction_jobs.error_code='LOW_QUALITY_SKIP'` 识别"业务跳过"。

- `0 < accepted_duration_ratio < 0.3` ⇒ 正常执行，但 `extraction_jobs.output_summary.curation_warning="low_quality"`
- `accepted_duration_ratio >= 0.3` ⇒ 正常执行（无 warning）

### DAG 内分段过滤（FR-008）

`audio_kb_extract` / `visual_kb_extract` 步骤拉取分段时增加 join 过滤：

```sql
SELECT vps.* FROM video_preprocessing_segments vps
JOIN video_curation_segment_results vcsr
  ON vcsr.segment_index = vps.segment_index
 AND vcsr.job_id = (SELECT last_curation_job_id FROM coach_video_classifications WHERE id = $1)
WHERE vps.preprocessing_job_id = $2
  AND vcsr.effective_decision = 'accepted'
ORDER BY vps.segment_index
```

被 rejected / uncertain 的分段不进入 LLM Prompt 拼装、不进入姿态聚合统计。

---

## 应急 bypass 开关

`task_channel_configs.kb_extraction.config_payload` JSONB 字段新增：

```json
{
  "bypass_curation_gate": false
}
```

`PATCH /api/v1/admin/channels/kb_extraction` 把该字段设为 `true` ⇒ 30 秒 TTL 内：

- 跳过门 1（不要求 `video_curation_jobs.status=success`）
- 跳过门 2（无视 `accepted_duration_ratio`）
- DAG 内回退到读全量 `video_preprocessing_segments`

`bypass=true` 下每次 KB 抽取入队都在 `extraction_jobs.output_summary.curation_bypass=true` 留痕，事后审计可定位。bypass 命中视为应急回滚剧本，登记到 `business-workflow.md § 10`（已扩展）。

---

## 合约测试用例（`tests/contract/test_kb_extraction_curation_gate.py`）

1. ❌ 视频无 success 清洗 ⇒ 409 `CURATION_REQUIRED`
2. ✅ 视频清洗 `accepted_duration_ratio=0` ⇒ 200，task 创建后 worker 短路完成；`extraction_jobs.error_code=LOW_QUALITY_SKIP`，`output_summary.kb_items_count=0`
3. ✅ 视频清洗 `accepted_duration_ratio=0.2` ⇒ 200，正常执行 + `output_summary.curation_warning="low_quality"`
4. ✅ 视频清洗 `accepted_duration_ratio=0.7` ⇒ 200，正常执行 + 无 warning
5. ✅ DAG 执行后 `output_summary.segments_processed = accepted 段数` 且**与 `video_curation_segment_results` 对账一致**（关键护栏）
6. ✅ `bypass_curation_gate=true` ⇒ 即使无清洗 success 行也允许入队，`output_summary.curation_bypass=true`
7. ✅ bypass 30 秒 TTL 过期后，重新走门 1 ⇒ 409
