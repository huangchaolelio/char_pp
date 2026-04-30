# 技术架构文档

> 最后更新：2026-04-30
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
  路由：/api/v1/（Feature-017 规范化后 + Feature-018 扩展）│
│    tasks  knowledge-base  classifications  coaches│
│    standards  teaching-tips  calibration          │
│    extraction-jobs  task-channels  admin          │
│    video-preprocessing  business-workflow         │
└────────────┬──────────────────┬───────────────────┘
             │ asyncpg           │ Celery send_task
┌────────────▼──────┐  ┌────────▼─────────────────┐
│    PostgreSQL      │  │   Redis (Broker)          │
│  (主数据存储)      │  └────────┬─────────────────┘
└───────────────────┘           │
                      ┌─────────▼─────────────────┐
                      │  Celery 五队列              │
                      │  classification (conc=1)     │
                      │  kb_extraction  (conc=2)     │
                      │  diagnosis      (conc=2)     │
                      │  preprocessing  (conc=3)     │
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

### 视频预处理前置（Feature 016）

为消除大视频单次全量推理的 OOM 与重复下载，Feature-016 在 KB 提取前新增**预处理阶段**：

```
原视频 (COS_VIDEO_ALL_COCAH) → preprocess_video
  ├─ ffprobe + validate_video （probe 阶段质量门禁）
  ├─ ffmpeg 转码（目标 30 fps / 短边 720）
  ├─ 流式切分 180s/段 + ThreadPool(max=2) 并发上传 COS
  └─ 整段 16kHz mono WAV 提取并上传

产物：preprocessed/{original_cos_key}/jobs/{job_id}/seg_NNNN.mp4 + audio.wav
```

KB 提取四个 executor 改造为**消费预处理产物**：`pose_analysis` 按分段迭代 estimate_pose；`audio_transcription` 直接从 COS 拉 audio.wav 喂 Whisper（强制 `WHISPER_DEVICE=cpu`）；本地产物 24h 温缓存供同视频后续任务复用（先 COS head 校验再读本地）。

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

coach_video_classifications         # Feature-008 COS 视频分类（新增 preprocessed:bool，Feature-016）
video_classifications               # Feature-004 视频分类（老表）

video_preprocessing_jobs            # Feature-016 预处理作业顶级容器
  └── video_preprocessing_segments  # Feature-016 分段映射（job_id FK cascade）

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
| `TechKnowledgeBase` | `tech_knowledge_bases` | 知识库版本（Feature-019 重构：**复合主键 `(tech_category, version INTEGER)`**，per-category 独立生命周期；`uq_tech_kb_active_per_category` partial unique index 保证每类别单 active） |
| `TechStandard` | `tech_standards` | 聚合后的技术标准（中位数+P25/P75；Feature-019 新增 `source_fingerprint CHAR(64)` 列 + `uq_ts_fingerprint_per_category` 局部唯一索引支持 build 幂等） |
| `CoachVideoClassification` | `coach_video_classifications` | COS 全量视频分类（Feature-008） + 新增 `preprocessed` 字段（Feature-016） |
| `VideoPreprocessingJob` | `video_preprocessing_jobs` | 预处理作业（Feature-016），running/success/failed/superseded 四状态 |
| `VideoPreprocessingSegment` | `video_preprocessing_segments` | 分段 → COS object key 映射（Feature-016），(job_id, segment_index) 唯一 |
| `Coach` | `coaches` | 教练信息，与 COS 目录 1:1 对应 |
| `DiagnosisReport` | `diagnosis_reports` | 运动员诊断报告 |

### 业务阶段双列（Feature-018 迁移 0016）

为支持业务流程总览聚合（`GET /business-workflow/overview`），以下四张业务表统一下沉 `business_phase` + `business_step` 双列：

| 表 | phase 取值 | step 取值 |
|----|-----------|----------|
| `analysis_tasks` | `TRAINING` / `INFERENCE`（按 task_type 派生） | `scan_cos_videos` / `preprocess_video` / `classify_video` / `extract_kb` / `diagnose_athlete` |
| `extraction_jobs` | `TRAINING`（固定） | `extract_kb`（固定） |
| `video_preprocessing_jobs` | `TRAINING`（固定） | `preprocess_video`（固定） |
| `tech_knowledge_bases` | `STANDARDIZATION`（固定） | `kb_version_activate`（固定） |

- **派生单一事实来源**：`src/models/_phase_step_hook.py` 注册 SQLAlchemy `before_insert` 事件 → 新行自动派生两列；未知 `task_type` 或只传入单列 ⇒ `ValueError(PHASE_STEP_UNMAPPED)`（fail-fast）
- **DB 级兜底**：迁移 0016 将两列设为 `NOT NULL`；PostgreSQL enum type `business_phase_enum`（`TRAINING` / `STANDARDIZATION` / `INFERENCE`）限制取值
- **索引**：`analysis_tasks` 上建 `(business_phase, business_step)` 复合索引，`extraction_jobs` 上建 `(business_phase)` 单列索引，加速总览接口聚合
- **禁止手填**：业务代码 MUST NOT 手动设置 `business_phase` / `business_step`，统一由 ORM 钩子派生


### TaskStatus 状态机

```
pending → processing → success
                    → partial_success
                    → failed
                    → rejected
```

### 时区规范（全链路北京时间）

全项目禁止使用 UTC 时间作为存储与传输口径，所有时间字段按北京时间（Asia/Shanghai）存储与序列化。

- **应用层**：统一入口 `src/utils/time_utils.py::now_cst()` 返回 naive `datetime`（已剥离 tzinfo）。禁止使用 `datetime.now(timezone.utc)` / `datetime.utcnow()` / `datetime.now(UTC)`。
- **ORM 模型**：所有时间列统一 `TIMESTAMP(timezone=False)`；`server_default` / `onupdate` 统一为 `text("timezone('Asia/Shanghai', now())")`，不依赖会话时区设置。
- **数据库**：PostgreSQL 服务器 timezone 配置为 `Asia/Shanghai`；迁移文件默认值使用 `timezone('Asia/Shanghai', now())` 显式拼接，保证重放可复现。
- **API 序列化**：响应中时间字段形如 `"2026-04-28T11:39:11.287972"`（无 `Z`、无 `+08:00`），所见即北京时间。
- **Celery**：`timezone="Asia/Shanghai"` + `enable_utc=False`，beat 调度、任务时间戳与业务时间一致。

---

## API 接口层

> **Feature-017 已全面规范化（章程 v2.0.0 原则 IX）**：所有 `/api/v1/**` 接口统一使用
> `SuccessEnvelope` / `ErrorEnvelope` 响应信封、集中化错误码（38 个 ErrorCode 枚举）、
> 统一分页参数（`page`/`page_size`）、枚举归一化。接口下线采用**直接物理删除**策略
> （章程 v2.0.0 原则 IV + IX）——老路径不再保留哨兵路由，客户端调用老路径直接收到
> FastAPI 默认 404 `NOT_FOUND`。
> 详见 `docs/api-standardization-guide.md` 与 `specs/017-api-standardization/contracts/`。

### 路由模块（Feature-017 后的保留清单，12 个业务路由）

| 前缀 | 文件 | 主要功能 |
|------|------|---------|
| `/api/v1/tasks` | `tasks.py` | 任务提交 + 分页查询 + 删除 + 异步诊断/KB 提取入口 + 教练关联 |
| `/api/v1/knowledge-base` | `knowledge_base.py` | 知识库版本管理 |
| `/api/v1/classifications` | `classifications.py` | COS 扫描 + 分类（Feature-008，原 `videos.py` 已并入） |
| `/api/v1/coaches` | `coaches.py` | 教练 CRUD（PATCH `/tasks/{task_id}/coach` 已搬迁至 `tasks.py`） |
| `/api/v1/standards` | `standards.py` | 技术标准查询与构建（Feature-010） |
| `/api/v1/teaching-tips` | `teaching_tips.py` | 教学建议（Feature-005） |
| `/api/v1/calibration` | `calibration.py` | 多教练知识库对比（Feature-006） |
| `/api/v1/extraction-jobs` | `extraction_jobs.py` | KB 提取 DAG 作业查询 / 重跑（Feature-014） |
| `/api/v1/task-channels` | `task_channels.py` | 通道实时快照（Feature-013） |
| `/api/v1/admin/...` | `admin.py` | 通道热更新 + 管道重置（Feature-013）+ **优化杠杆台账 `GET /admin/levers`（Feature-018 US3）** |
| `/api/v1/video-preprocessing` | `video_preprocessing.py` | 预处理作业分页列表 + 单条审计详情（Feature-016） |
| `/api/v1/business-workflow` | `business_workflow.py` | **业务阶段总览 `GET /business-workflow/overview`（Feature-018 US1，三阶段 × 八步骤计数/耗时/吞吐）** |

**已下线模块**：`videos.py`（并入 `classifications.py`）、`diagnosis.py`（同步诊断并入 `tasks.py` 异步通道）。下线采用直接物理删除策略（章程 v2.0.0 原则 IV + IX），不保留哨兵路由或台账文件。

### 响应信封（Feature-017 强制）

所有接口响应体必须匹配下列两种信封之一，顶层 `success` 布尔位作为判别式：

```json
// 成功信封
{ "success": true, "data": <业务载荷>, "meta": { "page": 1, "page_size": 20, "total": 42 } }

// 错误信封
{ "success": false, "error": { "code": "TASK_NOT_FOUND", "message": "任务不存在", "details": {...} } }
```

- 成功响应通过 `src/api/schemas/envelope.py::SuccessEnvelope[T]` 泛型构造
- 非分页接口用 `ok(data)`，分页接口用 `page(items, page=, page_size=, total=)`
- 错误统一抛 `AppException(ErrorCode.XXX, message=..., details=...)`，由 `src/api/errors.py` 的全局异常处理器渲染为错误信封

### 分页规范（Feature-017 统一）

列表接口统一接受：
- `page`：从 1 开始，默认 1（Pydantic `Query(ge=1)` 约束）
- `page_size`：默认 20，最大 100（`Query(ge=1, le=100)` 硬约束，越界自动返回 422 + `VALIDATION_FAILED`，**禁止静默截断**）
- 返回结构：`data` 为纯业务列表，`meta = { page, page_size, total }`（`total_pages` 由前端按 `ceil(total/page_size)` 自算）

**禁用旧参数**：`limit` / `offset` / `skip` / `take` / `pageNum` / `pageSize`。

### 枚举归一化（Feature-017 统一）

所有枚举型查询参数/路径参数/请求体字段统一走 `src/api/enums.py` 的归一化三件套：

- `normalize_enum_value(raw)`：strip + lower + (`-` → `_`)
- `parse_enum_param(value, field=, enum_cls=)`：绑定到 `str Enum` 类
- `validate_enum_choice(value, field=, allowed=)`：白名单校验

失败统一抛 `AppException(INVALID_ENUM_VALUE)`，`details` 含 `field` / `value` / `allowed` 三元组。

### 错误码集中化（38 个 ErrorCode 枚举）

定义位置：`src/api/errors.py::ErrorCode`，按以下分组：

| 分组 | 数量 | 示例 |
|------|------|------|
| 通用 | 6 | `VALIDATION_FAILED` / `NOT_FOUND` / `INTERNAL_ERROR` |
| 认证 | 2 | `ADMIN_TOKEN_INVALID` |
| 资源不存在 | 6 | `TASK_NOT_FOUND` / `COACH_NOT_FOUND` / `TIP_NOT_FOUND` |
| 状态/业务约束 | 18 | `TASK_NOT_READY` / `COACH_INACTIVE` / `KB_VERSION_NOT_DRAFT` |
| 容量/队列 | 2 | `CHANNEL_QUEUE_FULL` / `CHANNEL_DISABLED` |
| 上游依赖 | 4 | `LLM_UPSTREAM_FAILED` / `COS_UPSTREAM_FAILED` |

三元同步约束：`ErrorCode` ↔ `ERROR_STATUS_MAP`（HTTP 状态）↔ `ERROR_DEFAULT_MESSAGE`（默认消息）
数量必须严格一致，由 `tests/contract/test_error_codes_contract.py` 参数化锁定。

### CI Linter

| 脚本 | 校验对象 |
|------|---------|
| `scripts/lint_api_naming.py` | 路径前缀/kebab-case/ID 参数命名/分页参数必填/禁用 limit&offset |
| `scripts/lint_error_codes.py` | 业务代码不得出现裸字符串错误码或 `raise HTTPException` |

两者 0 违规为合入 PR 的硬性前置（见 `docs/api-standardization-guide.md` §8 Pre-merge 自检清单）。

### 工作流漂移与 Spec 合规守卫（Feature-018 US2）

章程原则 X（业务工作流对齐）落地为两套离线扫描器 + 一条 pre-push hook：

| 脚本 | 作用 |
|------|------|
| `scripts/audit/workflow_drift.py` | 代码 ↔ `docs/business-workflow.md` 的 **8 类漂移扫描**（错误码前缀 / 状态机枚举 / 评分公式阈值 / 通道种子 / § 9 三类杠杆清单 / spec-template 子标签 / `config/optimization_levers.yml` 一致性 / 阶段步骤总表） |
| `scripts/audit/spec_compliance.py` | `specs/*/spec.md` 中「业务阶段映射」六项子标签完整性扫描（FR-020） |

**Makefile 目标**（仓库根）：

| 目标 | 命令 | 触发点 |
|------|------|--------|
| `make drift-changed` | 仅扫描 `git diff` 变更文件 | pre-push hook 自动执行 |
| `make drift-full` | 全量漂移扫描 | 手工 / 未来 CI |
| `make spec-compliance` | spec-template 六项子标签 | 手工 / 未来 CI |

**Git hook 安装**：`scripts/install-git-hooks.sh` 幂等将 `scripts/git-hooks/pre-push` 软链到 `.git/hooks/pre-push`（首次 clone 后执行一次）。pre-push 阻断含漂移的 push，**不支持 waiver**（Clarification Q5）；hotfix 走「代码修改 + 文档同步」原子 PR。

**辅助配置**：

- `config/optimization_levers.yml`：三类杠杆（运行时参数 / 算法模型 / 规则 Prompt）台账，与 `business-workflow.md` § 9 表格双向同步
- `scripts/audit/.scan-exclude.yml`：历史静态排除清单（`specs/001-*/` ~ `specs/017-*/` 共 17 目录免扫）

未来接入 GitHub Actions / GitLab CI / Jenkins 时，仅需在对应配置中调用 `make drift-full` + `make spec-compliance` 即可对接，无平台强依赖。

---

## 异步任务系统

### Celery 配置（Feature 013 四队列物理隔离 + Feature 016 新增第五通道）

- **Broker**：Redis (`redis://localhost:6379/0`)
- **架构原则**：一队列一 Worker，崩溃互不影响
- **启动方式**：`setsid celery -A src.workers.celery_app worker --concurrency=N -Q <queue_name> -n <worker_name>@%h`

### 队列与配额

| 队列 | Worker 并发 | 默认容量 | 任务来源 | 可热更新 |
|------|-----------|---------|---------|---------|
| `classification` | 1 | 5 | `classify_video` | ✅ |
| `kb_extraction` | 2 | 50 | `extract_kb`（需 tech_category 非空） | ✅ |
| `diagnosis` | 2 | 20 | `diagnose_athlete` | ✅ |
| `preprocessing` | 3 | 20 | `preprocess_video`（Feature-016）| ✅ |
| `default` | 1 | — | `scan_cos_videos` + `cleanup_expired_tasks` + `cleanup_intermediate_artifacts` | — |

> 前三队列容量/并发可通过 `PATCH /api/v1/admin/channels/{task_type}` 热更新，30 秒内生效（`TaskChannelService` TTL 缓存）。

### 主要任务

| 任务名 | 模块 | 功能 |
|--------|------|------|
| `classify_video` | `classification_task.py` | 单条视频技术分类 |
| `extract_kb` | `kb_extraction_task.py` | 已分类视频 → 知识库条目 |
| `diagnose_athlete` | `athlete_diagnosis_task.py` | 运动员视频 → 偏差+建议 |
| `preprocess_video` | `preprocessing_task.py` | COS 原视频 → 标准化分段 + 整段 WAV（Feature-016） |
| `scan_cos_videos` | `classification_task.py` | COS 全量扫描 |
| `cleanup_expired_tasks` | `housekeeping_task.py` | 周期性清理过期任务（beat 驱动） |
| `cleanup_intermediate_artifacts` | `housekeeping_task.py` | 清理 KB 提取 / 预处理本地中间结果（每小时 beat） |

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

### Feature-015 真实算法接入

Feature-014 交付了完整 DAG 骨架，但 4 个 step executor 是 scaffold（产出 `note="scaffold_output_pending_..."`）。Feature-015 替换为 Feature-002 既有算法模块的真实调用，不改 DAG 结构、不新增依赖、不加数据库迁移。

**Executor 算法接线**：

| Executor | 调用链 | 产出 artifact |
|----------|--------|--------------|
| `pose_analysis` | `video_validator.validate_video` → `pose_estimator.estimate_pose` | `pose.json`（33 关键点 × 帧数）|
| `audio_transcription` | `AudioExtractor.extract_wav` → `SpeechRecognizer.recognize` | `audio.wav` + `transcript.json` |
| `visual_kb_extract` | `action_segmenter.segment_actions` → `action_classifier.classify_segment` → `tech_extractor.extract_tech_points` | `kb_items`（内存，传 merger） |
| `audio_kb_extract` | `TranscriptTechParser.parse` → `LlmClient`（Venus → OpenAI fallback） | `kb_items`（含 `raw_text_span`） |

所有 CPU/HTTP 阻塞调用都用 `asyncio.to_thread` 包装，满足 Feature-014 wave 内并行约束。

**结构化错误码**（`src/services/kb_extraction_pipeline/error_codes.py`，FR-016）：

| 前缀 | step | 是否重试 |
|------|------|---------|
| `VIDEO_QUALITY_REJECTED:` | pose_analysis | 否（fps/分辨率不达标） |
| `POSE_NO_KEYPOINTS:` | pose_analysis | 否（估计器返回空） |
| `POSE_MODEL_LOAD_FAILED:` | pose_analysis | I/O 重试 |
| `WHISPER_LOAD_FAILED:` | audio_transcription | I/O 重试 |
| `WHISPER_NO_AUDIO:` | audio_transcription | 不进 `error_message`，走 skipped |
| `ACTION_CLASSIFY_FAILED:` | visual_kb_extract | 否 |
| `LLM_UNCONFIGURED:` | audio_kb_extract | 否（Venus + OpenAI 均未配置）|
| `LLM_JSON_PARSE:` | audio_kb_extract | 否（格式问题）|
| `LLM_CALL_FAILED:` | audio_kb_extract | I/O 重试 |

**artifact JSON 契约**：`pose.json` / `transcript.json` 无 schema 版本，读端容错（spec Q4）— `src/services/kb_extraction_pipeline/artifact_io.py` 的 `read_*_artifact` 缺字段用默认值、未知顶层键 ignore、畸形条目跳过。

**参考视频集回归**：`specs/015-kb-pipeline-real-algorithms/scripts/run_reference_regression.py` 对 manifest（`reference_videos.json`）驱动端到端流水线并生成 `verification.md` 表格；`--measure-wallclock` 开关加 `Baseline(s) / Ratio vs Baseline` 列，支撑 SC-002 耗时基线对比（要求 ≤ 0.9× Feature-002 旧流程）。

---

## 视频预处理流水线（Feature 016）

Feature-015 烟测（2026-04-25）暴露两个核心问题：`pose_analysis` 对整段大视频一次性推理触发 OOM-killed；rerun / 多 tech_category 并行提取每次都重新下载 + 转码 + 切分，浪费带宽。Feature-016 在 KB 提取前新增**预处理阶段**。

### DAG 位置

```
POST /tasks/preprocessing → preprocess_video (新 Celery 任务，第五队列)
    ├─ ffprobe + validate_video（VIDEO_QUALITY_REJECTED: 质量门禁前移）
    ├─ ffmpeg 标准化转码（target_fps=30 / 短边 720）
    ├─ 流式切分 180s/段 + ThreadPool(max=2) 并发上传 COS
    └─ 整段 16 kHz mono WAV 提取 + 上传 COS
           ↓
    preprocessed/{original_cos_key}/jobs/{job_id}/seg_NNNN.mp4
    preprocessed/{original_cos_key}/jobs/{job_id}/audio.wav
           ↓
POST /tasks/kb-extraction → extract_kb 消费预处理产物
```

### 关键设计

- **按 job_id 隔离 COS 路径**：`force=true` 覆盖同一 `cos_object_key` 时，旧 job 标 `status=superseded` + 同步删除旧分段 COS 对象；新 job 的产物放独立 `jobs/{job_id}/` 子目录，避免并发读写竞争
- **质量门禁前移**：probe 阶段即调 `validate_video`，fps / 分辨率不达标立即 `VIDEO_QUALITY_REJECTED:` failed，不进入转码/分段/上传，节省带宽和 COS 存储
- **双路径读取（COS 门禁 + 本地优先）**：KB 提取消费时先 COS head 校验分段和音频对象存在（防"本地有残留但 COS 上传失败"幽灵数据），通过后本地存在直接读本地，缺失则从 COS 下载
- **本地 24h 温缓存**：`EXTRACTION_ARTIFACT_ROOT/preprocessing/{job_id}/` 保留 24 小时；`cleanup_intermediate_artifacts` beat 每小时扫描，删前检查 mtime/atime（近 1h 内被访问则延期一轮），无显式文件锁
- **懒检测 COS 产物丢失**：KB 提取下载 404 → `SEGMENT_MISSING:` / `AUDIO_MISSING:` 失败；运维手动 `force=true` 重建；不引入主动 verify / sweep 基础设施
- **Whisper 强制 CPU**：预处理一次性产出整段 16 kHz mono WAV；`audio_transcription` 不再从视频实时提取音频，直接喂 Whisper；`WHISPER_DEVICE=cpu` 避开 torch CUDA 58 GB 虚拟地址占用

### 4 个 step executor 改造

| Executor | Feature-015 | Feature-016 改造 |
|----------|-------------|-----------------|
| `pose_analysis` | 单次 `estimate_pose(full_video)` | 按 segments 表迭代，每段独立 `estimate_pose` 后累积 frames；单段 RSS < 整体 50% |
| `audio_transcription` | ffmpeg 从视频提取 → Whisper | 直接从 COS 拉 `audio.wav` → Whisper(CPU) |
| `visual_kb_extract` | 不变 | 不变 |
| `audio_kb_extract` | 不变 | 不变 |

### 结构化错误前缀（8 类）

`VIDEO_DOWNLOAD_FAILED:` / `VIDEO_PROBE_FAILED:` / `VIDEO_QUALITY_REJECTED:` / `VIDEO_CODEC_UNSUPPORTED:` / `VIDEO_TRANSCODE_FAILED:` / `VIDEO_SPLIT_FAILED:` / `VIDEO_UPLOAD_FAILED:` / `AUDIO_EXPORT_FAILED:`（KB 提取侧新增：`SEGMENT_MISSING:` / `AUDIO_MISSING:`）

### 数据模型

- `video_preprocessing_jobs`：顶级作业容器（status ∈ running/success/failed/superseded，partial unique index on `cos_object_key WHERE status='success'`）
- `video_preprocessing_segments`：每段一条记录，`(job_id, segment_index)` 唯一；`job_id` CASCADE DELETE
- `coach_video_classifications` 新增 `preprocessed: bool` 字段，至少一次 success 即置 true

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
- KB 提取中间结果：`/tmp/coaching-advisor/jobs/{job_id}/`（Feature-014，success 24h / failed 7 天保留）
- 预处理产物本地温缓存：`/tmp/coaching-advisor/jobs/preprocessing/{job_id}/`（Feature-016，统一 24h 保留，供同视频 KB 提取复用）
- 清理由 `cleanup_intermediate_artifacts` beat 任务每小时扫一次；删除前检查 mtime/atime，近 1h 被访问则延期
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
Whisper：WHISPER_MODEL, WHISPER_DEVICE（Feature-016 起强制 cpu）
姿态：POSE_BACKEND (auto/mediapipe/yolov8)
通道/管道：ADMIN_RESET_TOKEN, BATCH_MAX_SIZE, ORPHAN_TASK_TIMEOUT_SECONDS, CHANNEL_CONFIG_CACHE_TTL_S
KB 提取：EXTRACTION_JOB_TIMEOUT_SECONDS, EXTRACTION_STEP_TIMEOUT_SECONDS, EXTRACTION_ARTIFACT_ROOT, EXTRACTION_SUCCESS_RETENTION_HOURS, EXTRACTION_FAILED_RETENTION_HOURS
预处理：VIDEO_PREPROCESSING_SEGMENT_DURATION_S, VIDEO_PREPROCESSING_TARGET_FPS, VIDEO_PREPROCESSING_TARGET_SHORT_SIDE, PREPROCESSING_LOCAL_RETENTION_HOURS, PREPROCESSING_UPLOAD_CONCURRENCY
```
