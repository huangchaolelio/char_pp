# 产品功能文档

> 最后更新：2026-04-24

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

