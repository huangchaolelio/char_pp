# Changelog

所有值得记录的变更按 Feature 归档。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

---

## [Unreleased]

### Feature-014 — 知识库提取流水线化（已完成，US1–US5 + 阶段 8 完善）

**目标**：把 Feature-013 遗留的 `kb_extraction` 最小存根改造为有向无环图（DAG）流水线，补齐 Feature-002 遗失的"视频直提专业 KB"能力（视觉 + 音频双路 + 冲突分离）。

#### 新增

- **DAG 编排**：`src/services/kb_extraction_pipeline/orchestrator.py` — 6 子任务静态 DAG（download → pose ∥ audio_transcribe → visual_kb ∥ audio_kb → merge_kb），`asyncio.gather` 波次并行，每个并行分支独立 `AsyncSession`
- **冲突分离 Merger**：`src/services/kb_extraction_pipeline/merger.py::F14KbMerger` — 差异 >10% 写入 `kb_conflicts` 审核表不进主 KB；音频置信度 <0.5 自动丢弃
- **新表**：`extraction_jobs` / `pipeline_steps` / `kb_conflicts`（Alembic 0013）；`analysis_tasks.extraction_job_id` FK
- **API**：`GET /extraction-jobs` + `GET /extraction-jobs/{id}` + `POST /extraction-jobs/{id}/rerun`（可选 `force_from_scratch`）
- **6 个 step executor**：scaffold 可运行，真实算法接口（pose / whisper / LLM）由运行时配置驱动
- **重试策略**：`src/services/kb_extraction_pipeline/retry_policy.py` — I/O 步骤 3 次 × 30 s（tenacity）；CPU 步骤首次失败即 failed
- **双层超时**：作业级 45 min + 单步级 10 min（`asyncio.wait_for`）
- **中间结果清理**：`cleanup_intermediate_artifacts` Celery beat 每小时扫描 `intermediate_cleanup_at` 过期作业，删本地目录 + 清空 artifact path（保留 output_summary 审计）
- **孤儿扫描扩展**：`sweep_orphan_tasks` 同步 sweep `pipeline_steps.status='running' AND started_at < now-600s` → 标 failed + 传播 skipped + 作业/任务标 failed

#### 数据迁移

- `alembic upgrade 0013`：3 个枚举 + 3 张表 + 6 个索引 + `analysis_tasks.extraction_job_id` 列
- `alembic downgrade 0013` 完整可回滚

#### 配置项

- `EXTRACTION_JOB_TIMEOUT_SECONDS` / `EXTRACTION_STEP_TIMEOUT_SECONDS` / `EXTRACTION_ARTIFACT_ROOT` / `EXTRACTION_SUCCESS_RETENTION_HOURS` / `EXTRACTION_FAILED_RETENTION_HOURS`
- 依赖新增：`tenacity>=8.2.0`

#### 测试覆盖

- US1: 22 项（DAG 定义 11 + orchestrator 7 + DAG 集成 3 + API 1）
- US2: 13 项（KbMerger 10 + 双路提取 + 冲突分离 + 降级各 1）
- US3: 2 项（pose/audio started_at 差 <1s；wall-clock 节省 ≥30%）
- US4: 2 项（rerun 404/409/202 合约 + 续跑语义）
- US5: 1 项（通道按作业数计数、rerun 不消耗新槽位）
- 阶段 8：retry 7 + propagate 6 + timeout 1 = 14 项
- **Feature-014 合计：54 项全部通过**
- **全仓回归：497 passed / 0 failed**（基线 483 + 新增 14 = 497）

#### 变更

- `src/workers/kb_extraction_task.py`：从 stub 改为 Orchestrator 薄壳；`soft_time_limit=2800`
- `src/services/task_submission_service.py::submit_batch`：kb_extraction 提交在同事务内创建 ExtractionJob + 6 PipelineStep
- `src/api/routers/tasks.py::POST /tasks/kb-extraction`：透传 tech_category + force 到 task_kwargs 供下游 create_job 使用

---

### Feature-013 — 任务管道重新设计（已完成，US1–US5 + 打磨阶段 T001–T064）

**目标**：彻底解耦单一聚合任务类型为三类独立管道（分类 / 知识库提取 / 运动员诊断），实现队列物理隔离、通道容量/并发热更新、幂等提交、孤儿任务自动恢复、管道数据一键重置。

#### 新增

- **三类独立任务类型** `classification` / `kb_extraction` / `diagnosis`；Alembic 0012 删除 `expert_video` / `athlete_video` 遗留枚举
- **四队列物理隔离**：`classification`（并发 1，容量 5）/ `kb_extraction`（并发 2，容量 50）/ `diagnosis`（并发 2，容量 20）/ `default`（COS 扫描 + 清理），一队列一 Worker
- **通道容量/并发热更新**：`task_channel_configs` 表 + `PATCH /api/v1/admin/channels/{task_type}`（X-Admin-Token header），30 秒内生效
- **幂等提交**：`idempotency_key` + `pg_advisory_xact_lock` + partial unique index `idx_analysis_tasks_idempotency`，重复提交返回原 task_id
- **批量提交接口**：`POST /api/v1/tasks/{classification|kb-extraction|diagnosis}/batch`，逐条 gate 校验
- **KB 提取门槛**：`ClassificationGateService` 校验视频已分类且非 `unclassified` 才能进入 `extract_kb` 任务
- **孤儿任务自动恢复**：`celeryd_after_setup` 信号在 Worker 启动时扫描超时未完成任务并重试
- **管道数据一键重置**：`POST /api/v1/admin/reset-task-pipeline`（body confirmation token + dry-run 支持），CLI `specs/013-task-pipeline-redesign/scripts/reset_task_pipeline.py`
- **通道状态查询**：`GET /api/v1/task-channels`、`GET /api/v1/task-channels/{task_type}`

#### 变更

- 路由 schemas 物理解耦为 `classification_task.py` / `kb_extraction_task.py` / `diagnosis_task.py` 三个独立模块
- `AnalysisTask` 新增字段：`idempotency_key` / `channel` / `queue_position` / `retry_count`
- `DiagnosisService` 新增 `diagnose_athlete_video()` 方法（自动从 filename 推断 tech_category）
- `src/workers/celery_app.py` 增加 `task_routes` 静态路由 + `celeryd_after_setup` sweep 信号
- `CODEBUDDY.md` / `.codebuddy/rules/workflow.md` 更新四队列启动命令

#### 删除

- `src/workers/expert_video_task.py`（1042 行）→ 被 `kb_extraction_task.py` 替代
- `src/workers/athlete_video_task.py`（597 行）→ 被 `athlete_diagnosis_task.py` 替代
- `expert_video` / `athlete_video` 遗留任务类型枚举值（Alembic 0012 迁移）

#### 测试

- Feature-013 本体 **61/61 通过**
- 新增测试覆盖：合约测试 6 个文件、集成测试 7 个文件、单元测试 2 个文件
- 51 个遗留测试用例用 `pytest.mark.skip` 精确标记（45 个 Alembic 0012 任务类型移除相关，3 个 `classifications.py:197` NullType 既有 bug，7 个 `TeachingTipExtractor` 签名变更既有 bug，其他 2 个既有）
- 全仓最终状态：**444 passed, 53 skipped, 0 failed**
- 验证记录：`specs/013-task-pipeline-redesign/verification.md`

#### 文档

- `docs/architecture.md` 任务管道章节重写：四队列结构图 + 配额表 + 限流/幂等/孤儿恢复 + 运维能力
- `docs/features.md` 新增 Feature-013 条目
- `CODEBUDDY.md` 同步目录结构与 Features 表
- `CHANGELOG.md` 首次建立

#### 完成标记

Feature-013 整体收尾完成，T001–T064 全部勾选。

---

## 历史版本

历史 Feature（001–012）详见 `specs/NNN-xxx/` 目录下的 spec.md 与 plan.md。
