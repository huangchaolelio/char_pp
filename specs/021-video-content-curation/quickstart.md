# Quickstart — Feature-021 内容清洗

**目标**：为开发 / 运维 / QA 三类角色提供"5 分钟跑通本 feature 端到端"的入口。

> 阅读前提：`charhuang_pp_cn` 项目本地已可启动（参见 `docs/environment-setup.md`），且 Feature-016 视频预处理流水线已落地（已分类的教练视频且已 success 预处理）。

---

## 1. 前置条件（一次性）

### 1.1 应用迁移

```bash
cd /data/charhuang/char_ai_coding/charhuang_pp_cn
alembic upgrade head        # 至 0020_video_content_curation
```

迁移会创建：

- `video_curation_jobs` / `video_curation_segment_results` 两张新表
- `coach_video_classifications` 增 3 列（`last_curation_job_id` / `low_quality` / `kb_stale_after_override`）
- `analysis_tasks.task_type` ENUM 增 `video_curation`

### 1.2 落地初版规范

```
src/config/curation_rubric/
├── schema.json           # 强制 schema
├── v1.yaml               # 初版规范
└── prompts/
    └── segment_decision_v1.md   # LLM 兜底 Prompt 模板
```

### 1.3 `.env` 新增

```bash
CURATION_JOB_TIMEOUT_SECONDS=600
CURATION_LLM_TIMEOUT_SECONDS=5
```

### 1.4 重启 API + Worker

```bash
pkill -f "uvicorn src.api.main" && \
  setsid /opt/conda/envs/coaching/bin/uvicorn src.api.main:app --host 0.0.0.0 --port 8080 \
    >> /tmp/uvicorn.log 2>&1 &

# default 队列 worker（同时跑 scan / housekeeping / curation；concurrency=1）
pkill -f "celery_default_worker" && \
  setsid /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app worker \
    --loglevel=info --concurrency=1 -Q default -n default_worker@%h \
    >> /tmp/celery_default_worker.log 2>&1 &
```

---

## 2. 端到端冒烟测试（开发场景）

### 2.1 选一条已分类已预处理的视频

```sql
SELECT id, cos_object_key, tech_category
  FROM coach_video_classifications
 WHERE preprocessed = true AND tech_category != 'unclassified'
 LIMIT 1;
-- 假设返回 id=1234
```

### 2.2 提交清洗任务

```bash
curl -X POST http://localhost:8080/api/v1/tasks/curation \
  -H 'Content-Type: application/json' \
  -d '{"coach_video_classification_id": 1234}'
```

期望响应：

```json
{
  "success": true,
  "data": {
    "job_id": 9001, "task_id": 99001,
    "curation_rubric_version": "v1",
    "status": "pending", "queued": true,
    "idempotent_short_circuit": false
  }
}
```

### 2.3 等任务完成（约 30 秒）

```bash
curl http://localhost:8080/api/v1/curation-jobs/9001
```

校验：

- `status="success"`
- `summary.total_segment_count == 视频分段数`
- `summary.accepted_segment_count + rejected_segment_count + uncertain_segment_count == total_segment_count`
- `summary.accepted_duration_ratio` ∈ [0, 1]
- `segments[i].effective_decision` 与 `auto_decision` 一致（无覆盖时）

### 2.4 触发 KB 抽取（验证强制门）

```bash
curl -X POST http://localhost:8080/api/v1/tasks/kb-extraction \
  -H 'Content-Type: application/json' \
  -d '{"coach_video_classification_id": 1234, "tech_category": "forehand_topspin"}'
```

期望：能正常入队（清洗已 success）。等作业完成后查询 `extraction_jobs.output_summary`：

```json
{
  "segments_processed": <accepted 段数>,
  "segments_skipped_by_curation": <rejected + uncertain 段数>,
  "curation_job_id": 9001,
  "curation_rubric_version": "v1"
}
```

### 2.5 反向验证 — 给一条新视频提 KB 抽取（应被拒绝）

```bash
# 选一条尚未跑过清洗的视频
curl -X POST http://localhost:8080/api/v1/tasks/kb-extraction \
  -H 'Content-Type: application/json' \
  -d '{"coach_video_classification_id": <未跑过清洗的 id>, "tech_category": "..."}'
```

期望响应（FR-010）：

```json
{
  "success": false,
  "error": {
    "code": "CURATION_REQUIRED",
    "message": "Video has not been curated; submit POST /tasks/curation first.",
    "details": {"coach_video_classification_id": ...}
  }
}
```

---

## 3. 人工覆盖场景（运营场景）

```bash
# 把 segment_index=3 从 rejected 覆盖为 accepted
curl -X PATCH http://localhost:8080/api/v1/curation-jobs/9001/segments/3 \
  -H 'Content-Type: application/json' \
  -d '{
    "override_decision": "accepted",
    "override_reason": "完整动作演示，仅缺关键词命中",
    "override_user": "ops_alice"
  }'
```

期望响应：

```json
{
  "success": true,
  "data": {
    "job_id": 9001, "segment_index": 3,
    "auto_decision": "rejected",
    "override_decision": "accepted",
    "effective_decision": "accepted",
    "summary_recomputed": {
      "accepted_segment_count": 15,
      "accepted_duration_ratio": 0.75,
      "kb_stale_after_override": true
    }
  }
}
```

随后查询 `coach_video_classifications` 应见 `kb_stale_after_override=true`：

```sql
SELECT id, kb_stale_after_override, last_curation_job_id
  FROM coach_video_classifications
 WHERE id = 1234;
```

如需让 KB 重新基于覆盖后的口径抽取，运营手动触发 rerun：

```bash
curl -X POST http://localhost:8080/api/v1/extraction-jobs/<extraction_job_id>/rerun
```

---

## 4. 应急回滚（运维 / SRE 场景）

### 4.1 清洗规则误伤导致 KB 读不到分段

**症状**：本批次 KB 抽取作业大量出现 `LOW_QUALITY_SKIP` 或 `output_summary.segments_processed` 远低于历史均值。

**应急动作**：

```bash
# 临时打开 bypass 门（30s TTL）
curl -X PATCH http://localhost:8080/api/v1/admin/channels/kb_extraction \
  -H "X-Admin-Token: <ADMIN_RESET_TOKEN>" \
  -H 'Content-Type: application/json' \
  -d '{"config_payload": {"bypass_curation_gate": true}}'

# 跑完积压批次后关闭 bypass
curl -X PATCH http://localhost:8080/api/v1/admin/channels/kb_extraction \
  -H "X-Admin-Token: <ADMIN_RESET_TOKEN>" \
  -H 'Content-Type: application/json' \
  -d '{"config_payload": {"bypass_curation_gate": false}}'

# 回滚规范文件版本（git revert + 重新部署）
cd /data/charhuang/char_ai_coding/charhuang_pp_cn
git revert <bad-rubric-commit>
# 重启 API 让规范缓存重新加载
```

bypass 命中会在 `extraction_jobs.output_summary.curation_bypass=true` 留痕，事后审计可定位（`business-workflow.md § 10` 已登记）。

### 4.2 清洗结果错误（单条视频）

```sql
-- 删除错误结果
DELETE FROM video_curation_jobs WHERE id = <bad_job_id>;
-- segment_results 通过 ON DELETE CASCADE 自动清理
```

```bash
# 重新提交清洗
curl -X POST http://localhost:8080/api/v1/tasks/curation \
  -H 'Content-Type: application/json' \
  -d '{"coach_video_classification_id": 1234, "force": true}'
```

---

## 5. 基准回归（QA / 算法负责人场景）

```bash
# 跑清洗 benchmark 脚本（首次执行会建立 baseline）
/opt/conda/envs/coaching/bin/python3.11 \
  scripts/run_curation_benchmark.py \
  --sample-set tests/data/curation_samples_v1/ \
  --output specs/021-video-content-curation/benchmark/v1_baseline.json
```

输出：

- `precision` / `recall`（对照人工标注）
- `llm_token_reduction`（清洗前 vs 清洗后 KB 抽取 token 量对比）
- `term_overlap_rate`（同 `tech_category` 多视频 KB 间技术术语重叠率）

期望（spec SC-001 / SC-002 / SC-003）：

- precision ≥ 0.85，recall ≥ 0.85
- llm_token_reduction ≥ 0.30
- term_overlap_rate 提升 ≥ 0.20

---

## 6. 本期"做了什么"速查表

| 文件 | 作用 |
|-----|-----|
| `src/config/curation_rubric/v1.yaml` | 初版规范（运营改 PR） |
| `src/config/curation_rubric/schema.json` | 规范 jsonschema 校验 |
| `src/services/curation/curation_service.py` | 清洗编排 |
| `src/services/curation/decision_engine.py` | 规则路 + LLM 兜底两层 |
| `src/services/curation/rubric_loader.py` | 规范加载 + 缓存 |
| `src/api/routers/curation_jobs.py` | 作业查询 + 覆盖接口 |
| `src/api/routers/curation_stats.py` | P3 聚合查询 |
| `src/api/routers/tasks.py` | 扩展 `/tasks/curation` + KB 抽取门 |
| `src/workers/curation_task.py` | Celery task |
| `src/db/migrations/versions/0020_video_content_curation.py` | 迁移 |

---

## 7. 常见问题

**Q：清洗失败会阻止 KB 抽取吗？**
A：会。失败 = 没有 `status='success'` 行 ⇒ KB 抽取拒绝（`CURATION_REQUIRED`）。重提清洗或运营 bypass 应急。

**Q：可不可以跳过清洗直接做 KB 抽取？**
A：常规流程禁止。仅运维持 `ADMIN_RESET_TOKEN` 通过 bypass 应急开关临时绕过；命中会留审计痕迹。

**Q：覆盖会自动让 KB 重抽吗？**
A：不会（spec Q5 决议）。监控暴露 `kb_stale_after_override=true` 提示，运营按需手工 rerun。

**Q：`v2.yaml` 上线后老作业会被覆盖吗？**
A：不会。每条 `video_curation_jobs` 行都持久化所用 `curation_rubric_version`；新作业用新版本，旧作业保持原版本号；P3 接口可对比两版差异。
