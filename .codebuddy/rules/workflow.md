---
alwaysApply: true
---

# LLM 调用优先级

Venus Proxy（`VENUS_TOKEN`）优先 → OpenAI fallback。两者共用 OpenAI 接口格式，通过 `src/services/llm_client.py` 统一封装。

# 姿态估计降级策略

`POSE_BACKEND=auto` 时：YOLOv8 GPU（首选，COCO AP≈50.4）→ MediaPipe CPU（fallback，33 关键点）

# 服务启动

```bash
# 启动 API（无热重载，代码变更后必须重启）
pkill -f "uvicorn src.api.main" && setsid /opt/conda/envs/coaching/bin/uvicorn src.api.main:app --host 0.0.0.0 --port 8080 >> /tmp/uvicorn.log 2>&1 &

# Feature 013 — 四队列多 Worker 架构，一队列一 Worker 物理隔离
# Worker 1：分类队列（并发=1）
setsid /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app worker --loglevel=info --concurrency=1 -Q classification -n classification_worker@%h >> /tmp/celery_classification_worker.log 2>&1 &

# Worker 2：知识库提取队列（并发=2）
setsid /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app worker --loglevel=info --concurrency=2 -Q kb_extraction -n kb_extraction_worker@%h >> /tmp/celery_kb_extraction_worker.log 2>&1 &

# Worker 3：运动员诊断队列（并发=2）
setsid /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app worker --loglevel=info --concurrency=2 -Q diagnosis -n diagnosis_worker@%h >> /tmp/celery_diagnosis_worker.log 2>&1 &

# Worker 4：默认队列（COS 扫描 + 清理，并发=1）
setsid /opt/conda/envs/coaching/bin/celery -A src.workers.celery_app worker --loglevel=info --concurrency=1 -Q default -n default_worker@%h >> /tmp/celery_default_worker.log 2>&1 &
```

# Celery 任务

- `classify_video`（`src.workers.classification_task`）：单条教练视频 → tech_category，静态路由到 `classification` 队列
- `extract_kb`（`src.workers.kb_extraction_task`）：已分类视频 → 知识库条目，静态路由到 `kb_extraction` 队列
- `diagnose_athlete`（`src.workers.athlete_diagnosis_task`）：运动员视频 → 偏差+建议，静态路由到 `diagnosis` 队列
- `scan_cos_videos`（`src.workers.classification_task`）：COS 全量扫描，静态路由到 `default` 队列
- `cleanup_expired_tasks`（`src.workers.housekeeping_task`）：周期性清理过期任务，beat 驱动，`default` 队列

# 队列说明

| 队列 | Worker | 并发 | 默认容量 | 任务来源 |
|------|--------|------|---------|---------|
| `classification` | classification_worker | 1 | 5 | `classify_video`（单条分类） |
| `kb_extraction` | kb_extraction_worker | 2 | 50 | `extract_kb`（需 tech_category 非空） |
| `diagnosis` | diagnosis_worker | 2 | 20 | `diagnose_athlete` |
| `default` | default_worker | 1 | — | `scan_cos_videos` + `cleanup_expired_tasks` |

> 通道容量/并发可在 `task_channel_configs` 表中热更新（PATCH `/api/v1/admin/channels/{task_type}`），配置 30 秒内生效。

# 测试运行

```bash
# 单元测试
/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/unit/ -v

# 集成测试（需要 PostgreSQL + Redis 服务）
/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/integration/ -v

# 全量
/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/ -v
```

# 数据库迁移

```bash
alembic upgrade head                           # 应用最新迁移
alembic revision --autogenerate -m "描述"      # 生成新迁移
```
