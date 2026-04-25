# Quickstart: 视频预处理流水线手工验证

**目标**: 对 Feature-016 的 US1（P1 MVP）+ US2（P1）做一次手工端到端验证。

**前置条件**:
- PostgreSQL + Redis 服务可用
- COS 凭证已配置（`.env` 中 `COS_SECRET_ID / COS_SECRET_KEY / COS_BUCKET / COS_REGION`）
- 项目虚拟环境：`source /opt/conda/envs/coaching/bin/activate`
- 迁移已应用：`alembic upgrade head`（应用 0014）
- 5 个 Celery Worker 已启动（新增 preprocessing worker + 原 4 个）

---

## 0. 启动 5 个 Worker + API

```bash
# API
pkill -f "uvicorn src.api.main" && setsid /opt/conda/envs/coaching/bin/uvicorn \
  src.api.main:app --host 0.0.0.0 --port 8080 \
  >> /tmp/uvicorn.log 2>&1 &

# Worker 1: 分类队列
setsid /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app worker \
  --loglevel=info --concurrency=1 -Q classification \
  -n classification_worker@%h \
  >> /tmp/celery_classification_worker.log 2>&1 &

# Worker 2: KB 提取队列（threads pool，避免 torch CUDA OOM）
setsid /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app worker \
  --loglevel=info --concurrency=2 --pool=threads -Q kb_extraction \
  -n kb_extraction_worker@%h \
  >> /tmp/celery_kb_extraction_worker.log 2>&1 &

# Worker 3: 诊断队列
setsid /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app worker \
  --loglevel=info --concurrency=2 -Q diagnosis \
  -n diagnosis_worker@%h \
  >> /tmp/celery_diagnosis_worker.log 2>&1 &

# Worker 4: 默认队列
setsid /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app worker \
  --loglevel=info --concurrency=1 -Q default \
  -n default_worker@%h \
  >> /tmp/celery_default_worker.log 2>&1 &

# Worker 5: 预处理队列（新）
setsid /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app worker \
  --loglevel=info --concurrency=3 -Q preprocessing \
  -n preprocessing_worker@%h \
  >> /tmp/celery_preprocessing_worker.log 2>&1 &

# 确认 5 个 Worker 都就位
sleep 5
ps aux | grep -E "celery.*(-Q classification|-Q kb_extraction|-Q diagnosis|-Q default|-Q preprocessing)" | grep -v grep
```

---

## 1. US1 — 提交单条预处理任务

### 1.1 准备：选一个短视频（便于快速验证）

```bash
# 选一个已分类的教练视频（建议 5-10 分钟以触发分段）
COS_KEY=$(psql -Atc "SELECT cos_object_key FROM coach_video_classifications
                     WHERE tech_category='forehand_attack' AND duration_s BETWEEN 300 AND 900
                     LIMIT 1")
echo "Selected: $COS_KEY"
```

### 1.2 提交预处理任务

```bash
curl -X POST http://localhost:8080/api/v1/tasks/preprocessing \
  -H 'Content-Type: application/json' \
  -d "{
    \"cos_object_key\": \"$COS_KEY\",
    \"force\": false
  }" | tee /tmp/submit_response.json

JOB_ID=$(jq -r .job_id /tmp/submit_response.json)
echo "JOB_ID=$JOB_ID"
```

**预期**: 返回 `{"job_id":"...","status":"running","reused":false,...}`

### 1.3 轮询 job 状态

```bash
while true; do
  RESP=$(curl -sS http://localhost:8080/api/v1/video-preprocessing/$JOB_ID)
  STATUS=$(echo "$RESP" | jq -r .status)
  echo "[$(date +%T)] status=$STATUS"
  if [[ "$STATUS" == "success" || "$STATUS" == "failed" ]]; then
    echo "$RESP" | jq .
    break
  fi
  sleep 10
done
```

**预期**: 在 原视频时长 × 5 内 达到 `status='success'`；返回的 JSON 包含：
- `original_meta.fps/width/height/duration_ms/codec/size_bytes/has_audio`
- `target_standard.target_fps=30`、`target_short_side=720`、`segment_duration_s=180`
- `has_audio=true` 时 `audio.cos_object_key` 和 `audio.size_bytes` 非空
- `segments[]` 长度 = `ceil(duration_ms / 180000)`，按 `segment_index` 升序

### 1.4 验证 DB 记录

```bash
psql -c "SELECT job_id, status, segment_count, has_audio, duration_ms
         FROM video_preprocessing_jobs WHERE id='$JOB_ID';"

psql -c "SELECT segment_index, start_ms, end_ms, size_bytes
         FROM video_preprocessing_segments
         WHERE job_id='$JOB_ID' ORDER BY segment_index;"

psql -c "SELECT cos_object_key, preprocessed FROM coach_video_classifications
         WHERE cos_object_key='$COS_KEY';"
```

**预期**:
- `video_preprocessing_jobs` 有 1 行 status=success
- `video_preprocessing_segments` 有 N 行，(job_id, segment_index) 唯一、`end_ms > start_ms`
- `coach_video_classifications.preprocessed = true`

### 1.5 验证 COS 对象

```bash
# 用 coscli 或 AWS CLI（兼容 COS）验证
coscli ls "cos://$COS_BUCKET/preprocessed/$COS_KEY/jobs/$JOB_ID/" | head -20
```

**预期**: 目录下有 `audio.wav` + N 个 `seg_NNNN.mp4`，每段大小与 DB 中 `size_bytes` 一致

### 1.6 验证本地温缓存

```bash
ls -la /tmp/coaching-advisor/jobs/preprocessing/$JOB_ID/
```

**预期**: 本地 `preprocessing/{job_id}/` 目录包含 `audio.wav` + 所有 `seg_NNNN.mp4`，mtime 在过去几分钟内

---

## 2. US1 验收场景 2 — 短视频（不分段）

```bash
# 选一个 < 180 秒的视频
SHORT_KEY=$(psql -Atc "SELECT cos_object_key FROM coach_video_classifications
                       WHERE duration_s < 180 LIMIT 1")

curl -X POST http://localhost:8080/api/v1/tasks/preprocessing \
  -H 'Content-Type: application/json' \
  -d "{\"cos_object_key\": \"$SHORT_KEY\"}" | tee /tmp/short_submit.json

SHORT_JOB=$(jq -r .job_id /tmp/short_submit.json)
# 等 success
sleep 60
curl -sS http://localhost:8080/api/v1/video-preprocessing/$SHORT_JOB | jq .segment_count
```

**预期**: `segment_count=1`，`segments[]` 长度为 1

---

## 3. US1 验收场景 3 — 幂等（force=false 命中已有）

```bash
# 重复提交 1.2 的 cos_object_key
curl -X POST http://localhost:8080/api/v1/tasks/preprocessing \
  -H 'Content-Type: application/json' \
  -d "{\"cos_object_key\": \"$COS_KEY\", \"force\": false}" | jq .
```

**预期**: 返回 `reused=true`，`job_id` 与 1.2 返回一致（HTTP 200 而非 202）

---

## 3.5 US4 — 预处理通道热更新（批量吞吐调优）

Feature-013 的通道机制允许在不重启 Worker 的前提下热调并发与容量。预处理通道初始 `concurrency=3 / capacity=20`，批量场景下可临时上调以加速消化积压。

```bash
# 查看当前通道状态
curl -s http://localhost:8080/api/v1/task-channels | jq '.channels[] | select(.task_type=="preprocessing")'
# {
#   "task_type": "preprocessing",
#   "concurrency": 3,
#   "queue_capacity": 20,
#   "enabled": true,
#   ...
# }

# 把并发从 3 调到 5（需要 ADMIN_RESET_TOKEN）
curl -X PATCH http://localhost:8080/api/v1/admin/channels/preprocessing \
  -H "X-Admin-Token: $ADMIN_RESET_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"concurrency": 5, "queue_capacity": 50}'

# 30 秒内通道配置自动刷新，后续批量提交吞吐翻倍
```

**注意**: 并发上调受 Worker 进程 `--concurrency` 上限约束。若 Worker 启动时 `--concurrency=3`，即使通道配置为 5，同时跑的任务数仍为 3；需同步重启 Worker 才能真正提速。

---


```bash
OLD_JOB=$JOB_ID

curl -X POST http://localhost:8080/api/v1/tasks/preprocessing \
  -H 'Content-Type: application/json' \
  -d "{\"cos_object_key\": \"$COS_KEY\", \"force\": true}" | tee /tmp/force_submit.json

NEW_JOB=$(jq -r .job_id /tmp/force_submit.json)
echo "OLD=$OLD_JOB NEW=$NEW_JOB"

# 等新 job 完成
while true; do
  S=$(curl -sS http://localhost:8080/api/v1/video-preprocessing/$NEW_JOB | jq -r .status)
  [[ "$S" == "success" || "$S" == "failed" ]] && break
  sleep 10
done

# 验证旧 job 变 superseded
psql -c "SELECT id, status FROM video_preprocessing_jobs WHERE id IN ('$OLD_JOB','$NEW_JOB');"
```

**预期**:
- `$OLD_JOB` 行 `status='superseded'`
- `$NEW_JOB` 行 `status='success'`
- COS 上 `preprocessed/$COS_KEY/jobs/$OLD_JOB/` 已被清空

---

## 5. US2 — KB 提取消费预处理产物

### 5.1 对预处理完的视频提交 KB 提取

```bash
# 使用 $COS_KEY（已有 success 预处理）
curl -X POST http://localhost:8080/api/v1/tasks/kb-extraction \
  -H 'Content-Type: application/json' \
  -d "{
    \"cos_object_key\": \"$COS_KEY\",
    \"enable_audio_analysis\": true
  }" | tee /tmp/kb_submit.json

KB_JOB_ID=$(jq -r .job_id /tmp/kb_submit.json)
```

### 5.2 轮询直到成功

```bash
while true; do
  R=$(curl -sS http://localhost:8080/api/v1/extraction-jobs/$KB_JOB_ID)
  S=$(echo "$R" | jq -r .status)
  echo "[$(date +%T)] status=$S"
  [[ "$S" == "success" || "$S" == "failed" ]] && echo "$R" | jq . && break
  sleep 15
done
```

### 5.3 验证 executor 确实消费了预处理产物

```bash
psql -c "SELECT step_type, status, output_summary
         FROM pipeline_steps WHERE job_id='$KB_JOB_ID'
         ORDER BY step_type;"
```

**预期**:
- `download_video.output_summary` 包含 `segments_downloaded` / `local_cache_hits` / `video_preprocessing_job_id=$NEW_JOB`
- `pose_analysis.output_summary.segments_processed` 等于预处理分段数
- `audio_transcription.output_summary.audio_source='cos_preprocessed'`、`whisper_device='cpu'`

### 5.4 验证 rerun 完全复用（不重切、不重上传）

```bash
# rerun 同一 KB job
curl -X POST "http://localhost:8080/api/v1/extraction-jobs/$KB_JOB_ID/rerun" | jq .

# 等 rerun 完成
sleep 60

# 验证 video_preprocessing_jobs 没有新增
psql -c "SELECT count(*) FROM video_preprocessing_jobs WHERE cos_object_key='$COS_KEY';"
```

**预期**: preprocessing job 数量 = 2（OLD superseded + NEW success），**没有第 3 条**；rerun 的 pose_analysis `segments_processed` 与首次一致

### 5.5 验证内存峰值下降（手工观察）

在 5.1 开始时另一终端：

```bash
watch -n 1 'ps aux --sort=-rss | grep "celery.*kb_extraction" | grep -v grep | head -3'
```

**预期**: `pose_analysis` 执行期间单进程 RSS < 20 GB（远小于 Feature-015 烟测的 58 GB 边界）

---

## 6. US2 验收场景 3 — 分段缺失恢复

### 6.1 模拟 COS 分段丢失

```bash
# 删除第 2 段（index=1）COS 对象
SEG_KEY=$(psql -Atc "SELECT cos_object_key FROM video_preprocessing_segments
                     WHERE job_id='$NEW_JOB' AND segment_index=1")
coscli rm "cos://$COS_BUCKET/$SEG_KEY"
```

### 6.2 清掉本地温缓存（强制走 COS）

```bash
rm -f /tmp/coaching-advisor/jobs/preprocessing/$NEW_JOB/seg_0001.mp4
```

### 6.3 触发新 KB 提取

```bash
curl -X POST http://localhost:8080/api/v1/tasks/kb-extraction \
  -H 'Content-Type: application/json' \
  -d "{\"cos_object_key\": \"$COS_KEY\"}" | tee /tmp/kb_missing.json

MISSING_JOB=$(jq -r .job_id /tmp/kb_missing.json)
sleep 90

# 查询错误
psql -c "SELECT status, error_message FROM extraction_jobs WHERE id='$MISSING_JOB';"
```

**预期**: `status='failed'`，`error_message` 以 `SEGMENT_MISSING:` 开头

### 6.4 恢复：force=true 重新预处理

```bash
curl -X POST http://localhost:8080/api/v1/tasks/preprocessing \
  -H 'Content-Type: application/json' \
  -d "{\"cos_object_key\": \"$COS_KEY\", \"force\": true}" | jq .
```

**预期**: 新 job 成功后 COS 完整，再触发 KB 提取可成功

---

## 7. US3 — 元数据可观察性

直接调用：

```bash
curl -sS http://localhost:8080/api/v1/video-preprocessing/$NEW_JOB | jq .
```

核对响应字段与 `contracts/get_preprocessing_job.md` 完全一致。

---

## 8. 清理任务验证（可选，需等 1h 以上）

```bash
# 调整 TTL 到 0.01h（36 秒）快速验证
psql -c "-- 暂未内置运行时配置表；通过环境变量重启 worker 验证"
# 或直接等 24h TTL 自然到期后检查
ls /tmp/coaching-advisor/jobs/preprocessing/ 2>&1 | wc -l
```

**预期**: `cleanup_intermediate_artifacts` beat 每小时运行一次；超过 TTL 的 `preprocessing/{job_id}/` 目录被删除；COS 侧不受影响（`preprocessed/...` 对象仍在）

---

## 9. 失败场景快速验证

### 9.1 质量不达标视频

```bash
# 找一个低 fps 视频（或手工构造）提交
# 预期：status=failed, error_message="VIDEO_QUALITY_REJECTED: fps=..."
```

### 9.2 cos_object_key 不存在

```bash
curl -X POST http://localhost:8080/api/v1/tasks/preprocessing \
  -H 'Content-Type: application/json' \
  -d '{"cos_object_key": "does/not/exist.mp4"}'
```

**预期**: HTTP 400 `COS_KEY_NOT_CLASSIFIED`

### 9.3 批量超限

```bash
# 构造 > BATCH_MAX_SIZE 的 items
curl -X POST http://localhost:8080/api/v1/tasks/preprocessing/batch \
  -H 'Content-Type: application/json' \
  -d "{\"items\": $(python -c 'import json; print(json.dumps([{"cos_object_key":"x"} for _ in range(150)]))')}"
```

**预期**: HTTP 400 `BATCH_TOO_LARGE`

---

## 10. 停服务

```bash
pkill -f "celery.*-Q preprocessing"
pkill -f "celery.*-Q classification"
pkill -f "celery.*-Q kb_extraction"
pkill -f "celery.*-Q diagnosis"
pkill -f "celery.*-Q default"
pkill -f "uvicorn src.api.main"
```

---

## 成功标准检查清单

本 quickstart 走完后应确认以下 SC 项：

- [ ] **SC-001**: 10 分钟视频预处理成功，无 OOM / SIGKILL（步骤 1）
- [ ] **SC-002**: pose_analysis 单段 RSS < 原视频整体处理峰值的 50%（步骤 5.5）
- [ ] **SC-003**: rerun 耗时相比首次降低 ≥ 30%（步骤 5.4）
- [ ] **SC-004**: 预处理失败率 ≤ 5%（需批量 20 个视频另外评测，非本 quickstart 范围）
- [ ] **SC-005**: 分段时长误差 < 1 秒（核对 segments 表的 end_ms - start_ms）
- [ ] **SC-006**: 预处理耗时 ≤ 原视频时长 × 5（步骤 1.3 时间戳）
- [ ] **SC-007**: 100% 失败带结构化错误前缀（步骤 6、9 验证）
