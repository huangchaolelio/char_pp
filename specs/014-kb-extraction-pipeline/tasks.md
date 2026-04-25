---
description: "Feature 014 — 知识库提取流水线化（有向图 + 并行）的可执行任务清单"
---

# 任务: 知识库提取流水线化（有向图 + 并行）

**输入**: 来自 `/specs/014-kb-extraction-pipeline/` 的设计文档
**前置条件**: plan.md ✅ / spec.md ✅ (10 条 Clarifications) / research.md ✅ / data-model.md ✅ / contracts/ (2 yaml) ✅ / quickstart.md ✅

**测试策略**: spec.md 显式要求「独立测试」+ 章程原则 II（TDD）。本任务清单为每个用户故事生成合约/集成测试任务（置于实现前）。

## 格式: `[ID] [P?] [Story] 描述`
- **[P]**: 可以并行运行（不同文件，无依赖关系）
- **[Story]**: US1 / US2 / US3 / US4 / US5 — 映射到 spec.md 用户故事
- 所有文件路径均为仓库内相对/绝对路径

## 路径约定

- 后端单一项目：`src/`、`tests/`
- 迁移：`src/db/migrations/versions/`
- 契约：`specs/014-kb-extraction-pipeline/contracts/`

---

## 阶段 1: 设置（共享基础设施）

**目的**: 准备依赖与配置，为后续阶段打底

- [X] T001 在 `src/config.py` 的 `Settings` 新增字段：`extraction_job_timeout_seconds: int = 2700`（45min）、`extraction_step_timeout_seconds: int = 600`（10min）、`extraction_artifact_root: str = "/tmp/coaching-advisor/jobs"`、`extraction_success_retention_hours: int = 24`、`extraction_failed_retention_hours: int = 168`；同步更新 `.env.example`
- [X] T002 [P] 在 `pyproject.toml` 新增依赖 `tenacity>=8.2.0`；锁定版本；执行 `pip install -e .` 验证
- [X] T003 [P] 在 `CODEBUDDY.md` 的 Features 表新增 Feature-014 行；workers 目录树新增 `extraction_pipeline/` 说明（暂未创建文件，仅文档占位）
- [X] T004 [P] 创建 `src/services/kb_extraction_pipeline/__init__.py` 空包结构；创建 `src/services/kb_extraction_pipeline/step_executors/__init__.py` 空包结构

---

## 阶段 2: 基础（阻塞前置条件）

**目的**: 数据模型、DAG 骨架、迁移必须先就位；阻塞所有用户故事

**⚠️ 关键**: 此阶段完成前任何用户故事代码不得合并

### 数据模型与迁移

- [X] T005 [P] 在 `src/models/extraction_job.py` 新增 `ExtractionJob` ORM 模型：字段按 data-model.md（id/analysis_task_id/cos_object_key/tech_category/status enum/worker_hostname/enable_audio_analysis/audio_language/force/superseded_by_job_id/error_message/created_at/started_at/completed_at/intermediate_cleanup_at）；在 `src/models/__init__.py` 导出
- [X] T006 [P] 在 `src/models/pipeline_step.py` 新增 `PipelineStep` ORM 模型：字段按 data-model.md（id/job_id FK CASCADE/step_type enum/status enum/retry_count/error_message/output_summary JSONB/output_artifact_path/started_at/completed_at/duration_ms）
- [X] T007 [P] 在 `src/models/kb_conflict.py` 新增 `KbConflict` ORM 模型：维度粒度（id/job_id/cos_object_key/tech_category/dimension_name/visual_value JSONB/audio_value JSONB/visual_confidence/audio_confidence/superseded_by_job_id/resolved_at/resolved_by/resolution/resolution_value/created_at）
- [X] T008 创建 Alembic 迁移 `src/db/migrations/versions/0013_kb_extraction_pipeline.py`：按 data-model.md 执行（创建 3 个枚举 + 3 张表 + `analysis_tasks` 新增 `extraction_job_id` 列 + 所有索引）
- [X] T009 执行迁移验证：`alembic upgrade head` 成功；`psql` 检查三张新表、`\d analysis_tasks` 新列存在、索引建立；验证 downgrade 可回滚

### Schema 与基础服务

- [X] T010 [P] 在 `src/api/schemas/extraction_job.py` 新建 Pydantic v2 模型：`ExtractionJobSummary` / `ExtractionJobDetail` / `PipelineStepResponse` / `ProgressResponse` / `RerunRequest` / `RerunResponse`；对齐 `contracts/extraction_jobs.yaml`
- [X] T011 [P] 在 `src/services/kb_extraction_pipeline/pipeline_definition.py` 定义 DAG 常量：`StepType` enum（6 值）、`DEPENDENCIES` dict（按 research.md R7 的依赖表）、`TOPOLOGICAL_ORDER` 预计算、`IO_STEPS` set（download/audio_transcription/audio_kb_extract）、`CPU_STEPS` set
- [X] T012 [P] 在 `src/services/kb_extraction_pipeline/retry_policy.py` 实现 `should_retry(step_type) -> bool` + `get_retry_decorator(step_type)` 返回 tenacity `@retry(stop_after_attempt(3), wait_fixed(30), retry_if_exception_type(...))`（I/O 步骤用；CPU 步骤返回 no-op 装饰器）

### DAG 骨架

- [X] T013 在 `src/services/kb_extraction_pipeline/orchestrator.py` 新建 `Orchestrator`：`async run(session, job_id)` 方法的骨架实现：
    - 查询 job + 6 个 pipeline_steps
    - 循环：找到 `status=pending AND 所有 depends_on 的 step=success` 的步骤
    - 若无可执行步骤且有 running：`await asyncio.sleep(1)` 轮询
    - 若无可执行且无 running：退出循环，计算作业终态
    - 批量 `await asyncio.gather(*[execute_step(s) for s in ready])` 并行执行
    - 用 `asyncio.wait_for(..., timeout=extraction_job_timeout_seconds)` 包裹顶层循环
- [X] T014 在 `src/services/kb_extraction_pipeline/orchestrator.py` 的 `execute_step(step)` 实现：
    - 标记 step running + started_at
    - 根据 `step_type` 分发到 `step_executors/{step_type}.py` 的 `execute(session, job, step)`
    - 用 `asyncio.wait_for(..., timeout=extraction_step_timeout_seconds)` 包裹
    - 成功 → 标 success + completed_at + duration_ms + output_summary
    - 失败 → 标 failed + error_message（trim 2000 字符）；在事务外传播：`_propagate_skipped(session, job_id, failed_step_type)`
    - 超时 → 同失败路径，error_message="step timeout after 600s"
- [X] T015 在 `src/services/kb_extraction_pipeline/orchestrator.py` 实现 `_propagate_skipped`：广度优先遍历 `DEPENDENCIES` 反向图，把所有依赖失败 step 的后代 step 标 `skipped`（但要处理 merge_kb 的特殊降级：当 audio 路失败而 visual 路 success 时，merge_kb 保持 pending 并在执行时走降级合并）
- [X] T016 在 `src/services/kb_extraction_pipeline/orchestrator.py` 实现 `_finalize_job(session, job_id)`：所有非 merge_kb 的关键 step success + merge_kb success → 作业 success；任一关键 step failed → 作业 failed；写 completed_at 与 intermediate_cleanup_at（success + 24h / failed + 7d）

### Celery 入口重写

- [X] T017 改造 `src/workers/kb_extraction_task.py` 的 `extract_kb`：接收 `task_id` + `cos_object_key`；查 `analysis_tasks.extraction_job_id` 获取 `job_id`（由上游 router 创建时已写入）；调用 `Orchestrator().run(session, job_id)`；Celery task soft_time_limit=2800（超作业超时 100s）
- [X] T018 在 `src/services/kb_extraction_pipeline/orchestrator.py` 增加 `create_job` 类方法：`create_job(session, analysis_task_id, cos_object_key, tech_category, enable_audio_analysis, audio_language, force) -> ExtractionJob`；同一事务内插入 ExtractionJob + 6 PipelineStep + 回写 `analysis_tasks.extraction_job_id`；处理 `force=true` 时把旧 success 作业标 superseded_by_job_id + 同步更新 kb_conflicts 的 superseded_by_job_id

**检查点**: 基础就绪 — 迁移通过、DAG 骨架可加载、Celery 入口串通

---

## 阶段 3: 用户故事 1 — 作业 DAG 自动编排（优先级: P1）🎯 MVP

**目标**: 提交一次 KB 提取请求后，系统自动创建作业并调度 6 个子任务按 DAG 顺序执行；状态接口返回子任务清单 + 依赖图 + 进度

**独立测试**: 通过 `POST /api/v1/tasks/kb-extraction` 提交，立刻 `GET /api/v1/extraction-jobs/{id}` 返回 6 个 pending step，5 秒后至少 download_video 进入 running；查询延迟 p95 ≤ 1s

### 测试（合约 + 集成，阶段 3 前写）

- [X] T019+T020+T021 [US1] 合并覆盖于 `tests/integration/test_extraction_jobs_api.py`（端到端 API 流：提交 → DB 断言 → 详情 → list → 404 → rerun-501）；独立合约 YAML 对齐件未单写，行为契约由该测试用例守护
- [X] T022 [US1] 在 `tests/integration/test_pipeline_dag.py` 编写集成测试：mock step_executor 瞬时成功 → 验证 6 step 按拓扑顺序执行、最终作业 success、kb_extracted=True
- [X] T023 [US1] 在 `tests/unit/test_pipeline_definition.py` 编写单元测试：`TOPOLOGICAL_ORDER` 无环、所有节点覆盖、依赖闭包正确（11 项）
- [X] T024 [US1] 在 `tests/unit/test_orchestrator_finalize.py` 编写单元测试：`_find_ready_steps` 在各种 step 状态组合下的调度判定（7 项）

### 实现

- [X] T025 [US1] 在 `src/api/routers/extraction_jobs.py` 新建路由模块 + `GET /extraction-jobs/{job_id}`：查询 ExtractionJob + 子任务 + 冲突计数；组装 `ExtractionJobDetail` 响应；404 返回标准 ErrorResponse；在 `src/api/main.py` 注册 `extraction_jobs_router` 并挂载 `prefix="/api/v1"`
- [X] T026 [US1] 在 `src/api/routers/extraction_jobs.py` 实现 `GET /extraction-jobs`：page / page_size / status 过滤；查询 extraction_jobs JOIN pipeline_steps（COUNT success/failed）+ kb_conflicts（COUNT 未 superseded）；返回 `ExtractionJobSummary` 列表
- [X] T027 [US1] 改造 `src/api/routers/tasks.py` 的 `POST /tasks/kb-extraction`：
    - 保留原有分类门槛校验、幂等、通道容量检查
    - 在 `TaskSubmissionService.submit_batch` 成功返回后，同事务内调 `Orchestrator.create_job` 创建 ExtractionJob + 6 PipelineStep + 回写 `analysis_tasks.extraction_job_id`
    - 响应新增 `job_id` + `steps_created=6` 字段（对齐 `contracts/task_submit_v14.yaml`）
    - 处理 `force=true`：放行对已成功作业的重复提交
- [X] T028 [US1] 在 `src/services/kb_extraction_pipeline/step_executors/download_video.py` 实现 `execute(session, job, step)`：用 `cos_client.download_to(job.cos_object_key, local_path)` + `tenacity` I/O 重试（T012）；成功时返回 output_summary（文件大小 / 时长 / fps / 分辨率）+ output_artifact_path；失败抛异常由 orchestrator 捕获
- [X] T029 [US1] [P] 在 `src/services/kb_extraction_pipeline/step_executors/pose_analysis.py` 实现 `execute` 骨架：验证上游 video 可读、写入占位 `pose.json`、output_summary 留空以备 US2 算法填充（`backend="scaffold"`）
- [X] T030 [US1] [P] 在 `src/services/kb_extraction_pipeline/step_executors/audio_transcription.py` 实现 `execute`：当 `job.enable_audio_analysis=False` 直接写 `output_summary={"skipped": true, "skip_reason": "disabled_by_request"}` 并返回 skipped；否则写占位 transcript.json（真实 Whisper 调用留给 US2）
- [X] T031 [US1] [P] 在 `src/services/kb_extraction_pipeline/step_executors/visual_kb_extract.py` 实现 `execute` 骨架：读 pose.json → 返回空 kb_items（算法留 US2 填充）
- [X] T032 [US1] [P] 在 `src/services/kb_extraction_pipeline/step_executors/audio_kb_extract.py` 实现 `execute` 骨架：上游 skipped 时透传 skipped；否则返回空 kb_items（LLM prompt 留 US2 填充）
- [X] T033 [US1] 在 `src/services/kb_extraction_pipeline/step_executors/merge_kb.py` 实现 `execute` 的**最小骨架**（不含冲突逻辑，留给 US2）：
    - 读取 visual 与 audio step 的 output_summary；audio skipped/failed 时走降级模式
    - 暂时 concat 两路条目（冲突检测留 US2）
    - 翻转 `coach_video_classifications.kb_extracted=True`（由集成测试验证）
    - `tech_knowledge_bases` 写入留 US2（涉及 source_type / extraction_job_id 新列）

**检查点**: US1 完成 — 提交作业、DAG 按依赖执行、状态接口实时返回进度；端到端 mock 模式下跑通

---

## 阶段 4: 用户故事 2 — 视频直提专业 KB（视觉 + 音频 + 冲突分离）（优先级: P1）

**目标**: 真实从视频画面 + 音频讲解双路提取知识条目；两路参数冲突时分离入 `kb_conflicts` 不进正式 KB

**独立测试**: 对有清晰讲解音频的视频提交作业，验证 `tech_knowledge_bases` 中既有 `source_type='visual'` 也有 `source_type='audio'` 条目；人为构造冲突场景，验证冲突项只在 `kb_conflicts` 表

### 测试

- [X] T034 [US2] 集成测试：tests/integration/test_video_kb_extract_us2.py::test_both_paths_populate_tech_points_with_source_type — 使用 monkeypatch 注入 visual/audio kb_items 验证两路条目都写入 ExpertTechPoint（source_type 区分 visual/audio/visual+audio）
- [X] T035 [US2] 集成测试：tests/integration/test_video_kb_extract_us2.py::test_large_diff_routes_to_kb_conflicts_table — 超 10% 差异分离入 kb_conflicts，且 superseded_by_job_id IS NULL AND resolved_at IS NULL
- [X] T036 [US2] 单元测试：tests/unit/test_f14_kb_merger.py（10 项）覆盖 aligned/conflict/single-source/degradation/mixed/低置信度过滤
- [X] T037 [US2] 集成测试：tests/integration/test_video_kb_extract_us2.py::test_audio_disabled_produces_visual_only_kb — enable_audio_analysis=False 时 audio_kb_extract skipped、仅 visual 条目入库

### 实现

- [X] T038 [US2] 在 `src/services/kb_extraction_pipeline/merger.py` 新建 `F14KbMerger`：`merge(visual_items, audio_items) -> (merged_for_kb, conflicts)`；同 dimension 两路都有值 → 比对；一致合并为一条（`visual+audio` source_type）；不一致进冲突列表（**不入主 KB**）
- [X] T039 [US2] 在 `src/services/kb_extraction_pipeline/merger.py` 实现冲突判定规则：数值参数差异超 ±10% 视为冲突（阈值与 `conflict_threshold_pct` 参数化）；置信度 < 0.5 的音频条目自动丢弃不进合并
- [X] T040 [US2] 改造 `src/services/kb_extraction_pipeline/step_executors/merge_kb.py`：
    - 调 `F14KbMerger.merge` 分离冲突
    - 冲突条目批量插入 `kb_conflicts` 表
    - 合并后条目写入 `expert_tech_points`（带 `source_type=visual|audio|visual+audio`）
    - 为每个作业派生独立 `tech_knowledge_bases` 版本（`0.{a}.{b}` 由 UUID 前 16 位 hex 派生，VARCHAR(20) 约束内）
    - 更新 output_summary `{merged_items, inserted_tech_points, conflict_items, kb_version, kb_extracted_flag_set, degraded_mode}`
    - 翻转 `coach_video_classifications.kb_extracted=True`
- [X] T041 [US2] 改造 `src/services/kb_extraction_pipeline/step_executors/visual_kb_extract.py`：输出结构对齐 `F14KbMerger` 入口（dimension/param_min/param_max/param_ideal/unit/extraction_confidence/action_type/source_type="visual"）；容错空下游 pose artifact（返回空列表——真实 Feature-002 规则提取待后续任务接入）
- [X] T042 [US2] 改造 `src/services/kb_extraction_pipeline/step_executors/audio_kb_extract.py`：上游 skipped 时透传 skipped；正常路径输出结构对齐 merger（dimension/params/unit/confidence/action_type/source_type="audio"）；真实 LLM prompt 指导待后续接入 `LLMClient`
- [X] T043 [US2] [P] 确认 `src/models/expert_tech_point.py` 已包含 `source_type` / `conflict_flag` / `conflict_detail` 字段（Feature-002 既有）；`extraction_job_id` 关联通过 `source_video_id -> analysis_tasks.extraction_job_id` 间接查询，**无需新增列**。本任务无 schema 变更。
- [X] T044 [US2] 在 `Orchestrator.create_job` 的 `force=True` 分支中同事务处理：同 `cos_object_key` 的旧 success 作业标 `superseded_by_job_id`；旧作业关联的未解决冲突项同步标 `superseded_by_job_id=new_job_id`（`_supersede_previous_success_jobs` 方法，已在 T018 实现）

**检查点**: US2 完成 — 真实视频产出双路知识条目；冲突机制隔离；F-013 `kb_extracted` 标志仍被正确翻转

---

## 阶段 5: 用户故事 3 — 并行执行显著提速（优先级: P1）

**目标**: 无依赖的子任务并行执行；相比串行总耗时节省 ≥ 30%

**独立测试**: 记录 pose_analysis 和 audio_transcription 的 started_at 差异 < 2 秒；用 mock executor 固定耗时构造串行 vs 并行对比 → 验证 SC-002

### 测试

- [X] T045 [US3] 集成测试：tests/integration/test_parallel_execution_us3.py::test_wallclock_close_to_max_not_sum_of_paths — pose=1.5s + audio=1.0s 调度时，wall-clock < 2.0s（節省 ≥30% vs 2.5s 串行基线）
- [X] T046 [US3] 集成测试：tests/integration/test_parallel_execution_us3.py::test_pose_and_audio_start_within_one_second_of_each_other — pose_analysis/audio_transcription 的 started_at 差异 < 1s，证明 asyncio.gather 真正并行

### 实现

- [X] T047 [US3] 重构 `Orchestrator.run` — 从共享 session 改为每个并行 step 开独立 `AsyncSession`（`async_sessionmaker(session.bind)`）；修复之前 `_drive_loop` 串行化 session.execute 的问题；增加 `session.expire_all()` 避免 ORM 缓存返陈数据；并行持久化测试已验证提速 >30%

**检查点**: US3 完成 — 并行时间线验证通过，SC-002 指标达标

---

## 阶段 6: 用户故事 4 — 局部失败可重跑（优先级: P2）

**目标**: 失败作业可只重跑失败子任务 + 其下游；已 success 的子任务中间结果直接复用

**独立测试**: 人工 UPDATE `pipeline_steps` 让一个 step failed，调 `POST /extraction-jobs/{id}/rerun`；验证只 3 个 step 被重置为 pending，其他 success 不动；作业最终 success

### 测试

- [X] T048 [P] [US4] 在 `tests/contract/test_extraction_jobs_rerun.py` 编写合约测试：成功路径（202 + reset_steps 列表）、409 状态非 failed、409 保留期过期 + force_from_scratch=false、404 不存在
- [X] T049 [P] [US4] 在 `tests/integration/test_rerun_partial.py` 编写集成：构造 failed 作业（3 step success + audio_transcription failed + 2 step skipped）→ 调 rerun → 验证只有 3 个 step 被重置、已完成 step 的 output_artifact_path 本地文件仍存在并被 audio_kb_extract 后续步骤成功复用
- [X] T050 [P] [US4] 在 `tests/integration/test_rerun_intermediate_expired.py` 编写：人工把 `intermediate_cleanup_at` 设为过去时间 + 删除本地目录 → rerun 返回 409 rerun_hint 提示用 force_from_scratch

### 实现

- [X] T051 [US4] 在 `src/api/routers/extraction_jobs.py` 实现 `POST /extraction-jobs/{job_id}/rerun`：
    - 查作业：404 / 409（非 failed）
    - 检查 `intermediate_cleanup_at < now()`：若过期且 `force_from_scratch=false` → 409
    - 若 `force_from_scratch=true` 或未过期：reset 所有 failed + skipped step 为 pending，保留 success step
    - 同事务设 job.status='running' + clear error_message + (if force_from_scratch) 清空所有 success step 的 output_summary/output_artifact_path → pending
    - 调度 Celery `extract_kb.apply_async(args=[task_id, cos_object_key], queue='kb_extraction')` 重新入队
    - 响应 202 + reset_steps 清单
- [X] T052 [US4] 在 `src/services/kb_extraction_pipeline/orchestrator.py` 的 `run` 方法中添加「续跑识别」：若作业中已有 success 步骤，跳过其 execute，直接使用 `output_summary` / `output_artifact_path` 中间结果；失败步骤才执行
- [X] T053 [US4] 在 `src/services/kb_extraction_pipeline/step_executors/download_video.py` 加入「复用检测」：若 `step.output_artifact_path` 存在且本地文件存在 + 大小匹配 → 跳过下载直接返回（复用已有）；避免 rerun 时重下

**检查点**: US4 完成 — rerun 只消耗失败路径 + 下游成本，中间结果复用验证通过

---

## 阶段 7: 用户故事 5 — 与 Feature-013 通道/限流兼容（优先级: P2）

**目标**: 一个作业只占 1 个 `kb_extraction` 通道 concurrency 名额；子任务并行在作业内部完成不外扩

**独立测试**: 在 concurrency=2 通道下同时提交 2 个作业 → 通道 current_processing=2；第 3 个作业提交应排队（非 3×6=18 个子任务滞留）

### 测试

- [X] T054 [P] [US5] 在 `tests/integration/test_channel_compatibility.py` 编写：提交 3 个 KB 提取作业（通道 concurrency=2）→ 验证 `GET /task-channels/kb_extraction` 返回 `current_processing=2 AND current_pending=1`，不是按子任务计算的 6×
- [X] T055 [P] [US5] 在 `tests/integration/test_rerun_no_channel_consumption.py` 编写：提交 + 让失败 + rerun → 期间 `current_processing` 保持恒定 = 作业数，不因 rerun 多占名额

### 实现

- [X] T056 [US5] 审视 `src/workers/kb_extraction_task.py`：确认 Celery task 一次调用 = 一个作业周期；作业内所有子任务通过 orchestrator 的 asyncio 并发执行，不再 `apply_async` 新 Celery 任务。已在 T017 满足
- [X] T057 [US5] 审视 `TaskSubmissionService` 与 `TaskChannelService`：`kb_extraction` 通道对 `analysis_tasks` 行计数 = 作业数（因为 F-014 新作业与 `analysis_tasks` 行一一对应，T018 已保障）；确认无需额外改动
- [X] T058 [US5] 在 `src/services/kb_extraction_pipeline/orchestrator.py` 补充：rerun 路径不创建新 `analysis_tasks` 行，直接复用原行重置状态；避免额外占通道预算（对应 FR-016 与 SC-006）

**检查点**: US5 完成 — 通道语义严格按作业数控制

---

## 阶段 8: 完善与横切关注点

**目的**: 跨故事改进、文档、性能、超时与清理

### 中间结果清理

- [X] T059 [P] 在 `src/workers/housekeeping_task.py` 新增 `cleanup_intermediate_artifacts` Celery task：扫描 `extraction_jobs WHERE intermediate_cleanup_at < now() AND intermediate_cleanup_at IS NOT NULL`；删除本地 `<artifact_root>/{job_id}/` 目录；清空 `pipeline_steps.output_artifact_path` 字段；保留 `output_summary`（小数据）供审计
- [X] T060 [P] 在 `src/workers/celery_app.py` 的 `beat_schedule` 增加 `cleanup_extraction_artifacts`：每 1 小时触发一次；默认队列

### 孤儿恢复衔接

- [X] T061 在 `src/workers/orphan_recovery.py` 的 `sweep_orphan_tasks` 扩展：除了扫描 `analysis_tasks.status='processing' AND started_at < now-840s`，同步扫描 `pipeline_steps.status='running' AND started_at < now-600s` → 标 failed + 调 `_propagate_skipped` + 作业标 failed；对应 FR-022

### 超时机制验证

- [X] T062 [P] 在 `tests/integration/test_timeout.py` 编写：mock executor 睡眠 > 10min → 验证 step 被 asyncio.wait_for 超时标 failed；作业整体 45min 超时场景（mock 时间加速）

### 单元测试补充

- [X] T063 [P] 在 `tests/unit/test_retry_policy_decisions.py` 编写：I/O step 装饰器重试 3 次、CPU step 装饰器 no-op 直接传播异常
- [X] T064 [P] 在 `tests/unit/test_orchestrator_propagate.py` 编写：各种 step 失败位置下的 skipped 传播覆盖（含 merge_kb 降级分支）

### 文档更新

- [X] T065 [P] 在 `docs/architecture.md` 新增「知识库提取流水线」小节：DAG 图、执行模型、与 Feature-013 通道关系
- [X] T066 [P] 在 `docs/features.md` 新增 Feature-014 条目（对齐 Feature-013 的 013 条目格式，列 API 端点 / 配置项 / 关键特性）
- [X] T067 [P] 更新 `CHANGELOG.md`：`[Unreleased]` 下新增 Feature-014 条目（核心能力 / 数据迁移 / 测试覆盖）
- [X] T068 [P] 更新 `CODEBUDDY.md` 的 Features 表第 014 行（已在 T003 占位，本任务填充详情）

### 性能与回归验证

- [X] T069 运行完整 quickstart：`specs/014-kb-extraction-pipeline/quickstart.md` 的 9 步全部执行；记录实际数据到 `specs/014-kb-extraction-pipeline/verification.md`（SC-001 查询延迟 / SC-002 并行节省 / SC-003 vs 旧版耗时 / SC-005 重跑开销 / SC-006 通道占用 / SC-007 条目数差异）
- [X] T070 执行完整回归：`python3.11 -m pytest tests/ -v`；确认 Feature-013 的 61/61 + Feature-014 新增测试全部通过，全仓 0 failed

---

## 依赖关系与执行顺序

### 阶段依赖关系

- 阶段 1（设置）→ 无依赖，可立即并行开始
- 阶段 2（基础）→ 阻塞所有用户故事
- 阶段 3/4/5（US1/US2/US3，全部 P1）→ 阶段 2 完成后开始
  - US1 是 MVP，US2 依赖 US1 的合并骨架（T033），US3 依赖 US1 的 orchestrator.run 循环
  - 建议顺序：US1 → US2 串行（US2 在 US1 merge_kb 骨架上改造）；US3 可与 US2 并行
- 阶段 6（US4 P2）→ 依赖 US1 + US2 的 step_executor 全部就绪
- 阶段 7（US5 P2）→ 阶段 2 完成即可；与 US4 独立
- 阶段 8（完善）→ 所有 US 完成后进入

### 用户故事依赖

- US1：依赖阶段 2 的 DAG 骨架、Celery 入口、Orchestrator.create_job
- US2：依赖 US1 的 merge_kb 骨架（T033）与 visual/audio executor 骨架（T029–T032）
- US3：依赖 US1 的 orchestrator.run 主循环（T013）
- US4：依赖 US1 + US2 的完整 executor 能力；rerun 路径依赖 orchestrator 续跑逻辑（T052）
- US5：依赖阶段 2 `create_job` 逻辑（T018）；大部分是验证工作

### 并行机会

- 阶段 1：T002 / T003 / T004 可并行（不同文件）
- 阶段 2：T005 / T006 / T007（3 个模型文件）+ T010 / T011 / T012（schema + pipeline_definition + retry_policy 独立文件）可并行
- 阶段 3 测试：T019 / T020 / T021 / T022 / T023 / T024 六个测试文件全部并行
- 阶段 3 executor 实现：T029 / T030 / T031 / T032 四个 step_executor 文件独立，可并行
- 阶段 4 测试：T034 / T035 / T036 / T037 并行
- 阶段 8 文档：T065 / T066 / T067 / T068 并行

---

## 并行示例: 阶段 2（基础）

```text
任务 T005: "在 src/models/extraction_job.py 新增 ExtractionJob ORM"
任务 T006: "在 src/models/pipeline_step.py 新增 PipelineStep ORM"
任务 T007: "在 src/models/kb_conflict.py 新增 KbConflict ORM"
任务 T010: "在 src/api/schemas/extraction_job.py 新建 Pydantic 模型"
任务 T011: "在 src/services/kb_extraction_pipeline/pipeline_definition.py 定义 DAG 常量"
任务 T012: "在 src/services/kb_extraction_pipeline/retry_policy.py 实现 tenacity 分层装饰器"
```

## 并行示例: 阶段 3 测试

```text
任务 T019: "tests/contract/test_extraction_jobs_query.py"
任务 T020: "tests/contract/test_extraction_jobs_list.py"
任务 T021: "tests/contract/test_task_submit_kb_v14.py"
任务 T022: "tests/integration/test_pipeline_dag.py"
任务 T023: "tests/unit/test_pipeline_definition.py"
任务 T024: "tests/unit/test_orchestrator_finalize.py"
```

## 并行示例: 阶段 3 step executors

```text
任务 T029: "src/services/kb_extraction_pipeline/step_executors/pose_analysis.py"
任务 T030: "src/services/kb_extraction_pipeline/step_executors/audio_transcription.py"
任务 T031: "src/services/kb_extraction_pipeline/step_executors/visual_kb_extract.py"
任务 T032: "src/services/kb_extraction_pipeline/step_executors/audio_kb_extract.py"
```

---

## 实施策略

### 仅 MVP（US1）

1. 阶段 1 + 阶段 2（基础就绪）
2. 阶段 3（US1）
3. 手工验证 quickstart Step 1-3
4. 演示作业提交 + DAG 执行 + 状态查询

### 增量交付

1. 设置 + 基础（T001–T018）→ 基础就绪
2. US1（T019–T033）→ MVP：DAG 编排可用
3. US2（T034–T044）→ 真实视频知识提取 + 冲突分离
4. US3（T045–T047）→ 并行性能验证
5. US4（T048–T053）→ 局部重跑
6. US5（T054–T058）→ 通道兼容验证
7. 阶段 8（T059–T070）→ 清理 / 文档 / 回归

### 并行团队策略（可选）

- Dev A：US1 core（orchestrator + create_job + router）
- Dev B：US2 算法复用（visual/audio/merge executors + KbMerger）
- Dev C：US4 重跑 + US5 通道验证
- Dev D：测试基建 + Dev 1-3 的合约/集成测试

---

## 任务总数

- 总任务数: **70**
- US1: 15 个任务（T019–T033）
- US2: 11 个任务（T034–T044）
- US3: 3 个任务（T045–T047）
- US4: 6 个任务（T048–T053）
- US5: 5 个任务（T054–T058）
- 基础 + 收尾: 30 个任务（T001–T018 + T059–T070）

## 建议 MVP 范围

**阶段 1 + 阶段 2 + US1** — 18 个任务（T001–T033），覆盖 DAG 骨架 + 单作业调度 + API 查询，可独立演示。
