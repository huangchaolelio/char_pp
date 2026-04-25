# 技术架构文档

> 最后更新：2026-04-24

## 目录

- [系统概述](#系统概述)
- [技术栈](#技术栈)
- [服务架构](#服务架构)
- [数据模型](#数据模型)
- [API 接口层](#api-接口层)
- [异步任务系统](#异步任务系统)
- [存储系统](#存储系统)
- [外部集成](#外部集成)
- [配置体系](#配置体系)

---

## 系统概述

乒乓球 AI 智能教练后端服务，核心功能：

1. **专家视频知识提取**：从教练教学视频中提取姿态数据，构建技术知识库
2. **运动员偏差诊断**：对比运动员动作与技术标准，生成诊断报告和改进建议
3. **视频分类管理**：COS 全量教练视频的自动分类与检索

---

## 技术栈

| 层次 | 技术 | 版本 |
|------|------|------|
| 语言 | Python | 3.11.15 |
| Web 框架 | FastAPI | 0.136.0 |
| ORM | SQLAlchemy (asyncio) | 2.0 |
| 数据库 | PostgreSQL + asyncpg | — |
| 任务队列 | Celery | 5.6.3 |
| 消息中间件 | Redis | — |
| 姿态估计（GPU） | YOLOv8-pose | 8.4.39 |
| 姿态估计（CPU） | MediaPipe | 0.10.33 |
| 深度学习框架 | PyTorch | 2.4.1 |
| 音频转录 | Whisper | small |
| 对象存储 | 腾讯云 COS | — |
| LLM | OpenAI / Venus Proxy | gpt-4o-mini |

---

## 服务架构

```
┌──────────────────────────────────────────────────┐
│                  客户端 / 管理后台                 │
└─────────────────────┬────────────────────────────┘
                      │ HTTP
┌─────────────────────▼────────────────────────────┐
│          FastAPI 应用 (uvicorn :8080)             │
│                                                   │
│  中间件：Request-ID、性能计时、全局异常处理         │
│                                                   │
│  路由：/api/v1/                                   │
│    tasks  knowledge-base  videos  coaches         │
│    classifications  standards  diagnosis          │
│    teaching-tips  calibration                    │
└────────────┬──────────────────┬───────────────────┘
             │ asyncpg           │ Celery send_task
┌────────────▼──────┐  ┌────────▼─────────────────┐
│    PostgreSQL      │  │   Redis (Broker)          │
│  (主数据存储)      │  └────────┬─────────────────┘
└───────────────────┘           │
                      ┌─────────▼─────────────────┐
                      │  Celery 四队列（Feature 013）│
                      │  classification (conc=1)     │
                      │  kb_extraction  (conc=2)     │
                      │  diagnosis      (conc=2)     │
                      │  default        (conc=1)     │
                      └─────────┬─────────────────┘
                                │
                      ┌─────────▼─────────────────┐
                      │  腾讯云 COS (视频存储)      │
                      └───────────────────────────┘
```

### 姿态估计双后端策略

```
优先使用 YOLOv8 (GPU)
  ↓ 失败或无 GPU
fallback: MediaPipe (CPU)
  ↓ 配置 pose_backend=auto 自动选择
```

---

## 数据模型

### 核心表关系

```
coaches
  └── analysis_tasks (coach_id FK)
        ├── expert_tech_points       # 专家技术要点（从视频提取）
        ├── tech_semantic_segments   # 技术语义分段
        ├── audio_transcripts        # 音频转录结果
        ├── athlete_motion_analyses  # 运动员动作分析
        └── coaching_advice          # 教练建议

tech_knowledge_bases
  └── tech_standard_points          # 技术标准点（聚合中位数）

diagnosis_reports
  └── diagnosis_dimension_results   # 诊断维度评分

coach_video_classifications         # Feature-008 COS 视频分类
video_classifications               # Feature-004 视频分类（老表）

skills
  └── skill_executions
        └── reference_videos
              └── reference_video_segments

teaching_tips                       # LLM 提炼的教学建议
```

### 主要模型说明

| 模型 | 表名 | 用途 |
|------|------|------|
| `AnalysisTask` | `analysis_tasks` | 视频处理任务，含状态机（pending→processing→success/failed） |
| `ExpertTechPoint` | `expert_tech_points` | 单帧姿态关键点数据 |
| `TechKnowledgeBase` | `tech_knowledge_bases` | 知识库版本，semver 格式（X.Y.Z） |
| `TechStandard` | `tech_standards` | 聚合后的技术标准（中位数+P25/P75） |
| `CoachVideoClassification` | `coach_video_classifications` | COS 全量视频分类（Feature-008） |
| `Coach` | `coaches` | 教练信息，与 COS 目录 1:1 对应 |
| `DiagnosisReport` | `diagnosis_reports` | 运动员诊断报告 |

### TaskStatus 状态机

```
pending → processing → success
                    → partial_success
                    → failed
                    → rejected
```

---

## API 接口层

### 路由模块

| 前缀 | 文件 | 主要功能 |
|------|------|---------|
| `/api/v1/tasks` | `tasks.py` | 任务查询（分页/筛选/排序） |
| `/api/v1/knowledge-base` | `knowledge_base.py` | 知识库管理 |
| `/api/v1/videos/classifications` | `videos.py` | 视频分类 + refresh（Feature-004） |
| `/api/v1/classifications` | `classifications.py` | COS 扫描 + 分类（Feature-008） |
| `/api/v1/coaches` | `coaches.py` | 教练 CRUD |
| `/api/v1/standards` | `standards.py` | 技术标准查询 |
| `/api/v1/diagnosis` | `diagnosis.py` | 运动员动作诊断 |
| `/api/v1/teaching-tips` | `teaching_tips.py` | 教学建议 |
| `/api/v1/calibration` | `calibration.py` | 多教练知识库对比 |

### 分页规范

列表接口统一支持：
- `limit`（默认 50，最大 200）
- `offset`
- 返回 `total` + `items`

---

## 异步任务系统

### Celery 配置（Feature 013 — 四队列物理隔离）

- **Broker**：Redis (`redis://localhost:6379/0`)
- **架构原则**：一队列一 Worker，崩溃互不影响
- **启动方式**：`setsid celery -A src.workers.celery_app worker --concurrency=N -Q <queue_name> -n <worker_name>@%h`

### 队列与配额

| 队列 | Worker 并发 | 默认容量 | 任务来源 | 可热更新 |
|------|-----------|---------|---------|---------|
| `classification` | 1 | 5 | `classify_video` | ✅ |
| `kb_extraction` | 2 | 50 | `extract_kb`（需 tech_category 非空） | ✅ |
| `diagnosis` | 2 | 20 | `diagnose_athlete` | ✅ |
| `default` | 1 | — | `scan_cos_videos` + `cleanup_expired_tasks` | — |

> 前三队列容量/并发可通过 `PATCH /api/v1/admin/channels/{task_type}` 热更新，30 秒内生效（`TaskChannelService` TTL 缓存）。

### 主要任务

| 任务名 | 模块 | 功能 |
|--------|------|------|
| `classify_video` | `classification_task.py` | 单条视频技术分类 |
| `extract_kb` | `kb_extraction_task.py` | 已分类视频 → 知识库条目 |
| `diagnose_athlete` | `athlete_diagnosis_task.py` | 运动员视频 → 偏差+建议 |
| `scan_cos_videos` | `classification_task.py` | COS 全量扫描 |
| `cleanup_expired_tasks` | `housekeeping_task.py` | 周期性清理过期任务（beat 驱动） |

### 限流与提交保护

- **DB 是容量唯一事实来源**：每次提交前 `pg_advisory_xact_lock(hash(task_type))` 序列化 + `COUNT(*)` 权威计数
- **幂等提交**：partial unique index `idx_analysis_tasks_idempotency` on `(cos_object_key, task_type)` WHERE status IN ('pending','processing','success')；重复提交返回原 task_id
- **批量语义**：`POST /tasks/{type}/batch` 单批 ≤100 条；超上限整批 400 `BATCH_TOO_LARGE`；容量不足时前 K 条 `ACCEPTED`、后 M-K 条 `QUEUE_FULL`（部分成功）
- **KB 提取门槛**：`ClassificationGateService` 校验视频已分类且 `tech_category != 'unclassified'` 才允许入队
- **孤儿任务自动恢复**：`celeryd_after_setup` 信号在 Worker 启动时 sweep `started_at < now - 840s AND status='processing'` 行并标记 `failed`

### 运维能力

- **管道数据一键重置**：`POST /api/v1/admin/reset-task-pipeline`（body confirmation token + dry-run），TRUNCATE tasks/transcripts/advice/tips 等流水表 + DELETE 草稿 KB；保留 coaches/classifications/standards/skills/reference_videos
- **CLI 脚本**：`specs/013-task-pipeline-redesign/scripts/reset_task_pipeline.py --confirm` 或 `--dry-run`
- **通道状态查询**：`GET /api/v1/task-channels` 返回三通道实时 pending/processing/remaining_slots/recent_completion_rate_per_min

---

## 知识库提取流水线（Feature 014）

Feature-013 的 `kb_extraction` 通道原本只是占位 stub（翻转 `kb_extracted=True`）。Feature-014 将单条 KB 提取重建为**有向无环图（DAG）**，在 Worker 内部用 asyncio 并行调度 6 个子步骤。

### DAG 定义

```
download_video
    ├─▶ pose_analysis ──▶ visual_kb_extract ─┐
    └─▶ audio_transcription ──▶ audio_kb_extract ─┤
                                                  ▼
                                              merge_kb
```

- **wave 1**：`download_video`（I/O）
- **wave 2**：`pose_analysis` ∥ `audio_transcription`（CPU ∥ I/O）
- **wave 3**：`visual_kb_extract` ∥ `audio_kb_extract`（CPU ∥ I/O）
- **wave 4**：`merge_kb`（合并 + 冲突分离入 `kb_conflicts`）

### 执行模型

- 一次 Celery `extract_kb` 任务 = 一个 `ExtractionJob` = 1 个 `kb_extraction` 通道槽位（FR-015）
- 作业内部并行由 `asyncio.gather` + 独立 `AsyncSession`/分支实现；**不新占通道名额**
- 作业级超时 45 min（`extraction_job_timeout_seconds`），单步超时 10 min（`extraction_step_timeout_seconds`）
- I/O 步骤（download/audio_transcription/audio_kb_extract）自动重试 3 次 × 30 s（tenacity）；CPU 步骤首次失败即 failed

### 与 Feature-013 通道的关系

- `AnalysisTask.extraction_job_id` 建立反向关联；`analysis_tasks` 行一对一映射 `extraction_jobs`
- 通道计数按 `analysis_tasks.status ∈ {pending, processing}` → 按**作业数**，不随子步骤放大
- rerun 复用原 `analysis_tasks.id`，不消耗新通道容量（FR-016）

### 冲突分离

- 视觉 + 音频两路提取同 dimension，差异 > 10% → 写入 `kb_conflicts` 表等待审核
- **冲突项不进主 KB**（`expert_tech_points`），非冲突条目正常入库
- `force=true` 覆盖旧作业时，旧冲突项自动打 `superseded_by_job_id`

### 局部重跑

- `POST /api/v1/extraction-jobs/{job_id}/rerun`（仅 failed 作业）
- 默认只重置 failed + 下游 skipped 的 step；success step 保留 artifact + output_summary（FR-005）
- `force_from_scratch=true` 重置 **所有** step（本地 artifact 已被清理时必须使用）

### 中间结果保留

- success 作业：24 小时（`extraction_success_retention_hours`）
- failed 作业：7 天（`extraction_failed_retention_hours`）
- Celery beat 每小时触发 `cleanup_intermediate_artifacts` 删除本地目录 + 清空 artifact path（output_summary 保留供审计）

### Worker 孤儿恢复

- Worker 启动时 `celeryd_after_setup` sweep：
  - `analysis_tasks.status='processing' AND started_at < now-840s` → failed
  - `pipeline_steps.status='running' AND started_at < now-600s` → failed + skipped 传播 + 作业/任务标 failed

---

## 存储系统

### PostgreSQL

- 异步连接：asyncpg + SQLAlchemy 2.0 asyncio
- 迁移：Alembic（`src/db/migrations/`）
- Session 管理：FastAPI `Depends(get_db)` 依赖注入

### 腾讯云 COS

| 环境变量 | 用途 |
|----------|------|
| `COS_VIDEO_PREFIX` | 孙浩泓主课目录（Feature-001 遗留） |
| `COS_VIDEO_ALL_COCAH` | 全量教练视频根目录（Feature-008） |
| `COS_BUCKET` | Bucket 名称 |
| `COS_REGION` | 地域（ap-guangzhou） |

### 临时文件

- 路径：`/tmp/coaching-advisor/`
- 任务完成后自动清理
- 磁盘空间不足会导致任务失败（`No space left on device`）

---

## 外部集成

### LLM（教学建议 + 诊断改进建议）

```python
# 优先级：venus_proxy > openai_base_url > openai
LlmClient(model=settings.openai_model)  # 默认 gpt-4o-mini
```

### Whisper（音频转录）

- 模型：`small`（中文优化）
- 设备：`auto`（有 GPU 用 GPU）
- 长视频分段处理（每段 180s，最长支持 5400s）

---

## 配置体系

所有配置通过 `.env` 文件注入，`src/config.py` 的 `Settings` 类统一管理（Pydantic BaseSettings）。

关键配置组：

```
数据库：DATABASE_URL
Redis：REDIS_URL
COS：COS_SECRET_ID, COS_SECRET_KEY, COS_REGION, COS_BUCKET
COS路径：COS_VIDEO_PREFIX, COS_VIDEO_ALL_COCAH
LLM：OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
Venus：VENUS_TOKEN, VENUS_BASE_URL, VENUS_MODEL
Whisper：WHISPER_MODEL, WHISPER_DEVICE
姿态：POSE_BACKEND (auto/mediapipe/yolov8)
```
