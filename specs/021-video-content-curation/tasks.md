---
description: "Feature-021 教练视频内容清洗与有效片段筛选规范 — 实施任务列表"
---

# 任务: 教练视频内容清洗与有效片段筛选规范（Feature-021）

**输入**: 来自 `/specs/021-video-content-curation/` 的设计文档
**前置条件**: plan.md ✅ / spec.md ✅ / research.md ✅ / data-model.md ✅ / contracts/ ✅ / quickstart.md ✅
**测试**: 包含合约测试 + 集成测试 + 单元测试（spec 在 SC-001 / SC-005 / SC-008 上有强护栏要求，CI 必经）

## 格式: `[ID] [P?] [Story] 描述`

- **[P]**: 可以并行运行（不同文件，无依赖关系）
- **[Story]**: 此任务属于哪个用户故事（US1 ~ US5）
- 在描述中包含确切的文件路径

## 路径约定

单一项目布局（沿用 `charhuang_pp_cn` 既有结构）：`src/` 与 `tests/` 在仓库根目录下；规范文件在 `src/config/curation_rubric/`。

---

## 阶段 1: 设置（共享基础设施）

**目的**: 项目结构与配置文件骨架；不引入新依赖。

- [x] T001 创建子包目录 `src/services/curation/`（含 `__init__.py`）与契约目录 `src/config/curation_rubric/prompts/`
- [x] T002 [P] 创建规范 jsonschema 文件 `src/config/curation_rubric/schema.json`（按 `data-model.md § 3.1`）
- [x] T003 [P] 创建初版规范文件 `src/config/curation_rubric/v1.yaml`（按 `research.md § R2`）
- [x] T004 [P] 创建 LLM 兜底 Prompt 模板 `src/config/curation_rubric/prompts/segment_decision_v1.md`（按 `research.md § R3` LLM Prompt 契约）
- [x] T005 [P] 在 `pyproject.toml` 的 `[project.optional-dependencies].dev`（或主 dependencies）中确认 `pyyaml` + `jsonschema` 已存在；如缺则添加（`research.md § 主要依赖`）
- [x] T006 [P] 在 `.env` 与 `.env.example` 新增 `CURATION_JOB_TIMEOUT_SECONDS=600` / `CURATION_LLM_TIMEOUT_SECONDS=5`，并在 `src/config.py::Settings` 中注册两个字段
- [x] T007 在 `CODEBUDDY.md` 的"核心配置"表格中追加这 2 个新配置项（与 `EXTRACTION_*_TIMEOUT_SECONDS` 同节）

---

## 阶段 2: 基础（阻塞前置条件）

**目的**: 数据库迁移 + 错误码登记 + ORM 模型 + `_phase_step_hook` 派生矩阵；这是所有用户故事的硬前置。

**⚠️ 关键**: 在此阶段完成之前, 无法开始任何用户故事工作。

### 数据库迁移

- [x] T008 创建 Alembic 迁移 `src/db/migrations/versions/0020_video_content_curation.py`：
  - upgrade: `CREATE TABLE video_curation_jobs` + `CREATE TABLE video_curation_segment_results`（含 `effective_decision GENERATED ALWAYS AS (COALESCE(override_decision, auto_decision)) STORED`）+ `ALTER TABLE coach_video_classifications ADD COLUMN last_curation_job_id BIGINT NULL REFERENCES video_curation_jobs(id) ON DELETE SET NULL, ADD COLUMN low_quality BOOLEAN NULL, ADD COLUMN kb_stale_after_override BOOLEAN NOT NULL DEFAULT FALSE` + `ALTER TYPE task_type_enum ADD VALUE IF NOT EXISTS 'video_curation'` + 全部索引（按 `data-model.md § 6`）
  - downgrade: 反向 DROP（ENUM 值保留不删，按 `plan.md` 复杂度跟踪表说明）
  - **注**：实际实施时根据既有事实将 ID 类型从 BIGSERIAL 改为 UUID（与 `coach_video_classifications` / `video_preprocessing_jobs` 对齐），并新增 `task_channel_configs` 默认行；相关分歧已在 `migration_smoke.md` 记录
- [x] T009 在本地 PostgreSQL 上跑 `alembic upgrade head` 验证迁移可正向 + `alembic downgrade -1` 验证可逆，记录到 `specs/021-video-content-curation/research.md` 末尾或新建 `migration_smoke.md`（**部分完成**：当次 sandbox 无 PG，仅做了 alembic 静态链路校验：head=0020、链 0020→0019→0018 正确；实际数据冒烟脚本已写入 `migration_smoke.md` 待 DB 可用时复跑）

### 错误码集中登记

- [x] T010 在 `src/api/errors.py::ErrorCode` 枚举中追加 7 个新值：`CURATION_REQUIRED` / `LOW_QUALITY_SKIP` / `RUBRIC_INVALID` / `RUBRIC_VERSION_NOT_FOUND` / `CURATION_TIMEOUT` / `CURATION_LLM_UNAVAILABLE` / `CURATION_RUBRIC_MISMATCH`（按 `contracts/error-codes.md`）
- [x] T011 在 `src/api/errors.py::ERROR_STATUS_MAP` 与 `ERROR_DEFAULT_MESSAGE` 同步登记 7 个错误码的 HTTP 状态码与默认消息（按 `contracts/error-codes.md` 表）
- [x] T012 [P] 跑 `make spec-compliance` 与 `make drift-changed` 确认没有触发章程级守卫漂移；若 `scripts/audit/workflow_drift.py` 抓到错误码差异，按提示修补（**已通过**：spec-compliance + drift-changed + drift-full 三项全绿）

### ORM 模型

- [x] T013 [P] 创建 `src/models/video_curation_job.py`，定义 `VideoCurationJob` ORM（按 `data-model.md § 2.1` 字段表 + 关系：`coach_video_classification`（一对多）/ `preprocessing_job`（多对一）/ `segment_results`（一对多））
- [x] T014 [P] 创建 `src/models/video_curation_segment_result.py`，定义 `VideoCurationSegmentResult` ORM（含 `effective_decision` 计算列 — SQLAlchemy `Computed("COALESCE(override_decision, auto_decision)", persisted=True)`，按 `data-model.md § 2.2`）
- [x] T015 修改 `src/models/coach_video_classification.py`：追加 3 列 `last_curation_job_id` / `low_quality` / `kb_stale_after_override`（与 T008 迁移列名一致）
- [x] T016 在 `src/models/__init__.py` 中导出 2 个新模型（保证 Alembic autogenerate 能识别）

### 阶段 / 步骤派生矩阵扩展

- [x] T017 修改 `src/models/_phase_step_hook.py`：`_PHASE_STEP_TASK_TYPE_MATRIX["video_curation"] = ("TRAINING", "curate_segments")`、`_PHASE_TASK_TYPES["TRAINING"].add("video_curation")`（**实现注**：`TaskType` enum 也需扩 `video_curation`；矩阵和 PHASE 集合在 `business_workflow_service.py`，本任务一并扩展）
- [x] T018 修改 `src/api/routers/tasks.py::_VALID_BUSINESS_STEPS` 白名单加 `"curate_segments"`（其它路径不动）
- [x] T019 [P] 单元测试 `tests/unit/test_curation_phase_step_hook.py`：断言 `task_type=video_curation` 在 `before_insert` 钩子中正确派生 `(TRAINING, curate_segments)`（**5 项测试全过**）

### 错误码合约测试

- [x] T020 [P] 单元测试 `tests/unit/test_errors_curation_codes.py`：断言 7 个新 `ErrorCode` 在 `ERROR_STATUS_MAP` 与 `ERROR_DEFAULT_MESSAGE` 中均有登记，且 status code 与 `contracts/error-codes.md` 表一致（**17 项测试全过**）

**检查点**: 基础就绪 - 现在可以开始并行实施用户故事

---

## 阶段 3: 用户故事 1 — 自动识别并筛选"有效教学片段"（优先级: P1）🎯 MVP 核心

**目标**: 单条/批量提交清洗任务 → Celery 异步执行规则路 + LLM 兜底两层判定 → 持久化逐分段结果 + 视频级摘要。这是本 feature 的最小可交付增量；US2 / US3 都依赖它。

**独立测试**: 选一条已知含有混合内容（闲聊 + 真讲解 + 比赛回放）的预处理完成视频，提交"内容清洗"任务，等任务完成后查询逐分段的清洗结果接口，校验：闲聊 / 比赛回放对应分段被标记为 rejected 且带 rejection_reason；真讲解分段被标记为 accepted 且带 validity_score；视频级 accepted_duration_ratio 落在合理范围；原始预处理分段对象在 COS 中保留不动。

### US1 测试（先写测试，确保失败）

- [x] T021 [P] [US1] 合约测试 `tests/contract/test_submit_curation.py`：覆盖 `contracts/submit_curation.md` 全部 10 条用例（单条 / 批量 / force / 幂等短路 / 错误路径）—— **10/10 PASSED**
- [x] T022 [P] [US1] 合约测试 `tests/contract/test_get_curation_job.py`：覆盖 `contracts/get_curation_job.md` 全部 6 条用例（含 `include_segments=false` 与 `status=running` 部分摘要）—— **6/6 PASSED**
- [x] T023 [P] [US1] 集成测试 `tests/integration/test_curation_end_to_end.py`：用 mock 的预处理产物（伪造 transcript.json + 分段表行）跑一次端到端清洗，断言视频级摘要字段与 `data-model.md § 4` 数据流一致 —— **6/6 PASSED**（DB 用 AsyncMock；真实 PG 集成由本地 PG 环境复跑）
- [x] T024 [P] [US1] 单元测试 `tests/unit/test_rubric_loader.py`：YAML 加载 + jsonschema 校验 + 缓存（含遍历 `src/config/curation_rubric/v*.yaml` 全部必须通过 schema 校验的 CI 护栏断言）—— **10/10 PASSED**
- [x] T025 [P] [US1] 单元测试 `tests/unit/test_decision_engine_rule_only.py`：规则路得分 ≥ 0.7 直接 accepted、≤ 0.3 直接 rejected、5 维加权 dim_breakdown 字段完整 —— **5/5 PASSED**
- [x] T026 [P] [US1] 单元测试 `tests/unit/test_decision_engine_llm_fallback.py`：模糊区间触发 LLM；LLM 返回非 JSON / 超时 / Venus+OpenAI 都不可用 → 落 `uncertain` + `rejection_reason="llm_unavailable"` —— **6/6 PASSED**
- [x] T027 [P] [US1] 单元测试 `tests/unit/test_coach_dominance_detector.py`：基于讲解时长 + 关键词的启发式判主导率 —— **6/6 PASSED**
- [x] T028 [P] [US1] 单元测试 `tests/unit/test_segment_text_provider.py`：按分段 `start_ms / end_ms` 切片对齐到 transcript 文本 —— **7/7 PASSED**
- [x] T029 [P] [US1] 单元测试 `tests/unit/test_curation_service_aggregation.py`：视频级摘要派生（`accepted_duration_ratio` / `low_quality` / `audio_unavailable` / `short_video` 边界值）—— **7/7 PASSED**

### US1 实施 — Service 层（无相互依赖的 [P]，按依赖图排顺序）

- [x] T030 [P] [US1] 创建 `src/services/curation/error_codes.py`：私域错误常量映射到 `src/api/errors.py::ErrorCode`（按 `research.md § R8`）
- [x] T031 [P] [US1] 创建 `src/services/curation/rubric_loader.py`：函数签名 `load(version: str | None = None) -> CurationRubric`；首次调用做 jsonschema 校验，结果加 `lru_cache`；失败抛 `AppException(ErrorCode.RUBRIC_INVALID, details={...})`；版本不存在抛 `RUBRIC_VERSION_NOT_FOUND`
- [x] T032 [P] [US1] 创建 `src/services/curation/segment_text_provider.py`：从既有 F-016 / F-014 已落地的 transcript 数据源（参考 `src/services/kb_extraction_pipeline/step_executors/audio_transcription.py` 的产物路径）按 `start_ms / end_ms` 提取分段对应文本
- [x] T033 [P] [US1] 创建 `src/services/curation/coach_dominance_detector.py`：纯计算函数 `estimate_dominance_ratio(segment_text, target_coach_name) -> float`
- [x] T034 [US1] 创建 `src/services/curation/decision_engine.py`：核心决策器 `decide(segment, rubric, tech_category, coach_name, llm_client) -> DecisionResult`，编排规则路 5 维加权 + 模糊区间 LLM 兜底（依赖 T031 / T032 / T033）
- [x] T035 [US1] 创建 `src/services/curation/curation_service.py`：编排器 `run(job_id)` 函数 — 加载规范 → 遍历分段调 `decision_engine` → 落子表 → 派生视频级摘要 → UPDATE jobs 表 → 同步 `coach_video_classifications.last_curation_job_id / low_quality`（依赖 T034）
- [x] T036 [US1] 在 `src/services/curation/curation_service.py` 中实现 `submit(coach_video_classification_id, rubric_version, force) -> JobSubmissionResult`：前置校验 + 幂等短路 + INSERT 行 + 派 Celery task；处理 `CURATION_RUBRIC_MISMATCH` 场景

### US1 实施 — Worker

- [x] T037 [US1] 创建 `src/workers/curation_task.py`：定义 Celery task `curate_video(job_id)`，路由到 `default` 队列（按 `plan.md` 与 `research.md § R4`）；超时控制读 `CURATION_JOB_TIMEOUT_SECONDS`；catch-all 异常转写到 `video_curation_jobs.status=failed` + `error_code=CURATION_TIMEOUT` 等
- [x] T038 [US1] 在 `src/workers/celery_app.py` 中注册 `curation_task` 并配置静态路由 `task_routes['src.workers.curation_task.curate_video'] = {'queue': 'default'}`
- [x] T039 [US1] 在 `src/workers/orphan_recovery.py` 的孤儿 sweep 中扩展覆盖 `video_curation_jobs.status='running'` 且 `started_at < NOW() - CURATION_JOB_TIMEOUT_SECONDS` 的行（参考 `extraction_jobs` 现有逻辑）

### US1 实施 — API 路由 + Schema

- [x] T040 [P] [US1] 创建 `src/api/schemas/curation.py`：Pydantic v2 模型 `SubmitCurationRequest` / `SubmitCurationBatchRequest` / `CurationJobItem` / `CurationSegmentResult` / `CurationJobDetail`（按 `contracts/submit_curation.md` + `contracts/get_curation_job.md`）
- [x] T041 [US1] 在 `src/api/routers/tasks.py` 中新增 `POST /api/v1/tasks/curation`（单 + 批量）端点，路由层只做 schema 校验 + 调 `curation_service.submit(...)` + 用 `SuccessEnvelope` 包装（依赖 T036 / T040）—— **实现注**：为遵循 `api.md` "每个路由文件对应一个资源"原则，新建独立路由文件 `src/api/routers/curation_jobs.py` 集中承载本资源域的 3 个端点（`POST /tasks/curation`、`/tasks/curation/batch`、`GET /curation-jobs/{job_id}`），与 F-020 `athlete_tasks.py` 同惯例；`tasks.py` 不动
- [x] T042 [US1] 创建 `src/api/routers/curation_jobs.py`：`GET /api/v1/curation-jobs/{id}` 端点（按 `contracts/get_curation_job.md`）
- [x] T043 [US1] 在 `src/api/main.py` 中注册 `curation_jobs` 路由

**检查点**: 此时, 用户故事 1 应该完全功能化且可独立测试 — `POST /tasks/curation` → Celery 跑完 → `GET /curation-jobs/{id}` 返回完整摘要

---

## 阶段 4: 用户故事 2 — 建立可审计的"内容清洗规范"（优先级: P1）

**目标**: 规范文件在 git 中版本化、加载期 schema 校验、任意一次清洗结果可按版本号回查 git 历史还原判据快照。

**独立测试**: 把规范文件以版本号 `v1` 发布，跑一次清洗；把规范微调发布为 `v2`，对同一视频再跑一次；校验两次清洗结果各自记录所用 `curation_rubric_version`，旧任务结果不被覆盖，通过版本对比能列出受影响的分段差异。

### US2 测试

- [x] T044 [P] [US2] 集成测试 `tests/integration/test_curation_rubric_versioning.py`：跑 v1 → 调整 rubric 模拟 v2 → 跑 v2 → 断言 `video_curation_jobs.curation_rubric_version` 在两个作业上分别 `"v1"` / `"v2"`，老作业字段不被覆盖 —— **8/8 PASSED**（含 v3 损坏不影响 v1/v2 用例）
- [x] T045 [P] [US2] 合约测试在 `tests/contract/test_submit_curation.py` 中已覆盖（T021）的 `RUBRIC_INVALID` / `RUBRIC_VERSION_NOT_FOUND` / `CURATION_RUBRIC_MISMATCH` 三条用例 — 此任务核对实现已让测试通过 —— **已验证**：c6/c7/c8 三条用例 PASSED

### US2 实施

- [x] T046 [P] [US2] 在 `src/services/curation/rubric_loader.py` 增加 `list_available_versions() -> list[str]` 与 `latest_version() -> str` 函数（按文件名规则 `v[0-9]+` 排序）—— **已在 US1 阶段 3 (T031) 实现**；T044 测试覆盖
- [x] T047 [US2] 在 `src/services/curation/curation_service.py::submit` 中实现：未传 `rubric_version` 时取 `latest_version()`；同视频既有 success 作业 rubric_version 与本次不同 + `force=false` ⇒ 抛 `CURATION_RUBRIC_MISMATCH` —— **已在 US1 阶段 3 (T036) 实现**；T044 + T021 c8 测试覆盖
- [x] T048 [P] [US2] 在 `src/api/main.py` 启动钩子中调用一次 `rubric_loader.load(latest_version())`，确保 API 启动期就发现规范文件错误（fail-fast）；失败时打印 critical 日志并仍允许 API 启动以便走 `/admin/channels` 应急 —— **已实现**：fail-soft 设计（启动期 critical log，但不阻断 app 启动；运行时 service 层会再做一次 fail-fast 校验作为第二道闸门）
- [x] T049 [P] [US2] 撰写规范文件作者指引 `src/config/curation_rubric/README.md`：解释字段语义、版本号规则、PR 必经步骤（包括跑 `tests/unit/test_rubric_loader.py` 的本地命令）—— **已撰写**：含 schema 字段速查 + 改规则工作流 + 不要做的事 + 应急回滚 + 进一步阅读链接

**检查点**: 此时, 用户故事 1 + 用户故事 2 都应独立运行；清洗结果与规范版本一一对应可审计。

---

## 阶段 5: 用户故事 3 — 下游 KB 抽取自动消费"已清洗的有效片段"（优先级: P1）

**目标**: KB 抽取强制门 + DAG 内分段过滤 + `LOW_QUALITY_SKIP` 业务短路 + `bypass_curation_gate` 应急开关。这是本 feature 的"价值兑现点"。

**独立测试**: 拿 US1 跑过的视频 V，提交一个 KB 抽取作业；校验：抽取过程中读取的分段集合 = 清洗结果中的 accepted 集合；当 accepted 集合为空时，KB 抽取作业落 `LOW_QUALITY_SKIP`；尚未跑清洗的视频提交 KB 抽取一律 `CURATION_REQUIRED` 拒绝。

### US3 测试

- [x] T050 [P] [US3] 合约测试 `tests/contract/test_kb_extraction_curation_gate.py`：覆盖 `contracts/kb_extraction_curation_gate.md` 全部 7 条用例（CURATION_REQUIRED / LOW_QUALITY_SKIP / warning / 正常 / 对账 / bypass / bypass TTL 过期）—— **7/7 PASSED**（含批量端点 1 项）
- [x] T051 [P] [US3] 集成测试 `tests/integration/test_kb_extract_consumes_accepted_only.py`：建立"5 段视频，3 段 accepted、2 段 rejected"的清洗结果 → 触发 KB 抽取 → 关键护栏断言"被 rejected 的分段 cos_object_key 从未在 LLM Prompt 拼装中出现"（spec SC-008 关键测试）—— **3/3 PASSED**（含 bypass 路径 + warning 路径）
- [x] T052 [P] [US3] 集成测试 `tests/integration/test_low_quality_skip_path.py`：建立 accepted_duration_ratio=0 的清洗结果 → 触发 KB 抽取 → 断言 `extraction_jobs.error_code='LOW_QUALITY_SKIP'`、未调 LLM —— **2/2 PASSED**（注：与原契约描述差异——orchestrator 模型决定了 status='failed' + error_code='LOW_QUALITY_SKIP'，与真实失败按错误码前缀区分；contracts/error-codes.md 已同步更新）

### US3 实施 — submission 层前置门

- [x] T053 [US3] 在 `src/api/routers/tasks.py::POST /tasks/kb-extraction` 增加门 1：调用 `evaluate_curation_gate(...)`；返回 `decision='required'` ⇒ 抛 `AppException(ErrorCode.CURATION_REQUIRED, details={...})`
- [x] T054 [US3] 同一路由文件增加门 2 透传：`decision='low_quality_skip' / 'low_quality_warn'` 时**不在 router 拦截**——交给 DAG 层 `download_video` 处理（避免 router 与 worker 双写 `extraction_jobs`）；router 仅用 GateResult 决定是否拒绝（required 拒；其它放行）
- [x] T055 [US3] 引入 `settings.kb_extraction_bypass_curation_gate` 应急开关（`.env` 注入；默认 false）；`evaluate_curation_gate` 在开关 true 时立即返回 `decision='bypassed'`；DAG 层据此跳过过滤、读全量分段并写 `output_summary.curation_bypass=true` 留痕（**实现差异**：未通过 `task_channel_configs.config_payload` JSONB 实现，因该列不存在；改用 Settings 字段更轻、与既有"敏感配置"模式一致）

### US3 实施 — DAG 内分段过滤

- [x] T056+T057 [US3] **统一在 `src/services/kb_extraction_pipeline/step_executors/download_video.py` 过滤**——而非分别改 `audio_kb_extract.py` / `visual_kb_extract.py`。原因：`download_video` 是所有下游 step 的**唯一分段入口**（产出 `job_dir/segments/seg_NNNN.mp4`），后续 step 读取的是 job_dir 中的文件；改这一处即对所有下游 step 生效，避免重复修改与不一致风险。新增 `_apply_curation_gate(session, view, cos_object_key)` 辅助函数封装"查 GateResult → 拉 effective_decision='accepted' segment_index 集合 → 过滤 view.segments"
- [x] T058 [US3] `download_video.execute()` 返回 `output_summary` 新增 5 个键：`segments_processed` / `segments_skipped_by_curation` / `curation_job_id` / `curation_rubric_version` / `curation_warning` / `curation_bypass`（与 `data-model.md § 2.5` 软扩展约定一致）
- [x] T059 [US3] `_apply_curation_gate` 内：当 `gate.decision == 'low_quality_warn'`（即 `0 < ratio < 0.3`）时设 `curation_warning='low_quality'`；其它情况设 None
- [x] T060 [US3] `_apply_curation_gate` 内：`gate.decision == 'bypassed'` ⇒ 直接返回 `view.segments` 整列 + `curation_bypass=true`；`bypass_curation_gate=true` 时 GateResult 由 `evaluate_curation_gate` 在 `kb_gate.py` 顶部短路构造，不查 DB

**检查点**: 此时, 用户故事 1 + 2 + 3 都应独立运行；F-021 的核心闭环（清洗 → KB 抽取强制门 → DAG 过滤）完全打通，可作为 MVP 上线。

---

## 阶段 6: 用户故事 4 — 人工复核与覆盖个别分段的清洗判定（优先级: P2）

**目标**: 单分段人工覆盖 → 视频级摘要事务内重算 → `kb_stale_after_override` 提示位维护。

**独立测试**: 选一条已清洗视频里被自动判 rejected 的分段 S，调用人工覆盖接口设为 accepted；再次发起 KB 抽取，校验抽取读取的分段集合包含 S；视频级摘要的 accepted_duration_ratio 自动重算；覆盖记录留痕。

### US4 测试

- [x] T061 [P] [US4] 合约测试 `tests/contract/test_override_curation_segment.py`：覆盖 `contracts/override_curation_segment.md` 全部 9 条用例 —— **10/10 PASSED**（含 1 项 extra-field 拒绝）
- [x] T062 [P] [US4] 集成测试 `tests/integration/test_override_recompute_summary.py`：rejected → accepted 覆盖后，视频级摘要 accepted_duration_ratio + low_quality 自动重算；`coach_video_classifications.kb_stale_after_override=true` 当且仅当该视频已有早于覆盖时间的 KB 抽取作业 —— **7/7 PASSED**

### US4 实施

- [x] T063 [US4] 在 `src/services/curation/curation_service.py` 新增 `override_segment(...) -> OverrideOutcome`：单事务内 UPDATE 子表 + 重新加载所有分段（按 effective_decision 重算）+ UPDATE `video_curation_jobs` + 维护 `coach_video_classifications.low_quality / kb_stale_after_override`（按 `data-model.md § 4` 覆盖路径）+ 输入校验（拒绝空 user / decision 非法 / decision 非空时缺 reason）
- [x] T064 [US4] 在 `src/api/routers/curation_jobs.py` 新增 `PATCH /curation-jobs/{job_id}/segments/{segment_index}` 端点（按 `contracts/override_curation_segment.md`）；新增 `CurationOverrideRequest` / `CurationOverrideResponse` Pydantic schemas
- [x] T065 [US4] `override_segment` 内"取消覆盖"分支：`override_decision=null` ⇒ 清空所有 `override_*` 字段 + `overridden_at=NULL` + 重新评估 `kb_stale_after_override`（重评估时 latest_overridden_at 自然变小或为 None）
- [x] T066 [US4] 在 `POST /api/v1/extraction-jobs/{id}/rerun` 入队**前**新增 `clear_kb_stale_after_override(cos_object_key)` 副作用（**实现注**：rerun 是 fire-and-forget，无法等 KB 完成；语义上"运营 ack 覆盖后口径并主动重抽"等价于 ack 标记，故在 dispatch 前清零；新 KB 跑出来的口径已是覆盖后；监控以"用户已 ack"为信号）
- [x] T067 [US4] 在 `GET /api/v1/curation-jobs/{id}` 响应的 `summary` 中暴露 `has_overrides` 与 `kb_stale_after_override` —— **已在 US1 阶段 3 实现并通过 T022 合约测试 c3 验证**；本任务为合并验证，覆盖路径回归测试由 T061/T062 完成

**检查点**: 用户故事 1 + 2 + 3 + 4 都应独立运行；运营可对边界误判做人审兜底。

---

## 阶段 7: 用户故事 5 — 清洗效果观测与跨教练有效率对比（优先级: P3）

**目标**: 按 `coach_name` / `tech_category` / `curation_rubric_version` 聚合查询接口；支持版本对比。

**独立测试**: 跑完一批清洗任务后，调用聚合查询接口，能按 coach_name 与 tech_category 两个维度返回平均有效率与样本量，且支持按 curation_rubric_version 对比同一批视频两版规范的差异。

### US5 测试

- [ ] T068 [P] [US5] 合约测试 `tests/contract/test_curation_stats.py`：覆盖 `contracts/curation_stats.md` 全部 6 条用例

### US5 实施

- [ ] T069 [P] [US5] 在 `src/api/schemas/curation.py` 中新增 `CurationStatsItem` / `CurationStatsResponse` Pydantic 模型
- [ ] T070 [US5] 在 `src/services/curation/curation_service.py` 新增 `aggregate_stats(group_by, filters, page, page_size) -> tuple[list[CurationStatsItem], int]`：基于 `video_curation_jobs` + `coach_video_classifications` + `coaches` 三表 JOIN 聚合
- [ ] T071 [US5] 创建 `src/api/routers/curation_stats.py`：`GET /api/v1/curation-stats` 端点（按 `contracts/curation_stats.md`）
- [ ] T072 [US5] 在 `src/api/main.py` 注册 `curation_stats` 路由
- [ ] T073 [US5] 在 `aggregate_stats` 实现中加上 `low_sample=true` 标记（`video_count < 5` 的分组项），与契约文档一致

**检查点**: 全部 5 个用户故事独立功能化。

---

## 阶段 8: 完善与横切关注点

**目的**: 基准测试 / 文档刷新 / 既有视频回填 / 安全加固。

### 基准测试与回归

- [x] T074 创建脚本 `scripts/run_curation_benchmark.py`：按 `research.md § R9` 设计 — 跑样本集对照人工标注计算 precision/recall + LLM token 对比，输出 JSON 到 `specs/021-video-content-curation/benchmark/`（**实现注**：SC-003 术语重叠率提升需要在 staging 环境跑两次完整 KB 抽取后比对，本脚本只产 SC-001/002 + 算法层指标 — 与 spec 对齐）
- [x] T075 创建样本数据目录 `tests/data/curation_samples_v1/`：放 schema 文档 + 3 条合成样本作为 smoke 数据（**实现注**：≥ 30 条真实人工标注样本是后续运营任务，仓库只携带 schema + synthetic 样本以让脚本能本地端到端跑通）
- [x] T076 [P] 跑 T074 建立合成基线，并在 `specs/021-video-content-curation/benchmark/README.md` 中说明真实 baseline 待 staging 环境跑（**已完成**：合成基线写入 `v1_synthetic_baseline.json`，3 样本 9 段 100% 命中；真实 baseline 待运营标注 ≥ 30 真实样本后跑）

### 既有视频回填

- [x] T077 创建脚本 `scripts/backfill_curation_for_existing_videos.py`（实际位置非 `scripts/backfill/` 子目录 — 仓库 scripts/ 是扁平结构，无 backfill 子目录；与 `build_knowledge_base.py` 同惯例）：对所有 `coach_video_classifications.preprocessed=true + tech_category != unclassified + 无 success 清洗` 的旧视频补跑清洗。支持 `--dry-run` / `--apply` / `--limit` / `--tech-category` / `--coach-name` 过滤；submit_curation 既有幂等短路兜底（重复跑安全）
- [x] T078 [P] 在 `quickstart.md` 中加入"§ 1.5 老库存视频回填（可选）"小节，含 dry-run + 通道容量查询 + 分批跑命令

### 文档刷新与守卫

- [x] T079 [P] 跑 `make drift-full` + `make spec-compliance`，确保 `docs/business-workflow.md` § 3.1 / § 3.4 / § 7.4 / § 8 / § 10 全部已扩展（已在 plan 阶段完成，本任务为 CI 二次确认）—— **绿** ✓
- [x] T080 [P] 刷新 `docs/architecture.md` + `docs/features.md`：features.md 加完整 Feature-021 章节（决策算法 / 强制门 / 应急回滚 / 规范版本化 / 人工覆盖 / 错误码 / 数据模型 / 测试覆盖）；architecture.md 加"内容清洗强制门"小节 + 数据表树更新；时间戳更新为 2026-05-19
- [x] T081 [P] 在 `CODEBUDDY.md` 的"活跃 Features"表追加 Feature-021 行（核心 API 4 个）；标题从 `001~016` 改为 `001~021`
- [x] T082 [P] 在 `CODEBUDDY.md` 的"关键设计决策"区追加 Feature-021 清洗强制门条目（含 CURATION_REQUIRED / LOW_QUALITY_SKIP / KB_EXTRACTION_BYPASS_CURATION_GATE）

### 性能与可观测

- [x] T083 [P] 在 Celery task `curate_video` 中加结构化日志 `_emit_curate_complete_log()`，落 `step_name=curate_segments` + `phase=TRAINING` + `tech_category` 三维 tag + 关键计数（accepted/rejected/uncertain/duration_ratio/low_quality）。**实现注**：项目当前没有专用 metric infra（无 Prometheus / statsd），与既有 `kb_extraction_task` 一致用结构化日志 `extra=` 字段，由 Loki / ELK 直接聚合；spec 中 "metric hook" 的语义在本项目中等价于 "structured log fields"
- [x] T084 [P] 在 `decision_engine._llm_fallback_decide` LLM 调用处加 `purpose=curation` 标识结构化日志（成功 + 失败两路），让"清洗 LLM 兜底次数 / 失败率 / token 消耗"可按 purpose 单独聚合（business-workflow.md § 7.5 成本观测）

### 验收（DB-required，本沙箱无法跑；归档为 staging-only 任务）

- [ ] T085 跑 `quickstart.md § 2` 端到端冒烟 —— **deferred to staging**（需 PG + Redis + Celery worker + COS 凭证）
- [ ] T086 跑 `quickstart.md § 3` 人工覆盖路径，确认事务原子 + `kb_stale_after_override` 提示位维护正确 —— **deferred to staging**
- [ ] T087 跑 `quickstart.md § 4` 应急回滚路径，确认 `KB_EXTRACTION_BYPASS_CURATION_GATE` env 切换生效（**实现差异**：原契约描述的 "30s TTL 热配置" 不再适用——见阶段 5 T055 实现注：bypass 改用 Settings env 字段而非 task_channel_configs.config_payload；切换需重启 worker，但与 既有 `Venus_TOKEN` 等敏感配置同惯例）—— **deferred to staging**
- [ ] T088 跑 `quickstart.md § 5` 基准回归 —— **deferred to staging**（需 ≥ 30 条真实人工标注样本数据集，本仓库只带 3 条合成样本作 smoke 用）

---

## 依赖关系与执行顺序

### 阶段依赖关系

- **阶段 1 设置**: 无依赖 - 可立即开始
- **阶段 2 基础**: 依赖阶段 1 - **阻塞所有用户故事**
- **阶段 3 US1**（P1，MVP 核心）: 依赖阶段 2
- **阶段 4 US2**（P1）: 依赖阶段 3 完成（共用 `curation_service.submit`）
- **阶段 5 US3**（P1）: 依赖阶段 3 完成（消费 US1 落地的清洗结果）
- **阶段 6 US4**（P2）: 依赖阶段 5 完成（覆盖路径需要 `kb_stale_after_override` 与 KB 抽取门契合）
- **阶段 7 US5**（P3）: 依赖阶段 6 完成（聚合查询需要覆盖统计字段）
- **阶段 8 完善**: 依赖所有期望的用户故事完成

### 用户故事级别依赖关系

- **US1（P1）**: 可在阶段 2 完成后立即开始 — 无其他故事依赖
- **US2（P1）**: 与 US1 强耦合（共用 service），建议串行（先 US1 → US2）；理论上 US2 的"版本化"骨架可与 US1 并行
- **US3（P1）**: 必须在 US1 完成后才能消费清洗结果；与 US2 可并行
- **US4（P2）**: 必须在 US3 完成后（`kb_stale_after_override` 维护逻辑要看 KB 抽取作业完成时间）
- **US5（P3）**: 与 US4 可独立测试但底层数据结构已就绪，建议在 US4 之后实施

### 每个用户故事内部

- 测试（合约 + 集成）先写，确保失败
- ORM 模型 → service 层 → router 层 → worker 层（如适用）
- 标记为 [P] 的测试可同时编写
- 标记为 [P] 的不同文件 service 模块可并行实现

### 并行机会

- 阶段 1：T002 / T003 / T004 / T005 / T006 全部 [P] 可并行
- 阶段 2：T013 / T014 / T019 / T020 [P] 可并行（它们改不同文件）
- 阶段 3：所有合约/集成/单元测试任务（T021–T029）[P] 可并行；service 层 T030–T033 可并行
- 阶段 5：T050 / T051 / T052 三个测试任务可并行；T056 / T057 改不同文件可并行
- 阶段 8：T079 / T080 / T081 / T082 / T083 / T084 全部 [P] 可并行

---

## 并行示例: 用户故事 1

```bash
# 一起启动 US1 的所有测试（先写测试）：
任务: "在 tests/contract/test_submit_curation.py 中编写合约测试（含 10 条用例）"
任务: "在 tests/contract/test_get_curation_job.py 中编写合约测试（含 6 条用例）"
任务: "在 tests/integration/test_curation_end_to_end.py 中编写端到端集成测试"
任务: "在 tests/unit/test_rubric_loader.py 中编写规范加载器单测"
任务: "在 tests/unit/test_decision_engine_rule_only.py 中编写规则路单测"
任务: "在 tests/unit/test_decision_engine_llm_fallback.py 中编写 LLM 兜底单测"
任务: "在 tests/unit/test_coach_dominance_detector.py 中编写主导率单测"
任务: "在 tests/unit/test_segment_text_provider.py 中编写分段文本切片单测"
任务: "在 tests/unit/test_curation_service_aggregation.py 中编写视频级摘要派生单测"

# 一起启动 US1 的"无相互依赖"service 模块：
任务: "创建 src/services/curation/error_codes.py"
任务: "创建 src/services/curation/rubric_loader.py"
任务: "创建 src/services/curation/segment_text_provider.py"
任务: "创建 src/services/curation/coach_dominance_detector.py"
```

---

## 实施策略

### 仅 MVP（US1 + US2 + US3）

US1 / US2 / US3 都是 P1，MVP 不可拆 — 缺任何一个 KB 抽取仍读全量分段，本 feature 价值未兑现。完整 MVP 路径：

1. 完成阶段 1: 设置（T001–T007）
2. 完成阶段 2: 基础（T008–T020）
3. 完成阶段 3: US1 自动判定（T021–T043）
4. 完成阶段 4: US2 规范版本化（T044–T049）
5. 完成阶段 5: US3 KB 强制门（T050–T060）
6. **停止并验证**: 跑 `quickstart.md § 2.4 / § 2.5` 验证强制门 + 跑 T088 基准回归
7. 部署 / 演示

### 增量交付

1. 设置 + 基础 → 基础就绪
2. US1 + US2 + US3 三个 P1 → MVP 上线（清洗 + 强制门闭环）
3. US4（P2）→ 人审兜底上线（运营可处置边界误判）
4. US5（P3）→ 聚合观测接口上线（持续优化数据支撑）
5. 阶段 8 完善 → 文档 / 守卫 / 基准 / 回填

### 并行团队策略

有 2~3 个开发：

1. 团队一起完成阶段 1 + 阶段 2
2. 阶段 3 完成后（US1 service 骨架 + 测试落地）：
   - 开发 A：US2 规范版本化（阶段 4，独立模块）
   - 开发 B：US3 KB 强制门（阶段 5，改 KB 抽取流水线）
3. US3 完成后：
   - 开发 A：US4（覆盖路径）
   - 开发 B：US5（聚合查询）
4. 一起完成阶段 8 收尾

---

## 注意事项

- [P] 任务 = 不同文件, 无依赖关系
- [Story] 标签将任务映射到特定用户故事以实现可追溯性（US1 ~ US5）
- 每个用户故事应该独立可完成和可测试
- 在实施前验证测试失败（先红 → 后绿 → 后重构）
- 在每个任务或逻辑组后提交（推荐：阶段 2 一次提交、每个用户故事一次合并 PR）
- 在每个检查点停止以独立验证故事
- **关键护栏**：T051（`tests/integration/test_kb_extract_consumes_accepted_only.py`）是 spec SC-008 的强护栏，CI 必经；T088（基准回归）对照 SC-001 / SC-002 / SC-003 达标
- 避免: 模糊任务, 相同文件冲突, 破坏独立性的跨故事依赖
- **章程级前置已完成**：`docs/business-workflow.md` 在 plan 阶段已扩展（§ 2 / § 3.1 / § 3.4 / § 7.4 / § 8 / § 10），本 tasks.md 不重复扩展
