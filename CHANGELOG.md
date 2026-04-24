# Changelog

所有值得记录的变更按 Feature 归档。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

---

## [Unreleased]

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
