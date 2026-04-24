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
                      │  Celery Worker              │
                      │  concurrency=2              │
                      │                             │
                      │  expert_video_task          │
                      │  classification_task        │
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

### Celery 配置

- **Broker**：Redis (`redis://localhost:6379/0`)
- **Concurrency**：2（每机器）
- **启动方式**：`setsid celery -A src.workers.celery_app worker`

### 主要任务

| 任务名 | 模块 | 功能 |
|--------|------|------|
| `process_expert_video` | `expert_video_task.py` | 专家视频完整处理流水线 |
| `scan_cos_videos` | `classification_task.py` | COS 视频扫描分类 |

### expert_video 处理流水线

```
1. 标记任务为 processing
2. 验证 COS 对象存在
3. 下载到 /tmp/coaching-advisor/
4. 验证视频质量（fps ≥15, 分辨率 ≥854×480）
5. 姿态估计（YOLOv8 / MediaPipe）
6. 动作片段分割（腕部速度峰值检测）
7. 片段技术分类
8. 音频增强提取（Whisper 转录 → 关键词匹配）
9. 持久化到 PostgreSQL（事务）
10. 清理临时文件
11. 更新任务状态为 success/partial_success/failed
```

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
