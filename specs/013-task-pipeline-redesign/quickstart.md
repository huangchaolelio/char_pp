# 快速验证指南：任务管道重构

**目标**: 通过一组端到端命令验证新管道的 5 个用户故事均能独立工作。
**前置**: 已执行 Alembic 0012 迁移，已启动 4 个 Worker 进程（classification / kb_extraction / diagnosis / default），API 服务在 8080 端口。
**日期**: 2026-04-24

---

## 准备：执行数据重置（US4）

```bash
# 前置：确保 .env 中已配置 ADMIN_RESET_TOKEN
curl -X POST http://localhost:8080/api/v1/admin/reset-task-pipeline \
  -H "Content-Type: application/json" \
  -d '{
    "confirmation_token": "<your-ADMIN_RESET_TOKEN>",
    "dry_run": false
  }'
```

**预期**:
- HTTP 200
- `deleted_counts.analysis_tasks = 2589`（旧记录全清）
- `preserved_counts.coach_video_classifications = 1015`（保留）
- `duration_ms < 60000`（SC-005：1 分钟内）

---

## 验证 US1：单条任务提交即时处理

### 步骤 1：观察 kb_extraction 通道初始空闲
```bash
curl http://localhost:8080/api/v1/task-channels/kb_extraction
# 预期：current_pending=0, current_processing=0, remaining_slots=50
```

### 步骤 2：先为目标视频提交分类任务（前置条件）
```bash
curl -X POST http://localhost:8080/api/v1/tasks/classification \
  -H "Content-Type: application/json" \
  -d '{"cos_object_key": "charhuang/tt_video/乒乓球合集【较新】/张继科/demo.mp4"}'
# 等待完成
```

### 步骤 3：提交 kb_extraction 任务
```bash
SUBMIT_AT=$(date +%s%3N)
curl -X POST http://localhost:8080/api/v1/tasks/kb-extraction \
  -H "Content-Type: application/json" \
  -d '{"cos_object_key": "charhuang/tt_video/乒乓球合集【较新】/张继科/demo.mp4"}'
# 记录响应中 task_id
```

### 步骤 4：循环查询任务状态
```bash
for i in 1 2 3 4 5; do
  sleep 1
  curl -s http://localhost:8080/api/v1/tasks/${task_id} | jq '.status, .started_at'
done
```

**预期**: 5 秒内 `status` 从 `pending` 转为 `processing`（SC-001 P95≤5s）

---

## 验证 US2：批量限流与上限保护

### 场景 A：超出通道容量（部分拒绝）
```bash
# kb_extraction 容量 50，先用 50 条占满
for i in $(seq 1 50); do
  curl -s -X POST http://localhost:8080/api/v1/tasks/kb-extraction \
    -H "Content-Type: application/json" \
    -d "{\"cos_object_key\": \"fill/video_${i}.mp4\"}" > /dev/null
done

# 再批量提交 5 条
curl -X POST http://localhost:8080/api/v1/tasks/kb-extraction/batch \
  -H "Content-Type: application/json" \
  -d '{"items":[
    {"cos_object_key":"extra1.mp4"},
    {"cos_object_key":"extra2.mp4"},
    {"cos_object_key":"extra3.mp4"},
    {"cos_object_key":"extra4.mp4"},
    {"cos_object_key":"extra5.mp4"}
  ]}'
```

**预期**:
- HTTP 200，`accepted_count=0, rejected_count=5`
- 每个 `items[i].rejection_code = "QUEUE_FULL"`

### 场景 B：超过 BATCH_MAX_SIZE（整体拒绝）
```bash
# 构造 101 条 items
items_json=$(python3 -c "import json; print(json.dumps([{'cos_object_key': f'k_{i}.mp4'} for i in range(101)]))")

curl -X POST http://localhost:8080/api/v1/tasks/kb-extraction/batch \
  -H "Content-Type: application/json" \
  -d "{\"items\": ${items_json}}"
```

**预期**: HTTP 400，body 中包含 `BATCH_TOO_LARGE`

---

## 验证 US3：任务类型解耦

### 步骤 1：停止 kb_extraction Worker
```bash
# 找到 kb_worker 进程
pgrep -f "kb_worker@" | xargs kill
```

### 步骤 2：提交 classification 与 diagnosis 任务
```bash
# classification 任务
curl -X POST http://localhost:8080/api/v1/tasks/classification \
  -H "Content-Type: application/json" \
  -d '{"cos_object_key": "some/video.mp4"}'

# diagnosis 任务
curl -X POST http://localhost:8080/api/v1/tasks/diagnosis \
  -H "Content-Type: application/json" \
  -d '{
    "video_storage_uri": "s3://test/athlete.mp4",
    "knowledge_base_version": "v1.0.0"
  }'
```

**预期**: 两类任务均在 5 秒内进入 processing（kb_extraction Worker 停止不影响其他类型）

### 步骤 3：观察 kb_extraction 通道仍在 pending
```bash
curl http://localhost:8080/api/v1/task-channels/kb_extraction
# 预期：current_pending 保持、current_processing=0（因 worker 已停）
```

### 步骤 4：重启 kb_extraction Worker，验证 pending 任务恢复处理
```bash
setsid /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app worker \
  -Q kb_extraction -n kb_worker@%h -c 2 >> /tmp/celery_kb_worker.log 2>&1 &
```

---

## 验证 US4：数据重置的选择性清理

### 步骤 1：先记录核心资产行数
```bash
psql "$DATABASE_URL" -c "SELECT
  (SELECT COUNT(*) FROM coaches) AS coaches,
  (SELECT COUNT(*) FROM coach_video_classifications) AS classifications,
  (SELECT COUNT(*) FROM tech_knowledge_bases WHERE is_draft=false) AS published_kb;"
```

### 步骤 2：执行重置
```bash
curl -X POST http://localhost:8080/api/v1/admin/reset-task-pipeline \
  -d '{"confirmation_token": "<token>"}' -H "Content-Type: application/json"
```

### 步骤 3：对比核心资产保持不变
```bash
psql "$DATABASE_URL" -c "SELECT
  (SELECT COUNT(*) FROM analysis_tasks) AS tasks,
  (SELECT COUNT(*) FROM coaches) AS coaches,
  (SELECT COUNT(*) FROM coach_video_classifications) AS classifications;"
```

**预期**: `tasks=0`、其他计数与步骤 1 相等

---

## 验证 US5：并发吞吐最大化

### 步骤 1：三类任务同时提交
```bash
# 同时提交 10 条 kb_extraction + 5 条 classification + 5 条 diagnosis
# （脚本示例）
python3 /tmp/concurrent_submit.py
```

### 步骤 2：立即查看三通道 processing 数
```bash
curl http://localhost:8080/api/v1/task-channels
```

**预期**: `current_processing` 严格等于各通道配置的 `concurrency`（分类 1、知识库 2、诊断 2），总共 5 条同时 processing

---

## 验证边界：幂等拒绝

```bash
# 提交一次
curl -X POST http://localhost:8080/api/v1/tasks/classification \
  -d '{"cos_object_key": "duplicate.mp4"}' -H "Content-Type: application/json"
# 立即再次提交同一 key
curl -X POST http://localhost:8080/api/v1/tasks/classification \
  -d '{"cos_object_key": "duplicate.mp4"}' -H "Content-Type: application/json"
```

**预期**: 第二次 HTTP 200，但 `items[0].rejection_code="DUPLICATE_TASK"`

### 用 force 覆盖已完成任务
```bash
# 等第一个任务成功后
curl -X POST http://localhost:8080/api/v1/tasks/classification \
  -d '{"cos_object_key": "duplicate.mp4", "force": true}' -H "Content-Type: application/json"
```

**预期**: 接受并分配新 task_id

---

## 验证边界：kb_extraction 前置分类检查

```bash
# 对一个从未分类的视频直接提交 kb 任务
curl -X POST http://localhost:8080/api/v1/tasks/kb-extraction \
  -d '{"cos_object_key": "unclassified_video.mp4"}' -H "Content-Type: application/json"
```

**预期**: HTTP 400，错误码 `CLASSIFICATION_REQUIRED`

---

## 验证边界：Orphan 任务回收

### 步骤 1：模拟 Worker 崩溃
```bash
# 启动任务后立即 -9 杀掉 worker
curl -X POST http://localhost:8080/api/v1/tasks/diagnosis -d '...' &
sleep 5
pgrep -f "diagnosis_worker@" | xargs kill -9
```

### 步骤 2：查询 DB 中该任务状态（应为 processing）
```bash
psql "$DATABASE_URL" -c "SELECT id, status, started_at FROM analysis_tasks WHERE task_type='athlete_diagnosis' ORDER BY created_at DESC LIMIT 1;"
```

### 步骤 3：等待 840 秒后重启 Worker
```bash
sleep 840
setsid celery -A src.workers.celery_app worker -Q diagnosis -n diagnosis_worker@%h -c 2 &
```

### 步骤 4：再次查询状态
**预期**: `status='failed'`，`error_message='orphan recovered on worker restart'`

---

## 验收清单

| 场景 | 对应需求 | 验证方式 |
|------|---------|---------|
| 单条任务 5 秒内进入处理 | US1 / SC-001 | 上述 US1 步骤 4 |
| 通道满后拒绝并返回 QUEUE_FULL | US2 / FR-006 | US2 场景 A |
| 批量超上限整体拒绝 | US2 场景 4 / FR-007 | US2 场景 B |
| 单类 Worker 崩溃不影响其他类 | US3 / FR-003 | US3 步骤 2 |
| 重置保留核心资产 | US4 / FR-016 | US4 步骤 3 |
| 三类任务同时并发 | US5 / FR-012 | US5 步骤 2 |
| 幂等拒绝与 force 覆盖 | 边界 / FR-Q5 | 幂等章节 |
| kb 前置分类校验 | FR-004a | 分类校验章节 |
| Orphan 任务回收 | FR-014 | Orphan 章节 |
