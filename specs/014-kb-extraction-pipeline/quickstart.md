# Quickstart: 知识库提取流水线化 (Feature-014)

**功能**: `014-kb-extraction-pipeline` | **日期**: 2026-04-24

## 目标

端到端验证 Feature-014 的 5 个用户故事，每步都有可观测产物，便于人工核查。

**前提条件**：
- PostgreSQL 中 Alembic 已升级至 `0013_kb_extraction_pipeline`
- 4 个 Celery Worker 按 Feature-013 规范启动（特别是 `kb_extraction` Worker 并发 2）
- `.env` 包含 Feature-013 所有必需配置 + `VENUS_TOKEN` 或 `OPENAI_API_KEY`
- 测试用教练视频已在 `coach_video_classifications` 表中有非 unclassified 的 `tech_category`

---

## Step 1: 提交一次新 KB 提取作业（US1 + US2）

```bash
curl -sS -X POST http://localhost:8080/api/v1/tasks/kb-extraction \
    -H 'Content-Type: application/json' \
    -d '{
        "cos_object_key": "charhuang/tt_video/乒乓球合集【较新】/马龙正手拉球/clip_01.mp4",
        "enable_audio_analysis": true,
        "audio_language": "zh",
        "force": false
    }' | tee /tmp/submit_response.json
```

**预期响应（202）**：

```json
{
    "task_id": "...",
    "job_id": "a1b2c3d4-...",
    "status": "pending",
    "cos_object_key": "...",
    "steps_created": 6,
    "estimated_completion_seconds": 600
}
```

**验证**：
- 响应包含 `job_id`（Feature-014 新增）
- `steps_created = 6`

---

## Step 2: 查询作业状态（US1 验证）

```bash
JOB_ID=$(jq -r .job_id /tmp/submit_response.json)

# 立即查询
curl -sS "http://localhost:8080/api/v1/extraction-jobs/$JOB_ID" | jq

# 5 秒后再查，观察 status 从 pending → running
sleep 5 && curl -sS "http://localhost:8080/api/v1/extraction-jobs/$JOB_ID" | jq
```

**验证**：
- 响应延迟 p95 ≤ 1s（SC-001）
- `steps` 数组长度 = 6
- 每个 step 有 `step_type` / `status` / `depends_on`
- `progress.total_steps = 6`，`progress.percent` 随时间增长
- `worker_hostname` 在 running 后填入

---

## Step 3: 验证并行执行（US3）

继续轮询作业状态并观察时间线：

```bash
# 每 10s 查一次，持续 10 分钟
while true; do
    curl -sS "http://localhost:8080/api/v1/extraction-jobs/$JOB_ID" | jq '.steps[] | {step_type, status, started_at, duration_ms}'
    sleep 10
done
```

**验证**：
- `pose_analysis` 和 `audio_transcription` 两个 step 的 `started_at` 差异 < 2 秒（说明 download_video 完成后立即并行启动）
- `visual_kb_extract` 和 `audio_kb_extract` 的 `started_at` 差异 < 2 秒（第二轮并行）
- `merge_kb` 的 `started_at` 不早于 `visual_kb_extract` 和 `audio_kb_extract` 的 `completed_at`

**并行节省验证**（SC-002）：
假设 `pose_analysis` 耗时 180s、`audio_transcription` 耗时 120s，两者并行总墙钟 ~180s；串行则是 300s。节省 (300-180)/300 ≈ 40% ≥ 30% ✅

---

## Step 4: 成功完成 + 冲突检查（US2）

作业成功后：

```bash
curl -sS "http://localhost:8080/api/v1/extraction-jobs/$JOB_ID" | jq '.status, .progress, .conflict_count'

# 查看最终 KB 条目（视觉 + 音频来源都有）
psql "$DATABASE_URL" -c "
SELECT source_type, COUNT(*) AS items_count
FROM tech_knowledge_bases
WHERE extraction_job_id = '$JOB_ID'
GROUP BY source_type;
"

# 若有冲突，查询冲突表
psql "$DATABASE_URL" -c "
SELECT dimension_name, visual_value, audio_value
FROM kb_conflicts
WHERE job_id = '$JOB_ID'
  AND superseded_by_job_id IS NULL
  AND resolved_at IS NULL;
"
```

**验证**：
- `status = success`
- 至少 1 条 `source_type = 'visual'` 和 1 条 `source_type = 'audio'` 的条目入正式 KB
- 冲突项（若有）在 `kb_conflicts` 表而非主 KB 表
- `coach_video_classifications.kb_extracted = TRUE`（被 merge_kb 翻转）

---

## Step 5: 模拟子任务失败 + 重跑（US4）

```bash
# 手动让 audio_transcription 步骤失败（模拟 Whisper 超时）
psql "$DATABASE_URL" -c "
UPDATE pipeline_steps
SET status='failed', error_message='simulated: whisper timeout', completed_at=NOW()
WHERE job_id='$JOB_ID' AND step_type='audio_transcription';

UPDATE pipeline_steps
SET status='skipped'
WHERE job_id='$JOB_ID' AND step_type IN ('audio_kb_extract', 'merge_kb');

UPDATE extraction_jobs SET status='failed', error_message='simulated failure' WHERE id='$JOB_ID';
"

# 发起重跑
curl -sS -X POST "http://localhost:8080/api/v1/extraction-jobs/$JOB_ID/rerun" -d '{}' -H 'Content-Type: application/json' | jq
```

**预期响应（202）**：

```json
{
    "job_id": "...",
    "status": "running",
    "reset_steps": ["audio_transcription", "audio_kb_extract", "merge_kb"]
}
```

**验证**：
- 只有 3 个子任务被重置（不含 `download_video`、`pose_analysis`、`visual_kb_extract`）
- `download_video.output_artifact_path` 仍指向原视频（磁盘上应存在）
- 重跑耗时显著短于首次（SC-005 ≤ 110%）

---

## Step 6: 已成功作业的 `force` 重跑（US1 + Q3 决策）

```bash
# 对已 success 的作业重复提交，默认拒绝
curl -sS -X POST http://localhost:8080/api/v1/tasks/kb-extraction \
    -H 'Content-Type: application/json' \
    -d '{
        "cos_object_key": "charhuang/tt_video/乒乓球合集【较新】/马龙正手拉球/clip_01.mp4",
        "force": false
    }' | jq '.error'

# 预期：409 DUPLICATE_TASK + existing_job_id 指向老作业

# 强制覆盖
NEW_RESP=$(curl -sS -X POST http://localhost:8080/api/v1/tasks/kb-extraction \
    -H 'Content-Type: application/json' \
    -d '{"cos_object_key": "...", "force": true}')
NEW_JOB_ID=$(echo "$NEW_RESP" | jq -r .job_id)

# 验证老作业被 supersede
psql "$DATABASE_URL" -c "
SELECT id, status, superseded_by_job_id FROM extraction_jobs
WHERE cos_object_key = '...';
"

# 验证旧冲突项被隐藏
psql "$DATABASE_URL" -c "
SELECT id, superseded_by_job_id FROM kb_conflicts
WHERE job_id = '$JOB_ID';
"
```

**验证**：
- 老作业 `superseded_by_job_id = $NEW_JOB_ID`
- 旧冲突项全部 `superseded_by_job_id = $NEW_JOB_ID`
- 待审核冲突查询（`WHERE resolved_at IS NULL AND superseded_by_job_id IS NULL`）不返回旧条目

---

## Step 7: 通道兼容验证（US5）

在提交 2 个作业让通道运行中（concurrency=2）时：

```bash
# 查通道快照
curl -sS http://localhost:8080/api/v1/task-channels/kb_extraction | jq
```

**验证**：
- `current_processing = 2`（作业数，不是子任务数）
- `remaining_slots = queue_capacity - inflight_jobs`（与 Feature-013 SC-006 一致）

再提交第 3 个 KB 提取：应返回 `QUEUE_FULL`（当容量=2 时），或进入 pending 等待（当容量>2 时）。

---

## Step 8: 列表查询（US5 + FR-023）

```bash
curl -sS "http://localhost:8080/api/v1/extraction-jobs?page=1&page_size=10&status=success" | jq '.items[] | {job_id, cos_object_key, status, duration_ms, conflict_count}'
```

**验证**：
- 返回的 items 全部 `status=success`
- 分页字段正确（`total, page, page_size`）

---

## Step 9: 中间结果清理（FR-013 + Q5 决策）

```bash
# 查看磁盘占用
du -sh /tmp/coaching-advisor/jobs/

# 等 24h 或手动触发 housekeeping
# celery -A src.workers.celery_app call src.workers.housekeeping_task.cleanup_intermediate_artifacts

# 清理后 success 作业的本地目录应消失
ls /tmp/coaching-advisor/jobs/$JOB_ID/ 2>&1 | head
# → 预期报「No such file or directory」
```

---

## 回归验证

```bash
# Feature-014 全套测试
/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/contract/test_extraction_jobs_*.py tests/integration/test_pipeline_*.py tests/integration/test_conflict_merge.py tests/integration/test_force_overwrite.py tests/unit/test_pipeline_*.py tests/unit/test_kb_merger.py -v

# 全仓回归（确保 Feature-013 没被破坏）
/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/ --tb=no -q
```

**门控期望**：0 failed；Feature-013 既有用例全部通过。
