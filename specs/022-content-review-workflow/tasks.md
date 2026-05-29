---
description: "Feature-022 业务流程四阶段化 + 内容准备阶段引入审核门 — 实施任务清单"
---

# 任务: 业务流程四阶段化 + 内容准备阶段引入审核门

**功能分支**: `022-content-review-workflow`
**输入**: 来自 `/specs/022-content-review-workflow/` 的设计文档
**前置条件**:
- ✅ [plan.md](./plan.md)（必需）
- ✅ [spec.md](./spec.md)（用户故事必需）
- ✅ [research.md](./research.md)
- ✅ [data-model.md](./data-model.md)
- ✅ [contracts/content-reviews.yaml](./contracts/content-reviews.yaml)
- ✅ [contracts/error-codes.md](./contracts/error-codes.md)
- ✅ [quickstart.md](./quickstart.md)

**测试**: 本 Feature 涉及章程级业务流程结构变更与新增 API 接口；按章程原则 II「测试优先」，所有 5 个新 endpoint **必须**先在 `tests/contract/` 创建合约测试再写实现；审核门 / stale 处理 / 绕过开关三条关键链路**必须**有集成测试。

**组织结构**: 任务按用户故事分组，以便每个故事能够独立实施和测试。

## 格式: `[ID] [P?] [Story] 描述`

- **[P]**: 可以并行运行（不同文件，无依赖关系）
- **[Story]**: 此任务属于哪个用户故事（US1 / US2 / US3 / US4）
- 描述中包含确切的文件路径

## 路径约定

单一项目（与项目 `src/` + `tests/` 布局一致，章程附加约束）。

---

## 阶段 1: 设置（共享基础设施 / 章程级前置）

**目的**: 章程级业务流程结构变更（原则 X 强制双向同步）+ Feature 工作目录就绪。

> ⚠️ **关键**：T001 与 T002 是**章程级前置**，与本 Feature 的所有代码任务严格串行——业务流程文档与章程未同步前，禁止合入任何代码改动（章程 v2.1.0 原则 X 硬约束）。

- [ ] T001 在 [docs/business-workflow.md](../../docs/business-workflow.md) 同步四阶段重构：§ 1 概述「三阶段八步骤 → 四阶段九步骤」；§ 2 阶段判据表新增 `CONTENT_PREP` 行 + 调整 `TRAINING` 入口判据；§ 3.x 调整 `scan_cos_videos` / `preprocess_video` / `classify_video` / `curate_segments` 的"所属阶段"为 `CONTENT_PREP`，并新增 § 3.5 `content_review` 步骤章节；§ 7.1/7.2 增加 `phase=CONTENT_PREP` 维度与 `content_review` 步骤指标；§ 9 调参杠杆映射追加"审核门绕过开关"；§ 10 新增"审核门绕过应急回滚剧本"；§ 11 交叉索引同步
- [ ] T002 在 [.specify/memory/constitution.md](../../.specify/memory/constitution.md) 同步章程升版：原则 X 措辞从"三阶段（TRAINING / STANDARDIZATION / INFERENCE）"修订为"四阶段（CONTENT_PREP / TRAINING / STANDARDIZATION / INFERENCE）"；版本头从 `v2.1.0` 升至 `v2.2.0`（MINOR：原则实质性扩展）；顶部 SYNC IMPACT REPORT 段落追加 v2.2.0 条目说明本次 Feature-022 触发的同步范围；**同步检查并修正下列 6 个文件中的"三阶段"措辞**：[.specify/templates/plan-template.md](../../.specify/templates/plan-template.md) / [.specify/templates/spec-template.md](../../.specify/templates/spec-template.md) / [.specify/templates/tasks-template.md](../../.specify/templates/tasks-template.md) / [docs/features.md](../../docs/features.md) / [docs/architecture.md](../../docs/architecture.md) / [docs/business-workflow.md](../../docs/business-workflow.md)（与 T001 协同；遵循章程 v2.0.0 SYNC IMPACT REPORT 规范）
- [ ] T003 [P] 验证 specs/022 工作目录完整性：确认 [plan.md](./plan.md) / [spec.md](./spec.md) / [research.md](./research.md) / [data-model.md](./data-model.md) / [contracts/content-reviews.yaml](./contracts/content-reviews.yaml) / [contracts/error-codes.md](./contracts/error-codes.md) / [quickstart.md](./quickstart.md) 均存在且无残留 `[NEEDS CLARIFICATION]` 标记

**检查点**：T001 + T002 必须先合入主干，章程双向同步通过后方可启动阶段 2。

---

## 阶段 2: 基础（阻塞前置条件 / 数据层与错误码）

**目的**: 建立数据模型、迁移与错误码登记，为所有用户故事提供底层支撑。

> ⚠️ **关键**：阶段 2 完成前，任何用户故事的实现任务都无法启动。

- [ ] T004 创建 Alembic 迁移 [src/db/migrations/versions/0021_content_review_workflow.py](../../src/db/migrations/versions/0021_content_review_workflow.py)：按 [data-model.md § 6](./data-model.md) 的脚本骨架完整实现 6 个 step（扩 enum / 回填既有任务 / 建 `content_review_decisions` 表 / `coach_video_classifications` 加 4 列 + CHECK / 4 个新索引 / 插入 `task_channel_configs` 配置行）；`upgrade()` 与 `downgrade()` 均需实现（enum 不回退，符合"测试阶段只前进"策略）
- [ ] T005 [P] 在 [src/models/coach_video_classification.py](../../src/models/coach_video_classification.py) 新增 4 列 ORM 映射：`review_state` / `review_version` / `last_decision_id` / `pending_since`；新增 CHECK 约束 `ck_cvclf_review_state` 与 4 个新索引；新增 `review_decisions` 与 `last_decision`（`post_update=True`）两个 `relationship`
- [ ] T006 [P] 创建新 ORM 模型 [src/models/content_review_decision.py](../../src/models/content_review_decision.py)：`ContentReviewDecision` 类，按 [data-model.md § 4.1](./data-model.md) 完整字段 + 2 个 CHECK 约束 + 3 个索引；导出至 `src/models/__init__.py`
- [ ] T007 在 [src/api/errors.py](../../src/api/errors.py) 集中登记 8 个新错误码：按 [contracts/error-codes.md § 2](./contracts/error-codes.md) 同步 `ErrorCode` 枚举 + `ERROR_STATUS_MAP` + `ERROR_DEFAULT_MESSAGE` 三张表；保留原"Feature-022: 内容审核门"分组注释
- [ ] T008 [P] 创建 Pydantic schema [src/api/schemas/content_reviews.py](../../src/api/schemas/content_reviews.py)：按 [contracts/content-reviews.yaml](./contracts/content-reviews.yaml) 的 `components.schemas` 节定义 `ReviewState` / `Decision` / `ReasonCode` 三个 Enum + `ContentReviewItem` / `ContentReviewDetail` / `ReviewDecision` / `DecisionSubmitRequest` / `StatsResponse` / `ReviewGateConfig` / `ReviewGatePatchRequest` 八个 BaseModel；统一使用 Pydantic v2 `model_config = ConfigDict(...)`
- [ ] T009 [P] 在 [src/config.py](../../src/config.py) 新增设置项 `kb_extraction_bypass_review_gate: bool = False`（环境变量 `KB_EXTRACTION_BYPASS_REVIEW_GATE`）作为应急熔断兜底；新增 `review_pending_red_line_hours: int = 24` 用于积压告警阈值（FR-016）
- [ ] T010 应用迁移并验证：执行 `alembic upgrade head`；按 [data-model.md § 8](./data-model.md) 验收清单逐项检查（enum 4 值、回填正确、新表 + 索引建立、配置行已插入）

**检查点**：阶段 2 完成后，数据层已就绪；可并行启动 US1 / US2 / US3 / US4 的实现工作。

---

## 阶段 3: 用户故事 1 — 业务流程升级为四阶段（优先级: P1）🎯 MVP

**故事目标**: 让"原始素材进入到形成可用语料"的全链路从 TRAINING 阶段独立出来，构建 `CONTENT_PREP` / `TRAINING` / `STANDARDIZATION` / `INFERENCE` 四阶段视图，并保证 `STANDARDIZATION` / `INFERENCE` 的对外行为不发生破坏性变化。

**独立测试**: 调用 `GET /api/v1/tasks` 后，响应中 `business_phase` 字段取值为四阶段之一；新提交的 `scan_cos_videos` / `preprocess_video` / `classify_video` / `curate_segments` 任务被归入 `CONTENT_PREP`；`extract_kb` 任务被归入 `TRAINING`；既有 `STANDARDIZATION` / `INFERENCE` 接口回归测试无失败。

### 用户故事 1 的实施

- [ ] T011 [P] [US1] 在 [src/services/task_submission_service.py](../../src/services/task_submission_service.py) 调整 `business_phase` 推导：当 `task_type` ∈ `{video_classification, video_preprocessing, video_curation}` 时返回 `CONTENT_PREP`；当 `task_type=kb_extraction` 时返回 `TRAINING`；其余按既有规则
- [ ] T012 [P] [US1] 在 [src/api/schemas/task_submit.py](../../src/api/schemas/task_submit.py) 与相关响应 schema 中确认 `business_phase` 字段已暴露给所有任务列表/详情接口；如有缺失则补全
- [ ] T013 [US1] 创建集成测试 [tests/integration/test_022_phase_routing.py](../../tests/integration/test_022_phase_routing.py)：覆盖 4 类任务的 `business_phase` 归属正确（验证 US1.AC1/AC2）；调用阶段视图聚合查询确保四阶段独立计数互不混算（验证 US1.AC3）；对 `STANDARDIZATION` / `INFERENCE` 既有任务做基线断言（验证 US1.AC4）

**检查点**：US1 应可独立通过验证——任务流入 4 个阶段桶，互不串扰；既有 STD/INF 行为不受影响。

---

## 阶段 4: 用户故事 2 — 审核门作为内容准备阶段的最终判据（优先级: P1）🎯 MVP

**故事目标**: 在内容清洗完成后视频条目自动置为"待审核"；审核员决策"通过"后才能被 KB 抽取消费；任何"未审核 / 已拒绝 / 已失效"条目的 KB 抽取请求必须被明确拒绝。

**独立测试**: 在审核台对一条已清洗视频提交"通过"后立即提交 KB 抽取，可成功入队；对另一条保留"待审核"或"拒绝"状态，提交 KB 抽取应被拒绝并返回 `CONTENT_NOT_REVIEWED` / `CONTENT_REVIEW_REJECTED` / `CONTENT_REVIEW_STALE` 之一。

### 用户故事 2 的合约测试（必须先于实现）⚠️

- [ ] T014 [P] [US2] 创建合约测试 [tests/contract/test_022_content_reviews_contract.py](../../tests/contract/test_022_content_reviews_contract.py)：覆盖 [contracts/content-reviews.yaml](./contracts/content-reviews.yaml) 中 5 个 endpoint（GET 列表 / GET 详情 / POST 决策 / GET 统计 / PATCH review-gate）的请求-响应 schema 与统一信封；覆盖 [contracts/error-codes.md § 5](./contracts/error-codes.md) 要求的 8 个错误码触发用例；测试**必须先失败**

### 用户故事 2 的实施

- [ ] T015 [P] [US2] 创建审核门服务 [src/services/content_review/review_gate.py](../../src/services/content_review/review_gate.py)：参照 [src/services/curation/kb_gate.py](../../src/services/curation/kb_gate.py) 的"两点对接"风格，实现 `evaluate_review_gate(session, *, cos_object_key)` 异步函数；返回 `GateResult(decision=ok|required|rejected|stale|bypassed, cvclf_id, review_state, review_version)`；`bypassed` 决策需双层判定（`settings.kb_extraction_bypass_review_gate=True` 或 `task_channel_configs.content_review_gate.enabled=False`）
- [ ] T016 [P] [US2] 创建 stale 处理器 [src/services/content_review/stale_handler.py](../../src/services/content_review/stale_handler.py)：实现 `mark_review_stale_after_recurate(session, cvclf_id)` 异步函数，按 [research.md R6](./research.md) 决策实现状态机（`approved → pending_review` 经 `stale` 中转 + `review_version += 1` + `pending_since=now()` + 旧决策行 `superseded_at=now()`；`rejected` 不动）
- [ ] T017 [US2] 在 [src/services/curation/curation_service.py](../../src/services/curation/curation_service.py) 的清洗作业 success 落库回调链尾部，调用 T016 实现的 `mark_review_stale_after_recurate`；保证回调失败时不阻塞清洗作业本身的成功落库（错误降级写入 logging）
- [ ] T018 [US2] 在 [src/api/routers/tasks.py](../../src/api/routers/tasks.py) 的 `submit_kb_extraction` 与 `submit_kb_extraction_batch` 入口，于既有 `evaluate_curation_gate` 调用之后追加 `evaluate_review_gate` 调用；按 GateResult.decision 抛 `AppException(ErrorCode.CONTENT_NOT_REVIEWED | CONTENT_REVIEW_REJECTED | CONTENT_REVIEW_STALE)`；`bypassed` 决策时在响应 header 写入 `X-Review-Gate-Bypass: true` 并允许通过
- [ ] T019 [US2] 在 KB 抽取 DAG 第一步（`src/services/kb_extraction_pipeline/step_executors/download_video.py`，由 Feature-014/021 已存在）的开头追加防御性审核门校验：调用 `evaluate_review_gate`，命中 `required/rejected/stale` 时让 step 直接 fail 并写入 step output_summary 中（与清洗门"两点对接"风格一致）
- [ ] T020 [US2] 在 [src/api/routers/tasks.py](../../src/api/routers/tasks.py) 的 KB 抽取入口校验位置增加结构化日志：记录 `cvclf_id` / `review_state` / `gate_decision` / `bypassed`，符合章程原则 V 可观测性要求
- [ ] T020a [US2] 创建路由文件 [src/api/routers/content_reviews.py](../../src/api/routers/content_reviews.py)，实现 **EP-3 `POST /content-reviews/{cvclf_id}/decisions`** （决策提交）为 MVP 必需端点；调用 T023 的 `submit_decision`（US3 被引用的 review_service 实现，**本任务需同步创建 `submit_decision` 的最小可用版**，仅覆盖 MVP 路径）；统一使用 `ok(data)` 信封构造器；统一抛 `AppException`；必须校验 `X-Reviewer-Id` header 与请求体 `reviewer_id` 一致（不一致 → `INVALID_REVIEWER_IDENTITY`）；同步在 [src/api/main.py](../../src/api/main.py) 注册：`app.include_router(content_reviews_router, prefix="/api/v1")`
- [ ] T021 [US2] 创建集成测试 [tests/integration/test_022_review_gate_blocks_kb.py](../../tests/integration/test_022_review_gate_blocks_kb.py)：覆盖 4 种状态（pending/approved/rejected/stale）下提交 KB 抽取的预期结果（验证 US2.AC1/AC2/AC3 + FR-008/009）
- [ ] T022 [US2] 创建集成测试 [tests/integration/test_022_stale_after_recurate.py](../../tests/integration/test_022_stale_after_recurate.py)：模拟 approved → 重洗 → stale → 新 KB 抽取被 `CONTENT_REVIEW_STALE` 拒绝；同时验证已派发的旧 `extract_kb` 任务**不**被级联中止（验证 US2.AC4 + FR-011/011a）

**检查点**：US2 完成后，**仅依赖 T020a 已创建的 EP-3 决策接口**即可独立端到端验证整条审核门链路。US3 的其余 4 个查询/统计端点可后续增量交付，不阻塞 MVP。

---

## 阶段 5: 用户故事 3 — 审核工作台与统计（优先级: P2）

**故事目标**: 提供集中的审核工作台，支持按教练/技术类别筛选待审核条目、查看清洗摘要、提交决策、回看历史与团队统计。

**独立测试**: 审核员通过 5 个 endpoint 完成"列表 → 详情 → 决策 → 统计"完整闭环；列表查询 `page_size ≤ 50` 的 P95 < 500 ms（FR-017）。

### 用户故事 3 的实施

- [x] T023 [P] [US3] 扩充审核服务 [src/services/content_review/review_service.py](../../src/services/content_review/review_service.py)：T020a 已交付 `submit_decision` 的 MVP 最小可用版；本任务**补齐**剩余 3 个异步方法：`list_reviews(filters, page, page_size)` / `get_review_detail(cvclf_id)` / `get_stats(from_, to_, group_by)`；同时加固 `submit_decision`：乐观锁（`expected_review_version` 不一致 → `REVIEW_VERSION_CONFLICT`）+ 事务内一次性完成"写新决策行 / 旧行 superseded / 主表三字段更新 / `review_version += 1`"
- [x] T024 [US3] 在 [src/api/routers/content_reviews.py](../../src/api/routers/content_reviews.py)（T020a 已创建的文件）追加剩余 3 个查询/统计端点：EP-1 `GET /content-reviews`、EP-2 `GET /content-reviews/{cvclf_id}`、EP-4 `GET /content-reviews/stats`；统一使用 `ok(data)` / `page(items, ...)` 信封构造器；统一抛 `AppException`；复用 T020a 已在 main.py 注册的 router、无需重复注册
- [x] T025 [P] [US3] 在 [src/api/routers/admin.py](../../src/api/routers/admin.py) 新增 EP-5a `GET /admin/review-gate` 与 EP-5b `PATCH /admin/review-gate`；写入 `task_channel_configs.content_review_gate` 行的同时记 `last_toggled_at` / `last_toggled_by` + 结构化审计日志（实现位置：合并入 [src/api/routers/content_reviews.py](../../src/api/routers/content_reviews.py)；admin.py 路径段由路由内绝对路径声明承载，避免单独 admin 模块体量过小）
- [x] T026 [US3] 验证 [src/api/main.py](../../src/api/main.py) 中 `content_reviews_router` 注册状态（T020a 已注册，本任务仅需交叉检验：启动服务后 `GET /openapi.json` 中 EP-1/EP-2/EP-3/EP-4 4 个路径均已暴露；若发现遗漏则补全注册）
- [x] T027 [US3] 创建单元测试 [tests/unit/test_022_review_service.py](../../tests/unit/test_022_review_service.py)：覆盖 `submit_decision` 的乐观锁冲突、`rejected` 必须带 `reason_code`、`reason_code=other` 必须带 `note`、`approved` → 主表 `review_state` 与 `last_decision_id` 同步更新等业务规则
- [x] T028 [US3] 在 T023 的 `list_reviews` 中实现 [research.md R7](./research.md) 提到的 SQL 查询形态：默认列表过滤 `rejected`（除非显式传 `?state=rejected`）；按 `coach_name` / `tech_category` / 时间窗筛选走对应索引；`ORDER BY pending_since ASC NULLS LAST`
- [x] T029 [US3] 创建性能验证测试 [tests/integration/test_022_review_list_performance.py](../../tests/integration/test_022_review_list_performance.py)：插入 50,000 条 `coach_video_classifications` 数据后，反复调用列表接口测量 P95；目标 `page_size=20` < 200ms、`page_size=50` < 500ms（FR-017）

**检查点**：US3 完成后，审核员可通过 5 个 endpoint 完成完整工作流；列表性能达标。

---

## 阶段 6: 用户故事 4 — 阶段级可观测性与回滚（优先级: P3）

**故事目标**: 让 `CONTENT_PREP` 阶段拥有独立的指标维度（进入/退出/时延/失败分布）；审核步骤具备步骤级监控锚点；审核门绕过开关可在 30 秒内热生效，每次切换有审计留痕。

**独立测试**: 监控仪表盘可看到 `phase=CONTENT_PREP` 独立曲线；切换审核门为"绕过"后 30 秒内待审核条目可被 KB 直接消费；切回严格后立即恢复且不留遗留豁免（验证 US4.AC1/AC2/AC3 + SC-007）。

### 用户故事 4 的实施

- [x] T030 [P] [US4] 在 [src/services/content_review/review_service.py](../../src/services/content_review/review_service.py)（与 T023 同文件）扩展指标埋点：用现有结构化日志 anchor（与 Feature-018 § 7.2 步骤级监控锚点一致）记录 `content_review_pending_count` / `content_review_decision_count{decision}` / `content_review_latency_seconds` / `content_review_pending_since_p95_seconds` 四个指标
- [x] T031 [P] [US4] 在 [src/services/task_submission_service.py](../../src/services/task_submission_service.py) 的任务 enqueue/dequeue 路径，按 `business_phase` 维度记录 `phase_enter_count` / `phase_exit_count` / `phase_dwell_seconds` 三个阶段级指标（满足 FR-012）
- [x] T032 [P] [US4] 创建积压告警逻辑 [src/services/content_review/backlog_monitor.py](../../src/services/content_review/backlog_monitor.py)：实现 `check_pending_backlog()` 异步函数，扫描 `coach_video_classifications WHERE review_state='pending_review' AND pending_since < now() - settings.review_pending_red_line_hours`；命中即写入 ERROR 级结构化日志（不阻塞流程）；由现有 housekeeping `cleanup_intermediate_artifacts` 同 worker 周期触发，每小时执行一次
- [x] T033 [US4] 在 [src/workers/housekeeping_task.py](../../src/workers/housekeeping_task.py) 把 T032 的 `check_pending_backlog` 接入 Celery Beat 周期调度（沿用 `default` 队列，每小时一次）
- [x] T034 [US4] 创建集成测试 [tests/integration/test_022_bypass_switch.py](../../tests/integration/test_022_bypass_switch.py)：调用 PATCH `/admin/review-gate` 切换为 `enabled=false`，30 秒内提交 KB 抽取应放行（含 `X-Review-Gate-Bypass: true` header）；切回 `enabled=true` 后立即恢复严格行为；切换全程 ≤ 30s（验证 SC-007）；审计字段 `last_toggled_at` / `last_toggled_by` 落库
- [x] T035 [US4] 创建集成测试 [tests/integration/test_022_phase_observability.py](../../tests/integration/test_022_phase_observability.py)：模拟一条视频从 scan → preprocess → classify → curation → review → kb_extraction 的完整流转，断言每个阶段在结构化日志中产生独立的 `phase_enter` / `phase_exit` 事件，`phase` 字段取四阶段之一（验证 US4.AC1）

**检查点**：US4 完成后，所有可观测性 + 回滚剧本到位，可以联通 SRE 仪表盘做端到端演练。

---

## 阶段 7: 完善与横切关注点

**目的**: 收尾文档、跨故事性能与稳定性优化、quickstart 演练落地。

- [ ] T036 [P] 用 `refresh-docs` skill 同步更新 [docs/architecture.md](../../docs/architecture.md) 与 [docs/features.md](../../docs/features.md)：架构图加入 `CONTENT_PREP` 阶段与审核门控件；features.md 追加 Feature-022 章节（含 5 个 endpoint、错误码、业务流程升级摘要）
- [ ] T037 按 [quickstart.md](./quickstart.md) 主演练（步骤 1–5）真实执行一次端到端验证；记录任何与预期不一致之处并回填到 quickstart 故障排查表
- [ ] T038 按 [quickstart.md § 6](./quickstart.md) 附加演练（重洗失效 / 拒绝过滤 / 绕过开关 / 统计接口）逐项执行验证，覆盖所有 SC 指标（SC-001 ～ SC-007）
- [ ] T039 [P] 在 [tests/unit/test_022_review_state_machine.py](../../tests/unit/test_022_review_state_machine.py) 增加状态机的单测覆盖：枚举所有 16 种 `(current_state, action)` 组合，断言转移结果或 `REVIEW_NOT_PENDING` 拒绝
- [ ] T040 检查 [src/api/errors.py](../../src/api/errors.py) 三张表与 [contracts/error-codes.md](./contracts/error-codes.md) 完全一致；确认无裸字符串错误码出现在业务代码中（grep `raise.*"CONTENT_` / `raise.*"REVIEW_`）
- [ ] T041 验证迁移可回滚：在测试库执行 `alembic downgrade -1` → `alembic upgrade head` 一次往返，断言无 schema 漂移、无残留索引
- [ ] T042 全量跑 contract + integration + unit 测试：`/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/ -v -k "022 or content_review or review_gate"`；所有用例通过

---

## 依赖关系与执行顺序

### 阶段依赖关系

```
阶段 1 (设置 / 章程级前置 T001-T003)
       │
       ▼
阶段 2 (基础 / 数据层与错误码 T004-T010)  ← 阻塞所有用户故事
       │
       ▼
   ┌───────────┬───────────┬───────────┐
   ▼           ▼           ▼           ▼
阶段 3        阶段 4        阶段 5      阶段 6
US1 P1🎯MVP   US2 P1🎯MVP   US3 P2     US4 P3
T011-T013     T014-T022     T023-T029  T030-T035
   └───────────┴───────────┴───────────┘
       │
       ▼
阶段 7 (完善 T036-T042)
```

### 用户故事依赖关系

- **US1（业务流程升级）**：可在阶段 2 后立即启动；与 US2/US3/US4 完全独立
- **US2（审核门 KB 拦截）**：可在阶段 2 后启动；**T020a 已将 EP-3 决策接口 + `submit_decision` 最小可用版下放到 US2 段**，使 T021/T022 集成测试可独立端到端跑通、不再依赖 US3 交付
- **US3（审核工作台）**：可在阶段 2 后启动；负责补齐剩余 3 个查询/统计端点与 `submit_decision` 加固版；与 MVP 不再存在依赖倒转
- **US4（可观测与回滚）**：可在阶段 2 后启动；与其他三个故事完全独立（共享指标埋点位置不冲突）

### 每个用户故事内部

- 合约测试（T014）必须先于实现（T015-T020a）编写并失败
- 模型 / Schema（阶段 2 已就位）→ 服务（T015/T016/T023）→ 端点（T018/T020a/T024/T025）→ 集成（T021/T022/T029/T034/T035）
- 故事内部 [P] 任务可并行；非 [P] 任务串行
- **MVP 内依赖**：T020a 依赖 T015（审核门）与 T023 同文件的 `submit_decision` 最小版（本任务同步交付）；T021/T022 依赖 T020a

### 并行机会

- **阶段 1 内**：T001 与 T002 必须串行（T002 是 T001 的章程级承认）；T003 可与 T001/T002 并行
- **阶段 2 内**：T005 / T006 / T008 / T009 互不影响可全部并行；T004 必须先于 T010
- **MVP 段（US1 + US2）**：T011 / T012 与 T015 / T016 完全不冲突，可由两位开发并行推进
- **测试**：所有 contract / integration 测试文件互不重叠，全部 [P]

---

## 并行示例

### MVP 段并行（阶段 2 完成后立即启动两条赛道）

```bash
# 赛道 A — US1 阶段路由
任务 T011: 调整 task_submission_service.py 的 business_phase 推导
任务 T012: 暴露 task_submit.py 中的 business_phase 字段
任务 T013: tests/integration/test_022_phase_routing.py

# 赛道 B — US2 审核门与 MVP 决策接口
任务 T014 [P]: tests/contract/test_022_content_reviews_contract.py（先失败）
任务 T015 [P]: src/services/content_review/review_gate.py
任务 T016 [P]: src/services/content_review/stale_handler.py
任务 T017:    curation_service.py 接入 stale_handler
任务 T018:    tasks.py 入口接入审核门（依赖 T015）
任务 T019:    download_video.py 接入审核门（依赖 T015）
任务 T020:    tasks.py 增加结构化日志
任务 T020a:   src/api/routers/content_reviews.py 创建 + EP-3 + main.py 注册（MVP 必需）
任务 T021:    tests/integration/test_022_review_gate_blocks_kb.py
任务 T022:    tests/integration/test_022_stale_after_recurate.py
```

### 阶段 2 内部并行（一名开发可同时操刀 4 个文件）

```bash
任务 T005 [P]: src/models/coach_video_classification.py 加 4 列
任务 T006 [P]: src/models/content_review_decision.py 新建
任务 T008 [P]: src/api/schemas/content_reviews.py 新建
任务 T009 [P]: src/config.py 新增配置项
# 完成后串行执行 T004（迁移）→ T007（错误码）→ T010（迁移上线验证）
```

---

## 实施策略

### MVP（仅 US1 + US2）

1. ✅ 完成阶段 1（T001 章程同步 + T002 升版 + T003 制品自检）
2. ✅ 完成阶段 2（T004-T010 数据层 / 错误码 / Schema / 配置）
3. ✅ 完成阶段 3（T011-T013 业务阶段推导 + 集成测试）
4. ✅ 完成阶段 4（T014-T022 含 T020a 审核门两点对接 + EP-3 决策接口 + stale 处理 + 集成测试）
5. **停止并验证**：按 [quickstart.md 步骤 1–5](./quickstart.md) 跑端到端
6. 准备好则部署 / 演示——**MVP 已交付，业务可用**

### 增量交付

1. MVP（US1+US2）→ 部署演示
2. 追加 US3（T023-T029）→ 审核工作台上线 → 部署演示
3. 追加 US4（T030-T035）→ 可观测性 + 绕过开关上线 → 部署演示
4. 收尾阶段 7（T036-T042）→ 文档同步 + 全量验证 → 交付

### 并行团队策略（多人）

阶段 1 + 阶段 2 完成后：
- **开发 A**：负责 US1（T011-T013）+ US3（T023-T029）— 偏重 API 与服务
- **开发 B**：负责 US2（T014-T022）— 集中啃审核门 + stale 处理
- **开发 C**：负责 US4（T030-T035）— 可观测 + Beat 任务 + 绕过开关
- 收尾阶段 7 由全员合并完成

---

## 注意事项

- [P] 任务 = 不同文件、无相互依赖；可由不同开发同时推进
- [Story] 标签将任务映射到具体用户故事，便于增量交付与回归测试
- 所有审核相关接口必须走 `X-Admin-Token` 鉴权；EP-3 决策接口必须额外校验 `X-Reviewer-Id` header 与 body 一致
- 所有错误必须抛 `AppException(ErrorCode.X)`，禁止裸 `HTTPException` 或字典（章程 IX）
- 任何状态机变更必须 `review_version += 1`（乐观锁）
- 数据层迁移在测试库验证可回滚后才能合入主干（T041）
- 章程级前置（T001/T002）未合入前禁止合入任何代码任务（章程 X 硬约束）

---

## 任务统计

| 阶段 | 任务范围 | 任务数 | 备注 |
|------|---------|--------|------|
| 阶段 1 设置 / 章程级前置 | T001–T003 | 3 | 阻塞所有后续 |
| 阶段 2 基础 / 数据层与错误码 | T004–T010 | 7 | 阻塞所有用户故事 |
| 阶段 3 US1 业务流程升级 (P1🎯MVP) | T011–T013 | 3 | |
| 阶段 4 US2 审核门 KB 拦截 (P1🎯MVP) | T014–T022（含 T020a） | 10 | 含 1 合约 + 2 集成测试；T020a 为 MVP 必需的 EP-3 决策接口 |
| 阶段 5 US3 审核工作台 (P2) | T023–T029 | 7 | 含 1 单元 + 1 性能测试 |
| 阶段 6 US4 可观测与回滚 (P3) | T030–T035 | 6 | 含 2 集成测试 |
| 阶段 7 完善与横切关注点 | T036–T042 | 7 | 含 1 单元测试 + quickstart 演练 |
| **总计** | T001–T042（含 T020a） | **43** | MVP 边界 = T001-T022（含 T020a，共 23 个任务） |

**MVP 范围**：T001–T022（阶段 1 + 阶段 2 + US1 + US2，含新增的 T020a，共 23 个任务）—— 完成后即可让"清洗 → 待审核 → 审核通过 → KB 抽取"主链路在严格模式下全程跑通，对外部宣告四阶段化业务流程升级。

**独立测试标准**：
- US1：调用 4 类任务的 submit 接口后，响应中 `business_phase` 字段取值正确归属
- US2：5 个状态组合（pending/approved/rejected/stale/bypassed）下提交 KB 抽取的预期结果全部命中
- US3：5 个 endpoint 完整闭环；列表性能 P95 达标
- US4：30s 内完成绕过切换；阶段维度日志独立可聚合

**并行执行机会**：
- 阶段 1 内：T003 可与 T001/T002 并行
- 阶段 2 内：T005 / T006 / T008 / T009 完全并行（4 个不同文件）
- MVP 段内：US1（T011-T013）与 US2（T014-T022 含 T020a）可双线并行
- 阶段 6/7 内：T030 / T031 / T032 / T039 完全并行
