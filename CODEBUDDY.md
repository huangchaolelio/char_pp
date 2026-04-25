# charhuang_pp_cn 开发指南

乒乓球 AI 智能教练系统 — 后端分析服务。最后更新：2026-04-24

> **规范体系**：本文件是入口速查手册；分模块细则在 `.codebuddy/rules/*.md`，按文件路径自动加载。章节末尾的"📖"链接指向更深入的规范。

---

## 项目概述

基于计算机视觉 + 语音识别 + LLM 的乒乓球教学分析平台。核心能力：
- 教练视频自动分析 → 提取技术要点 → 构建知识库
- 运动员视频诊断 → 偏差计算 → AI 改进建议
- COS 全量视频分类管理（1015 个视频，21 类技术）

---

## 技术栈

| 层次 | 技术 | 版本 |
|------|------|------|
| Web 框架 | FastAPI + uvicorn | 0.111+ / 0.29+ |
| 数据库 | PostgreSQL (asyncpg) | 2.0+ (SQLAlchemy async) |
| 异步任务 | Celery + Redis | 5.4+ |
| 姿态估计 | YOLOv8 (GPU 优先) / MediaPipe (CPU fallback) | 8.0+ / 0.10+ |
| 语音识别 | Whisper | 20231117 |
| LLM | Venus Proxy (优先) / OpenAI | GPT-4 级别 |
| 云存储 | 腾讯云 COS SDK | 1.9.30+ |
| Python | 3.11+ | 项目虚拟环境 |

---

## 项目结构

```text
charhuang_pp_cn/
├── src/
│   ├── api/
│   │   ├── main.py               # FastAPI 应用入口，端口 8080
│   │   ├── routers/              # 10 个路由模块（见 .codebuddy/rules/api.md）
│   │   └── schemas/              # 8 个 Pydantic 请求/响应模型
│   ├── models/                   # 22 个 SQLAlchemy ORM 模型
│   ├── services/                 # 26 个业务服务模块 + kb_extraction_pipeline/ 子包
│   ├── workers/
│   │   ├── celery_app.py
│   │   ├── classification_task.py    # 分类 + COS 扫描（Feature-013）
│   │   ├── kb_extraction_task.py     # DAG Orchestrator 入口（Feature-014）
│   │   ├── athlete_diagnosis_task.py # 运动员视频诊断（Feature-013）
│   │   ├── housekeeping_task.py      # 周期清理（过期任务 + 过期中间结果）
│   │   └── orphan_recovery.py        # Worker 启动 sweep 孤儿任务 + 孤儿 pipeline_steps
│   ├── db/
│   │   ├── session.py            # async_session_factory
│   │   └── migrations/           # Alembic 迁移（0001~0013）
│   ├── config/
│   │   ├── video_classification.yaml  # 12 教练规则 + 21 类技术规则
│   │   └── keywords/tech_hint_keywords.json
│   └── config.py                 # Pydantic Settings（全局配置入口）
├── config/
│   ├── coach_directory_map.json  # COS 目录名 → 教练姓名静态映射（20 条）
│   └── tech_classification_rules.json  # 21 类技术关键词规则（顺序敏感）
├── specs/                        # Feature 规范（001~014）
├── docs/
│   ├── architecture.md           # 技术架构文档
│   └── features.md               # 产品功能文档
├── tests/                        # unit / integration / contract
├── .codebuddy/
│   ├── rules/                    # 分模块开发规范（api/code-style/cos/db/file-org/tech/workflow）
│   └── skills/refresh-docs/      # 刷新文档 skill
├── pyproject.toml
├── alembic.ini
└── .env                          # 不提交到 git
```

📖 目录职责速查：[.codebuddy/rules/file-organization.md](.codebuddy/rules/file-organization.md)

---

## 常用命令

```bash
# Python 环境（必须用项目 3.11，不用系统默认 3.9）
source /opt/conda/envs/coaching/bin/activate
# 或直接指定：/opt/conda/envs/coaching/bin/python3.11

# 启动 API 服务（代码修改后必须重启，无热重载）
pkill -f "uvicorn src.api.main" && setsid /opt/conda/envs/coaching/bin/uvicorn src.api.main:app --host 0.0.0.0 --port 8080 >> /tmp/uvicorn.log 2>&1 &

# 启动 4 个 Celery Worker（物理隔离：classification / kb_extraction / diagnosis / default）
# → 详细启动命令见 .codebuddy/rules/workflow.md

# 数据库迁移
alembic upgrade head
alembic revision --autogenerate -m "描述"

# 运行测试
/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/ -v
/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/unit/ -v
/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/integration/ -v

# 安装依赖
/opt/conda/envs/coaching/bin/pip install -e ".[dev]"
```

📖 服务启动与 Celery 队列细节：[.codebuddy/rules/workflow.md](.codebuddy/rules/workflow.md)

---

## 核心配置

所有敏感配置通过 `.env` 注入，`src/config.py` 的 `Settings` 类读取：

| 配置项 | 说明 |
|--------|------|
| `DATABASE_URL` | PostgreSQL 连接串 |
| `REDIS_URL` | Redis 地址（默认 localhost:6379/0）|
| `COS_SECRET_ID/KEY` | 腾讯云 COS 凭证 |
| `COS_BUCKET` / `COS_REGION` | COS 存储桶 |
| `COS_VIDEO_PREFIX` | 旧路径（孙浩泓120集，Feature-001 遗留）|
| `COS_VIDEO_ALL_COCAH` | 全量教练视频根路径（`charhuang/tt_video/乒乓球合集【较新】/`）|
| `VENUS_TOKEN` / `VENUS_BASE_URL` | Venus Proxy（LLM 优先级高于 OpenAI）|
| `OPENAI_API_KEY` / `OPENAI_MODEL` | OpenAI fallback |
| `WHISPER_MODEL` | 默认 `small`（中文）|
| `POSE_BACKEND` | `auto`（YOLOv8 GPU → MediaPipe CPU）|
| `ADMIN_RESET_TOKEN` | 管理员重置 / 通道热更新 token（Feature-013）|
| `BATCH_MAX_SIZE` | 批量提交单次上限，默认 100（Feature-013）|
| `ORPHAN_TASK_TIMEOUT_SECONDS` | 孤儿任务判定阈值，默认 840（Feature-013）|
| `EXTRACTION_JOB_TIMEOUT_SECONDS` | KB 提取作业级超时，默认 2700（Feature-014）|
| `EXTRACTION_STEP_TIMEOUT_SECONDS` | KB 提取单步超时，默认 600（Feature-014）|
| `EXTRACTION_ARTIFACT_ROOT` | Worker 本地中间文件根目录，默认 `/tmp/coaching-advisor/jobs`（Feature-014）|
| `EXTRACTION_SUCCESS_RETENTION_HOURS` | 成功作业中间结果保留，默认 24（Feature-014）|
| `EXTRACTION_FAILED_RETENTION_HOURS` | 失败作业中间结果保留，默认 168（7 天，Feature-014）|

📖 COS 存储与教练-目录映射：[.codebuddy/rules/cos-storage.md](.codebuddy/rules/cos-storage.md)

---

## 代码风格（速览）

- **类型注解**：`X | None`（Python 3.10+ union），禁止 `Optional[X]`
- **异步优先**：DB 操作全部 `async/await`，禁止同步 session
- **Pydantic v2**：`model_config = ConfigDict(...)`，禁止 v1 的 `class Config`
- **分层**：业务逻辑只放 `src/services/`；路由层只做参数校验 + 响应组装；Celery 调 service 层
- **错误处理**：服务层抛 `ValueError` / 自定义异常，路由层统一转 `HTTPException`
- **日志**：标准 `logging` 模块，禁止 `print`

📖 完整代码风格细则：[.codebuddy/rules/code-style.md](.codebuddy/rules/code-style.md)
📖 API 设计规范（路由前缀、分页、错误码）：[.codebuddy/rules/api.md](.codebuddy/rules/api.md)
📖 数据库模型与迁移规范：[.codebuddy/rules/database.md](.codebuddy/rules/database.md)

---

## 关键设计决策

- **两张视频分类表并存**：`video_classifications`（Feature-004，yaml 规则）+ `coach_video_classifications`（Feature-008，COS 全量 + `kb_extracted` 字段），**禁止合并**
- **LLM 调用优先级**：Venus Proxy → OpenAI fallback，统一封装在 `src/services/llm_client.py`
- **姿态估计降级**：`POSE_BACKEND=auto` 时 YOLOv8 GPU → MediaPipe CPU
- **COS 扫描**：分页 `MaxKeys=1000` + 零字节文件跳过 + 增量比对 `cos_object_key`
- **四队列物理隔离**（Feature-013）：classification / kb_extraction / diagnosis / default，一队列一 Worker
- **KB 提取 DAG**（Feature-014）：`download → (pose ∥ audio_transcribe) → (visual_kb ∥ audio_kb) → merge_kb`；作业级占 1 个通道槽位，内部 asyncio 并行

📖 分类与知识库工作流细节：[.codebuddy/rules/tech-classification.md](.codebuddy/rules/tech-classification.md)
📖 LLM / 姿态估计 / Celery 任务 / 队列：[.codebuddy/rules/workflow.md](.codebuddy/rules/workflow.md)

---

## 活跃 Features（001~014，均已完成）

| # | Feature | 核心 API |
|---|---------|----------|
| 001 | 视频教练顾问 | `POST /tasks`, `GET /tasks/{id}` |
| 002 | 音频增强知识库提取 | Whisper 转录 → 技术关键词 |
| 003 | Skill 知识库→参考视频 | `GET /knowledge-base` |
| 004 | 视频分类体系 | `POST /videos/classifications/refresh` |
| 005 | 音频知识库教学建议 | `GET /teaching-tips` |
| 006 | 多教练知识库 | `GET /coaches`, `GET /calibration` |
| 007 | 处理速度优化 | 并行预分割，并发 2 |
| 008 | 教练视频技术分类DB | `POST /classifications/scan`, `GET /classifications` |
| 009 | SQL 查询脚本 | `specs/009-sql-query-scripts/` |
| 010 | 构建技术标准 | `POST /standards/build`, `GET /standards` |
| 011 | 运动员动作诊断 | `POST /diagnosis`, `GET /diagnosis/{id}` |
| 012 | 全量任务查询接口 | `GET /tasks?page=1&page_size=20&status=X` |
| 013 | 任务管道重新设计 | `POST /tasks/{classification|kb-extraction|diagnosis}`, `GET /task-channels`, `PATCH /admin/channels/{type}`, `POST /admin/reset-task-pipeline` |
| 014 | 知识库提取流水线化（DAG + 并行） | `GET /extraction-jobs`, `GET /extraction-jobs/{id}`, `POST /extraction-jobs/{id}/rerun`（扩展 `POST /tasks/kb-extraction`）|

📖 产品功能详情：[docs/features.md](docs/features.md)
📖 技术架构：[docs/architecture.md](docs/architecture.md)

---

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
