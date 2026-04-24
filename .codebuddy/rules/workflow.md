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
pkill -f "uvicorn src.api.main" && setsid uvicorn src.api.main:app --host 0.0.0.0 --port 8080 &

# 启动 Celery Worker
celery -A src.workers.celery_app worker --loglevel=info --concurrency=2
```

# Celery 任务

- `expert_video_task`：教练视频处理，11 步流水线，最长 6 分钟超时
- `athlete_video_task`：运动员视频处理
- `classification_task`：COS 扫描分类（`scan_cos_videos`），异步返回 task_id

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
