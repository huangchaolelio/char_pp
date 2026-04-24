# 阶段 0 研究: 任务处理管道重新设计

**日期**: 2026-04-24
**范围**: 解决 plan.md 技术背景中的实现路径选择与最佳实践

---

## R1. Celery 多队列隔离与静态路由

**Decision**: 使用 Celery `task_queues` + `task_routes` 静态路由 + `task_default_queue` 机制；启动三个 Worker 进程，每个进程绑定单一队列；内部 `scan_cos_videos` 保留在 `default` 队列。

**Rationale**:
- 静态路由由 `celery_app.py` 统一声明，不依赖 API 层运行时传参（移除 expert_video 的 `apply_async(queue=...)` 动态路由），降低出错概率。
- 每个 Worker 单一队列、独立并发参数（`-c`），符合 FR-011 「每类通道独立并发」，也便于观测（`celery -A ... inspect active` 按 Worker 查看）。
- 无需引入 Kafka、RabbitMQ 等新中间件（YAGNI 原则 IV）。

**Alternatives considered**:
- 基于优先级队列（单一队列 + `x-max-priority`）：Redis broker 不支持原生优先级，且无法满足「崩溃隔离」。❌ 放弃
- 单 Worker 多队列（`-Q a,b,c`）：无法做独立并发控制，仍会资源竞争，不满足 FR-003。❌ 放弃
- Dramatiq / RQ 替代 Celery：现有代码深度集成 Celery，替换成本远超收益。❌ 放弃

**落地要点**:
- 队列命名：`classification`、`kb_extraction`、`diagnosis`、`default`（内部扫描 & beat 任务）
- 过渡期不再保留 `video` / `celery` 旧队列（配合数据重置，历史消息一并清理）
- Worker 启动：
  ```bash
  celery -A src.workers.celery_app worker -Q classification -n classification_worker@%h -c 1
  celery -A src.workers.celery_app worker -Q kb_extraction   -n kb_worker@%h             -c 2
  celery -A src.workers.celery_app worker -Q diagnosis       -n diagnosis_worker@%h      -c 2
  celery -A src.workers.celery_app worker -Q default         -n default_worker@%h        -c 1
  ```

---

## R2. 限流计数权威数据源（DB vs Redis）

**Decision**: 计数以 `analysis_tasks` 表 (`task_type, status IN ('pending','processing')`) 的 SELECT COUNT(\*) 为权威（FR-010）。在提交事务中使用 `SELECT ... FOR UPDATE` 锁行 + INSERT，保证并发提交下配额不超卖。Redis 队列长度仅作监控用。

**Rationale**:
- Redis 队列长度与 DB 任务状态存在漂移（ack late、重试、orphan 等场景），不可作为权威。
- DB 事务级别保证严格小于等于配额（FR-006）。
- 单次提交请求中的多条任务，放入同一事务一次性 COUNT + INSERT，减少 round-trip。

**Alternatives considered**:
- Redis `INCR` 计数器：实现简单但与 DB 状态不一致，违反 FR-010。❌ 放弃
- 乐观锁（version 字段）：任务表并发高时回滚率高，不如 FOR UPDATE 简单可靠。❌ 放弃

**落地要点**:
- 提交路径：事务开始 → `SELECT COUNT(*) FROM analysis_tasks WHERE task_type=X AND status IN ('pending','processing') FOR UPDATE` → 校验配额 → INSERT N 条 → 提交 → `apply_async()` 入队
- 使用 `pg_advisory_xact_lock(task_type_hash)` 在事务开始时获取通道级锁，避免全表 FOR UPDATE 锁竞争过大

---

## R3. 批量提交与部分成功语义

**Decision**:
- 批量请求条数 > `BATCH_MAX_SIZE`（默认 100）→ 整体 400 `BATCH_TOO_LARGE`（澄清 Q2）。
- 条数 ≤ 上限但通道剩余容量不足时 → 部分成功：前 K 条 INSERT 成功（K=剩余容量），后 N-K 条每条在响应中标记 `rejected` + `reason=QUEUE_FULL`，HTTP 状态 207 Multi-Status 或 200 + body 标记（项目历来 200，沿用）。
- 单条提交视为 N=1 的批量（FR-008），共用同一限流逻辑。

**Rationale**:
- 硬上限（`BATCH_MAX_SIZE`）防止请求过大耗内存；用户澄清选整体拒绝，语义清晰。
- 部分成功（容量不足时）符合 FR-007 验收场景 3 "前 K 条受理、后 M-K 条返回 429"，但 HTTP 层用 200+body 返回（否则必须 207，而项目现有 API 不使用 207）。
- 响应体结构统一为 `SubmissionResult { accepted, rejected, items[{index, task_id?, error?}] }`。

**Alternatives considered**:
- 全或无（所有任务必须同时成功，否则整体失败）：对批量使用者不友好，需客户端实现重试切片逻辑。❌ 放弃
- 每条独立 HTTP 请求：消除部分成功语义但 QPS 暴涨 100×，违反 SC-003 批量 ≤ 2 秒响应。❌ 放弃

---

## R4. 幂等判断机制

**Decision**: 数据库层 `UNIQUE INDEX (cos_object_key, task_type) WHERE status IN ('pending','processing','success')`（PostgreSQL partial unique index）。提交时 INSERT 冲突则返回 `409 DUPLICATE_TASK`；`force=true` 请求参数会先软删掉既有 completed 记录（`deleted_at=now()`）再插入新记录。

**Rationale**:
- Partial unique index 让数据库直接保证幂等，不依赖应用层先查后插的 race condition。
- `force=true` 允许重跑 completed 任务（人工复核场景）；failed/cancelled 自动允许重提（因不命中 partial index）。
- 与澄清 Q5 决议一致。

**Alternatives considered**:
- 应用层 SELECT + INSERT：并发场景下存在竞态窗口。❌ 放弃
- 完整 UNIQUE（含 failed）：与「允许重试失败任务」冲突。❌ 放弃

**落地要点**:
- 现有 `AnalysisTask` 模型无 `cos_object_key` 字段，当前用 `video_storage_uri` 加密存储；需新增明文 `cos_object_key` 索引字段（幂等判断不能用加密密文）
- 迁移 0012 中新增列 + partial unique index

---

## R5. 数据重置与 confirmation token

**Decision**: 数据重置作为 **管理型 API**（`POST /api/v1/admin/reset?confirmation_token=<env-configured>`），token 从 `.env` 的 `ADMIN_RESET_TOKEN` 读取，不匹配则 403。同时提供 `scripts/reset_task_pipeline.py` CLI（读 env 同一 token 或需加 `--confirm` 二次确认）。

**Rationale**:
- 满足 FR-017「显式确认标识」与边界情况「防误删」。
- CLI 版本便于运维直接执行，不依赖 API 服务存活。

**清理范围（精确列表）**:
- 清空（TRUNCATE ... CASCADE）:
  - `analysis_tasks`
  - `audio_transcripts`
  - `coaching_advice`
  - `teaching_tips`
  - `expert_tech_points`
  - `tech_semantic_segments`
  - `athlete_motion_analyses`
  - `diagnosis_reports`
  - `deviation_reports`
  - `skill_executions`
- 删除 draft 版本（条件 DELETE）:
  - `tech_knowledge_bases WHERE is_draft = true OR published_at IS NULL`
- 保留（不动）:
  - `coaches`、`coach_video_classifications`、`video_classifications`、`tech_standards`、已发布 `tech_knowledge_bases`、`skills`、`reference_videos`、`reference_video_segments`

**落地要点**: 封装在 `src/services/task_reset_service.py`，提供 `reset(confirm_token: str) -> ResetReport(deleted_counts: dict[str,int])`。

---

## R6. Orphan Task 回收（FR-014）

**Decision**: Celery worker 启动信号 `celeryd_after_setup` 中执行 `orphan_recovery.sweep()`：对 `analysis_tasks.status='processing'` 但 Redis 队列中无对应消息、且 `started_at < now() - 2*task_time_limit`（即 840s）的任务，批量更新为 `failed` 并写入 `error_message='orphan recovered on worker restart'`。

**Rationale**:
- Celery 的 `task_acks_late=True` 已经设置，正常情况下 worker 崩溃后消息会被重新投递；但数据库 `processing` 状态是应用层设置的，worker 崩溃时不会自动回滚。
- 定义 orphan 时间窗 2×task_time_limit 足够覆盖正常运行的最长任务，避免误杀。

**落地要点**:
- 检测方式：比较 DB processing 任务的 `id`（存入 Celery args 或 task header）与 Redis 未 ack 消息列表
- 简化：仅基于时间窗判断（> 840s 未完成则认为 orphan），不做 Redis 端精确比对，避免引入复杂度（YAGNI）

---

## R7. 通道配置存储：DB 表 vs 环境变量

**Decision**: 使用 DB 表 `task_channel_configs`（task_type PK, queue_capacity, concurrency, enabled, updated_at），启动时加载到内存 + TTL 缓存 30 秒（满足 SC-004 "配置变更 30 秒内生效"）。Worker 并发参数仍由启动命令 `-c` 指定（Celery 运行时不支持动态 reload，但查询接口会读 DB 值用于监控一致性）。

**Rationale**:
- DB 存储便于 API 热更新 `queue_capacity`（影响限流，无需重启），满足 SC-004。
- Worker 进程并发 `-c` 与 DB 记录保持一致即可；变更并发需重启 Worker（运维操作，不频繁）。

**Alternatives considered**:
- 纯 `.env` 配置：变更需重启服务，不满足 SC-004。❌ 放弃
- 全部 Redis 持久化：与 DB 权威原则冲突，不必要。❌ 放弃

---

## R8. TaskType 枚举重建的迁移策略

**Decision**: 迁移 0012 一次性完成：
1. 先执行数据清理（清空任务相关表，使用 CASCADE）
2. DROP TYPE `task_type_enum` CASCADE（会同时去除依赖列的类型绑定）
3. CREATE TYPE `task_type_enum` 为新 3 值枚举：`('video_classification','kb_extraction','athlete_diagnosis')`
4. 重建 `analysis_tasks.task_type` 列类型
5. 新增 `cos_object_key VARCHAR(1000)` 明文列 + partial unique index
6. 新增 `task_channel_configs` 表 + 插入默认 3 行配置

**Rationale**:
- 旧任务数据全删（用户澄清 Q4），无需 Alembic 数据迁移逻辑，简化 migration。
- 单一迁移文件保证原子性（事务内）。

**Alternatives considered**:
- 保留旧枚举值做归档：澄清 Q4 明确否定。❌
- 分两次迁移（先清数据，再改类型）：多余的版本号消耗，YAGNI。❌

---

## R9. kb_extraction 前置分类校验（FR-004a）

**Decision**: `POST /api/v1/tasks/kb-extraction` 请求处理流程：
1. 根据 `cos_object_key` 查询 `coach_video_classifications`
2. 若无记录或 `tech_category IS NULL` 或 `tech_category='unclassified'` → 返回 `400 CLASSIFICATION_REQUIRED`，错误详情指示「请先提交 `video_classification` 任务」
3. 记录命中 → 正常进入限流/幂等 → 入队

**Rationale**: 硬性前置（澄清 Q3），避免 kb_extraction 任务在 Worker 内部再做分类造成职责混杂（违反 FR-004）。

---

## R10. 历史 Worker 文件的处置

**Decision**: `expert_video_task.py` 与 `athlete_video_task.py` 两个文件直接删除；其中有用的业务逻辑（如 Whisper 转录、知识点提取、姿态估计调用）迁移至新的 `kb_extraction_task.py` / `athlete_diagnosis_task.py`，同步拆分出 service 层。`athlete_video_task.cleanup_expired_tasks` 的 Celery beat 任务迁移到新模块 `src/workers/housekeeping_task.py`。

**Rationale**: 澄清 Q4「彻底删除历史类型」的自然延伸；避免遗留文件引起歧义。

---

## 研究结果汇总

| ID | 主题 | 结论 | 关键产出位置 |
|----|------|------|-------------|
| R1 | 队列隔离机制 | 静态路由 + 多 Worker 进程 | `celery_app.py` / 启动命令 |
| R2 | 限流计数权威 | DB 为准 + pg_advisory_xact_lock | `task_channel_service.py` |
| R3 | 批量语义 | 部分成功 + 400 上限校验 | `task_submission_service.py` / contracts |
| R4 | 幂等机制 | Partial unique index + force 参数 | 迁移 0012 + schemas |
| R5 | 数据重置 | API + CLI 双通道，token 保护 | `task_reset_service.py` + script |
| R6 | Orphan 回收 | Worker 启动时基于时间窗扫描 | `orphan_recovery.py` |
| R7 | 通道配置 | DB 表 + 30 秒 TTL 缓存 | `task_channel_config.py` 模型 |
| R8 | 枚举迁移 | 清数据 → DROP/CREATE TYPE → 建列 → 新表 | 迁移 0012 |
| R9 | kb 前置分类 | 提交时硬性校验 tech_category | `task_submission_service.py` |
| R10 | 旧 Worker 处置 | 删除旧文件，业务逻辑迁移拆分 | `src/workers/` + `src/services/` |

所有 `NEEDS CLARIFICATION` 均已解决。阶段 0 完成。
