# 产品功能文档

> 最后更新：2026-05-31 · Feature-023 技术分类体系重构（V2 字典 56 行 / 全局 `tech_category` → `action` 列名重命名 / 启发式 lower bound 基线已落地）

## 目录

- [产品概述](#产品概述)
- [Feature-001 视频教练顾问](#feature-001-视频教练顾问)
- [Feature-002 音频增强知识库提取](#feature-002-音频增强知识库提取)
- [Feature-003 Skill 知识库到参考视频](#feature-003-skill-知识库到参考视频)
- [Feature-004 视频分类体系](#feature-004-视频分类体系)
- [Feature-005 音频知识库教学建议](#feature-005-音频知识库教学建议)
- [Feature-006 多教练知识库](#feature-006-多教练知识库)
- [Feature-007 处理速度优化](#feature-007-处理速度优化)
- [Feature-008 教练视频技术分类数据库](#feature-008-教练视频技术分类数据库)
- [Feature-009 SQL 查询脚本](#feature-009-sql-查询脚本)
- [Feature-010 构建技术标准](#feature-010-构建技术标准)
- [Feature-011 运动员动作诊断](#feature-011-运动员动作诊断)
- [Feature-012 全量任务查询接口](#feature-012-全量任务查询接口)
- [Feature-013 任务管道重新设计](#feature-013-任务管道重新设计)
- [Feature-014 知识库提取流水线化](#feature-014-知识库提取流水线化)
- [Feature-015 真实算法接入（知识库提取流水线）](#feature-015-真实算法接入知识库提取流水线)
- [Feature-016 视频预处理流水线](#feature-016-视频预处理流水线)
- [Feature-017 API 规范化](#feature-017-api-规范化)
- [Feature-018 业务工作流标准化](#feature-018-业务工作流标准化)
- [Feature-020 运动员推理流水线](#feature-020-运动员推理流水线)
- [Feature-021 视频内容清洗与有效片段筛选](#feature-021-视频内容清洗与有效片段筛选)
- [Feature-022 业务流程四阶段化 + 内容准备阶段引入审核门](#feature-022-业务流程四阶段化--内容准备阶段引入审核门)
- [全链路时区统一（基础治理）](#全链路时区统一基础治理)

---

## 产品概述

乒乓球 AI 智能教练后端平台，提供从「专家教学视频」到「运动员动作改进建议」的完整 AI 分析链路。

**核心链路：**

```
教练教学视频
    ↓ Feature-001/002 (知识库提取)
技术知识库 (TechKnowledgeBase)
    ↓ Feature-010 (标准构建)
技术标准 (TechStandard)
    ↓ Feature-011 (动作诊断)
诊断报告 + 改进建议
```

**教练视频来源：**
- COS 路径：`charhuang/tt_video/乒乓球合集【较新】/`
- 共 1015 个 mp4 文件，覆盖 12+ 位教练

---

## Feature-001 视频教练顾问

**状态：已完成**  
**规范：** `specs/001-video-coaching-advisor/`

### 功能描述

从专家教练视频中提取姿态关键点，生成可用于运动员诊断的技术知识库。

### 核心流程

1. 提交视频到处理队列（`POST /api/v1/tasks`）
2. Celery worker 异步处理：姿态估计 → 分段 → 技术提取 → 入库
3. 查询任务状态和处理结果

### 关键 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/tasks` | 提交视频处理任务 |
| `GET` | `/api/v1/tasks/{task_id}` | 查询单个任务详情 |
| `GET` | `/api/v1/tasks` | 列表查询（支持筛选/分页） |

### 技术指标

- 单视频处理时间 ≤5 分钟
- 支持 mp4/mov/avi/mkv 格式
- 最低视频质量：fps ≥15，分辨率 ≥854×480

---

## Feature-002 音频增强知识库提取

**状态：已完成**  
**规范：** `specs/002-audio-enhanced-kb-extraction/`

### 功能描述

在视频姿态提取基础上，叠加 Whisper 音频转录，从教练语音中提取技术关键词，增强知识库质量。

### 核心能力

- Whisper `small` 模型（中文优化）转录教学音频
- 关键词匹配映射到技术动作类型（`action_type_hint`）
- 长视频支持：分段处理（180s/段），最长 5400s（90 分钟）
- SNR 阈值过滤低质量音频片段（阈值 8.0 dB）

### 数据

- `audio_transcripts` 表：存储转录文本和置信度
- `audio_fallback_reason`：记录音频分析失败原因

---

## Feature-003 Skill 知识库到参考视频

**状态：已完成**  
**规范：** `specs/003-skill-kb-to-reference-video/`

### 功能描述

将技术知识库提炼流程封装为可重复执行的 Skill，提炼完成后自动生成参考视频供管理员审核。

### 核心实体

- `Skill`：技术动作技能定义
- `SkillExecution`：知识库 → 技能的执行记录
- `ReferenceVideo`：从知识库片段拼接生成的参考视频
- `ReferenceVideoSegment`：参考视频的片段构成

---

## Feature-004 视频分类体系

**状态：已完成**  
**规范：** `specs/004-video-classification/`

### 功能描述

对 COS 全量教学视频按「教练 × 技术」进行三层分类，支持按分类批量提交知识库提取任务。

### 分类维度

- `coach_name`：教练名称
- `tech_category`：技术大类（正手/反手/步法/发球等）
- `tech_sub_category`：技术中类
- `tech_detail`：技术细分
- `video_type`：tutorial（讲解）/ training（训练计划）
- `action_type`：对应枚举值

### 关键 API

> ⚠️ **Feature-017 / v2.0.0 更新**：下表中 `/api/v1/videos/classifications*` 系列已于 2026-04-28 物理删除，
> 请改用 `/api/v1/classifications*`（Feature-008）。旧路径收到 FastAPI 默认 404 `NOT_FOUND`。

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/videos/classifications` | 查询分类记录（支持按教练/技术过滤） |
| `POST` | `/api/v1/videos/classifications/refresh` | 全量刷新（扫描 COS_VIDEO_ALL_COCAH） |
| `PATCH` | `/api/v1/videos/classifications/{key}` | 人工修正分类 |
| `POST` | `/api/v1/videos/classifications/batch-submit` | 按分类批量提交任务 |

### 分类规则

配置文件：`src/config/video_classification.yaml`  
- 12 位教练的 `cos_prefix_keywords` 关键词匹配
- 技术分类关键词规则（require/match/exclude 三级）
- 置信度：精确匹配 1.0，大类匹配 0.7，无匹配兜底 0.5

---

## Feature-005 音频知识库教学建议

**状态：已完成**  
**规范：** `specs/005-audio-kb-coaching-tips/`

### 功能描述

从 Whisper 转录的教学文本中，用 GPT 提炼结构化教学建议，存入 `teaching_tips` 表。

### 关键 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/teaching-tips` | 查询教学建议 |

---

## Feature-006 多教练知识库

**状态：已完成**  
**规范：** `specs/006-multi-coach-kb/`

### 功能描述

支持多位教练的知识库并行管理，提供跨教练的技术对比（校准）功能。

### 核心变更

- `Coach` 实体：教练基础信息，`name` 唯一
- `analysis_tasks.coach_id`：外键关联教练
- 校准接口：对比多位教练在同一技术动作上的标准差异

### 当前教练数据（共 19 条）

| 教练名 | COS 课程 | 视频数 |
|--------|---------|--------|
| 孙浩泓 | 知行合一120集 | 125 |
| 小孙 | 接发球/步伐/实战/正反手/发球 5个子课程 | 104 |
| 沙指导 | 源动力系列250节 | 250 |
| 全世爆 | 101节 + 106节 | 207 |
| 郭焱 | 全集107节 | 107 |
| 穆静毓 | 56节 | 56 |
| 高云娇 | 42节 | 42 |
| 张蔷 | 38节 | 38 |
| 孙霆 | 勾手发球 | 27 |
| 尹航 | 国手19节 | 19 |
| 张继科 | 大师课13节 | 13 |
| 王增羿 | 直拍反手 | 7 |

> `coaches` 表与 COS 目录 1:1 对应（同名目录加数字后缀区分，如 `小孙_2`~`小孙_5`）

---

## Feature-007 处理速度优化

**状态：已完成**  
**规范：** `specs/007-processing-speed-optimization/`

### 功能描述

提升视频处理吞吐量，降低单视频处理延迟。

### 主要优化

- **并行预分割**：`ProcessPoolExecutor` 多核并行
- **FFmpeg 快速编码**：优化视频片段提取参数
- **耗时可观察性**：`analysis_tasks.timing_stats`（JSONB）记录各阶段耗时，支持性能分析

---

## Feature-008 教练视频技术分类数据库

**状态：已完成**  
**规范：** `specs/008-coach-tech-classification/`

### 功能描述

扫描 `COS_VIDEO_ALL_COCAH` 路径下所有 1015 个教练视频，基于关键词规则（+ LLM 兜底）进行乒乓球技术分类，入库后支持批量提交知识库提取任务。

### 核心组件

| 组件 | 文件 | 说明 |
|------|------|------|
| COS 扫描器 | `cos_classification_scanner.py` | 全量/增量扫描，自动同步 coaches 表 |
| 技术分类器 | `tech_classifier.py` | 关键词规则 + LLM 兜底 |
| 动作分类器 | `action_classifier.py` | 细分动作类型识别 |

### 扫描行为

- **全量扫描** (`scan_full`)：更新所有记录，跳过 `classification_source=manual`
- **增量扫描** (`scan_incremental`)：仅处理新增视频
- **coaches 同步**：扫描时自动 upsert `coaches` 表，bio 取自 COS 目录名

### 技术分类类别（21类）

`forehand_topspin`、`forehand_attack`、`forehand_push_long`、`forehand_flick`、`forehand_loop_fast`、`forehand_loop_high`、`forehand_backhand_transition`、`forehand_topspin_backspin`、`backhand_topspin`、`backhand_push`、`backhand_flick`、`backhand_loop`、`footwork`、`serve`、`receive`、`defense`、`multiball`、`grip`、`fitness`、`tactics`、`other`

### 关键 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/classifications/scan` | 触发异步扫描任务 |
| `GET` | `/api/v1/classifications/scan/{task_id}` | 查询扫描进度 |
| `GET` | `/api/v1/classifications` | 分类记录列表 |
| `GET` | `/api/v1/classifications/summary` | 按教练+技术统计汇总 |
| `PATCH` | `/api/v1/classifications/{id}` | 人工修正分类 |

### 批量提取流程

```bash
# 按技术类别批量提交知识库提取
python specs/008-coach-tech-classification/scripts/batch_extract_kb.py \
  --tech_category forehand_topspin
```

---

## Feature-009 SQL 查询脚本

**状态：已完成**  
**规范：** `specs/009-sql-query-scripts/`

### 功能描述

常用运营和调试 SQL 脚本集合，用于查询任务状态、知识库版本、分类统计等。

---

## Feature-010 构建技术标准

**状态：已完成**  
**规范：** `specs/010-build-technique-standard/`

### 功能描述

从 `ExpertTechPoint` 聚合多位教练的技术数据，生成统计标准（中位数 + P25/P75），作为运动员诊断的对比基准。

### 数据模型

- `TechStandard`：技术标准主记录（技术类别 + 知识库版本）
- `TechStandardPoint`：关键点统计值（每个关节的中位数 + 置信区间）

### 关键 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/standards` | 查询技术标准 |
| `POST` | `/api/v1/standards/build` | 触发标准构建 |

---

## Feature-011 运动员动作诊断

**状态：已完成**  
**规范：** `specs/011-amateur-motion-diagnosis/`

### 功能描述

用户提交运动员视频和技术类别，系统进行姿态分析，与 `TechStandard` 对比后生成诊断报告，包含维度评分和 LLM 改进建议。

### 核心流程

```
POST /api/v1/diagnosis (同步，60s 超时)
  ↓
视频下载 + 姿态估计
  ↓
与 TechStandard 逐维度对比
  ↓
线性插值评分（0~100）
  ↓
LLM 生成改进建议
  ↓
返回 DiagnosisReport
```

### 数据模型

- `DiagnosisReport`：诊断报告主记录
- `DiagnosisDimensionResult`：每个技术维度的偏差和评分

### 关键 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/diagnosis` | 提交诊断（同步返回） |
| `GET` | `/api/v1/diagnosis/{id}` | 查询诊断报告 |

### 特点

- **匿名模式**：无需用户账户
- **同步返回**：阻塞等待结果（≤60s）
- **评分算法**：线性插值，0=最差，100=与标准完全一致

---

## Feature-012 全量任务查询接口

**状态：已完成**  
**规范：** `specs/012-task-query-all/`

### 功能描述

扩展任务查询接口，支持全量列表查询（分页、多维筛选、排序）和任务详情聚合统计。

### 筛选维度

- `status`：任务状态
- `task_type`：任务类型（video_classification / kb_extraction / athlete_diagnosis，Feature 013 重构后）
- `coach_id`：教练
- `created_after` / `created_before`：时间范围

### 排序字段

- `created_at`（默认）
- `started_at`

### 任务详情 `summary` 字段

```json
{
  "total_segments": 42,
  "processed_segments": 42,
  "progress_pct": 100.0,
  "timing_stats": { ... }
}
```

### 关键 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/tasks` | 全量任务列表（分页+筛选+排序） |
| `GET` | `/api/v1/tasks/{task_id}` | 任务详情（含 summary） |

---

## Feature-013 任务管道重新设计

**状态：已完成（US1–US5，T001–T061）**  
**规范：** `specs/013-task-pipeline-redesign/`

### 功能描述

将原单一聚合任务（`expert_video` / `athlete_video`）拆解为三类独立管道，
实现队列物理隔离、通道容量/并发热更新、幂等提交、孤儿任务自动恢复、
管道数据一键重置。

### 核心能力

- **三类独立任务类型**：`video_classification` / `kb_extraction` / `athlete_diagnosis`
- **四队列物理隔离**：一队列一 Worker，崩溃互不影响
  | 队列 | 并发 | 默认容量 |
  |------|------|---------|
  | `classification` | 1 | 5 |
  | `kb_extraction` | 2 | 50 |
  | `diagnosis` | 2 | 20 |
  | `default` | 1 | — |
- **幂等提交**：`idempotency_key` + `pg_advisory_xact_lock` + partial unique index
- **批量提交**：单批 ≤100 条；部分成功语义（accepted/rejected + QUEUE_FULL）
- **KB 提取门槛**：`ClassificationGateService` 校验已分类且 `tech_category != 'unclassified'`
- **孤儿任务自动恢复**：Worker 启动时 sweep `status='processing' AND started_at < now-840s`
- **通道容量/并发热更新**：`PATCH /api/v1/admin/channels/{task_type}`（X-Admin-Token，30 秒生效）
- **管道数据一键重置**：TRUNCATE 流水表 + DELETE 草稿 KB；保留 coaches / classifications / standards / skills / reference_videos

### 关键 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/tasks/classification` | 提交单条分类任务 |
| `POST` | `/api/v1/tasks/kb-extraction` | 提交单条 KB 提取任务（前置门槛） |
| `POST` | `/api/v1/tasks/diagnosis` | 提交单条运动员诊断任务 |
| `POST` | `/api/v1/tasks/{type}/batch` | 三类批量提交（上限 100 条） |
| `GET` | `/api/v1/task-channels` | 所有通道实时快照 |
| `GET` | `/api/v1/task-channels/{task_type}` | 单通道实时快照 |
| `PATCH` | `/api/v1/admin/channels/{task_type}` | 热更新容量/并发（需 X-Admin-Token） |
| `POST` | `/api/v1/admin/reset-task-pipeline` | 一键重置管道（需 confirmation token） |

### 配置项（.env）

| 键 | 说明 |
|-----|------|
| `ADMIN_RESET_TOKEN` | 管理员 token（重置 + PATCH 通道均需） |
| `BATCH_MAX_SIZE` | 批量提交单次上限（默认 100） |
| `ORPHAN_TASK_TIMEOUT_SECONDS` | 孤儿任务判定阈值（默认 840） |
| `CHANNEL_CONFIG_CACHE_TTL_S` | 通道配置缓存 TTL（默认 30） |

### CLI

```bash
# 重置预览
python specs/013-task-pipeline-redesign/scripts/reset_task_pipeline.py --dry-run

# 执行重置
python specs/013-task-pipeline-redesign/scripts/reset_task_pipeline.py --confirm
```

---

## Feature-014 知识库提取流水线化

**状态：已完成（US1–US5 + 阶段 8 完善）**  
**规范：** `specs/014-kb-extraction-pipeline/`

### 功能描述

将 Feature-013 遗留的 `kb_extraction` 最小 stub 重建为**有向无环图（DAG）流水线**：一次 KB 提取作业自动拆解为 6 个子任务，无依赖分支并行执行，补齐 Feature-002 遗失的"视频直提专业知识库"能力（姿态序列 → 视觉路 + 音频讲解 → LLM 抽取 → 冲突分离入审核表）。

### 核心能力

- **DAG 子任务编排**：`download_video → (pose ∥ audio_transcribe) → (visual_kb ∥ audio_kb) → merge_kb`
- **作业级通道计数**：一作业 = 1 个 `kb_extraction` 槽位，子步骤并行**不外扩**通道预算（FR-015）
- **双路知识提取 + 冲突分离**：视觉路 + 音频路两路产出 → 差异 >10% 进独立 `kb_conflicts` 表，非冲突条目进 `expert_tech_points`
- **降级模式**：音频路失败不阻塞主流程，`merge_kb` 仅合入视觉条目
- **局部重跑**：`POST /extraction-jobs/{id}/rerun` 只重置 failed + 下游 skipped 步骤，success step 的 artifact + output_summary 直接复用
- **force 覆盖**：`force=true` 覆盖已 success 作业时，旧冲突项自动标 `superseded_by_job_id` 隐藏审核队列
- **分层超时**：作业级 45 min + 单步级 10 min（`asyncio.wait_for`）
- **分层重试**：I/O 步骤（download/audio 转写/audio_kb LLM）3 次 × 30 s（tenacity）；CPU 步骤不重试
- **中间结果保留期**：success 24 h / failed 7 天；Celery beat 每小时清理过期
- **孤儿步骤恢复**：Worker 启动 sweep `pipeline_steps.status='running' AND started_at < now-600s` → failed + 传播 skipped + 作业标 failed

### 关键 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/tasks/kb-extraction` | 提交单条 KB 提取（Feature-013 保留，内部扩展创建 ExtractionJob + 6 steps） |
| `GET` | `/api/v1/extraction-jobs` | 分页列表（`page/page_size/status` 过滤，page_size 上限 100） |
| `GET` | `/api/v1/extraction-jobs/{job_id}` | 单作业详情（子任务清单 + 依赖图 + 进度 + 冲突计数） |
| `POST` | `/api/v1/extraction-jobs/{job_id}/rerun` | 重跑失败作业（可选 `force_from_scratch`） |

### 配置项（.env）

| 键 | 默认值 | 说明 |
|-----|--------|------|
| `EXTRACTION_JOB_TIMEOUT_SECONDS` | 2700 | 作业级超时（45 min） |
| `EXTRACTION_STEP_TIMEOUT_SECONDS` | 600 | 单步超时（10 min） |
| `EXTRACTION_ARTIFACT_ROOT` | `/tmp/coaching-advisor/jobs` | Worker 本地中间文件根目录 |
| `EXTRACTION_SUCCESS_RETENTION_HOURS` | 24 | 成功作业中间结果保留 |
| `EXTRACTION_FAILED_RETENTION_HOURS` | 168 | 失败作业中间结果保留（7 天） |

### 数据模型

- `extraction_jobs`：作业顶级容器（status / worker_hostname / force / superseded_by_job_id / intermediate_cleanup_at）
- `pipeline_steps`：6 行/作业，step_type 枚举 + status + output_summary JSONB + output_artifact_path
- `kb_conflicts`：维度粒度冲突表（visual_value / audio_value / resolution 字段）
- `analysis_tasks` 新增 `extraction_job_id` FK（SET NULL on delete）

### 冲突审核协议

- `kb_conflicts.resolved_at IS NULL AND superseded_by_job_id IS NULL` → 待审核
- 审核字段预留：`resolved_by` / `resolution` (`use_visual` | `use_audio` | `use_custom` | `reject_both`) / `resolution_value`
- 审核 UI/API 不在本 Feature 范围，仅提供存储层

### 新 Celery beat

- `cleanup-extraction-artifacts`：每小时一次，扫描 `extraction_jobs.intermediate_cleanup_at <= now()`，删本地目录 + 清空 `output_artifact_path`

---

## Feature-015 真实算法接入（知识库提取流水线）

### 背景

Feature-014 交付了 DAG 编排 + 并行 + 冲突分离 + 重跑 + 通道兼容的完整骨架，但 4 个 step executor（`pose_analysis` / `audio_transcription` / `visual_kb_extract` / `audio_kb_extract`）是 scaffold——读写空 artifact、产出 `note="scaffold_output_pending_..."`。Feature-015 只做"接线"：把 scaffold 替换为 Feature-002 既有算法模块的真实调用。

### 交付范围

**改造 4 个 executor**：
1. `pose_analysis` 接入 `video_validator.validate_video` + `pose_estimator.estimate_pose`（YOLOv8 GPU / MediaPipe CPU）
2. `audio_transcription` 接入 `AudioExtractor.extract_wav` + `SpeechRecognizer.recognize`（Whisper）
3. `visual_kb_extract` 接入 `action_segmenter` + `action_classifier` + `tech_extractor`（4 维度规则抽取）
4. `audio_kb_extract` 接入 `TranscriptTechParser` + `LlmClient`（Venus → OpenAI fallback）

**新增辅助模块**：
- `src/services/kb_extraction_pipeline/artifact_io.py`：`pose.json` / `transcript.json` 读写 + 容错解析（FR-002/FR-007/Q4）
- `src/services/kb_extraction_pipeline/error_codes.py`：9 个结构化错误码前缀 + `format_error()` 工具（FR-016）

**不变**：数据库 schema（无迁移）、API 路由、Celery 任务注册、包依赖、算法阈值。

### 核心 API

Feature-015 不新增路由。运维使用的命令行工具：

| 工具 | 用途 |
|------|------|
| `specs/015-kb-pipeline-real-algorithms/scripts/run_reference_regression.py --manifest <json> --output <md>` | US3 回归：manifest 模式，比对 `expected_items_min..max` |
| `--random-sample N` | 从 `/classifications?kb_extracted=false` 抽 N 个视频做批次采样（SC-005 / SC-006 口径）|
| `--measure-wallclock` | US4：比对真实耗时 vs manifest 中 `baseline_f002_seconds`（SC-002 ≤0.9×）|

### 可观测性输出（FR-014）

每个 executor 的 `pipeline_steps.output_summary` 暴露真实算法后端，不再是 `"scaffold"`：

- `pose_analysis`：`backend=yolov8|mediapipe`、`fps`、`resolution`、`keypoints_frame_count`
- `audio_transcription`：`whisper_model`、`language_detected`、`snr_db`、`quality_flag`、`transcript_chars`
- `visual_kb_extract`：`backend=action_segmenter+tech_extractor`、`segments_processed`、`segments_skipped_low_confidence`
- `audio_kb_extract`：`llm_model`、`llm_backend=venus|openai`、`parsed_segments_total`、`dropped_low_confidence`、`dropped_reference_notes`

### 错误码前缀

失败时 `pipeline_steps.error_message` 以 `<CODE>: <details>` 格式开头，便于 `grep` 映射到 runbook。9 个前缀详见 `docs/architecture.md § Feature-015`。

### 参考视频 manifest

`specs/015-kb-pipeline-real-algorithms/reference_videos.json`：3 条占位条目，运维填真实 COS key + 预期条目数范围 + 可选的 Feature-002 耗时基线。

### 验证状态

详见 `specs/015-kb-pipeline-real-algorithms/verification.md`：
- CI 自动化覆盖 SC-001（visual/audio 部分）+ SC-004（结构化错误码）
- SC-002 / SC-003 / SC-005 / SC-006 需要部署环境 + 真实视频集回归

---

## Feature-016 视频预处理流水线

**状态：已完成**  
**规范：** `specs/016-video-preprocessing-pipeline/`

### 背景

Feature-015 部署烟测（2026-04-25）暴露两个核心问题：

1. **内存峰值不可控**：`pose_analysis` 对整段大视频一次性 `estimate_pose` 触发 OOM-killed（pod memcg 64 GB）；Feature-007 已用"180s 分段 + 顺序处理"成功绕开，但 Feature-015 未继承
2. **重复计算**：rerun / 多 tech_category 并行提取每次都要重新下载 + 转码 + 切分，浪费带宽和 CPU
3. **Whisper OOM**：torch CUDA 初始化占 58 GB 虚地址擞 pod memcg

### 功能描述

在 KB 提取前新增预处理阶段：下载 → probe + 质量门禁 → 转码标准化 → 按 180s 切分 → 流式并发上传 COS + 同步产出整段 16 kHz mono WAV。产物按 `job_id` 隔离写入 `preprocessed/{cos_key}/jobs/{job_id}/`。

### 核心能力

- **新第五个通道 `preprocessing`**（3 并发 / 容量 20，可热更新）
- **流式切分 + 并发上传**：ffmpeg 顺序切段 + `ThreadPoolExecutor(max_workers=2)` 并发上传；主线程不被上传阻塞（沿用 Feature-007 commit `8713543` 实证模式）
- **作业级隔离**：`force=true` 覆盖 → 旧 job 标 `superseded`（保留 DB 审计） + 同步删除旧 COS 对象；新 job 放独立子目录避免并发读写竞争
- **质量门禁前移**：probe 阶段即调 `validate_video`，fps / 分辨率不达标立即 `VIDEO_QUALITY_REJECTED:` failed，不进转码/分段/上传
- **本地 24h 温缓存**：`EXTRACTION_ARTIFACT_ROOT/preprocessing/{job_id}/` 统一保留 24h（success / failed 不区分）；beat 每小时扫描，删前检查 mtime/atime 防止误删
- **COS 存在性门禁 + 本地优先读取**：KB 提取消费预处理产物时，先 COS head 校验存在防止幽灵数据，通过后本地优先 → 缺失再从 COS 下载
- **懒检测产物丢失**：KB 提取下载 404 → `SEGMENT_MISSING:` / `AUDIO_MISSING:`；运维手动 `force=true` 重建恢复；不引入主动 verify 基础设施
- **Whisper 强制 CPU**：预处理一次性产出 16 kHz mono WAV；`audio_transcription` 直接从 COS 拉音频喂 Whisper，无需从视频实时提取

### 4 个 step executor 改造（仅数据入口层）

| Executor | Feature-016 改造 |
|----------|-----------------|
| `pose_analysis` | 按 segments 表迭代单段 `estimate_pose`，累积 frames 到 pose.json；单段 RSS < 整体 50% |
| `audio_transcription` | 从 COS 拉 `audio.wav` 直接喂 Whisper（CPU small，1–2 GB RSS） |
| `visual_kb_extract` / `audio_kb_extract` / `merge_kb` | 不变 |

### 关键 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/tasks/preprocessing` | 单条视频预处理提交（支持 `force`） |
| `POST` | `/api/v1/tasks/preprocessing/batch` | 批量提交（上限 100，部分成功语义） |
| `GET` | `/api/v1/video-preprocessing` | 分页列表：按 `status` / `cos_object_key` 过滤，运维/前端任务中心入口（预处理任务独立于 `/api/v1/tasks`） |
| `GET` | `/api/v1/video-preprocessing/{job_id}` | 审计查询：原视频元数据 + 标准化参数 + 音频 + 分段列表 |

### 数据模型

- `video_preprocessing_jobs`：作业顶级容器（status ∈ running/success/failed/superseded；partial unique index on `cos_object_key WHERE status='success'`）
- `video_preprocessing_segments`：每段一条记录，`(job_id, segment_index)` 唯一；`job_id` CASCADE DELETE
- `coach_video_classifications.preprocessed: bool`（新增字段）：至少一次 success 即置 true

### 配置项（.env / Settings）

| 键 | 默认值 | 说明 |
|-----|--------|------|
| `VIDEO_PREPROCESSING_SEGMENT_DURATION_S` | 180 | 分段秒数阈值（Feature-007 实证值） |
| `VIDEO_PREPROCESSING_TARGET_FPS` | 30 | 标准化目标帧率 |
| `VIDEO_PREPROCESSING_TARGET_SHORT_SIDE` | 720 | 标准化目标短边像素 |
| `PREPROCESSING_LOCAL_RETENTION_HOURS` | 24 | 本地产物保留时长 |
| `PREPROCESSING_UPLOAD_CONCURRENCY` | 2 | ThreadPoolExecutor max_workers |

### 结构化错误前缀（8 类）

`VIDEO_DOWNLOAD_FAILED:` / `VIDEO_PROBE_FAILED:` / `VIDEO_QUALITY_REJECTED:` / `VIDEO_CODEC_UNSUPPORTED:` / `VIDEO_TRANSCODE_FAILED:` / `VIDEO_SPLIT_FAILED:` / `VIDEO_UPLOAD_FAILED:` / `AUDIO_EXPORT_FAILED:`；KB 提取消费侧新增：`SEGMENT_MISSING:` / `AUDIO_MISSING:`

### 性能指标（SC）

- SC-001：10 分钟大视频端到端无 OOM / SIGKILL
- SC-002：`pose_analysis` 单段峰值 RSS < 原视频整体处理峰值 50%
- SC-003：同视频第 N 次 KB 提取（N≥2）耗时相比第 1 次降低 ≥ 30%
- SC-004：预处理失败率 ≤ 5%（排除源编码不支持）
- SC-005：分段时长误差 < 1 秒，累计误差 < 原时长 1%
- SC-006：预处理耗时 ≤ 原视频时长 × 5
- SC-007：100% 失败带结构化错误前缀


## Feature-017 API 规范化

**状态：已完成**
**规范：** `specs/017-api-standardization/`
**章程依据：** v1.4.0 原则 IX
**合入策略：** Big Bang（无废弃期，无 `/api/v2`；同一合入窗口内前后端联动切换）

### 背景

Feature-001 ~ Feature-016 累积 16 个功能迭代后，API 外观出现三类漂移：
1. **响应体形态各异**：列表接口混用 `{data:[], total}` / `{items:[], total, page, page_size}` / 裸数组；错误体混用 `{detail}` / `{error:{code,message}}` / 裸字符串
2. **命名不一致**：`/videos/classifications` vs `/coaches` 路径层级混乱；`{id}` / `{cos_object_key}` / `{coach_id}` 路径参数命名不统一；`limit/offset` vs `page/page_size` 分页参数并存
3. **错误码散落**：25+ 个裸字符串错误码写死在路由层 `HTTPException(detail={"code":"X"})` 中，无集中化、无 CI 阻断

### 功能描述

本 Feature **只重塑 API 外观，不改变业务行为**，四条主线并进：
- **US1**（P1）：响应体统一信封 `SuccessEnvelope` / `ErrorEnvelope`
- **US2**（P1）：物理删除 7 条废旧接口（v2.0.0 后直接移除，不再保留哨兵路由）
- **US3**（P2）：路径命名统一 kebab-case + `{resource_id}` + `page/page_size` 分页 + 枚举归一化
- **US4**（P2）：错误码集中化 38 个 `ErrorCode` 枚举 + CI 阻断裸字符串

### 核心能力

- **统一响应信封**（`src/api/schemas/envelope.py`）：
  - `SuccessEnvelope[T]` 泛型 + `ok(data)` / `page(items, page=, page_size=, total=)` 构造器
  - `ErrorEnvelope` 错误信封 + 全局异常处理器（`src/api/errors.py::register_exception_handlers`）
  - 顶层 `success` 布尔位作为判别式；JSON Schema 约束见 `contracts/response-envelope.schema.json`
- **集中化错误码**（38 个 `ErrorCode` 枚举）：
  - 三元同步：`ErrorCode` ↔ `ERROR_STATUS_MAP`（HTTP 状态）↔ `ERROR_DEFAULT_MESSAGE`（默认消息）
  - 已发布错误码禁止改名或更换 HTTP 状态，只允许新增
  - `AppException(ErrorCode.XXX, message=..., details=...)` 替代所有 `HTTPException`
- **统一分页参数**：
  - `page`（ge=1，默认 1）+ `page_size`（ge=1, le=100，默认 20）
  - Pydantic `Query` 硬约束，越界自动 422 + `VALIDATION_FAILED`，**禁止静默截断**
  - 禁用 `limit/offset/skip/take/pageNum/pageSize`
- **枚举归一化**（`src/api/enums.py`）：
  - `normalize_enum_value`：strip + lower + (`-` → `_`)
  - `parse_enum_param(value, field, enum_cls)`：绑定到 str Enum 类
  - `validate_enum_choice(value, field, allowed)`：白名单校验
- **下线策略**（章程 v2.0.0 原则 IV + IX）：
  - 7 条已下线接口采用**直接物理删除**，老路径由 FastAPI 默认 404 `NOT_FOUND`拥有
  - 迁移说明在 Feature changelog / `spec.md`「业务阶段映射」一次性简述
- **CI Linter 双管齐下**：
  - `scripts/lint_api_naming.py`：路径命名 + 分页参数 + 禁用 limit/offset
  - `scripts/lint_error_codes.py`：业务代码裸字符串错误码 / `raise HTTPException` 扫描

### 已下线接口（7 条）

| 旧端点 | 替代 |
|--------|------|
| `POST /api/v1/tasks/expert-video` | `POST /api/v1/tasks/classification` + `POST /api/v1/tasks/kb-extraction` |
| `POST /api/v1/tasks/athlete-video` | `POST /api/v1/tasks/diagnosis` |
| `GET /api/v1/videos/classifications` | `GET /api/v1/classifications` |
| `POST /api/v1/videos/classifications/refresh` | `POST /api/v1/classifications/scan` |
| `PATCH /api/v1/videos/classifications/{cos_object_key}` | `PATCH /api/v1/classifications/{id}` |
| `POST /api/v1/videos/classifications/batch-submit` | `POST /api/v1/tasks/kb-extraction/batch` |
| `POST /api/v1/diagnosis` | `POST /api/v1/tasks/diagnosis`（同步 → 异步）|

### 搬迁（1 处）

- `PATCH /tasks/{task_id}/coach` 从 `coaches.py` 搬迁至 `tasks.py`（资源归属 task）；路径不变，仅跨文件剪切

### 性能指标（SC）

- SC-001：统一信封 100% 覆盖所有 `/api/v1/**` 接口
- SC-002：7 条废旧接口 100% 已物理删除（v2.0.0 后不再保留哨兵）
- SC-003：前后端联动切换后接口合约测试全绿
- SC-004：CI 扫描脚本 0 违规（裸字符串错误码 + 命名规范）
- SC-005：`/api/v1/videos/classifications*` / `/api/v1/diagnosis` 直接物理删除（FastAPI 默认 404）
- SC-006：8 条主要业务端点 `curl` 手工验证响应体含 `success` 布尔位
- SC-007：命名规范一致，后续新 Feature 无需扩展 linter 规则
- SC-008：新成员 `docs/api-standardization-guide.md` 5 分钟内理解
- SC-009：OpenAPI 契约 100% 引用 `SuccessEnvelope` / `ErrorEnvelope` schema

### 新成员入口文档

- **一般开发**：`docs/api-standardization-guide.md`（10 节，含路径/信封/分页/枚举/错误码/下线/TDD/Pre-merge 自检清单/FAQ）
- **架构细节**：本文档对应章节 + `docs/architecture.md` 「API 接口层」
- **完整规范**：`specs/017-api-standardization/` 目录


---

## Feature-018 业务工作流标准化

**状态：已完成（US1+US2+US3，MVP 38/54 任务已交付）**
**规范：** `specs/018-workflow-standardization/`
**章程依据：** v1.5.0 原则 X（业务工作流对齐）

### 背景

项目章程 v1.4.0 未将业务流程作为一等约束，导致 Feature-001 ~ Feature-017 在迭代中累积两类漂移：
1. **代码↔文档漂移**：错误码前缀、状态机枚举、诊断评分阈值、通道种子等关键约束在代码修改时，`docs/business-workflow.md` 未同步刷新
2. **业务视角缺失**：缺乏一个可以一次性看到「三阶段 × 八步骤」全链路计数、耗时、吞吐的总览接口；`analysis_tasks` / `extraction_jobs` 等四张业务表缺少业务阶段语义列

### 功能描述

本 Feature 将业务工作流约束升格为**章程级一等原则**（原则 X），并通过四条主线落地到可执行 / 可观测 / 可守卫的基础设施：

- **US1（P1）**：业务阶段总览接口 `GET /api/v1/business-workflow/overview`
- **US2（P2）**：工作流漂移 + spec 合规离线扫描器 + pre-push hook
- **US3（P2）**：优化杠杆台账 `GET /api/v1/admin/levers` + `config/optimization_levers.yml` 双向同步

### 核心能力

- **业务阶段双列下沉**（迁移 0016）：四张业务表（`analysis_tasks` / `extraction_jobs` / `video_preprocessing_jobs` / `tech_knowledge_bases`）统一新增 `business_phase` + `business_step` 双列
  - PostgreSQL enum type `business_phase_enum`（`TRAINING` / `STANDARDIZATION` / `INFERENCE`）
  - 列级 `NOT NULL` 作兜底；`analysis_tasks` 上建 `(business_phase, business_step)` 复合索引、`extraction_jobs` 上建 `(business_phase)` 单列索引
  - **ORM 钩子派生**：`src/models/_phase_step_hook.py` 注册 `before_insert` 事件，新行自动派生两列；未知 `task_type` 或只传单列 ⇒ `ValueError(PHASE_STEP_UNMAPPED)` fail-fast
  - 业务代码 MUST NOT 手动填充两列（单一事实来源规则）
- **业务阶段总览接口**（US1 `GET /business-workflow/overview`）：
  - 一次请求返回三阶段 × 八步骤的计数、P50/P95 耗时、近 24h 吞吐
  - 响应信封 `meta` 含 `generated_at`（CST）/ `window_hours` / `degraded` / `degraded_reason`
  - **降级档**：`analysis_tasks` 行数 > 100 万时省略 P50/P95（节省聚合代价），仅保留计数与吞吐；> 1000 万行超出本 Feature 范围（留给物化视图 Feature）
  - 路由层手工 `JSONResponse` 绕过 `SuccessEnvelope.meta` 严格校验（与分页 meta 不同构）
- **工作流漂移扫描器**（US2 `scripts/audit/workflow_drift.py`）：
  - 8 类漂移：错误码前缀 / 状态机枚举 / 评分公式阈值 / 通道种子 / § 9 三类杠杆清单 / spec-template 子标签 / `config/optimization_levers.yml` 一致性 / 阶段步骤总表
  - `scripts/audit/.scan-exclude.yml` 历史静态排除清单（`specs/001-*/` ~ `specs/017-*/` 共 17 目录免扫）
  - **不支持 waiver**：任何漂移 ⇒ `exit 1` ⇒ CI 阻断
- **Spec 合规扫描器**（US2 `scripts/audit/spec_compliance.py`）：
  - 校验 `specs/*/spec.md` 中「业务阶段映射」六项子标签完整性（FR-020）
- **Pre-push hook**：
  - `scripts/git-hooks/pre-push` 调用 `make drift-changed` 阻断含漂移的 push
  - `scripts/install-git-hooks.sh` 幂等安装（首次 clone 后执行一次）
  - hotfix 统一走「代码修改 + 文档同步」原子 PR（Clarification Q5）
- **优化杠杆台账**（US3 `GET /api/v1/admin/levers`）：
  - 一次请求返回三类杠杆（运行时参数 / 算法模型 / 规则 Prompt）的当前值、生效路径、重启范围、阶段归属
  - 敏感键（`COS_SECRET_KEY` / `VENUS_TOKEN` / `OPENAI_API_KEY` / `POSTGRES_PASSWORD` 等）仅返回 `is_configured: bool`，不泄露明文
  - `config/optimization_levers.yml` 作为单一事实来源，与 `docs/business-workflow.md` § 9 表格双向同步；漂移由 `workflow_drift.py` 守护
- **CI 对接策略**（Clarification Q6 选项 A）：
  - 不引入托管 CI 平台配置（GitHub Actions / Jenkins 等）
  - `Makefile` 声明 `drift-changed` / `drift-full` / `spec-compliance` 三目标，本地 / hook / 未来 CI 统一调用
  - 未来接入任何 CI 平台时，仅需调用两个 Makefile 目标即可对接

### 关键 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/business-workflow/overview` | 业务阶段总览（`?window_hours=1..168`，默认 24） |
| `GET` | `/api/v1/admin/levers` | 优化杠杆台账（可选 `?phase=TRAINING/STANDARDIZATION/INFERENCE` 过滤） |

### 数据模型变更

- **迁移 0016_business_phase_step**：
  - 创建 `business_phase_enum` PG enum type
  - 四张业务表 `ADD COLUMN business_phase business_phase_enum` + `ADD COLUMN business_step VARCHAR(64)`（先 NULL 可空）
  - 单事务回填（按 `task_type` 派生规则，当前数据量可一次完成）
  - `ALTER COLUMN SET NOT NULL`
  - 创建两个索引（`analysis_tasks.phase_step` 复合索引 / `extraction_jobs.phase` 单列索引）
  - `downgrade` 反向五步，可完整回滚

### 配置项

| 键 | 默认值 | 说明 |
|-----|--------|------|
| `config/optimization_levers.yml` | — | 三类杠杆台账，与 § 9 表格双向同步 |
| `scripts/audit/.scan-exclude.yml` | — | 历史静态排除清单 |

### 章程更新

- **v1.5.0 原则 X（业务工作流对齐）**：新增为强制原则，要求所有新 Feature 的 `spec.md` 包含「业务阶段映射」六项子标签（`phase` / `step` / `upstream_state` / `downstream_state` / `observability_delta` / `optimization_lever_delta`），由 `spec_compliance.py` 守护

### 性能指标（SC）

- SC-001：`GET /business-workflow/overview` 单次请求 P95 ≤ 2s（≤ 100 万行数据量）
- SC-002：ORM `before_insert` 钩子开销 ≤ 0.5ms（单行 insert）
- SC-003：`make drift-full` 全量扫描 ≤ 10s
- SC-004：`make drift-changed`（pre-push 场景）≤ 2s
- SC-005：漂移扫描 0 误报（所有 8 类漂移通过参数化测试锁定）
- SC-006：敏感键 100% 不泄露明文（`GET /admin/levers` 参数化测试验证）

### 验证与守卫

- 56 unit/contract/integration tests 全绿
- `specs/018-workflow-standardization/tasks.md` 54 任务，MVP 38 已交付
- `specs/018-workflow-standardization/analyze-report.md` 17 项发现，零 CRITICAL / HIGH
- 本地 pre-push hook 已在 master + 特性分支两次 push 中真实触发并通过（`[workflow_drift] no drift.` + `[spec_compliance] all specs compliant.`）

### 延后任务（非 MVP，文档登记）

- T015：迁移 down/up 完整往返集成测试
- T050：性能压测（≥ 80 万行数据量下 P95 验证）
- T053：quickstart 手工走查
- T054：CHANGELOG 条目

---

## Feature-020 运动员推理流水线

**状态：已完成**

**核心价值**：一次扫描 + 批量调用 = 从"运动员视频上传到 COS"到"拿到综合得分诊断报告"的全链路闭环，运营侧**零手工填写 `video_storage_uri`**。

### 业务流程

```
COS 根路径（athletes/） → [扫描入库] athlete_video_classifications
         → [预处理批量] analysis_tasks(athlete_video_preprocessing) + VideoPreprocessingJob
         → [诊断批量]   analysis_tasks(athlete_diagnosis) + DiagnosisReport
         → [反查清单]   GET /diagnosis-reports?athlete_id=... / cos_object_key=... / preprocessing_job_id=...
```

### 新增 API

| 方法 | 路径 | 职责 |
|------|------|------|
| POST | `/api/v1/athlete-classifications/scan` | 触发运动员根路径全量/增量扫描 |
| GET | `/api/v1/athlete-classifications/scan/{task_id}` | 扫描进度查询 |
| GET | `/api/v1/athlete-classifications` | 素材清单（支持 athlete_id / has_diagnosis / tech_category / preprocessed 等复合筛选）|
| POST | `/api/v1/tasks/athlete-preprocessing/batch` | 批量提交运动员视频预处理 |
| POST | `/api/v1/tasks/athlete-diagnosis/batch` | 批量提交运动员诊断 |
| GET | `/api/v1/diagnosis-reports` | 诊断报告聚合查询（9 维度过滤 + 排序 + 分页）|

### 数据模型变更（迁移 0018）

- **新增 2 表**：`athletes`（可选补表）、`athlete_video_classifications`（与 `coach_video_classifications` 双向独立，**零交叉污染**）
- **新增列**：`diagnosis_reports.cos_object_key` / `preprocessing_job_id` / `source`（用于三要素追溯）
- **新增 `task_type` 枚举**：`athlete_video_classification` / `athlete_video_preprocessing`
- **新增 `business_step` 枚举**：`scan_athlete_videos` / `preprocess_athlete_video`（`INFERENCE` 阶段）

### 任务监控隔离（SC-004）

`GET /api/v1/tasks?business_phase=INFERENCE` 100% 只返回运动员侧三类任务；`business_phase=TRAINING` 则只返回教练侧；两侧求和 == 全量，交集为空。`(TRAINING, scan_athlete_videos, *)` 等矛盾组合 400 `INVALID_PHASE_STEP_COMBO`。

### 错误码（6 个）

| 错误码 | HTTP | 触发场景 |
|--------|------|----------|
| `ATHLETE_ROOT_UNREADABLE` | 502 | 扫描时 COS 根路径不可读 / 凭证无效 |
| `ATHLETE_DIRECTORY_MAP_MISSING` | 500 | 目录映射配置文件缺失 |
| `ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND` | 404 | 素材记录不存在 |
| `ATHLETE_VIDEO_NOT_PREPROCESSED` | 409 | 未预处理直接诊断 |
| `ATHLETE_VIDEO_POSE_UNUSABLE` | 422 | 姿态提取全程无可用关键点 |
| `STANDARD_NOT_AVAILABLE` | 409 | 目标 `tech_category` 暂无 active 标准 |

### 成功标准（8 项 SC）

- **SC-001 ~ SC-008** 全部达成；核心验证：
  - SC-004（监控隔离）：`test_business_phase_filter_isolation.py` 5 集成测试
  - SC-005（报告三要素锚点）：`test_athlete_reports_list.py` 8 合约测试
  - SC-006（双表零交叉污染）：`athlete_classifications.py` import 禁用 `coach_video_classification` ORM

### 复用与边界

- **复用**：F-008 COS 扫描骨架 / F-016 视频预处理 / F-011 偏差+建议生成器 / F-018 阶段步骤钩子 / Beat `sweep_orphan_jobs` + `cleanup_intermediate_artifacts`
- **新增 0 队列**：运动员诊断沿用现有 `diagnosis` 队列，预处理沿用 `preprocessing` 队列
- **范围外**：运动员数据遗忘权（删除接口）；另开独立 Feature 承载

### 关键里程碑

- T001–T007：Setup + 阶段 0 基础（迁移、配置、目录映射、错误码、schemas）
- T008–T025：US1 素材归集（扫描 / 分类 / 幂等）
- T026–T038：US2 预处理 + US3 诊断（批量入口 + 异步链路）
- T047–T051：US4 监控隔离（业务阶段 / 步骤过滤）
- T052–T057：US5 报告聚合查询（`/diagnosis-reports` + 复合筛选）
- T058–T065：完善（quickstart 走查 / 漂移守卫 / 全量回归 / 迁移说明 / orphan sweep 验证）

---

## 全链路时区统一（基础治理）

**状态：已完成**
**类型：** 横向基础改造（不走 `specs/NNN-xxx/` 流程，面向整个仓库）

### 背景

早期实现中部分模块按 UTC 写入数据库、部分按北京时间写入；前端读到的时间要么是 `2026-04-28T03:35:06Z`（UTC），要么是带 `+08:00` 的北京时间，造成"看到的时间要减 8 小时"的体验问题。

### 功能描述

全项目时间口径统一为**北京时间（Asia/Shanghai, naive）**，不再存在 UTC 与北京时间的转换需求。

### 核心变更

- **统一时间工具**：新增 `src/utils/time_utils.py::now_cst()` 作为全项目唯一当前时间入口，返回 naive `datetime`（已剥离 tzinfo）
- **ORM 模型**：22 个模型文件统一将 `TIMESTAMP(timezone=True)` 改为 `TIMESTAMP(timezone=False)`；`server_default` / `onupdate` 统一使用 `text("timezone('Asia/Shanghai', now())")`，不依赖会话时区设置
- **Alembic 迁移**：14 个迁移文件同步修改，保证 `downgrade/upgrade` 可重放
- **业务代码**：17 个文件 37 处 `datetime.now(timezone.utc)` 统一替换为 `now_cst()`；移除所有 `timezone.utc` / `_tz.utc` / `UTC` 导入
- **Celery**：`timezone="Asia/Shanghai"` + `enable_utc=False`，确保 beat 调度、任务时间戳与业务时间一致
- **API 序列化**：返回形如 `"2026-04-28T11:39:11.287972"`（无 `Z`、无 `+08:00`），前端直接可视为北京时间展示

### 约束（项目规范）

- 禁止使用 `datetime.now(timezone.utc)` / `datetime.utcnow()` / `datetime.now(UTC)`
- 禁止模型和迁移中使用 `TIMESTAMP(timezone=True)`
- 禁止 `server_default=func.now()`（依赖会话时区），统一使用 `timezone('Asia/Shanghai', now())` 显式拼接
- 所有时间入口统一调用 `from src.utils.time_utils import now_cst`

### 验证

- 全量测试 781 passed / 45 skipped / 0 failed
- 数据库重建后写入样本：`started_at = 2026-04-28 11:39:11.287972`，与系统北京时间一致
- API 响应字段 `"started_at": "2026-04-28T11:39:11.287972"`，前端无需任何时区转换

---

## Feature-019 KB 按技术类别独立生命周期

**状态：MVP 已完成（US1+US2+US3，P1 三故事全部交付）**

### 背景

Feature-014 / 017 原设计：`tech_knowledge_bases.version` 为单列 VARCHAR 主键，全局任意时刻仅 1 行 `status='active'`，导致批准"反手拉"草稿会把"正手攻球"的 active 同时归档（跨类别副作用）。

### 设计决议

1. **主键语义重构**：`tech_knowledge_bases` 主键从 `(version VARCHAR)` 改为 `(tech_category, version INTEGER)` 复合键
2. **单 active 约束作用域 = 单 tech_category**：由 partial unique index `uq_tech_kb_active_per_category ON tech_knowledge_bases (tech_category) WHERE status='active'` 在 DB 层强制
3. **teaching_tips 与 KB 绑同生命周期**：新增 `tech_category / kb_tech_category / kb_version / status` 四列，删除老 `action_type`；KB approve 事务内联动批量归档/激活（`source_type='human'` 不参与批量）
4. **tech_standards 按单类别 build**：`POST /standards/build` 强制 `tech_category` 必填，删除"不传就全量"老路径；新增 `source_fingerprint` 列 + partial unique index 支持 FR-019 幂等检查

### 核心业务行为

- **US1 跨类别隔离审批**：批准 `(backhand_topspin, v1)` 时，`(forehand_attack, *)` active 保持不变
- **US2 列表 + 过滤 + 反查**：`GET /api/v1/knowledge-base/versions?tech_category=&status=&extraction_job_id=`；详情接口返回 `dimensions_summary`
- **US3 幂等 standards build**：同 active KB + 相同 points 指纹二次 build 返回 409 `STANDARD_ALREADY_UP_TO_DATE`

### 数据库迁移

迁移 `0017_kb_per_category_redesign.py` 单次迁移完成：
- 显式 `drop_constraint` 摘除 5 张 FK 引用表（`expert_tech_points` / `analysis_tasks` / `reference_videos` / `skill_executions` / `athlete_motion_analyses`）的老单列 FK，禁用 `DROP ... CASCADE`（保 DDL 可审计性）
- 重建 `tech_knowledge_bases` 复合主键结构 + `uq_tech_kb_active_per_category` partial unique index + `idx_tech_kb_extraction_job` / `idx_tech_kb_status`
- 5 张 FK 引用表 `drop_column` 老单列 + `add_column` `kb_tech_category` + `kb_version` + `create_foreign_key` 复合 FK（每表独立 NULL/ondelete 策略）
- `teaching_tips` 先 DELETE 清空，删 `action_type` 列，加 4 新列，加复合 FK CASCADE
- `tech_standards.source_fingerprint CHAR(64)` 新列 + `uq_ts_fingerprint_per_category` 局部唯一索引

### API 变更

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/knowledge-base/versions` | GET | 列表 + 过滤 + 分页（新）|
| `/api/v1/knowledge-base/versions/{tech_category}/{version}` | GET | 详情（新）|
| `/api/v1/knowledge-base/versions/{tech_category}/{version}/approve` | POST | 按类别审批（新）|
| `/api/v1/knowledge-base/{version}` | GET | 已物理删除（v2.0.0，FastAPI 默认 404）|
| `/api/v1/knowledge-base/{version}/approve` | POST | 已物理删除（v2.0.0，FastAPI 默认 404）|
| `/api/v1/standards/build` | POST | `tech_category` 改为必填；批量模式下线 |

### 错误码（4 个新增，全部 HTTP 409）

- `KB_CONFLICT_UNRESOLVED` — 目标 KB 下存在 `conflict_flag=true` 的 points
- `KB_EMPTY_POINTS` — `point_count=0` 的 KB 不可 approve
- `NO_ACTIVE_KB_FOR_CATEGORY` — 诊断读 / standards build 时该类别尚无 active KB
- `STANDARD_ALREADY_UP_TO_DATE` — 同类别 build 指纹一致（幂等拒绝）

### 测试验证

- 合约测试：`tests/contract/test_kb_per_category.py`（8 测，含跨类别隔离核心验收）+ `tests/contract/test_standards_build_per_category.py`（4 测，含 build 幂等）
- 集成测试：`tests/integration/test_0017_migration_roundtrip.py`（3 测，含 SC-006 upgrade/downgrade 幂等 3 次 + FR-002/FR-004 DB 级强约束）
- 15/15 Feature-019 新测试 PASSED；回归 841 passed / 43 skipped

### 章程合规

- **原则 IX**：老单列 version 路径已物理删除（v2.0.0 後不再保留哨兵）；4 个新错误码同步 `src/api/errors.py` 三张映射表
- **原则 X**：`docs/business-workflow.md` § 4.2 单 active 措辞 / § 4.3 状态机作用域 / § 7.2 步骤级 tag / § 7.4 错误码表四处双向同步

### Post-landing Hotfix（2026-04-30）

- **问题**：Feature-019 将 `AnalysisTask.knowledge_base_version` 从物理列改为基于复合列 `(kb_tech_category, kb_version)` 拼接的只读 property，但 `TaskSubmissionService._insert_row` 仍尝试对 property 赋值 ⇒ 任何 KB 抽取/诊断任务提交 500
- **修复**：`src/services/task_submission_service.py` 新增模块级 `_split_kb_version(value)` 工具解析 `"tech_category/version"` 为 `(kb_tech_category, kb_version)`，ORM 构造直接写两个物理列；对外 API / worker 协议字段全部保持 `knowledge_base_version: str` 零变更
- **端到端冒烟**：`charhuang/tt_video/.../全世爆 第03节正手对攻及基本要领.mp4` 全链路贯通 ——
  * 预处理 `3869d295`（复用已有产物）→ KB 抽取 job `59492811`（6 DAG 步全 success，92.7s；4 个 expert_tech_points 全部 visual 通道）
  * 审批激活 `forehand_attack/1` draft→active → `POST /standards/build` 生成 `tech_standards.id=46 v=1`
  * 诊断任务 `355f9f3b`（70.1s，kb_version="forehand_attack/1" 解析落库成功）→ 报告 `f0f04af8`：3 维 LLM 改进建议完整生成
  * 全链路零 mock、零跳过，DB/Celery/COS/Venus/OpenAI/Whisper/YOLO 全部真实调用

---

## Feature-021 视频内容清洗与有效片段筛选

> 状态：MVP 上线（2026-05-19）；已闭合 spec.md US1 + US2 + US3 + US4
> 规范文档：`specs/021-video-content-curation/`
> 业务工作流：`docs/business-workflow.md § 3.4`

### 业务问题

不同教练的视频风格差异极大：有的整段就是真讲解，有的开头闲聊半分钟、中间插赛事回放、结尾推广告；
也有"采访 + 颁奖 + 现场"这种无技术内容的整段。直接把全量分段喂给 KB 抽取 LLM
会让"知识离散度"骤增（同一类技术，不同视频提取出来的要点关键词重叠率低、
术语用法不一致），同时浪费 LLM token。

Feature-021 在 KB 抽取流水线之前**多加一步**清洗：以"预处理后的分段"为最小判定粒度，
过滤掉非教学片段、与目标技术不相关的段、不是目标教练讲解的段，让 KB 抽取只看高信噪比片段。

### 三阶段位置

```
TRAINING:
  scan_cos_videos → preprocess_video → classify_video → curate_segments → extract_kb
                                                            ↑ Feature-021 新增
```

`curate_segments` 走既有 `default` Celery 队列（与 scan / housekeeping 同列，concurrency=1），
不引入新 worker。

### 决策算法（5 维加权 + LLM 兜底）

每个分段输出 `validity_score ∈ [0, 1]`：

| 维度 | 权重 (v1) | 信号 |
|------|-----------|------|
| `tech_keyword` | 0.35 | 教学关键词命中（复用 F-002 关键词字典） |
| `non_teaching` | 0.25 | "比赛 / 采访 / 颁奖" 等非教学词命中即重罚 |
| `coach_dominance` | 0.20 | 启发式：第一/第二人称 + 教学动词密度，扣"教练姓名 ≥ 3 次"分 |
| `topic_relevance` | 0.15 | 与目标 `tech_category` 关键词的命中数 |
| `duration_floor` | 0.05 | 单分段 < 5 秒直接 0 分 |

- `validity_score ≥ 0.7` ⇒ `accepted`（直接定案，**不调 LLM**）
- `validity_score ≤ 0.3` ⇒ `rejected`
- `0.3 < score < 0.7` ⇒ 模糊区间，调一次 LLM 复核（Venus 优先 → OpenAI fallback）；LLM 不可用 ⇒ 落 `uncertain`，不阻断作业

### 视频级摘要（FR-004）

清洗作业 success 时落 `video_curation_jobs` 行的视频级派生字段：

- `accepted_duration_ratio = sum(accepted segs duration) / total_duration`
- `low_quality = ratio < 0.3`（rubric 阈值，可调）
- `audio_unavailable`：视频无 transcript（无音频或 Whisper skipped）
- `short_video`：总时长 < 30 秒

### KB 抽取强制门（spec FR-008/009/010 + SC-008）

`POST /api/v1/tasks/kb-extraction` 在两层做门控：

1. **路由层 Gate 1**：视频未跑过 success 清洗 ⇒ 立即 `409 CURATION_REQUIRED` 拒绝
2. **DAG 层 (`download_video`)**：
   - 加载预处理 segments，**按 `effective_decision='accepted'` 过滤** 后才进 head_object/download
   - `accepted_duration_ratio == 0` ⇒ 抛 `LOW_QUALITY_SKIP:`，整个 KB 作业短路（不调 LLM）
   - `0 < ratio < 0.3` ⇒ 正常执行 + `output_summary.curation_warning='low_quality'` 留痕
   - **被 rejected / uncertain 的分段从不进入下游 LLM Prompt 拼装、姿态聚合或任何下游计算**（关键护栏，由 `tests/integration/test_kb_extract_consumes_accepted_only.py` 强制守卫）

### 应急回滚开关

`KB_EXTRACTION_BYPASS_CURATION_GATE=true`（env 字段，默认 false）：

- 跳过路由层 Gate 1；DAG 层读全量 segments
- 命中后 `extraction_jobs.output_summary.curation_bypass=true` 留痕，事后审计可定位
- 仅在"清洗规则误伤导致 KB 读不到分段"时启用，恢复后立即关闭重启 worker
- 完整剧本见 `docs/business-workflow.md § 10` + `src/config/curation_rubric/README.md` "应急回滚"

### 规范版本化（spec US2 / Q1）

清洗规范以 `src/config/curation_rubric/vN.yaml` 维护，与代码同源：

- 文件名严格 `vN.yaml`（N ≥ 1 整数）；顶层 `version` 字段必须与文件名匹配
- jsonschema 校验（`src/config/curation_rubric/schema.json`）；启动期 fail-soft 加载（CRITICAL log，不阻塞 app 启动）；运行时 `submit_curation` 第二道闸门 fail-fast
- 改规则走 PR + CI 校验（不允许线上编辑）；详见 `src/config/curation_rubric/README.md`
- 每个 `video_curation_jobs` 行持久化所用 `curation_rubric_version`；后续 v2 上线后老作业行不被覆盖

### 人工覆盖（spec US4）

`PATCH /api/v1/curation-jobs/{job_id}/segments/{segment_index}`：

- 运营对单分段把 `effective_decision` 改写为 `accepted` 或 `rejected`，必填 `override_user / override_reason`
- 计算列 `effective_decision = COALESCE(override_decision, auto_decision)` 由 PostgreSQL `GENERATED STORED` 实现，应用层无法漏算
- 视频级摘要在事务内重算
- **不级联触发 KB 重抽**（spec Q5）：监控暴露 `kb_stale_after_override=true` 提示位；运营按需手工 `POST /extraction-jobs/{id}/rerun`，rerun dispatch 前清零提示位

### 错误码登记

7 个新错误码集中登记在 `src/api/errors.py`（同步到 `docs/business-workflow.md § 7.4`）：

| 错误码 | HTTP | 含义 |
|-------|------|------|
| `CURATION_REQUIRED` | 409 | 视频未跑过 success 清洗，KB 抽取拒绝 |
| `LOW_QUALITY_SKIP` | 409 (内部信号) | accepted_duration_ratio==0；写入 `extraction_jobs.error_code` |
| `RUBRIC_INVALID` | 422 | 规范文件 schema 校验失败 |
| `RUBRIC_VERSION_NOT_FOUND` | 404 | 提交的版本号无对应文件 |
| `CURATION_TIMEOUT` | 500 | 作业超出 `CURATION_JOB_TIMEOUT_SECONDS` 被孤儿回收 |
| `CURATION_LLM_UNAVAILABLE` | 409 (内部信号) | 模糊区间分段需 LLM 但调用失败；写入 `segment.rejection_reason` |
| `CURATION_RUBRIC_MISMATCH` | 409 | 重提时 rubric 版本与既有 success 作业不一致 + force=false |

### 关键 API

| 方法 + 路径 | 用途 |
|------------|------|
| `POST /api/v1/tasks/curation` | 单条提交清洗任务 |
| `POST /api/v1/tasks/curation/batch` | 批量提交（≤ 100 条） |
| `GET /api/v1/curation-jobs/{job_id}` | 摘要 + 逐分段判定 + override 留痕 |
| `PATCH /api/v1/curation-jobs/{job_id}/segments/{segment_index}` | 单分段人工覆盖 / 取消覆盖 |
| `GET /api/v1/curation-stats?group_by=...` | 跨教练 / `tech_category` / `rubric_version` 聚合有效率（US5 P3）|

### 数据模型新增

- 新表 `video_curation_jobs`（作业级摘要）+ `video_curation_segment_results`（含 `effective_decision STORED` 计算列）
- `coach_video_classifications` 新增 3 列：`last_curation_job_id` / `low_quality` / `kb_stale_after_override`
- `analysis_tasks.task_type` ENUM 新增 `video_curation`；任务监控按 `business_phase=TRAINING` + `business_step=curate_segments` 筛选
- 迁移：`src/db/migrations/versions/0020_video_content_curation.py`

### 测试覆盖

- 单元（8 文件，56 测试）：rubric_loader / decision_engine（rule + LLM）/ coach_dominance / segment_text_provider / curation_service aggregation / phase_step_hook / errors
- 合约（5 文件，39 测试）：submit_curation / get_curation_job / kb_extraction_curation_gate / override_curation_segment / **curation_stats（US5 6 测试）**
- 集成（5 文件，25 测试）：end_to_end / kb_extract_consumes_accepted_only **(SC-008 关键护栏)** / low_quality_skip / override_recompute / rubric_versioning
- 总计 **149/149** PASSED；与 F-013 / F-019 / F-020 既有测试零回归
- 基准回归脚本：`scripts/run_curation_benchmark.py` 对照 SC-001/002/003 三项指标


---

## Feature-022 业务流程四阶段化 + 内容准备阶段引入审核门

> 状态：MVP 全量上线（2026-05-28）；spec.md US1 + US2 + US3 + US4 全部闭合
> 规范文档：`specs/022-content-review-workflow/`
> 业务工作流：`docs/business-workflow.md` 章程级业务流程结构变更（三阶段 → 四阶段）
> 章程依据：v2.1.0 原则 X（业务工作流对齐）

### 业务问题

Feature-021 引入清洗后，仍存在两个治理盲区：
1. **训练阶段产能账本被前置链路噪声污染**：scan / preprocess / classify / curate 四步本质属于"原始素材到可用语料"链路，与 KB 抽取的"语料到知识库"链路混在 TRAINING 阶段统计，导致训练阶段的吞吐 / 成功率指标失真
2. **清洗只能做规则级把关，无法承载"内容质量"二元决策**：需要人工运营对单条视频做"通过 / 拒绝"决策（涉及内容质量、技术相关性、教练权威性、视频质量），这一闸门不能下放给规则

### 功能描述

把当前的"训练-建标-诊断"**三阶段升级为"内容准备-训练-建立标准-诊断"四阶段**，并在内容准备阶段末端引入**审核门**作为该阶段的最终判据：

```
CONTENT_PREP（新阶段）：scan_cos_videos → preprocess_video → classify_video → curate_segments → content_review
  TRAINING：           extract_kb → kb_conflicts 审阅 → kb_version_activate
  STANDARDIZATION：    build_standards（保持不变）
  INFERENCE：          diagnose_athlete（保持不变）
```

只有"通过审核"的视频条目才能被 TRAINING 阶段的 KB 抽取消费；审核状态通过 4 个新错误码在 KB 抽取入口强制拦截。

### 核心能力

- **章程级阶段升级**（FR-001/FR-002/FR-003/FR-004/FR-005）：四张业务表统一识别 4 个 phase 取值（`CONTENT_PREP` / `TRAINING` / `STANDARDIZATION` / `INFERENCE`）
- **审核门状态机**（FR-006/FR-007/FR-008/FR-009/FR-010/FR-011）：在既有 `coach_video_classifications` 行上承载 4 状态枚举：`pending_review` → `approved` / `rejected` / `stale`
- **审核工作台**（FR-007/FR-007a，US3）：列表 + 详情 + 通过 / 拒绝（含理由码）+ 已审核历史 + 跨教练 / tech_category / 时间窗筛选
- **KB 抽取入口三态拦截**（FR-009）：3 个新错误码 `CONTENT_NOT_REVIEWED` / `CONTENT_REVIEW_REJECTED` / `CONTENT_REVIEW_STALE` 在 `POST /tasks/kb-extraction` 路由层 + DAG 入口双闸门生效
- **重新清洗自动失效**（FR-011/FR-011a）：清洗版本递进 → 旧审核结论自动 `stale`；已入队 / running 的 `extract_kb` **不级联中止**（继续跑完落库），仅新提交的 KB 抽取在入口被 `CONTENT_REVIEW_STALE` 拒绝
- **审核门绕过开关**（FR-014）：`PATCH /api/v1/admin/review-gate {enabled}` 30 秒内热生效；切回 `enabled=true` 立即恢复严格行为，不留遗留豁免；切换全程审计字段 `last_toggled_at` / `last_toggled_by` 落库（SC-007）
- **阶段级可观测性**（FR-012/FR-013）：4 个审核门指标 `content_review_pending_count` / `content_review_decision_count{decision}` / `content_review_latency_seconds` / `content_review_pending_since_p95_seconds` + 任务级 `phase_enter_count{phase}` 锚点
- **积压告警 hourly beat**（FR-016）：`cleanup_pending_backlog` 每小时巡检 `pending_since < now() - review_pending_red_line_hours`（默认 24h），命中即写 ERROR 级结构化日志触发 SRE 高优先级告警；不阻塞业务流程
- **拒绝条目永久保留**（FR-010a，澄清 Q5）：拒绝条目 DB 行保留并置 `rejected`；不软删除、不物理删除、COS 文件不同步删除；工作台默认列表过滤 `rejected`，但支持 `?state=rejected` 显式查看；未来"申诉重审"零迁移即可改状态机

### 关键 API

| 方法 | 路径 | 用途 |
|------|------|------|
| `GET` | `/api/v1/content-reviews` | 审核工作台列表（按 `state` / `coach_name` / `tech_category` / `decided_after` / `decided_before` 筛选；page_size ≤ 50 时 P95 < 500 ms，page_size = 100 时 P95 < 1s）|
| `GET` | `/api/v1/content-reviews/{cvclf_id}` | 审核详情（含决策历史 + 当前清洗版本）|
| `POST` | `/api/v1/content-reviews/{cvclf_id}/decisions` | 提交审核决策（`decision` ∈ `approved`/`rejected`，`reason_code` 拒绝必填，`expected_review_version` 乐观锁）|
| `GET` | `/api/v1/content-reviews/stats` | 审核统计（按时间窗：总量 / 通过率 / 平均时延 / 人均吞吐）|
| `GET` | `/api/v1/admin/review-gate` | 查询审核门当前状态（`enabled` / `last_toggled_at` / `last_toggled_by`）|
| `PATCH` | `/api/v1/admin/review-gate` | 切换审核门（`enabled=false` 应急绕过；30s 内全局热生效）|

### 错误码（4 个新增，全部 HTTP 409）

| 错误码 | 触发场景 |
|-------|---------|
| `CONTENT_NOT_REVIEWED` | KB 抽取提交时来源条目 `review_state='pending_review'` |
| `CONTENT_REVIEW_REJECTED` | KB 抽取提交时来源条目 `review_state='rejected'` |
| `CONTENT_REVIEW_STALE` | KB 抽取提交时来源条目 `review_state='stale'`（清洗版本已递进，旧审核失效）|
| `REVIEW_VERSION_CONFLICT` | 提交决策时 `expected_review_version` 与 DB 当前版本不一致（并发冲突）|

### 数据模型变更（迁移 0021_content_review_workflow）

- `coach_video_classifications` 新增 5 列：
  - `review_state VARCHAR(20)` — 4 状态枚举（`pending_review` / `approved` / `rejected` / `stale`），`server_default='pending_review'`
  - `cleansing_version INTEGER` — 单调递增计数（清洗版本号），用于审核失效判定
  - `pending_since TIMESTAMP` — 进入 `pending_review` 时间戳，驱动积压告警 + p95 指标
  - `review_version INTEGER` — 决策乐观锁版本号
  - `last_decision_id UUID` — 反向指针指向 `content_review_decisions.id`（最新决策）
- `coach_video_classifications` 新增 3 个复合索引：
  - `(review_state, decided_at DESC)` — 驱动「待审核列表 / 已审核历史」主路径
  - `(coach_id, review_state)` — 驱动按教练过滤的列表
  - `(tech_category, review_state)` — 驱动按技术类别过滤的列表
- 新表 `content_review_decisions`：每次审核动作的留痕（`reviewer_id` / `decision` / `reason_code` / `note` / `decided_at` / `cleansing_version`）；`cvclf_id` FK CASCADE
- 新表 `task_channel_configs.content_review_gate`：审核门开关行（`enabled` / `last_toggled_at` / `last_toggled_by`）
- `business_phase_enum` PG enum 新增取值 `CONTENT_PREP`；四阶段映射在 `_phase_step_hook.py` 派生规则中：
  - `video_classification` / `video_preprocessing` / `video_curation` 全部从 `TRAINING` 改归 `CONTENT_PREP`
  - `TRAINING` 仅保留 `kb_extraction`
  - `business_step` 新增 `content_review`

### 业务行为决策（spec.md Clarifications）

| 议题 | 决策 |
|------|------|
| **审核粒度** | 整段视频条目（与 `coach_video_classifications` 行 1:1），不做片段级拆分 |
| **审核员角色** | 单一 `reviewer` 角色（admin 为其超集），无分级 / 分组 / 二审 / 申诉 / 审计员 |
| **重新清洗对入队 KB 任务的影响** | 已入队 / running 不级联中止（继续跑完）；新提交在入口被 `CONTENT_REVIEW_STALE` 拒绝 |
| **工作台列表性能 SLO** | 50 万条规模下：page_size ≤ 50 时 P95 < 500 ms；page_size = 100 时 P95 < 1s |
| **拒绝条目保留策略** | DB 行永久保留 + `state='rejected'`；不软删 / 不物理删 / COS 不同步删；默认列表过滤 + `?state=rejected` 显式查看 |
| **存量数据迁移** | 章程原则 XI（测试阶段未上线）：不做强一致回填脚本；存量数据按 `pending_review` 默认值进入新流程，运营按需补审或绕过开关降级 |

### 性能指标（SC）

| SC | 目标 | 达成方式 |
|----|------|---------|
| SC-001 | 阶段拆分后运营在仪表盘 100% 区分四阶段吞吐与积压 | `business_phase_enum` 新增取值 `CONTENT_PREP` + `phase_enter_count` 指标按 phase 派生 |
| SC-002 | 审核平均时延 ≤ 4h | `content_review_latency_seconds` 指标实时观测；超过 4h 触发运营复盘（不直接作为系统验收）|
| SC-003 | "未审核即被消费"比例 = 0 | 路由层 + DAG 双闸门强制；`tests/integration/test_022_review_gate_blocks_kb.py` 6 集成测试 PASS |
| SC-005 | 待审核积压超红线告警从触发到响应 ≤ 30 分钟 | hourly beat `cleanup_pending_backlog` + ERROR 级结构化日志（`alert.severity=high`）|
| SC-006 | 阶段拆分对 STANDARDIZATION / INFERENCE 兼容性影响 = 0 | 既有 4 张业务表 phase 派生规则保持向后兼容；`tests/contract/test_022_phase_compat.py` 验证 |
| SC-007 | 审核门切换从切换到生效 ≤ 30s + 100% 留审计 | `task_channel_configs.content_review_gate` 30s TTL 缓存；`tests/integration/test_022_bypass_switch.py` 3 集成测试 PASS（含端到端时延断言）|

### 测试覆盖

- **合约**（5 文件）：`test_022_content_reviews_contract.py` / `test_022_admin_review_gate_contract.py` / `test_022_kb_extraction_review_gate_contract.py` / `test_022_phase_compat.py` / `test_022_decision_lock.py`
- **集成**（4 文件）：`test_022_review_gate_blocks_kb.py`（6 测试，AC1-AC5 端到端）/ `test_022_stale_after_recurate.py`（清洗递进自动 stale）/ `test_022_bypass_switch.py`（3 测试，30s 热生效 + 切回严格 + 审计字段）/ `test_022_phase_observability.py`（4 测试，4 个日志锚点契约）
- **总计 51/51 PASSED**；与 F-013 / F-019 / F-020 / F-021 既有测试零回归

### 关键里程碑

- **阶段 1 — Setup**（T001-T004）：迁移 0021 + 4 状态枚举 + `business_phase_enum` 扩展 + 错误码登记
- **阶段 2 — Foundational**（T005-T010）：`_phase_step_hook.py` 派生规则升级 + content_review_decisions 模型 + review_gate 服务 + ORM 钩子单测
- **阶段 3 — US1（P1🎯MVP）**：业务流程升级为四阶段（spec FR-001 ~ FR-005）
- **阶段 4 — US2（P1🎯MVP）**：审核门 KB 抽取强制拦截（spec FR-006 ~ FR-011a + SC-003）
- **阶段 5 — US3（P2）**：审核工作台 + 决策乐观锁 + 统计接口（spec FR-007/FR-015/FR-017/FR-018）
- **阶段 6 — US4（P3）**：阶段级可观测性 + 绕过开关回滚剧本（spec FR-012/FR-013/FR-014/FR-016 + SC-007）

### 章程合规

- **原则 IV**（接口下线物理删除）：本 Feature 0 接口下线，仅新增
- **原则 IX**（错误码集中化）：4 个新错误码同步 `src/api/errors.py` 三张映射表（`ErrorCode` / `ERROR_STATUS_MAP` / `ERROR_DEFAULT_MESSAGE`）
- **原则 X**（业务工作流对齐）：`docs/business-workflow.md` § 1 概述 / § 2 阶段全景图（三阶段 → 四阶段）/ § 3 阶段一·内容准备 / § 7.4 错误码表 / § 10 回滚剧本（新增审核门绕过）/ § 11 交叉索引六处同步；`docs/.specify/memory/constitution.md` 措辞修订为四阶段
- **原则 XI**（测试阶段宽松）：不做存量数据回填脚本；存量按默认 `pending_review`
 进入新流程

---

## Feature-023 技术分类体系重构（21 类 → 56 行 V2 字典 / 全局列名重命名）

**状态**：已交付 (2026-05-31)
**核心问题**：旧 21 类 `TECH_CATEGORIES` 枚举为单一字符串字面量，缺乏层级结构、扩展能力差、字典口径与代码字面量两份事实来源；分类准确率被 21 类的粗粒度桶位拉低（实测 70%），无法满足 SC-002 ≥ 85% 目标。
**目标**：以严格四级（`grip_style / hand_side / stroke_phase / action`）+ 字典强约束（`tech_actions` 56 行 v2）作为单一事实来源，物理删除旧 21 类枚举与 `tech_category` 列，全局重命名为 `action` / `kb_action`。

### 核心交付（仅口径重建，业务流程零改动）

| 子项 | 说明 |
|------|------|
| **数据字典 56 行 V2** | `tech_actions` 表：`(category_l1, category_l2, category_l3, action)` 四级唯一键；本期仅 seed 横拍·反胶子集（44 个 action × 5 个 L3 桶 = 56 行）；直拍位预留 |
| **全局列名重命名** | 7 张业务表统一：`tech_category` → `action`（多列另增 `category_l1/l2/l3`）；4 张子表 FK 列：`kb_tech_category` → `kb_action`；`tech_knowledge_bases` 复合 PK 重命名为 `(action, version)`；partial unique index `uq_tech_kb_active_per_action` |
| **TechClassifier V2** | `src/services/tech_classifier.py` 重写：规则层 + LLM 兜底两层，LLM 出参强制落 `tech_actions` 字典 enum 块；`confidence < 0.5` 强制降级 `unclassified`；旧 `TECH_CATEGORIES` 物理删除 |
| **启发式 lower bound 评估** | T071 阶段一：`scripts/build_heuristic_eval_set.py` 生成 100 条强信号样本 → top-1=65% / L3=79% / L1=99%（lower bound，标签噪声 ~14%；目检估算真实 ≈ 88%）；阶段二待人工标注 |
| **Feature-021 上位实现** | `docs/feature-021-proposal.md` 顶部加状态注释；021 的 V2 兼容方案被 023 直接物理替换，无需保留 |

### 主要 API 影响（直接物理替换，无兼容层）

| 接口 | 变化 | 说明 |
|------|------|------|
| `GET /api/v1/classifications` | `tech_category` 查询参数 → `action` | 统一为字典枚举值，旧字面量入参返回 `INVALID_ENUM_VALUE` |
| `POST /api/v1/standards/build` | 必填 `action`（旧 `tech_category`）| `tech_standards` 表也已迁移 |
| `GET /api/v1/knowledge-base/versions/{action}/{version}` | 路径参数重命名 | KB 复合主键改为 `(action, version)` |
| `GET /api/v1/athlete-classifications` | 复合筛选 `tech_category` → `action` | Feature-020 路由同步迁移 |

> **章程原则 IV（接口下线物理删除）合规**：旧 `tech_category` 路径与查询参数直接物理替换，不保留哨兵或兼容字段；调用旧名一律 422 / 404。

### 数据迁移

- `0022_tech_taxonomy_rebuild`（一次性原子迁移）
  - Step 1：CREATE `tech_actions` 字典 + seed 56 行（来源：`contracts/tech-actions-seed.csv`）
  - Step 2-3：DROP 5 张子表外键到 `tech_knowledge_bases`
  - Step 4-5：`coach_video_classifications` / `video_classifications` DROP 旧 `tech_category` + 索引 + ADD 4 级字段 + 复合 FK + 重建 4 个索引
  - Step 6：`tech_knowledge_bases` PK 重命名 + 5 列改造 + partial unique index
  - Step 7-12：`expert_points` / `tech_standards` / `teaching_tips` / `analysis_tasks` / `reference_videos` 子表 `kb_tech_category` → `kb_action` rename + 重建复合 FK
- 系统未上线，**未保留 downgrade 路径**（章程 v2.0.0 原则 IV）；如需回退须从 `0021` 重新升级

### 验收（Success Criteria）

| ID | 指标 | 状态 |
|----|------|------|
| SC-001 | 分类四级口径单一事实来源（`tech_actions` 字典 + ORM 与配置不再出现字面量） | ✅ 达成（`grep` 验证 `TECH_CATEGORIES` / 旧 `tech_category` 字面量在源码中清零，仅在 docs 历史段保留） |
| SC-002 | top-1 action accuracy ≥ 85%（人工标注集） | 🟡 阶段一启发式 lower bound 65%（标签噪声拉低，估算真实 ≈ 88%）；阶段二人工标注待业务专家排期 |
| SC-003 | LLM 兜底输出 100% 落字典 | ✅ JSON mode + `enum` 块强约束 + 字典校验，违反一律 fallback `unclassified` |
| SC-004 | Feature-019 / 020 / 021 / 022 既有测试零回归 | ✅ `pytest tests/` 841 passed / 65 skipped / 0 failed |
| SC-005 | 字段重命名零 schema 漂移 | ✅ 7 张表 + 4 张子表 + 5 个索引/约束统一重命名，alembic upgrade head 一次成功 |

### 测试覆盖

- **合约 + 集成**：`pytest tests/` 全套 841 PASSED / 65 SKIPPED / 0 FAILED（与 F-013 / F-019 / F-020 / F-021 / F-022 既有测试零回归）
- **评估**：`specs/023-tech-classification-rebuild/scripts/eval_v2_accuracy.py` + 启发式 100 条评估集（`data/eval/tech_classification_v2_eval.csv`）；详细分析见 [`specs/023-tech-classification-rebuild/eval_results.md`](../specs/023-tech-classification-rebuild/eval_results.md)

### 关键里程碑

- **阶段 1-2 — 字典 + 迁移**（T001-T020）：`tech_actions` seed CSV + 0022 迁移落库
- **阶段 3-4 — 分类器 V2**（T021-T050）：`TechClassifier` 重写 + 字典强约束 LLM prompt + 51 个单测
- **阶段 5-6 — API + ORM 重命名**（T051-T070）：7 张业务表列改名 + 5 个路由参数重命名 + Feature-021 兼容方案废止
- **阶段 7 — 全链路回归**（T074）：`pytest tests/` 全 GREEN 841 PASSED
- **阶段 8 — 基线评估**（T071 阶段一 + T072 + T073，2026-05-31）：启发式 lower bound 100 条 + `eval_v2_accuracy.py` 跑通 + `docs/benchmarks/tech_classification_v2.md` 落地
- **待排期**：T071 阶段二（人工标注 ground truth）+ T075（quickstart 真实 Worker 演练）

### 章程合规

- **原则 IV**（接口下线物理删除）：`tech_category` 字段与所有相关 API 路径/查询参数直接物理删除/重命名，不保留哨兵
- **原则 VIII**（算法精度可观测）：`docs/benchmarks/tech_classification_v2.md` 基线文档 + `eval_v2_accuracy.py` 评估脚本 + `eval_results.md` 错例目检
- **原则 IX**（错误码集中化）：本 Feature 0 新增错误码（沿用 `INVALID_ENUM_VALUE` / `VALIDATION_FAILED`）
- **原则 X**（业务工作流对齐）：本次为字段级重命名，业务阶段 / 队列 / 状态机 / 错误码 / 评分公式均无变化，`business-workflow.md` 仅刷新顶部时间戳
- **原则 XI**（测试阶段宽松）：未上线系统，迁移不保留 downgrade 路径

