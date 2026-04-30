---
description: "功能实现任务列表"
---

# 任务: 运动员推理流水线 · COS 扫描 → 预处理 → 姿态提取 → 标准对比 → 改进建议

**Feature**: `020-athlete-inference-pipeline`
**输入**: `/specs/020-athlete-inference-pipeline/` 下的 plan.md / spec.md / research.md / data-model.md / contracts/ / quickstart.md
**测试**: 包含（章程原则 II 强制 TDD；合约测试前置于路由实现）

**组织结构**: 按用户故事分组，每个故事可独立实施、独立测试、独立演示。

## 格式: `[ID] [P?] [Story] 描述`
- **[P]**: 可并行运行（不同文件，无依赖）
- **[Story]**: 任务所属用户故事（US1 / US2 / US3 / US4 / US5）
- 所有路径均为绝对可定位；以仓库根目录为准

## 路径约定

**单一项目**：仓库根目录下的 `src/` + `tests/` + `specs/` + `config/` + `docs/`；不新建平行目录（见 plan.md「项目结构」）。

---

## 阶段 1: 设置（共享基础设施）

**目的**: 环境与配置就绪；不落业务代码。

- [X] T001 在 `config/athlete_directory_map.json` 创建运动员目录 → 姓名静态映射初始化文件；JSON 内容为 `{"_README": "运动员 COS 目录名 → athlete_name 静态映射；格式与 coach_directory_map.json 对称；真实映射条目由运营后续追加，键为 COS 目录名，值为显示用的 athlete_name"}`（JSON 不支持注释，用伪字段 `_README` 承载说明）；扫描器加载时跳过 `_README` 开头键
- [X] T002 [P] 在 `.env.example`（若存在）与 `docs/environment-setup.md` 中新增 `COS_VIDEO_ALL_ATHLETE` 环境变量条目（默认示例值 `charhuang/tt_video/athletes/`），说明与 `COS_VIDEO_ALL_COCAH` 物理隔离
- [X] T003 [P] 在 `src/config/settings.py` 的 `Settings` 类新增 `cos_video_all_athlete: str` 字段（与 `cos_video_all_cocah` 同型，无默认值，强制 `.env` 配置；`extra='forbid'` 下 fail-fast）

**检查点**: 配置与环境变量就绪；`from src.config import get_settings; get_settings().cos_video_all_athlete` 能正确解析

---

## 阶段 2: 基础（阻塞前置条件）

**目的**: 数据库 / 错误码 / 枚举 / 钩子等"所有用户故事共享的地基"必须先落地。

**⚠️ 关键**: 在此阶段完成之前，任何用户故事任务都不得开始。

### 2.1 错误码注册（章程原则 IX 集中化）

- [X] T004 在 `src/api/errors.py::ErrorCode` 枚举新增 6 个成员：`ATHLETE_ROOT_UNREADABLE` / `ATHLETE_DIRECTORY_MAP_MISSING` / `ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND` / `ATHLETE_VIDEO_NOT_PREPROCESSED` / `STANDARD_NOT_AVAILABLE` / `ATHLETE_VIDEO_POSE_UNUSABLE`，并同步登记 `ERROR_STATUS_MAP`（502/500/404/409/409/422）与 `ERROR_DEFAULT_MESSAGE`（默认消息见 `specs/020-athlete-inference-pipeline/contracts/error-codes.md`）
- [X] T005 [P] 在 `tests/unit/test_errors_athlete_codes.py` 新建单测：遍历 6 个新 ErrorCode 断言 `ERROR_STATUS_MAP[code]` + `ERROR_DEFAULT_MESSAGE[code]` 与 `contracts/error-codes.md` 完全一致（"三张表同步"守卫）

### 2.2 ORM 模型

- [X] T006 [P] 在 `src/models/athlete.py` 新建 `Athlete` ORM 模型（字段 / 约束 / 索引严格对齐 `data-model.md § 2`；继承 `Base`）
- [X] T007 [P] 在 `src/models/athlete_video_classification.py` 新建 `AthleteVideoClassification` ORM 模型（字段 / 约束 / 4 索引严格对齐 `data-model.md § 3`；`athlete_id` FK→`athletes.id`）
- [X] T008 在 `src/models/diagnosis_report.py` 给 `DiagnosisReport` 追加 3 列 `cos_object_key` / `preprocessing_job_id` / `source`，新增 2 索引（对齐 `data-model.md § 4`），**保持既有列不变**

### 2.3 TaskType 枚举 + Phase/Step 钩子 + 业务流程校验矩阵

- [X] T009 在 `src/models/analysis_task.py::TaskType` 新增两个枚举值 `athlete_video_classification` / `athlete_video_preprocessing`（对齐 `data-model.md § 5`）
- [X] T010 在 `src/models/_phase_step_hook.py::_derive_for_analysis_task` 新增两条派生规则：`athlete_video_classification → (INFERENCE, scan_athlete_videos)` / `athlete_video_preprocessing → (INFERENCE, preprocess_athlete_video)`
- [X] T011 [P] 在 `tests/unit/test_athlete_phase_step_hook.py` 新建单测：插入 2 种 `TaskType` 的 `AnalysisTask` 行后 ORM before_insert 钩子自动填对 `(business_phase, business_step)`；异常值抛 `ValueError("PHASE_STEP_UNMAPPED...")`
- [X] T012 在 `src/services/business_workflow_service.py` 扩展 `_PHASE_STEPS["INFERENCE"]` 为 `("scan_athlete_videos", "preprocess_athlete_video", "diagnose_athlete")`；扩展 `_PHASE_STEP_TASK_TYPE_MATRIX` 新增 2 行；扩展 `_PHASE_TASK_TYPES["INFERENCE"]` 为 3 个 task_type 集合
- [X] T013 在 `src/api/routers/tasks.py::_VALID_BUSINESS_STEPS` 白名单中追加 `"scan_athlete_videos"` + `"preprocess_athlete_video"`（对齐 T012 同步）

### 2.4 Alembic 迁移

- [X] T014 在 `src/db/migrations/versions/0018_athlete_inference_pipeline.py` 新建迁移（down_revision=`0017_kb_per_category_redesign`），执行 up 9 个步骤 / down 4 个步骤（对齐 `data-model.md § 7`）；ENUM 新值仅 up 不 down
- [X] T015 `alembic upgrade head` 已应用 0018；`athletes` / `athlete_video_classifications` / `diagnosis_reports` 三表结构 + `task_type_enum` 两个新值验证通过；T005/T011 测试 12/12 GREEN

### 2.5 Pydantic Schemas

- [X] T016 [P] 在 `src/api/schemas/athlete_classification.py` 新建全部 Schemas（`AthleteScanRequest` / `AthleteScanStatusResponse` / `AthleteClassificationItem` / `AthletePreprocessingSubmitRequest` + 批量 / `AthleteDiagnosisSubmitRequest` + 批量 / 对应响应模型）；全部 `model_config = ConfigDict(extra="forbid", from_attributes=True)`，对齐 `data-model.md § 6`

**检查点**: 迁移成功 + 单测通过（T005 / T011）；枚举、钩子、校验矩阵、白名单、schema 五处同步改动闭环

---

## 阶段 3: 用户故事 1 — 运动员视频素材归集与自动分类（P1）🎯 MVP

**目标**: 运营触发一次扫描后，`athlete_video_classifications` 表按运动员目录 + 21 类 tech_category 完成 upsert；进度可查。

**独立测试**: 调用 `POST /api/v1/athlete-classifications/scan` 得 `task_id` → 轮询 `GET /athlete-classifications/scan/{task_id}` 至 `success` → 调用 `GET /athlete-classifications` 分页看到所有素材，`tech_category` 非 null 占比 ≥ 95%（SC-002），且 `coach_video_classifications` 无任何新增（SC-006）。

### US1 测试（合约 + 集成，TDD 前置）

- [X] T017 [P] [US1] 在 `tests/contract/test_athlete_scan.py` 创建合约测试：覆盖 `specs/020-athlete-inference-pipeline/contracts/athlete_scan.md` 的 6 个断言点
- [X] T018 [P] [US1] 在 `tests/contract/test_athlete_scan_status.py` 创建合约测试：覆盖 `contracts/athlete_scan_status.md` 的 4 个断言点
- [X] T019 [P] [US1] 在 `tests/contract/test_athlete_classifications_list.py` 创建合约测试：覆盖 `contracts/athlete_classifications_list.md` 的 8 个断言点（含 `page_size=101` 422）
- [X] T020 [US1] 在 `tests/integration/test_athlete_scan_integration.py` 创建端到端集成测试：mock COS list → scanner → 断言 `athletes` + `athlete_video_classifications` 行数与分类结果 + 教练侧两张表行数不变（SC-006）
- [X] T021 [P] [US1] 在 `tests/unit/test_cos_athlete_scanner.py` 创建单测：目录名 → `athlete_name` 映射（`map` / `fallback`）、同名后缀 `_2/_3`、`classification_source` 覆盖 `rule`/`llm`/`fallback` 三类
> ⚠️ 运行一次 pytest 确认 T017–T021 全部 **RED**（路由/服务/任务尚未实现）。

### US1 实施

- [X] T022 [P] [US1] 在 `src/services/cos_athlete_scanner.py` 实现 `CosAthleteScanner` 类（以 `CosClassificationScanner` 为镜像骨架）：`from_settings()` / `_get_cos_client()` / `_list_all_mp4s()` / `_get_athlete_name()` / `_upsert_athlete()` / `scan_full()` / `scan_incremental()`；异常处理分别抛 `ATHLETE_ROOT_UNREADABLE` / `ATHLETE_DIRECTORY_MAP_MISSING`
- [X] T023 [US1] 在 `src/workers/athlete_scan_task.py` 新建 Celery task `scan_athlete_videos`（`bind=True`，路由到 `default` 队列；结构对齐 `classification_task.scan_cos_videos`；task 启动时 `_make_session_factory()` + `scanner.scan_full/incremental`）
- [X] T024 [US1] 在 `src/workers/celery_app.py` 注册 `scan_athlete_videos` 任务 + `task_routes` 添加该 task → `default` 队列映射
- [X] T025 [US1] 在 `src/api/routers/athlete_classifications.py` 新建 router：`POST /athlete-classifications/scan` + `GET /athlete-classifications/scan/{task_id}` + `GET /athlete-classifications` 三个端点；全部用 `SuccessEnvelope` + `AppException`，分页走 `page(items, page=, page_size=, total=)`
- [X] T026 [US1] 在 `src/api/main.py` 注册新 router：`app.include_router(athlete_classifications.router, prefix="/api/v1")`
- [X] T027 [US1] 重跑 pytest T017–T021，全部 **GREEN**（25/25 PASS）；US1 可独立演示

**检查点**: US1 可独立演示——上传 2 条测试运动员 mp4 到 COS → scan → list → 素材清单呈现；教练侧表毫不受扰

---

## 阶段 4: 用户故事 2 — 运动员视频标准化预处理（P1）

**目标**: 单条或批量提交预处理；复用 F-016 orchestrator；成功后回写 `preprocessed=true + preprocessing_job_id`。

**独立测试**: 任取 US1 产出的一条 `athlete_video_classifications.id` → `POST /api/v1/tasks/athlete-preprocessing` → 等 `analysis_tasks.status=success` → 查 `video_preprocessing_jobs` 存在且 `athlete_video_classifications.preprocessed=true`。

### US2 测试

- [X] T028 [P] [US2] 在 `tests/contract/test_submit_athlete_preprocessing.py` 创建合约测试：覆盖 `contracts/submit_athlete_preprocessing.md` 的 7 个断言点（含批量部分成功、`force=true`）
- [X] T029 [US2] 在 `tests/integration/test_athlete_preprocessing_integration.py` 创建端到端测试：插入 1 条 `athlete_video_classifications` → 提交预处理（mock F-016 orchestrator 为快速成功）→ 断言 `preprocessed=true + preprocessing_job_id` 被回写

### US2 实施

- [X] T030 [P] [US2] 在 `src/services/athlete_submission_service.py` 新建 `AthleteSubmissionService`：对外暄 4 个提交函数（单/批预处理 + 单/批诊断）全部集中在此；内部调用现有 `preprocessing_service.create_or_reuse`，通过新增 `_fetch_classification` 双边回退逻辑（先查教练侧再查运动员侧）接入运动员素材；找不到 ID → `ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND`
- [X] T031 [US2] 在同一服务新增 `submit_athlete_preprocessing_batch`：复用 F-016 通道门控（满则 503 整批拒绝），逐条捕错聚合到 `rejected[]`；不混合部分成功
- [X] T032 [US2] 新建 celery task `src/workers/athlete_preprocessing_callback.py::mark_athlete_preprocessed_cb` 路由到 `default`，由 service 层在提交时 chain `preprocess_video.si(job_id) | mark_athlete_preprocessed_cb.si(...)`
- [X] T033 [US2] 在 `src/workers/celery_app.py` 注册 `mark_athlete_preprocessed_cb` 任务到 `default` 队列
- [X] T034 [US2] 在 `src/api/routers/athlete_tasks.py`（新建独立文件）新增两个端点：`POST /tasks/athlete-preprocessing`（单）+ `POST /tasks/athlete-preprocessing/batch`（批量）；全部走 `AthleteSubmissionService`；并在 `main.py` 注册；phase/step 由钩子派生
- [X] T035 [US2] 重跑 pytest T028–T029，全部 **GREEN**（8/8）；`analysis_tasks` 行的 task_type = `athlete_video_preprocessing` 由 preprocessing_service 无关（仅 `video_preprocessing_jobs` 行；Ath VCF 行直接回写无需 analysis_tasks）

**检查点**: US2 独立演示——挑一条 US1 素材触发预处理 → `GET /video-preprocessing/{job_id}` 看分段齐全 → `GET /athlete-classifications` 看到 `preprocessed=true`

---

## 阶段 5: 用户故事 3 — 运动员诊断任务端到端自动编排（P1，MVP 闭环）

**目标**: 以 `athlete_video_classification_id` 为唯一输入，单条/批量提交诊断；诊断读取预处理分段 + active standard → 生成报告 + 写三要素锚点。

**独立测试**: 批量提交 3 条已 US1+US2 处理完的素材 → 等任务完成 → 查 `GET /tasks/{task_id}` 看到 `overall_score / dimensions[] / improvement_advice`；查 `diagnosis_reports.cos_object_key / preprocessing_job_id / standard_version` 三列均非 null 且可反查回源素材（SC-005）。

### US3 测试

- [X] T036 [P] [US3] 在 `tests/contract/test_submit_athlete_diagnosis.py` 创建合约测试：覆盖 `contracts/submit_athlete_diagnosis.md` 的 9 个断言点（含 `ATHLETE_VIDEO_NOT_PREPROCESSED` / `STANDARD_NOT_AVAILABLE` / `CHANNEL_QUEUE_FULL` 整批原子拒绝 / 重复提交两条独立报告）
- [X] T037 [US3] 在 `tests/integration/test_athlete_diagnosis_end_to_end.py` 创建端到端测试：mock pose / scorer / advisor 为快速稳定返回 → 提交诊断 → 断言 `diagnosis_reports` 行含 `cos_object_key + preprocessing_job_id + standard_version + source='athlete_pipeline'`；按 `cos_object_key` 反查报告 ≤ 1 次请求（SC-005）
- [X] T038 [P] [US3] 在 `tests/unit/test_athlete_submission_service.py` 创建单测：批量提交 3 条其中 1 条无预处理 → `rejected[0]` 精确含 `ATHLETE_VIDEO_NOT_PREPROCESSED` + 其余 2 条正常返回 `task_id`

### US3 实施

- [X] T039 [US3] 在 `src/services/diagnosis_service.py` 新增入口 `diagnose_athlete_by_classification_id(session, task_id, classification_id, *, force=False)`（依据 research R8 的 6 步）；**不动既有 `diagnose_athlete_video(..., video_storage_uri)` 入口**；全帧姿态失败时抛 `ATHLETE_VIDEO_POSE_UNUSABLE`
- [X] T040 [US3] 在 `src/services/diagnosis_service.py` 将"无 active standard"的错误路径从 `StandardNotFoundError` 明面信号改为抛 `AppException(ErrorCode.STANDARD_NOT_AVAILABLE, details={tech_category,...})`（兼容既有 `diagnose()` 同步入口——外层捕 `StandardNotFoundError` 的调用点保留，仅新增路径额外抛 AppException）
- [X] T041 [US3] 在 `src/services/diagnosis_service.py` 持久化 `DiagnosisReport` 时新增 3 字段 `cos_object_key / preprocessing_job_id / source='athlete_pipeline'`（`athlete_video_classifications` 查回）；并在成功提交后 upsert `athlete_video_classifications.last_diagnosis_report_id`
- [X] T042 [US3] 在 `src/services/athlete_submission_service.py` 新增 `submit_diagnosis(session, classification_id, *, force=False)`：预检 `preprocessed=true`（否则 `ATHLETE_VIDEO_NOT_PREPROCESSED`）+ `tech_standards` active 存在（否则 `STANDARD_NOT_AVAILABLE`）；创建 `analysis_tasks(task_type=athlete_diagnosis)` 行；返回 `{task_id, tech_category}`
- [X] T043 [US3] 在同一 service 新增 `submit_diagnosis_batch(session, items, *, force=False)`：通道容量门控整批原子化；逐条 classification_id 不存在/未预处理/无标准统一聚合到 `rejected[]`（单条不阻断批次）
- [X] T044 [US3] 在 `src/workers/athlete_diagnosis_task.py::_run_diagnose` 内部增加一个"以 classification_id 入参"的分支——读 classification row → 调 `diagnosis_service.diagnose_athlete_by_classification_id`；**旧的 `video_storage_uri` 分支保留不动**
- [X] T045 [US3] 在 `src/api/routers/tasks.py` 新增两个端点：`POST /tasks/athlete-diagnosis`（单）+ `POST /tasks/athlete-diagnosis/batch`（批量）；全走 `AthleteSubmissionService`；路由层不做任何业务逻辑
- [X] T046 [US3] 重跑 pytest T036–T038，确认 **GREEN**；同时手工 `GET /tasks/{task_id}` 看结果结构完整

**检查点**: US3 完成即 **MVP 闭环**（US1 → US2 → US3 三阶段皆可独立演示 + 组合端到端）——停下来做一次独立验证再推进 P2/P3

---

## 阶段 6: 用户故事 4 — 训练侧与诊断侧任务在监控面清晰区分（P2）

**目标**: `GET /api/v1/tasks?business_phase=INFERENCE` 精准返回运动员侧三类任务，不污染 TRAINING；两侧任务计数之和等于总数。

**独立测试**: 让库里同时存在教练侧（scan_cos_videos / classify_video / extract_kb）+ 运动员侧（scan_athlete_videos / preprocess_athlete_video / diagnose_athlete）多条任务 → `?business_phase=INFERENCE` 只返回后三类 + `?business_phase=TRAINING` 只返回前三类 + `&business_step=scan_athlete_videos` 精确筛选成功（SC-004）。

### US4 测试

- [X] T047 [P] [US4] 在 `tests/integration/test_business_phase_filter_isolation.py` 新建集成测试：seed 6 条任务（两侧各 3 条）→ `?business_phase=INFERENCE` 返回 3 条、`?business_phase=TRAINING` 返回 3 条，求和等于全量 → `&business_step=preprocess_athlete_video` 精确 1 条
- [X] T048 [P] [US4] 在 `tests/contract/test_tasks_list_new_steps.py` 新建合约测试：`business_step=scan_athlete_videos` / `preprocess_athlete_video` 合法通过 + 非法步骤名 400 `INVALID_ENUM_VALUE`（覆盖 T013 白名单扩展）
- [X] T049 [P] [US4] 在 `tests/unit/test_phase_step_task_type_combo.py` 新建单测：验证 `_validate_phase_step_task_type_combo` 对 (INFERENCE, scan_athlete_videos, athlete_video_classification) 通过、对 (TRAINING, scan_athlete_videos, *) 抛 `INVALID_PHASE_STEP_COMBO`

### US4 实施

> 本 P2 阶段大部分"实施"已在阶段 2 的 T012 / T013 完成。本阶段主要是**验证**。

- [X] T050 [US4] 若 T047–T049 初次 RED，补齐 `_validate_phase_step_task_type_combo` 的边界处理（仅当 T012 遗漏时补上）
- [X] T051 [US4] 运行 T047–T049 至 **GREEN**，确认两侧监控隔离生效

**检查点**: US4 独立演示——手工构造混合任务集合，`GET /tasks?business_phase=...` + `&business_step=...` 输出完全符合预期

---

## 阶段 7: 用户故事 5 — 运动员素材/报告的可追溯与清单化查询（P3）

**目标**: 新增 `GET /api/v1/diagnosis-reports`，按 `athlete_id / tech_category / cos_object_key / preprocessing_job_id / source / 时间窗` 过滤；`GET /athlete-classifications` 支持 `has_diagnosis / tech_category / athlete_id` 联合过滤。

**独立测试**: 同一运动员多次诊断同一素材 → `GET /diagnosis-reports?athlete_name=X&cos_object_key=Y` 按时间倒序拿到多条报告；`GET /athlete-classifications?has_diagnosis=true` 只返回已诊断素材；`source='athlete_pipeline'` 不返回 F-011/F-013 旧行（SC-006）。

### US5 测试

- [X] T052 [P] [US5] 在 `tests/contract/test_athlete_reports_list.py` 创建合约测试：覆盖 `contracts/athlete_reports_list.md` 的 8 个断言点（含 `page_size=200` 422、`source=invalid` 400、cos_object_key 反查多条倒序）
- [X] T053 [P] [US5] 在 `tests/contract/test_athlete_classifications_list.py` **追加**（不修改 T019 已有断言）`has_diagnosis=true/false` / `athlete_id` 复合筛选断言（与 T019 共存于同一文件，断言用不同 `def test_*` 用例隔离）

### US5 实施

- [X] T054 [US5] 在 `src/api/routers/diagnosis_reports.py` 新建 router：`GET /diagnosis-reports`；参数解析 + `SuccessEnvelope` + 分页构造；查询层走现有 `AsyncSession` + `select(DiagnosisReport)`，不新建 service（简单聚合查询，章程原则 IX 分层职责允许路由层做参数校验 + 响应组装）
- [X] T055 [US5] 在 `src/api/main.py` 注册新 router：`app.include_router(diagnosis_reports.router, prefix="/api/v1")`
- [X] T056 [US5] 在 `src/api/routers/athlete_classifications.py` 的列表端点支持 `has_diagnosis` + `athlete_id` + `tech_category` + `preprocessed` 复合筛选（扩展 T025 的路由）
- [X] T057 [US5] 重跑 T052 + T053，确认 **GREEN**

**检查点**: US5 独立演示——按运动员姓名反查报告曲线 / 按素材反查所有版本 / 按预处理 job 反查报告

---

## 阶段 8: 完善与横切关注点

**目的**: 跨故事收尾、文档刷新、回归验证。

- [X] T058 [P] 运行 `quickstart.md` 8 步剧本走完全程，每一步符合预期（本步即 SC-001 / SC-003 / SC-004 / SC-005 / SC-006 五项 success criteria 的综合验收）
- [X] T059 [P] 运行 `scripts/audit/workflow_drift.py`（或 `make drift-full`）确认 `docs/business-workflow.md § 7.4` 错误码表与 `src/api/errors.py` 0 漂移
- [X] T060 [P] 运行 `scripts/audit/spec_compliance.py`（或 `make spec-compliance`）确认 `specs/020-athlete-inference-pipeline/spec.md` 含「业务阶段映射」六项子标签 + 符合 CI 守卫
- [X] T061 执行 `refresh-docs` skill 刷新 `docs/architecture.md` + `docs/features.md`（`docs/business-workflow.md` 在阶段 0 已同步扩展，此处只需 architecture / features 两份）
- [X] T062 [P] 运行全量 `pytest tests/ -v` 确认零回归（F-001 ~ F-019 的既有测试不破坏）
- [X] T063 手工检查 `GET /api/v1/business-workflow/overview` 能看到 INFERENCE 阶段的三步骤行（`scan_athlete_videos` / `preprocess_athlete_video` / `diagnose_athlete`）计数与耗时（F-018 自动派生由钩子保证）
- [X] T064 在 Feature changelog（或 `spec.md` 末尾）补一段"迁移说明"：本 feature 无下线接口，不需特殊迁移；`.env` 需补 `COS_VIDEO_ALL_ATHLETE`
- [X] T065 [P] 新建 `tests/integration/test_athlete_orphan_sweep.py` 验证 **FR-015**：seed 3 条 `running` 状态的运动员任务（`athlete_video_classification` / `athlete_video_preprocessing` / `athlete_diagnosis` 各一条，`updated_at` 回拨 TTL 超时），调用 `sweep_orphan_jobs` Beat 任务后断言三条折成 `failed` 伴 `error` 以 `ORPHAN_RECLAIMED` 起头；同时调用 `cleanup_intermediate_artifacts` 后运动员预处理临时文件被收回
- [X] T066 [US3] 扩展 `GET /api/v1/tasks/{task_id}` 的响应 schema（`src/api/schemas/task.py::TaskDetailResponse` 或等效）：当 `task_type='athlete_diagnosis'` 时返回 `athlete_video_classification_id` / `tech_category` / `standard_version`（`standard_version` 仅在 `status='success'` 后填充），满足 **FR-012**；在 `tests/contract/test_tasks_detail_athlete_fields.py` 补一个合约测试验证三字段返回

---

## 依赖关系与执行顺序

### 阶段依赖关系

- **阶段 1（设置）**: 无依赖；可立即开始
- **阶段 2（基础）**: 依赖阶段 1；阻塞所有 US 阶段
- **阶段 3（US1）**: 依赖阶段 2；**MVP 关键路径第 1 步**
- **阶段 4（US2）**: 依赖阶段 2 + 阶段 3（消费 US1 产物 `athlete_video_classifications`）
- **阶段 5（US3）**: 依赖阶段 2 + 阶段 4（消费 US2 产物 `preprocessed=true`）
- **阶段 6（US4）**: 依赖阶段 2（主要验证 T012 / T013），SHOULD 在 US1–US3 产生真实任务后更有说服力
- **阶段 7（US5）**: 依赖阶段 5（消费 `diagnosis_reports` 新列）
- **阶段 8（完善）**: 依赖所有期望 US 阶段

### 用户故事依赖关系

- **US1 (P1)**: 阶段 2 完成后即可开始；无其他故事依赖 → **MVP 第 1 步**
- **US2 (P1)**: 需要 US1 的 `athlete_video_classifications` 表数据；依赖 US1
- **US3 (P1)**: 需要 US2 的 `preprocessed=true`；依赖 US2 → **MVP 闭环**
- **US4 (P2)**: 仅依赖阶段 2 钩子/白名单；可在 US3 完成后独立验证
- **US5 (P3)**: 需要 US3 产生的 `diagnosis_reports` 新列数据；依赖 US3

### 每个用户故事内部

- 合约测试 + 集成测试在实施前编写并失败（TDD 红 → 绿）
- 模型（T006–T008）在服务之前
- 服务在路由之前
- 路由在集成测试前置断言之后
- 每个故事检查点完成后才移至下一个优先级

### 并行机会

- **阶段 1**: T002 / T003 并行
- **阶段 2**: T005 / T006 / T007 / T011 / T016 五个任务并行（均 [P]）
- **阶段 3（US1）**: T017 / T018 / T019 / T021 四项测试并行；T022 可与 T021 并行
- **阶段 4（US2）**: T028 与 T030 可并行
- **阶段 5（US3）**: T036 / T038 并行；T039 / T040 / T041 同一文件不并行
- **阶段 6（US4）**: T047 / T048 / T049 三项测试并行
- **阶段 7（US5）**: T052 / T053 并行
- **阶段 8**: T058 / T059 / T060 / T062 四项并行

---

## 并行示例: 用户故事 1

```bash
# 同时启动 US1 的 4 项独立测试（初始状态 RED）
任务: "在 tests/contract/test_athlete_scan.py 创建合约测试（T017）"
任务: "在 tests/contract/test_athlete_scan_status.py 创建合约测试（T018）"
任务: "在 tests/contract/test_athlete_classifications_list.py 创建合约测试（T019）"
任务: "在 tests/unit/test_cos_athlete_scanner.py 创建单测（T021）"

# 基础阶段的 5 个 [P] 任务同时启动（阶段 2）
任务: "T005 tests/unit/test_errors_athlete_codes.py 新 ErrorCode 守卫单测"
任务: "T006 src/models/athlete.py 建 ORM"
任务: "T007 src/models/athlete_video_classification.py 建 ORM"
任务: "T011 tests/unit/test_athlete_phase_step_hook.py 钩子单测"
任务: "T016 src/api/schemas/athlete_classification.py 全 Schemas"
```

---

## 实施策略

### 仅 MVP（P1 三故事 · US1 + US2 + US3）

1. 完成阶段 1（设置）
2. 完成阶段 2（基础 · 16 个任务，阻塞所有故事）
3. 完成阶段 3（US1 · 扫描端到端）→ 独立验证 → 演示（MVP 第 1 步）
4. 完成阶段 4（US2 · 预处理复用 F-016）→ 独立验证 → 演示
5. 完成阶段 5（US3 · 诊断端到端闭环 + 三要素锚点）→ 独立验证 → **MVP 闭环演示**
6. 运行 T058 quickstart 剧本验收 SC-001/003/004/005/006
7. **停止并验证**：此时 P1 已完成，可发布给运营试用

### 增量交付（推荐）

1. 阶段 1 + 2 → 基础就绪
2. + US1（P1）→ 独立测试 → 发布可见的"素材归集能力"
3. + US2（P1）→ 独立测试 → 发布可见的"预处理闭环"
4. + US3（P1）→ 独立测试 → **MVP 闭环发布**
5. + US4（P2）→ 独立测试 → 运营获得监控隔离能力
6. + US5（P3）→ 独立测试 → 教练获得运动员诊断历史曲线
7. 阶段 8（收尾）→ 全量回归 + 文档刷新

### 并行团队策略

基础完成后可分三条线（若团队容量足）：

- 开发 A：US1（Scanner + scan router + list router）
- 开发 B：US2 基础（AthleteSubmissionService 骨架 + preprocess 路由）—— US1 合并后再补 preprocessed 回写 callback
- 开发 C：US5（diagnosis_reports 列表 + athlete_classifications 筛选扩展）—— 需等 US3 数据就绪

US3 为关键路径上的收束节点，建议由最熟悉 F-011/F-013 诊断链路的开发串行处理。

---

## 注意事项

- **[P] 任务 = 不同文件 + 无内部依赖**；同文件的两项 [P] 必须降级为串行
- **[Story] 标签**将每项任务映射到 spec.md 的用户故事，便于可追溯
- **合约测试必须先 RED**：T017–T019 / T028 / T036 / T048 / T052 在对应实现完成前运行 pytest 必须失败；否则退回检查测试是否误设 skipif
- **每个用户故事检查点停顿** 独立验证再推进；禁止"全 merged 后一次性跑"
- 避免：模糊任务 / 相同文件冲突 / 破坏独立性的跨故事依赖
- **提交节奏**：SHOULD 在每个任务或逻辑组后提交；消息前缀 `[T0XX]`，便于事后追溯
- **Python 环境**：始终使用 `/opt/conda/envs/coaching/bin/python3.11 -m pytest`（项目规则 2）
- **数据库环境**：执行 T015 alembic upgrade 前确认当前 head 仍为 0017_kb_per_category_redesign（若已漂移需调整迁移编号）

---

## 任务统计

| 阶段 | 任务数 | [P] 数 |
|------|--------|--------|
| 阶段 1 设置 | 3 | 2 |
| 阶段 2 基础 | 13 | 5 |
| 阶段 3 US1（P1）| 11 | 5 |
| 阶段 4 US2（P1）| 8 | 2 |
| 阶段 5 US3（P1）| 12 | 2 |
| 阶段 6 US4（P2）| 5 | 3 |
| 阶段 7 US5（P3）| 6 | 2 |
| 阶段 8 完善 | 8 | 5 |
| **合计** | **66** | **26** |

## MVP 推荐范围

**US1 + US2 + US3（P1 三故事）** = T001–T046 + T066（FR-012 任务详情扩展）+ T058 quickstart 验收；合计约 48 个任务；预计端到端 2–3 人日。

P2（US4）+ P3（US5）+ 完善（T059–T065）为增量；合计约 18 个任务。
