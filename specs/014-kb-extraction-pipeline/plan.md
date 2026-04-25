# 实施计划: 知识库提取流水线化（有向图 + 并行）

**分支**: `014-kb-extraction-pipeline` | **日期**: 2026-04-24 | **规范**: [spec.md](./spec.md)
**输入**: 来自 `/specs/014-kb-extraction-pipeline/spec.md` 的功能规范

## 摘要

把 Feature-013 遗留的 `kb_extraction` 最小存根（只翻转 `kb_extracted=True`）改造为**有向无环图（DAG）流水线**。一次 KB 提取作业被编排为 6 个子任务：
`download_video → (pose_analysis ∥ audio_transcription) → (visual_kb_extract ∥ audio_kb_extract) → merge_kb`

视频下载为唯一根节点（两路共享，只下载一次）；姿态分析/音频转写无依赖可并行；视觉/音频知识点提炼各自依赖前置分析路，也可并行；合并入库依赖两路提炼。作业对 `kb_extraction` 通道只占用 1 个 `concurrency` 名额，作业内通过 `asyncio.gather` 实现并行；子任务孤儿判 failed 依赖 Feature-013 既有 sweep 机制。

业务能力补齐 Feature-002 遗失的**视频直提专业 KB**：姿态序列 → 技术维度规则 + 音频转写 → LLM 抽取教练口述要点 + 冲突标注分离入「待审核冲突表」。

## 技术背景

**语言/版本**: Python 3.11（`/opt/conda/envs/coaching`）
**主要依赖**:
- FastAPI 0.111+ / uvicorn（HTTP API）
- SQLAlchemy 2.0 async + asyncpg 0.29+ + Alembic 1.13+（持久化）
- Celery 5.4+ + Redis（任务队列；复用 Feature-013 `kb_extraction` 通道）
- MediaPipe 0.10+（姿态降级；YOLOv8 GPU 优先，沿用现有 `POSE_BACKEND=auto`）
- openai-whisper 20231117（音频转写，本地模型）
- openai>=1.0.0 / Venus Proxy（LLM 抽取知识点）

**存储**:
- PostgreSQL（作业 + 子任务 + 冲突标注元数据）
- Worker 本地文件系统 `/tmp/coaching-advisor/jobs/{job_id}/`（视频 + 姿态 JSON + 转写 JSON 中间结果）
- 复用 COS（源视频）和既有 `tech_knowledge_bases` / `coach_video_classifications` 表

**测试**: pytest + pytest-asyncio（tests/contract、tests/integration、tests/unit 三层）

**目标平台**: Linux 服务器（Docker / 虚拟机）；与 Feature-013 Worker 同宿主

**项目类型**: Web 服务（后端算法 + API）

**性能目标**:
- 作业状态查询 p95 ≤ 1s（SC-001）
- 10 分钟视频的作业总耗时 ≤ Feature-002 单体耗时的 90%（SC-003）
- 并行 vs 串行节省 ≥ 30%（SC-002）

**约束条件**:
- 作业整体超时 45 分钟、单子任务 10 分钟（Clarification Q1）
- I/O 子任务自动重试 2 次 × 30s（Q4），CPU 子任务不重试
- 中间结果保留期：success 24h / failed 7d（Q5）
- `kb_extraction` 通道 concurrency=2 指并行**作业数**，不是子任务数（Q1 第二轮）
- 不引入新外部依赖（SSE/WebSocket/消息队列）（Q2 第二轮）

**规模/范围**:
- 单作业典型 6 子任务（可扩展至 10 内）
- 一次作业合理总耗时 5-40 分钟（取决视频长度与 LLM 响应）
- 初期并发作业上限 2（通道 concurrency），可通过 Feature-013 admin PATCH 热更新

## 章程检查

*门控: 必须在阶段 0 研究前通过。阶段 1 设计后重新检查。*

### I. 规范驱动开发 ✅

- `specs/014-kb-extraction-pipeline/spec.md` 已通过两轮 clarify，10 条 Clarifications 全部集成
- 功能分支 `014-kb-extraction-pipeline` 符合命名规范
- 用户故事均以后端服务能力为中心（API + 流水线编排），无前端交互作为验收前提

### II. 测试优先 ✅

将按 TDD 执行：合约测试（`tests/contract/test_extraction_jobs_*.py`）+ 集成测试（`tests/integration/test_pipeline_dag.py`, `test_parallel_execution.py`, `test_orphan_recovery.py`）+ 单元测试（DAG 调度、冲突合并、重试策略）。所有测试在 impl 任务前创建并应先失败。

AI 模型精度验证：姿态估计 + LLM 抽取复用 Feature-002 已有基准（见 docs/benchmarks/），本 Feature 不引入新模型，因此不单独建立新基准；但 SC-007 要求条目数差异 ≤20% vs Feature-002，作为回归指标。

### III. 增量交付 ✅

5 个用户故事可独立交付：
- US1 MVP（单作业 DAG 编排 + 状态查询）
- US2（视频直提 KB 业务能力）
- US3（并行性能）
- US4（局部重跑）
- US5（通道兼容）

实现任务按阶段分：设置 → 基础（Alembic 迁移 + 模型 + DAG 骨架）→ US1 → US2 → US3 → US4 → US5 → 完善。

### IV. 简洁性与 YAGNI ✅

- 复用 Feature-013 的 `TaskSubmissionService` / `TaskChannelService` / Celery 队列配置，**不新增 Redis/MQ**
- 作业 DAG 静态定义在代码常量中（6 子任务），**不引入通用 DAG 引擎**（Airflow/Prefect 等）
- 作业内并行用 `asyncio.gather` 实现，**不新增线程池/进程池组件**
- LLM 抽取直接复用 Feature-002 既有的提取器，**不重写算法**

### V. 可观测性 ✅

- 每个子任务写结构化日志（作业 ID / 子任务类型 / 状态 / 耗时 / 错误）
- 作业状态查询接口直接暴露子任务清单，无需翻日志（FR-003, FR-019）
- 中间结果（姿态 JSON、转写 JSON）在 Worker 本地可读，便于 debug

### VI. AI 模型治理 ✅

- 本 Feature 不引入新模型，复用 Feature-002 的 Whisper + YOLOv8/MediaPipe + OpenAI/Venus
- 推理超时策略通过子任务 10 分钟超时 + 重试上限保证（FR-020, FR-021）
- LLM 输出结构化（知识条目有来源、维度、参数、置信度字段），无纯标量评分

### VII. 数据隐私 ✅

- 教练视频为系统内部数据，非用户个人视频；存储传输沿用现有 COS + TLS
- 中间结果保留期（24h/7d）已在规范量化（FR-013）
- 无新增用户数据采集

### VIII. 后端算法精准度 ✅

- SC-007 定义量化指标：与旧版 Feature-002 对同视频的输出条目数差异 ≤20%
- SC-002 / SC-003 定义性能指标（并行节省 ≥30%、总耗时 ≤90% 旧版）
- 复用 Feature-002 已建立的基准（关键点检测、LLM 抽取精度）

**门控结论：✅ 通过。无违规，无需填写复杂度跟踪表。**

## 项目结构

### 文档（此功能）

```
specs/014-kb-extraction-pipeline/
├── plan.md              # 此文件
├── spec.md              # 功能规范（已完成，含 10 条 Clarifications）
├── research.md          # 阶段 0 输出（见下）
├── data-model.md        # 阶段 1 输出
├── quickstart.md        # 阶段 1 输出
├── contracts/           # 阶段 1 输出（extraction_jobs API OpenAPI）
├── checklists/
│   └── requirements.md  # 已完成
└── tasks.md             # 阶段 2 输出（/speckit.tasks 创建，本命令不创建）
```

### 源代码（仓库根目录）

```
src/
├── api/
│   ├── routers/
│   │   └── extraction_jobs.py      # 新增：作业提交/查询/重跑/列表
│   └── schemas/
│       └── extraction_job.py       # 新增：作业 + 子任务 + 冲突项 Pydantic 模型
├── models/
│   ├── extraction_job.py           # 新增：ExtractionJob ORM
│   ├── pipeline_step.py            # 新增：PipelineStep ORM
│   └── kb_conflict.py              # 新增：KbConflict ORM（待审核冲突表）
├── services/
│   ├── kb_extraction_pipeline/     # 新增：DAG 编排服务包
│   │   ├── __init__.py
│   │   ├── pipeline_definition.py  # 静态 DAG 定义（6 子任务 + 依赖）
│   │   ├── orchestrator.py         # 运行作业、调度子任务、处理依赖 + 并行
│   │   ├── step_executors/         # 每个子任务一个 executor 类
│   │   │   ├── download_video.py
│   │   │   ├── pose_analysis.py
│   │   │   ├── audio_transcription.py
│   │   │   ├── visual_kb_extract.py
│   │   │   ├── audio_kb_extract.py
│   │   │   └── merge_kb.py
│   │   └── retry_policy.py         # I/O vs CPU 分层重试策略
│   └── kb_extraction_service.py    # 改造：薄层入口，调 orchestrator
├── workers/
│   ├── kb_extraction_task.py       # 改造：Celery 入口调 orchestrator
│   └── housekeeping_task.py        # 改造：新增清理过期中间结果
├── db/
│   └── migrations/versions/
│       └── 0013_kb_extraction_pipeline.py   # Alembic 迁移

tests/
├── contract/
│   ├── test_extraction_jobs_submit.py    # POST /extraction-jobs 合约
│   ├── test_extraction_jobs_query.py     # GET /extraction-jobs/{id} + list
│   └── test_extraction_jobs_rerun.py     # POST /extraction-jobs/{id}/rerun
├── integration/
│   ├── test_pipeline_dag.py              # DAG 调度、依赖传播、skipped 级联
│   ├── test_parallel_execution.py        # SC-002 并行节省 ≥30%
│   ├── test_retry_policy.py              # I/O 重试 / CPU 不重试
│   ├── test_orphan_recovery.py           # Worker 崩溃后子任务状态
│   ├── test_conflict_merge.py            # 冲突项入 kb_conflicts 不入 KB
│   └── test_force_overwrite.py           # force=true 覆盖 + superseded 标记
└── unit/
    ├── test_pipeline_definition.py       # DAG 拓扑 / 循环检测
    ├── test_retry_policy_decisions.py    # 分层重试判定
    └── test_kb_merger.py                 # 合并逻辑 + 冲突检测
```

**结构决策**: 采用现有 `src/` 单一项目结构，新增 `src/services/kb_extraction_pipeline/` 子包承载 DAG 编排。与 Feature-013 的服务/路由结构保持一致，复用 session、settings、Celery app、TaskChannelService。

## 复杂度跟踪

> 无章程违规，无需填写复杂度跟踪表。
