---
description: "Feature 013 — 任务处理管道重新设计与提交限流 的可执行任务清单"
---

# 任务: 任务处理管道重新设计与提交限流

**输入**: 来自 `/specs/013-task-pipeline-redesign/` 的设计文档
**前置条件**: plan.md ✅ / spec.md ✅ / research.md ✅ / data-model.md ✅ / contracts/ ✅ / quickstart.md ✅

**测试策略**: spec.md 显式要求「独立测试」验收（US1–US5 验收场景），同时章程原则 II 要求合约测试与集成测试。本任务清单为每个用户故事生成合约/集成测试任务（位于实现前）。

## 格式: `[ID] [P?] [Story] 描述`
- **[P]**: 可以并行运行（不同文件，无依赖关系）
- **[Story]**: US1 / US2 / US3 / US4 / US5 — 映射到 spec.md 用户故事
- 所有文件路径均为仓库内绝对/相对路径

## 路径约定
- 后端单一项目：`src/`、`tests/`
- 迁移：`src/db/migrations/versions/`
- 契约：`specs/013-task-pipeline-redesign/contracts/`

---

## 阶段 1: 设置（共享基础设施）

**目的**: 准备依赖与配置，为后续阶段打底

- [X] T001 在 `src/config.py` 的 `Settings` 中新增字段：`admin_reset_token: str`、`batch_max_size: int = 100`、`orphan_task_timeout_seconds: int = 840`；同步更新 `.env.example`
- [X] T002 [P] 在 `src/workers/celery_app.py` 重构队列定义：替换 `video,default` 为 `classification / kb_extraction / diagnosis / default` 四队列；`task_default_queue='default'`；`task_routes` 绑定 `classify_video → classification`、`extract_kb → kb_extraction`、`diagnose_athlete → diagnosis`、`scan_cos_videos → default`、`cleanup_expired_tasks → default`
- [X] T003 [P] 在 `CODEBUDDY.md` 与 `.codebuddy/rules/workflow.md` 更新启动命令段：删除 `video_worker/default_worker` 描述，替换为四 Worker 启动（classification / kb_extraction / diagnosis / default）
- [X] T004 [P] 在 `pyproject.toml` 检查依赖（无新增依赖；kombu 已随 celery 传递），确认版本锁定与项目虚拟环境 `/opt/conda/envs/coaching` 一致

---

## 阶段 2: 基础（阻塞前置条件）

**目的**: 数据模型、基础服务、Celery 骨架必须先就位；阻塞所有用户故事

**⚠️ 关键**: 此阶段完成前任何用户故事代码不得合并

### 数据模型与迁移

- [X] T005 在 `src/models/task_channel_config.py` 新增 `TaskChannelConfig` ORM 模型（task_type PK、queue_capacity、concurrency、enabled、updated_at），并在 `src/models/__init__.py` 导出
- [X] T006 在 `src/models/analysis_task.py` 改造：重建 `TaskType` 枚举为 3 值 `video_classification / kb_extraction / athlete_diagnosis`；新增列 `cos_object_key: Mapped[str | None]`、`submitted_via: Mapped[str] = 'single'`、`parent_scan_task_id: Mapped[uuid.UUID | None]`（自引用 FK）
- [X] T007 创建 Alembic 迁移 `src/db/migrations/versions/0012_task_pipeline_redesign.py`：按 data-model.md 的迁移计划执行（清数据 → DROP TYPE CASCADE → CREATE TYPE → 改列类型 → 新增三列 → 两个索引 → 建 task_channel_configs + 插入 3 行默认配置）
- [X] T008 执行迁移验证：`alembic upgrade head` 成功；`psql` 检查 `\d analysis_tasks` 新列存在、`SELECT * FROM task_channel_configs` 返回 3 行、`idx_analysis_tasks_idempotency` 索引存在

### 基础服务与共享组件

- [X] T009 [P] 在 `src/services/task_channel_service.py` 新建 `TaskChannelService`：`load_config(task_type)`（TTL 30s 缓存）、`get_snapshot(session, task_type)`（查 DB 计数 + 配置合成 ChannelSnapshot）、`update_config(task_type, patch)`；使用 `async_session_factory`
- [X] T010 [P] 在 `src/services/task_submission_service.py` 新建 `TaskSubmissionService`：`submit_batch(task_type, items, force=False) -> SubmissionResult`；内部实现 `pg_advisory_xact_lock(hash(task_type))` → COUNT 权威计数 → 幂等 partial unique 捕获 → INSERT N 条 → `apply_async` 入队；返回每条 accepted/rejected 结果
- [X] T011 [P] 在 `src/api/schemas/task_submit.py` 新建统一提交请求/响应 Pydantic 模型：`ClassificationSingleRequest / KbExtractionSingleRequest / DiagnosisSingleRequest / *BatchRequest / SubmissionItem / SubmissionResult / ChannelSnapshot`；对齐 `contracts/task_submit.yaml` 与 `contracts/channel_status.yaml`
- [X] T012 [P] 在 `src/workers/orphan_recovery.py` 实现 `sweep_orphan_tasks()` 函数：扫描 `analysis_tasks.status='processing' AND started_at < now() - interval '840 seconds'`，批量 UPDATE 为 `failed`，`error_message='orphan recovered on worker restart'`；在 `celery_app.py` 注册 `celeryd_after_setup` 信号调用

### Celery 任务骨架

- [X] T013 [P] 在 `src/workers/classification_task.py` 新增 `classify_video(task_id: str, cos_object_key: str)` Celery task（骨架：更新状态 pending→processing→success，暂不实现分类逻辑）；保留既有 `scan_cos_videos`
- [X] T014 [P] 在 `src/workers/kb_extraction_task.py` 新建 `extract_kb(task_id: str, cos_object_key: str, enable_audio_analysis: bool, audio_language: str)` Celery task（骨架）
- [X] T015 [P] 在 `src/workers/athlete_diagnosis_task.py` 新建 `diagnose_athlete(task_id: str, video_storage_uri: str, knowledge_base_version: str)` Celery task（骨架）
- [X] T016 [P] 在 `src/workers/housekeeping_task.py` 新建并迁移 `cleanup_expired_tasks`（从旧 `athlete_video_task.py` 搬移），并在 beat_schedule 中重新引用
- [X] T017 删除旧文件 `src/workers/expert_video_task.py` 与 `src/workers/athlete_video_task.py`；同步移除 `celery_app.py` 中的旧 `include` 引用

**检查点**: 基础就绪 — 迁移通过、四队列骨架可启动、服务层合约就位

---

## 阶段 3: 用户故事 1 — 单条任务即时处理（优先级: P1）🎯 MVP

**目标**: 用户提交单条任意类型任务后，在对应通道有空闲槽位时 ≤5 秒进入 processing

**独立测试**: 在分类通道堆积 5 条占满的情况下，提交一条 kb_extraction 任务，观测其在 5 秒内状态转为 `processing`（对应 SC-001）

### 测试（合约 + 集成）

- [X] T018 [P] [US1] 在 `tests/contract/test_task_submit_classification.py` 编写对 `POST /api/v1/tasks/classification` 的合约测试（验证请求/响应与 `contracts/task_submit.yaml` 一致：字段、类型、errors）
- [X] T019 [P] [US1] 在 `tests/contract/test_task_submit_kb.py` 编写对 `POST /api/v1/tasks/kb-extraction` 的合约测试（含 CLASSIFICATION_REQUIRED 分支）
- [X] T020 [P] [US1] 在 `tests/contract/test_task_submit_diagnosis.py` 编写对 `POST /api/v1/tasks/diagnosis` 的合约测试
- [X] T021 [P] [US1] 在 `tests/integration/test_task_pipeline_isolation.py` 编写集成测试：分类通道满后提交 kb_extraction，任务 5 秒内进入 processing（使用真实 Celery worker + Redis）

### 实现

- [X] T022 [P] [US1] 在 `src/api/routers/tasks.py` 重写路由：删除旧 `/tasks/expert-video`、`/tasks/athlete-video`；新增 `POST /api/v1/tasks/classification`（调用 `TaskSubmissionService.submit_batch(task_type=classification, items=[request])`）
- [X] T023 [US1] 在 `src/api/routers/tasks.py` 新增 `POST /api/v1/tasks/kb-extraction`：提交前调用 `ClassificationGateService.check_classified(cos_object_key)`；未分类返回 400 `CLASSIFICATION_REQUIRED`
- [X] T024 [US1] 在 `src/api/routers/tasks.py` 新增 `POST /api/v1/tasks/diagnosis`（单条提交）
- [X] T025 [P] [US1] 在 `src/services/classification_gate_service.py` 新建 `ClassificationGateService.check_classified(cos_object_key: str) -> bool`：查询 `coach_video_classifications` 的 `tech_category` 是否为非空且 != 'unclassified'
- [X] T026 [US1] 在 `src/api/main.py` 注册新路由与异常处理器（将 `ValueError` 与服务层自定义异常转为 HTTPException 400/409/429/503）

**检查点**: US1 完成 — 三类单条提交接口可用，kb 前置分类校验生效，空通道提交 ≤5 秒进入 processing

---

## 阶段 4: 用户故事 2 — 批量提交限流与上限保护（优先级: P1）

**目标**: 支持批量提交；单次超 100 条整体拒绝；部分成功语义（前 K 条接受、后 M-K 条 QUEUE_FULL）

**独立测试**: 分类通道容量 5，先提交 3 条占 3/5；再批量提交 5 条 → 响应 `accepted=2, rejected=3`，拒绝项 `rejection_code=QUEUE_FULL`

### 测试

- [X] T027 [P] [US2] 在 `tests/contract/test_task_submit_batch.py` 编写三类批量端点的合约测试（含 BATCH_TOO_LARGE 分支）
- [X] T028 [P] [US2] 在 `tests/integration/test_task_throttling.py` 编写集成测试：容量满后批量提交 → 部分 QUEUE_FULL；提交 101 条 → 整体 400 BATCH_TOO_LARGE

### 实现

- [X] T029 [US2] 在 `src/api/routers/tasks.py` 新增 `POST /api/v1/tasks/classification/batch`（调用 `submit_batch(items=body.items)`）
- [X] T030 [US2] 在 `src/api/routers/tasks.py` 新增 `POST /api/v1/tasks/kb-extraction/batch`（批量提交前逐条调用 `ClassificationGateService.check_classified`，未分类项直接标记为 rejected `CLASSIFICATION_REQUIRED`）
- [X] T031 [US2] 在 `src/api/routers/tasks.py` 新增 `POST /api/v1/tasks/diagnosis/batch`
- [X] T032 [US2] 在 `TaskSubmissionService.submit_batch` 中实现：`len(items) > settings.batch_max_size` 抛 `BatchTooLargeError`（路由层转 400 `BATCH_TOO_LARGE`）
- [X] T033 [US2] 在 `TaskSubmissionService.submit_batch` 中实现部分成功逻辑：循环遍历 items，每条依次校验配额、幂等、前置（对 kb）→ 合并结果返回 `SubmissionResult`；最后一次性获取 ChannelSnapshot 写入响应

**检查点**: US2 完成 — 批量端点就绪，部分成功与整体拒绝语义符合合约

---

## 阶段 5: 用户故事 3 — 任务类型解耦（优先级: P1）

**目标**: 三类任务物理隔离（队列、Worker、service 模块、schema）；一类崩溃不影响其他类

**独立测试**: 停止 kb_extraction worker 后，classification 与 diagnosis 任务仍可正常提交并进入 processing

### 测试

- [X] T034 [P] [US3] 在 `tests/integration/test_pipeline_crash_isolation.py` 编写集成测试：kill kb_extraction worker 进程 → 提交 classification 任务验证 5 秒内 processing
- [X] T035 [P] [US3] 在 `tests/integration/test_orphan_recovery.py` 编写集成测试：启动 diagnosis 任务→ kill -9 worker → 等待 > 840s → 重启 worker → 验证任务状态变为 `failed` 且 `error_message` 包含 "orphan recovered"

### 实现

- [X] T036 [US3] 在 `src/services/classification_service.py` 实现 `classify_single_video(task_id, cos_object_key)`（从旧 `expert_video_task` 分类部分拆出；写入 `coach_video_classifications`）
- [X] T037 [US3] 在 `src/workers/classification_task.py` 的 `classify_video` 中接入 `ClassificationService.classify_single_video`，更新任务状态 processing→success/failed
- [X] T038 [US3] 在 `src/services/kb_extraction_service.py` 实现 `extract_knowledge(task_id, cos_object_key, enable_audio, lang)`（从旧 `expert_video_task` 的音频/知识点提取部分拆出）
- [X] T039 [US3] 在 `src/workers/kb_extraction_task.py` 的 `extract_kb` 中接入 `KbExtractionService.extract_knowledge`
- [X] T040 [US3] 在 `src/services/diagnosis_service.py` 确认或新增 `diagnose_athlete_video(task_id, ...)`（若既有 diagnosis.py service 已存在则改造，复用）
- [X] T041 [US3] 在 `src/workers/athlete_diagnosis_task.py` 的 `diagnose_athlete` 中接入诊断 service
- [X] T042 [US3] 在 `src/api/schemas/` 拆分为 `classification_task.py` / `kb_extraction_task.py` / `diagnosis_task.py`，任一 schema 仅暴露该类型所需字段（验证 FR-002）

**检查点**: US3 完成 — 三类任务物理解耦，单类崩溃/停止不影响其他类

---

## 阶段 6: 用户故事 4 — 数据重置（优先级: P2）

**目标**: 清空历史任务相关数据（保留核心资产），需 confirmation token

**独立测试**: 调用 `POST /api/v1/admin/reset-task-pipeline` 带正确 token → 任务表清空、教练与分类表行数不变

### 测试

- [X] T043 [P] [US4] 在 `tests/contract/test_data_reset.py` 编写合约测试：确认 token 校验 403、dry_run 与非 dry_run 分支、响应结构与 `contracts/data_reset.yaml` 一致
- [X] T044 [P] [US4] 在 `tests/integration/test_data_reset.py` 编写集成测试：插入样本任务数据 → 调用 reset → 校验 `analysis_tasks` 清空、`coach_video_classifications` 行数不变、`tech_knowledge_bases WHERE is_draft=true` 被删除

### 实现

- [X] T045 [P] [US4] 在 `src/services/task_reset_service.py` 新建 `TaskResetService.reset(token: str, dry_run: bool) -> ResetReport`：按 research.md R5 的清理范围执行 TRUNCATE CASCADE；dry_run 仅查询计数不执行
- [X] T046 [US4] 在 `src/api/routers/admin.py` 新建管理路由：`POST /api/v1/admin/reset-task-pipeline`（校验 token 与 `settings.admin_reset_token` 严格相等，否则 403）
- [X] T047 [US4] 在 `src/api/main.py` 注册 `admin` 路由，并在响应头附加 `X-Admin-Operation: true` 便于审计
- [X] T048 [P] [US4] 在 `specs/013-task-pipeline-redesign/scripts/reset_task_pipeline.py` 新建 CLI 脚本：读取 `.env` 的 `ADMIN_RESET_TOKEN` + 要求 `--confirm` 二次确认；调用 `TaskResetService.reset`

**检查点**: US4 完成 — reset API 与 CLI 均可用，核心资产保留

---

## 阶段 7: 用户故事 5 — 通道并发与监控（优先级: P2）

**目标**: 三类通道同时并发运行，各自不超自身 concurrency；提供状态查询接口

**独立测试**: 同时提交 >concurrency 数量任务到三类通道 → `GET /api/v1/task-channels` 返回每类 processing=concurrency

### 测试

- [X] T049 [P] [US5] 在 `tests/contract/test_channel_status.py` 编写对 `GET /api/v1/task-channels` 与 `GET /api/v1/task-channels/{task_type}` 的合约测试
- [X] T050 [P] [US5] 在 `tests/contract/test_channel_admin.py` 编写对 `PATCH /api/v1/admin/channels/{task_type}` 的合约测试（含 token 校验）
- [X] T051 [P] [US5] 在 `tests/integration/test_channel_concurrency.py` 编写集成测试：提交 10 条 kb_extraction → `current_processing=2`（严格等于 concurrency）

### 实现

- [X] T052 [P] [US5] 在 `src/api/routers/task_channels.py` 新建路由：`GET /api/v1/task-channels`（返回三通道列表）、`GET /api/v1/task-channels/{task_type}`（单通道快照）
- [X] T053 [US5] 在 `src/api/routers/admin.py` 追加 `PATCH /api/v1/admin/channels/{task_type}`：校验 token → 更新 `task_channel_configs` → 让 30s 缓存在下一次查询时刷新
- [X] T054 [US5] 在 `TaskChannelService.get_snapshot` 中实现 `recent_completion_rate_per_min`：查询 `completed_at > now() - 10 min AND status='success'` 的条数 / 10
- [X] T055 [US5] 在 `src/api/main.py` 注册 `task_channels` 路由

**检查点**: US5 完成 — 通道状态可观测，并发在控制范围内

---

## 阶段 8: 完善与横切关注点

**目的**: 跨故事改进、文档刷新、性能与安全

- [X] T056 [P] 在 `src/api/routers/tasks.py` 改造 `GET /api/v1/tasks/{task_id}`：按 `task_type` 返回差异化字段（对齐 `contracts/task_query.yaml`）
- [X] T057 [P] 在 `src/api/routers/tasks.py` 改造 `GET /api/v1/tasks` 列表接口：新增 `task_type` 过滤参数；沿用 `page`/`page_size`
- [X] T058 [P] 在 `tests/unit/test_task_channel_service.py` 新建 TaskChannelService 单元测试（配额计算、缓存 TTL）
- [X] T059 [P] 在 `tests/unit/test_task_submission_service.py` 新建 TaskSubmissionService 单元测试（limit 边界、幂等冲突、force 覆盖、部分成功）
- [X] T060 [P] 在 `docs/architecture.md` 更新任务管道章节，同步四队列结构图、配额表、限流流程
- [X] T061 [P] 在 `docs/features.md` 新增 Feature 013 条目，标注「已完成」
- [X] T062 运行完整 quickstart 验证流程（`specs/013-task-pipeline-redesign/quickstart.md` 全部步骤），记录实际数据到 `specs/013-task-pipeline-redesign/verification.md`
- [X] T063 在 `CODEBUDDY.md` 的「Celery 任务」与「队列说明」表格中补充新枚举名 / 移除旧 `expert_video_task` 引用
- [X] T064 执行完整回归测试：`python3.11 -m pytest tests/ -v`，确保所有用例通过

---

## 依赖关系与执行顺序

### 阶段依赖关系

- 阶段 1（设置） → 无依赖，可立即并行开始
- 阶段 2（基础） → 阻塞所有用户故事
- 阶段 3/4/5（US1/US2/US3，全部 P1）→ 可在阶段 2 完成后并行推进；US3 的 service 拆分对 US1/US2 端到端可用性有帮助，建议 US1 → US2 → US3 的串行次序，但代码路径独立，并行也可行
- 阶段 6（US4 P2）→ 阶段 2 完成后即可独立进行
- 阶段 7（US5 P2）→ 阶段 2 完成后即可独立进行
- 阶段 8（完善）→ 依赖所有故事完成

### 用户故事依赖关系

- US1：依赖阶段 2 的 `TaskSubmissionService`、`ClassificationGateService`
- US2：依赖阶段 2 的 `TaskSubmissionService`（扩展批量语义）
- US3：依赖阶段 2 Celery 骨架；业务 service 可与其他故事并行实现
- US4：独立，只依赖 AnalysisTask 模型存在
- US5：依赖阶段 2 的 `TaskChannelService`

### 并行机会

- 阶段 1 的 T002/T003/T004 可并行
- 阶段 2 中 T009/T010/T011/T012/T013/T014/T015/T016 均为独立文件，可并行
- 各用户故事的测试任务（带 [P]）均可并行编写
- US3 的 T036/T038/T040（三个独立 service 文件）可并行
- 完善阶段 T056–T061 大部分可并行

---

## 并行示例: 阶段 2（基础）

```bash
# 同时启动基础阶段中相互独立的任务：
任务 T009: "在 src/services/task_channel_service.py 新建 TaskChannelService"
任务 T010: "在 src/services/task_submission_service.py 新建 TaskSubmissionService"
任务 T011: "在 src/api/schemas/task_submit.py 新建统一提交 schema"
任务 T012: "在 src/workers/orphan_recovery.py 实现 sweep_orphan_tasks"
任务 T013: "在 src/workers/classification_task.py 新增 classify_video 骨架"
任务 T014: "在 src/workers/kb_extraction_task.py 新建 extract_kb 骨架"
任务 T015: "在 src/workers/athlete_diagnosis_task.py 新建 diagnose_athlete 骨架"
任务 T016: "在 src/workers/housekeeping_task.py 迁移 cleanup_expired_tasks"
```

## 并行示例: 用户故事 1 测试

```bash
任务 T018: "tests/contract/test_task_submit_classification.py"
任务 T019: "tests/contract/test_task_submit_kb.py"
任务 T020: "tests/contract/test_task_submit_diagnosis.py"
任务 T021: "tests/integration/test_task_pipeline_isolation.py"
```

---

## 实施策略

### 仅 MVP（US1 + US3）

1. 完成阶段 1（设置）
2. 完成阶段 2（基础 — 迁移、骨架、服务合约）
3. 完成阶段 3（US1 — 单条提交入口）+ 阶段 5（US3 — 业务逻辑拆分）
4. 停下来独立验证：三类单条任务可独立提交与处理
5. 演示/部署作为 MVP

### 增量交付

1. 设置 + 基础（T001–T017）→ 基础就绪
2. US1（单条提交）→ 可演示
3. US2（批量 + 限流）→ 可演示
4. US3（业务逻辑与崩溃隔离完整）→ 解决根本痛点
5. US4（数据重置）→ 运维可用
6. US5（监控与配额管理）→ 生产就绪
7. 阶段 8 完善 → 文档/测试/回归

### 并行团队策略

- Dev A：US1 端点 + ClassificationGate
- Dev B：US2 批量与限流扩展
- Dev C：US3 三个 service 拆分（classification / kb_extraction / diagnosis）
- Dev D：US4 + US5 管理与监控

---

## 任务格式校验清单

- ✅ 所有任务有 `- [ ]` 复选框
- ✅ 所有任务有 `T001–T064` 顺序编号
- ✅ 用户故事阶段任务均带 `[US1–US5]` 标签
- ✅ 可并行任务均带 `[P]` 标记
- ✅ 所有任务均给出明确文件路径
- ✅ 依赖关系显式说明（阶段 2 阻塞 US1–US5）
- ✅ 每个用户故事包含独立测试标准

---

## 注意事项

- 任一任务完成后按「工作流」章程建议提交；提交消息携带任务 ID（如 `feat(013): T022 add classification single submit endpoint`）
- 阶段 2 是硬阻塞：迁移 0012 不通过则所有用户故事代码均不能合并
- 所有数据库操作统一使用 `async_session_factory`（章程 & code-style.md）
- 测试前必须确认测试首先失败，再进入实现（章程原则 II）
- 运行命令统一使用 `/opt/conda/envs/coaching/bin/python3.11`（不用系统 Python 3.9）
