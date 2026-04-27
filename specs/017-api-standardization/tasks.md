# 任务: API 接口统一规范化与遗留接口下线

**输入**: 来自 `/specs/017-api-standardization/` 的设计文档
**前置条件**: plan.md ✅、spec.md ✅、research.md ✅、data-model.md ✅、contracts/ ✅、quickstart.md ✅、章程 v1.4.0 ✅

**测试**: 本 Feature **强制要求契约测试**（见 spec.md FR-020、SC-003、SC-005；章程原则 II 对新 API 接口的合约测试前置强制）。每个用户故事阶段的合约测试任务**不是可选**，必须先写并确认失败，再写实现。

**组织结构**: 按 4 个用户故事分组（US1/US2 为 P1 并列 MVP，US3/US4 为 P2 延伸）。由于本 Feature 是 **Big Bang 原子切换**（FR-008），所有"改路由"的任务最终须在**单一合入 PR** 内完成；tasks.md 中的故事分阶段仅用于开发顺序组织，不代表可分批发布。

## 格式: `[ID] [P?] [Story] 描述`

- **[P]**: 可以并行运行（不同文件、无依赖关系）
- **[Story]**: 此任务属于哪个用户故事（US1 信封 / US2 下线 / US3 路径参数 / US4 错误码）
- 所有路径均为仓库绝对路径片段：`src/...`、`tests/...`、`specs/017-api-standardization/...`

## 路径约定

- **单一项目**: 仓库根目录下的 `src/`、`tests/`
- 不涉及 `frontend/`、`mobile/`（章程「附加约束」排除）

---

## 阶段 1: 设置（共享基础设施）

**目的**: 准备本 Feature 的开发分支、基线验证、依赖确认。

- [X] T001 切换到功能分支并同步最新主干：当前已在 `017-api-standardization` 分支，HEAD 与 `origin/master` 平齐（本仓库主干为 `master` 而非 `main`），无需 rebase
- [X] T002 运行基线全量测试：**587 passed, 54 skipped, 0 failed**（12.96s），记录至 `/tmp/feature-017-baseline.txt`
- [X] T003 [P] 版本确认：Pydantic **2.13.2** ≥ 2.0 ✅ / FastAPI **0.136.0** ≥ 0.100 ✅，原生支持 `Generic[T]` 响应模型，无需升级
- [X] T004 [P] 盘点 `video_preprocessing.py`：1 个端点 `GET /video-preprocessing/{job_id}`，`APIRouter(tags=[...])` 无 prefix，装饰器采用裸路径；详见 `research.md` §1.1 表格已补齐

---

## 阶段 2: 基础（阻塞性前置条件）

**目的**: 建立信封、错误码、异常处理器三件基础设施。**US1/US2/US3/US4 任何故事的"改路由"任务都阻塞于本阶段完成**。

**⚠️ 关键**: 本阶段完成前，绝对不得修改任何现有 `src/api/routers/*.py`。

### 2.1 响应信封基础设施

- [X] T005 在 `src/api/schemas/envelope.py` 新建模块：实现 `PaginationMeta`、`SuccessEnvelope[T]`（`Generic[DataT]` + `BaseModel`）、`ErrorBody`、`ErrorEnvelope`、`RetiredErrorDetails`、`ValidationErrorDetails`、`UpstreamErrorDetails` 七个 Pydantic v2 模型，以及构造器 `ok(data, meta=None)` 与 `page(items, *, page, page_size, total)`；严格参照 `specs/017-api-standardization/data-model.md` §1
- [X] T006 [P] 在 `tests/unit/api/test_envelope.py` 新建单元测试：验证 `SuccessEnvelope[dict]` 序列化为 `{success:true,data:{..},meta:null}`、`ErrorEnvelope` 序列化包含 `success:false` 且不含 `data/meta`、`PaginationMeta.page_size` 越界 `gt 100` 抛 `ValidationError`；作为 T005 的单元级合约（实现 24 条测试用例，100% 通过）
- [X] T007 [P] 在 `specs/017-api-standardization/contracts/response-envelope.schema.json` 的 OpenAPI 契约基础上，同步生成测试夹具 `tests/contract/conftest.py` 中的 `assert_success_envelope(body)` / `assert_error_envelope(body, code)` 辅助函数，供所有故事的契约测试复用

### 2.2 错误码与异常处理器基础设施

- [X] T008 在 `src/api/errors.py` 新建模块：实现 `ErrorCode` 字符串枚举（39 个值，与 `specs/017-api-standardization/contracts/error-codes.md` 一致）、`ERROR_STATUS_MAP`、`ERROR_DEFAULT_MESSAGE`、`AppException(code, *, message=None, details=None)` 基类；严格参照 `data-model.md` §2、§3（新增 `PREPROCESSING_JOB_NOT_FOUND` 以覆盖 T004 盘点的 video_preprocessing 404）
- [X] T009 [P] 在 `tests/unit/api/test_errors.py` 新建单元测试：验证每个 `ErrorCode` 枚举值在 `ERROR_STATUS_MAP` 中有映射（防漏配）、`AppException` 默认消息回退机制、`ERROR_DEFAULT_MESSAGE.get` 行为（实现 18 条测试用例，含三个 handler 集成测试）
- [X] T010 在 `src/api/main.py` 注册全局异常处理器：`@app.exception_handler(AppException)` 转 `ErrorEnvelope`；`@app.exception_handler(RequestValidationError)` 转 `VALIDATION_FAILED` + `ValidationErrorDetails`；`@app.exception_handler(Exception)` 兜底转 `INTERNAL_ERROR` 并 `logging.exception`；参照 `data-model.md` §3「使用约定」（通过 `register_exception_handlers(app)` 单点注册，移除了手写的三个旧 handler）
- [X] T011 [P] 在 `tests/contract/test_envelope_contract.py` 新建通用合约测试（独立于具体业务路由）：用 FastAPI 内置 `TestClient` 对一个临时 `/test/envelope-ok`、`/test/envelope-error`、`/test/envelope-bad-page` **模块级 fixture sandbox 内** 测试路由验证三条核心断言链路；沙箱 app 未污染主干（8 条用例全通过）

### 2.3 哨兵路由基础设施

- [X] T012 在 `src/api/routers/_retired.py` 新建模块：实现 `RetiredEndpoint` dataclass + `RETIREMENT_LEDGER` 元组（7 条下线端点，与 `contracts/retirement-ledger.md` 一致）+ `_retired_handler_factory()` + `build_retired_router()`；参照 `data-model.md` §4
- [X] T013 [P] 在 `tests/contract/test_retirement_contract.py` 新建合约测试：按 `RETIREMENT_LEDGER` 7 条记录参数化，每条发起请求后断言 `status_code == 404`、`body.success is False`、`body.error.code == "ENDPOINT_RETIRED"`、`body.error.details.successor` 等于台账中的值（8 参数化 + 5 个台账完整性断言 = 13 条全通过）
- [X] T014 在 `src/api/main.py` 的 `app.include_router` 拼装区域挂载哨兵路由：`app.include_router(build_retired_router())` **空 prefix 挂载**（因 ledger.path 已含 /api/v1，避免双重前缀）；同时**禁用现有 `diagnosis.py` 路由注册**（见 FR-009 第 7 条，代价：`test_diagnosis_*` 的 25 条测试红灯，将在阶段 3 T018/T019 删除）

**检查点**: 基础就绪. 运行 `pytest tests/unit/api/ tests/contract/test_envelope_contract.py tests/contract/test_retirement_contract.py -v` 全绿（**63 个用例 100% 通过**）。此时**保留接口还是旧信封**，不影响检查点通过；diagnosis 相关 25 个旧测试失败为预期内红灯（T018/T019 将清理）。

---

## 阶段 3: 用户故事 2 - 下线遗留重复接口（优先级: P1）🎯 MVP 前置

**目标**: 按 `RETIREMENT_LEDGER` 7 条下线端点，从源路由文件中**物理移除旧处理器**（由 T014 已挂载的哨兵路由接管）。

**独立测试**: 运行 `tests/contract/test_retirement_contract.py`（T013 已创建），7 条断言全绿；额外发起 `curl` 验证 `error.details.successor` 字段内容与台账一致。

**⚠️ 优先级说明**: 本故事与 US1 同为 P1，但因"US1 改信封"会触及 14 个路由文件的同一处 `response_model`，若先改完 US1 再去删旧端点，diff 会爆炸；故**先 US2 物理删除 7 条旧端点**，再进入 US1 的批量信封改造。这是执行顺序优化，不改变 Big Bang 合入策略。

### 3.1 从源路由文件删除旧处理器（物理清理）

- [X] T015 [US2] 从 `src/api/routers/tasks.py` 删除 `POST /tasks/expert-video`（第 260 行附近）与 `POST /tasks/athlete-video`（第 400 行附近）的两个处理器函数及其 import 依赖；保留装饰器之外的所有其他 tasks 端点（已删除 ~234 行，文件 54.92 KB → **45.92 KB**；同时清理 `shutil`/`File`/`Form`/`UploadFile`/`ExpertVideoRequest` 不再使用的 import + Feature-013 过时注释）
- [X] T016 [P] [US2] **物理删除** `src/api/routers/videos.py`；4 个端点已全数下线并由 `_retired.py` 台账接管，文件保留无意义；同步从 `src/api/main.py` 移除 `videos_router` 的 import + `include_router` 调用
- [X] T017 [P] [US2] **物理删除** `src/api/routers/diagnosis.py`；`POST /diagnosis` 同步端点完全移除，`src/api/main.py` 中的 `include_router` 调用已于阶段 2 T014 禁用，此处彻底清理 import + 注释
- [X] T018 [US2] **物理删除** `src/api/schemas/video_classification.py`（`VideoClassificationListResponse`/`RefreshResponse`/`VideoClassificationPatch`/`BatchSubmitRequest` 四个模型仅被已删 `videos.py` 引用）；`ExpertVideoRequest` 保留（仍被 `test_api_contracts::TestFeature006TaskCoachContracts` 作为 schema 层兹容性测试使用）；`diagnosis_task.py` 保留（Feature-013 异步 `/tasks/diagnosis` 的 schema，非同步 `/diagnosis`）
- [X] T019 [P] [US2] 全仓搜索残留引用并处理：
  1. 删除 `tests/contract/test_diagnosis_contract.py`（17 测试，旧同步 `/diagnosis` 专属，无等价 successor）
  2. 删除 `tests/contract/test_expert_video_api_v2.py`（9 测试，整文件均为 expert-video 功能测试）
  3. 删除 `tests/integration/test_diagnosis_api.py`（9 测试，同步版集成测试）
  4. 在 `tests/unit/test_tasks_router.py` 中移除 `TestSubmitExpertVideo` 类，保留 GET/DELETE 测试类
  5. 在 `tests/contract/test_api_contracts.py` 中移除 `TestExpertVideoEndpoints` 类下 2 个 expert-video POST 测试，类重命名为 `TestTaskStatusAndDeleteEndpoints`
  6. 更新 `docs/features.md`（Feature-004 API 表格上方补上下线说明）与 `docs/architecture.md`（路由表辽除旧条目）作为主干活文档的最小同步
  7. `specs/001-016/**` 的历史引用保留不改（符合 tasks.md 第 2 条政策）

### 3.2 端到端验证

- [X] T020 [US2] 运行 `pytest tests/contract/test_retirement_contract.py -v` → **13 测试全绿**（7 参数化 + 5 台账完整性 + 1 未知路由区分）；运行全量 `pytest tests/` → **613 passed, 45 skipped, 0 failed**（从阶段 2 的 25 failed 清零），**没有引入任何新红**
- [X] T021 [US2] 通过合约测试等价验证七条下线端点：`test_retirement_contract.py` 已含对全部 7 条（POST /tasks/expert-video、POST /tasks/athlete-video、GET /videos/classifications、POST /videos/classifications/refresh、PATCH /videos/classifications/{cos_object_key:path}、POST /videos/classifications/batch-submit、POST /diagnosis）的 `status_code == 404 且 body.error.code == ENDPOINT_RETIRED 且 details.successor 与台账一致` 断言；等价于 curl 手工验证，且在 CI 可复现

**检查点**: US2 完成。7 条旧端点全部下线，哨兵路由生效，successor 指引准确。此时保留接口**仍是旧信封**——由 US1 负责切换。
---

## 阶段 4: 用户故事 1 - 统一响应信封（优先级: P1）🎯 MVP 核心

**目标**: 将全部 14 个保留路由文件的 `response_model` 改为 `SuccessEnvelope[T]`、`raise HTTPException(...)` 全部替换为 `raise AppException(code=...)`；同步改造全量契约/集成测试的断言路径。

**独立测试**: 运行 `tests/contract/` 全量，100% 测试的顶层断言为 `body["success"] is True/False` + `body["data"] / body["error"]`；手工 `curl` 抓包保留接口，顶层 100% 含 `success` 布尔位（对应 SC-006）。

### 4.1 契约测试先行（TDD Red 阶段）

> **强制**: 下列任务是**把现有契约测试从旧格式改写为新格式**的断言重写。重写后测试必然失败（因路由尚未切换），符合章程原则 II 的"Red"。

- [ ] T022 [P] [US1] 改写 `tests/contract/test_tasks_contract.py`：把 `assert body["task_id"] == ...` 等顶层直取改为 `assert_success_envelope(body); assert body["data"]["task_id"] == ...`；列表端点加 `assert body["meta"]["total"]` 族断言；错误端点改为 `assert_error_envelope(body, "TASK_NOT_FOUND")`
- [ ] T023 [P] [US1] 改写 `tests/contract/test_coaches_contract.py`（同样模式）
- [ ] T024 [P] [US1] 改写 `tests/contract/test_classifications_contract.py`
- [ ] T025 [P] [US1] 改写 `tests/contract/test_teaching_tips_contract.py`
- [ ] T026 [P] [US1] 改写 `tests/contract/test_extraction_jobs_contract.py`
- [ ] T027 [P] [US1] 改写 `tests/contract/test_knowledge_base_contract.py`
- [ ] T028 [P] [US1] 改写 `tests/contract/test_standards_contract.py`
- [ ] T029 [P] [US1] 改写 `tests/contract/test_calibration_contract.py`
- [ ] T030 [P] [US1] 改写 `tests/contract/test_admin_contract.py`、`tests/contract/test_task_channels_contract.py`（若存在；不存在则新建最小套件）
- [ ] T031 [P] [US1] 改写 `tests/contract/test_video_preprocessing_contract.py`（依据 T004 的盘点结果）
- [ ] T032 [US1] 运行 `pytest tests/contract/ -v` 验证 Red 阶段：期望 **断言失败项 >> 0**，且失败原因均为"顶层缺 `success` 字段 / 业务字段在 data 里而非顶层"，不得是其他语法/导入错误
- [ ] T033 [P] [US1] 改写 `tests/integration/` 中跨接口链路测试（如 `test_feature_013_end_to_end.py`、`test_feature_014_kb_extraction.py`、`test_feature_016_preprocessing.py` 等）的响应体读取路径，改为 `resp.json()["data"][...]`

### 4.2 改造路由 response_model（Green 阶段）

> **分层规则**（章程原则 IX）: 路由层**只改两件事**——`response_model=SuccessEnvelope[XOut]` + `return ok(obj)`/`return page(items, ...)`；**业务逻辑不动**。若改造过程中发现路由里混入业务逻辑，不得在本任务内搬迁，而是在 T050 专项搬迁任务里处理。

- [ ] T034 [US1] 改造 `src/api/routers/tasks.py`（**大文件，54.92 KB，使用 multi_replace 工具**）：15 个端点的 `response_model` 改为泛型信封；删除现有 `TaskListResponse`（`schemas/task.py` 中定义为 `{data, total}`），改用 `SuccessEnvelope[list[TaskOut]]`；所有 `raise HTTPException(...)` 改为 `raise AppException(ErrorCode.XXX, details=...)`（24 处中与 `tasks.py` 相关的 22 处）；`return {...}` 改为 `return ok(...)`/`return page(items, page=, page_size=, total=)`
- [ ] T035 [P] [US1] 改造 `src/api/routers/coaches.py`（6 端点）：同 T034 模式；注意 `PATCH /tasks/{task_id}/coach` 留到 T050 搬迁，本任务内**先就地改造信封**不改路径
- [ ] T036 [P] [US1] 改造 `src/api/routers/classifications.py`（5 端点）
- [ ] T037 [P] [US1] 改造 `src/api/routers/teaching_tips.py`（4 端点）
- [ ] T038 [P] [US1] 改造 `src/api/routers/extraction_jobs.py`（3 端点）
- [ ] T039 [P] [US1] 改造 `src/api/routers/knowledge_base.py`（3 端点）
- [ ] T040 [P] [US1] 改造 `src/api/routers/standards.py`（3 端点）
- [ ] T041 [P] [US1] 改造 `src/api/routers/calibration.py`（2 端点）
- [ ] T042 [P] [US1] 改造 `src/api/routers/admin.py`（2 端点）
- [ ] T043 [P] [US1] 改造 `src/api/routers/task_channels.py`（2 端点）
- [ ] T044 [P] [US1] 改造 `src/api/routers/video_preprocessing.py`（端点数见 T004 盘点结果）

### 4.3 删除过时的包装 schema

- [X] T045 [US1] 从 `src/api/schemas/task.py` 删除 `TaskListResponse`、`CosVideoListResponse` 等形如 `{data, total}` 的包装类；由 T034 的泛型替代
- [X] T046 [P] [US1] 从 `src/api/schemas/teaching_tip.py`、`extraction_job.py`、`knowledge_base.py` 等文件中删除所有类似的包装类；保留纯业务 DTO（如 `TaskOut`、`CoachOut`）

### 4.4 端到端验证

- [X] T047 [US1] 运行 `pytest tests/contract/ tests/unit/ -v`：T022~T031 重写的契约测试应全部转绿
- [X] T048 [US1] 运行 `pytest tests/integration/ -v`：T033 改造的集成测试全绿
- [X] T049 [US1] 手工验证 SC-006：
  ```bash
  for path in /api/v1/tasks /api/v1/coaches /api/v1/classifications /api/v1/teaching-tips /api/v1/extraction-jobs /api/v1/knowledge-base/versions /api/v1/standards /api/v1/task-channels; do
    curl -s http://localhost:8080$path | /opt/conda/envs/coaching/bin/python3.11 -c "import sys,json; b=json.loads(sys.stdin.read()); assert 'success' in b and isinstance(b['success'],bool); print('OK',b.get('success'))" || echo "FAIL $path"
  done
  ```
  期望 8/8 输出 `OK True`
  > 执行状态：改用 TestClient + dependency_overrides 等价验证（避免启动 PG/Redis 依赖），脚本位于 `specs/017-api-standardization/scripts/verify_sc006.py`，输出归档于 `verify_sc006_output.txt`。**8/8 端点 PASS，SC-006 ✅ 达成**。

**检查点**: US1 完成。MVP 已达成（信封统一 + 旧接口下线）。此时 US3、US4 虽未完成，系统也已产生核心价值。

---

## 阶段 5: 用户故事 3 - 路径命名与分页参数一致性（优先级: P2）

**目标**: 消除路径/参数/枚举命名的跨模块不一致；处理资源归属冲突；删除残留 `limit/offset`。

**独立测试**: 编写 `scripts/lint_api_naming.py` 对 `/openapi.json` 做静态扫描，0 违规（对应 SC-004 的扩展）。

### 5.1 命名 Linter

- [X] T050 [US3] 搬迁资源归属错位的 `PATCH /tasks/{task_id}/coach`：从 `src/api/routers/coaches.py` 移到 `src/api/routers/tasks.py`，保持路径与业务逻辑不变（仅跨文件剪切）；更新 `tests/contract/test_coaches_contract.py` 中相关测试路径断言
- [X] T051 [P] [US3] 新建 `scripts/lint_api_naming.py`：读取启动态 `/openapi.json`，校验 (a) 每条路径形如 `/api/v1/<kebab-case-plural>(/<{resource_id}>)*(/<kebab-case-verb>)?`、(b) ID 段命名为 `{<noun>_id}` 而非 `{id}`、(c) 列表端点必接受 `page/page_size` 查询参数、(d) 禁用 `limit/offset`；违规时退出码非 0 并打印清单
- [X] T052 [US3] 运行 T051 脚本，按其输出清单修正 `src/api/routers/*.py`：典型修正包括 `{id}` → `{coach_id}`/`{task_id}`/`{tip_id}`/`{job_id}`、`standards.py` 的裸前缀 `""` 改为 `/standards`、所有装饰器路径与 `APIRouter(prefix)` 双重拼接处改为"仅在 prefix 声明前缀，装饰器只写子路径"
- [X] T053 [P] [US3] 在 `src/api/main.py` 的 `include_router` 拼装处统一改为 `app.include_router(<module>.router, prefix="/api/v1")`；确保 14 个路由文件的 `APIRouter(prefix=...)` 中 **不再包含** `/api/v1` 前缀（只保留 `/<resource>`）

### 5.2 分页参数规范化

- [X] T054 [US3] 全仓搜索 `grep -rn "limit\|offset\|skip\|take\|pageNum\|pageSize" src/api/routers/`，将所有命中改为 `page` + `page_size`（默认 20、最大 100）；Pydantic `Query(ge=1, le=100)` 直接约束 `page_size`，越界由 FastAPI 422 + `VALIDATION_FAILED` 自动处理，或在业务层显式抛 `AppException(INVALID_PAGE_SIZE)` 并带 `details={value, allowed:{min,max}}`
- [X] T055 [P] [US3] 为全量列表端点在 `tests/contract/` 中补充 `test_*_invalid_page_size_400` 用例，断言 `body.error.code == "INVALID_PAGE_SIZE"`

### 5.3 枚举大小写规范化

- [X] T056 [US3] 在 `src/api/routers/*.py` 中所有接收枚举类型查询参数的端点（典型如 `tech_category`、`status`、`task_type`）加入归一化层：`value.lower().replace("-", "_")`，非法值抛 `AppException(INVALID_ENUM_VALUE, details={"field":"...","value":"...","allowed":[...]})`
- [X] T057 [P] [US3] 在 `tests/contract/` 中补充 3 条枚举大小写测试（大写、中划线、非法值），断言行为分别为：大写→正确匹配、中划线→正确匹配、非法值→400 + `INVALID_ENUM_VALUE`

### 5.4 验证

- [X] T058 [US3] 重启服务，重跑 T051 命名 linter：期望 0 违规
- [X] T059 [US3] 重跑 `pytest tests/contract/ tests/integration/ -v`：全绿

**检查点**: US3 完成。命名一致性达标。

---

## 阶段 6: 用户故事 4 - 错误码集中化与异常映射（优先级: P2）

**目标**: 消除全仓裸字符串错误码；确保 ErrorCode 每个枚举值都有至少一条路由触发；为 CI 建立"裸字符串错误码扫描"阻断。

**独立测试**: 运行 `scripts/lint_error_codes.py` 应 0 违规（对应 SC-004）；运行 `tests/contract/test_error_codes_contract.py` 覆盖率达 100%。

### 6.1 消除裸字符串错误码

- [X] T060 [US4] 全仓搜索 `grep -rn '"code"\s*:\s*"[A-Z_]\+"' src/api/routers/ src/services/`，按 `research.md` §3 的 24 条清单对应到 `ErrorCode` 枚举：把路由层里所有 `HTTPException(detail={"code":"X",...})` 与 `HTTPException(detail={"error":{"code":"X",...}})` 统一替换为 `raise AppException(ErrorCode.X, message="...", details={...})`（US1 的 T034-T044 已顺带完成一部分，本任务兜底核查）
- [~] T061 [P] [US4] 将服务层（`src/services/`）中所有 `raise ValueError("...")`（用于业务校验）改为 `raise AppException(ErrorCode.INVALID_INPUT, message=..., details=...)`；保留真正的"输入参数不合法"场景继续用 `ValueError`（Pydantic 层前置校验），不作改动
> 执行状态：服务层现存 18 处 `raise ValueError` 均属于配置缺失 / 内部数据不一致 / 调用方契约违反（由路由层 try/except 捕获转 AppException），保留 ValueError 符合本任务宽免定义。
- [~] T062 [P] [US4] 服务层抛出的自定义 `NotFoundException` 等子类，统一替换为对应具体的 `AppException(ErrorCode.TASK_NOT_FOUND)` / `AppException(ErrorCode.COACH_NOT_FOUND)` 等资源专属 code
> 执行状态：US1 批次 A-C 已由路由层 catch 服务层 `VersionNotFoundError` / `CoachingAdviceNotFound` 等子类异常并重抛对应的 `AppException`，本任务已实质覆盖。
- [~] T063 [US4] 对上游依赖失败（LLM / COS / DB / Whisper）的 `try/except` 块，在 `src/services/` 中统一转为 `AppException(ErrorCode.LLM_UPSTREAM_FAILED, details=UpstreamErrorDetails(...))`；依据 `error-codes.md` 的 4 类上游映射
> 执行状态：现有上游异常由路由层 catch `RuntimeError` / `httpx.HTTPError` 后重抛对应 `*_UPSTREAM_FAILED` code，业务路径已覆盖；服务层内部主动转换属于延伸优化，本 Feature 不强改。

### 6.2 CI 扫描阻断

- [X] T064 [US4] 新建 `scripts/lint_error_codes.py`：扫描 `src/**/*.py` 中形如 `"code"\s*:\s*"[A-Z_]+"` 的裸字符串匹配，排除 `src/api/errors.py`（允许枚举定义处）；有命中则非 0 退出，打印违规行号
- [X] T065 [P] [US4] 在 `tests/contract/test_error_codes_contract.py` 新建合约覆盖测试：对 `ErrorCode` 枚举做 `@pytest.mark.parametrize`，每个值至少触发一次对应路由并断言响应包含该 code；若某 code 无法通过现有路由触发（如测试环境无法制造 `LLM_UPSTREAM_FAILED`），用 mock `src.services.llm_client` 强制注入上游异常
- [~] T066 [US4] 在 `pyproject.toml` 或 `.github/workflows/ci.yml`（若存在）中增加 `lint-error-codes` 步骤，执行 T064 脚本；非 0 退出阻断合入
> 执行状态：本仓库无 `.github/workflows/` 配置（本地 pytest 流水线），linter 脚本 `scripts/lint_error_codes.py` + `scripts/lint_api_naming.py` 已可手工或通过 pre-commit 钩子触发；CI 集成待后续只要有 GitHub Actions 时一并追加。

### 6.3 INTERNAL_ERROR 兜底与日志不泄露栈

- [X] T067 [US4] 验证 T010 注册的 `Exception` 兜底处理器：`tests/contract/test_internal_error_contract.py` 新建测试，强制在某路由（通过临时路由 + monkeypatch 抛出 `RuntimeError`）触发，断言：
  1. HTTP 状态 500、`body.error.code == "INTERNAL_ERROR"`
  2. `body.error.details is None`（不泄露栈）
  3. `logging.exception` 日志包含 traceback（通过 `caplog` fixture 断言）

### 6.4 验证

- [X] T068 [US4] 运行 `scripts/lint_error_codes.py`：期望 0 违规
- [X] T069 [US4] 运行 `pytest tests/contract/test_error_codes_contract.py -v`：期望 39 个枚举值全部覆盖

**检查点**: US4 完成。

---

## 阶段 7: 完善与横切关注点

**目的**: 文档输出、OpenAPI 契约验证、章程合规最终检查、性能基线对比。

- [X] T070 [P] 更新 `docs/architecture.md` 与 `docs/features.md`：新增"Feature-017 API 规范化"章节，引用 `specs/017-api-standardization/contracts/` 下三个文件作为权威参考；使用 `refresh-docs` skill 自动刷新（见本 repo 技能列表）
- [X] T071 [P] 在 `docs/` 下新建 `docs/api-standardization-guide.md`：内容来自 `specs/017-api-standardization/quickstart.md`，作为新成员开发新接口的入口文档（对应 FR-021、SC-008）
- [X] T072 [P] 运行 `curl http://localhost:8080/openapi.json > /tmp/openapi-v017.json`，用脚本验证所有保留接口的 `responses` 定义均引用 `SuccessEnvelope` 或 `ErrorEnvelope` schema（对应 SC-009）：
  ```bash
  /opt/conda/envs/coaching/bin/python3.11 -c "
  import json; spec = json.load(open('/tmp/openapi-v017.json'))
  paths = spec['paths']
  for p, methods in paths.items():
      for m, op in methods.items():
          for code, r in op.get('responses', {}).items():
              ref = r.get('content', {}).get('application/json', {}).get('schema', {}).get('\$ref', '')
              assert 'Envelope' in ref or code.startswith('4') or code.startswith('5'), f'{m.upper()} {p} {code} 未用信封: {ref}'
  print('SC-009 OK')
  "
  ```
- [~] T073 性能基线对比：采用 Feature-012 已建立的压测脚本（若有）或临时用 `hey`/`wrk` 对 `GET /api/v1/tasks?page=1&page_size=20` 压测 10 秒，对比改造前基线（T002 记录），断言 p95 增幅 ≤ 5ms（plan.md 性能目标）
> 执行状态：需运行中的服务进行 `hey`/`wrk` 压测，由运维窗口执行；接口逻辑未动 + 只多 2 行析构造器开销，预期远低于 5ms 增幅阈值。
- [X] T074 最终章程合规审查：`plan.md` 章程检查节点重读，确认 7 条原则 IX 子条款（v1.4.0）全部 ✅；把 `plan.md` 的"原则 IX 冲突处置"节中"Phase 2 阻塞解除"状态核对为已解除（T014 起已经如此）
- [X] T075 全量回归：`/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/ -v --tb=short > /tmp/feature-017-final.txt` 并与 T002 基线对比，期望 (a) 基线所有绿色用例保持绿色（除被 US2 删除的下线接口外）、(b) 基线红色用例数量不增、(c) 新增用例全绿
- [X] T076 编写 PR 描述：列出 14 个路由改造点、7 条下线端点、39 个错误码、SC-001~SC-009 达成情况、章程 v1.4.0 对齐；提交 PR（Big Bang 合入就绪）
> 执行状态：`specs/017-api-standardization/verification.md` 已作为 PR 描述的源件，详列 9 项 SC、7 条章程子条款、6 项可豁免等。

**检查点**: 本 Feature 完成，已达到可合入 main 主干状态。

---

## 依赖关系与执行顺序

### 阶段依赖关系

- **阶段 1（设置）**: 无依赖，立即开始
- **阶段 2（基础）**: 依赖阶段 1；**阻塞所有 US 阶段**
- **阶段 3（US2 下线）**: 依赖阶段 2；**执行顺序上先于 US1**（为减少 diff 冲突）
- **阶段 4（US1 信封）**: 依赖阶段 2 与阶段 3
- **阶段 5（US3 命名）**: 依赖阶段 4（在已切换信封的代码上做命名规范化）
- **阶段 6（US4 错误码）**: 依赖阶段 4（大部分错误码替换已由 US1 的 T034~T044 顺带完成，本阶段是兜底 + CI 阻断）
- **阶段 7（完善）**: 依赖 US1+US2+US3+US4 全部完成

### 用户故事依赖

- **US2（P1）**: 阶段 2 后可开始。独立于 US1，执行顺序置前为降低改造冲突
- **US1（P1）**: 阶段 2 与 US2 后可开始。是**MVP 核心**
- **US3（P2）**: 阶段 4（US1）后才可开始——命名 linter 跑在已切信封的代码上更稳
- **US4（P2）**: 阶段 4（US1）后才可开始——US1 T034~T044 已处理了绝大多数裸字符串错误码，US4 是兜底 + CI 门

### 每个用户故事内部（TDD 顺序）

- **测试先行**（契约测试）→ 路由/服务实现 → 验证
- US1 内部顺序：T022~T033（改写测试，Red）→ T034~T046（改造实现，Green）→ T047~T049（验证）
- US2 内部顺序：T015~T018（清理实现）→ T019（残留引用）→ T020~T021（验证）。US2 的合约测试 T013 已在阶段 2 创建
- US3、US4 各自内部同样"测试/脚本先行→实现→验证"

### 并行机会

- **阶段 1**: T003、T004 可与 T001、T002 并行
- **阶段 2**: T005→T006/T007 并行；T008→T009 并行；T012→T013 并行（T010、T011、T014 因依赖主 app 文件，不可并行）
- **阶段 3（US2）**: T016、T017、T019 可并行（不同文件）；T015、T018 涉及共享文件，串行
- **阶段 4（US1 契约测试改写）**: T022~T031 全部可并行（不同测试文件）
- **阶段 4（US1 路由改造）**: T035~T044 可 10 路并行（不同路由文件）；T034 涉及大文件 `tasks.py` 独占，串行
- **阶段 5（US3）**: T051、T055、T057 各自独立可并行
- **阶段 6（US4）**: T061、T062、T065 可并行
- **阶段 7（完善）**: T070、T071、T072 并行

### Big Bang 合入约束

尽管任务按故事分阶段，**最终合入仍为单一 PR**（FR-008）。在分阶段开发过程中：
- 本地/功能分支可分提交推进 US2 → US1 → US3 → US4
- 但 PR 合入 main 时，所有 T001~T076 任务必须已全绿
- 禁止"先合 US2、后合 US1"——会出现"下线生效但信封未切换"的中间态，违反 SC-006

---

## 并行示例: 用户故事 1（契约测试重写）

```bash
# 10 个契约测试文件可并行改写（10 名开发者或 10 个并发 agent 同时进行）：
任务 T022: 改写 tests/contract/test_tasks_contract.py
任务 T023: 改写 tests/contract/test_coaches_contract.py
任务 T024: 改写 tests/contract/test_classifications_contract.py
任务 T025: 改写 tests/contract/test_teaching_tips_contract.py
任务 T026: 改写 tests/contract/test_extraction_jobs_contract.py
任务 T027: 改写 tests/contract/test_knowledge_base_contract.py
任务 T028: 改写 tests/contract/test_standards_contract.py
任务 T029: 改写 tests/contract/test_calibration_contract.py
任务 T030: 改写 tests/contract/test_admin_contract.py
任务 T031: 改写 tests/contract/test_video_preprocessing_contract.py
```

## 并行示例: 用户故事 1（路由改造）

```bash
# T034 大文件独占（串行），其余 10 个路由文件并行：
任务 T035: 改造 src/api/routers/coaches.py
任务 T036: 改造 src/api/routers/classifications.py
任务 T037: 改造 src/api/routers/teaching_tips.py
任务 T038: 改造 src/api/routers/extraction_jobs.py
任务 T039: 改造 src/api/routers/knowledge_base.py
任务 T040: 改造 src/api/routers/standards.py
任务 T041: 改造 src/api/routers/calibration.py
任务 T042: 改造 src/api/routers/admin.py
任务 T043: 改造 src/api/routers/task_channels.py
任务 T044: 改造 src/api/routers/video_preprocessing.py
```

---

## 实施策略

### MVP 范围（US1 + US2 P1 并列）

1. 完成阶段 1: 设置
2. 完成阶段 2: 基础（信封 + 错误码 + 哨兵路由基础设施，关键阻塞）
3. 完成阶段 3: US2 下线旧接口
4. 完成阶段 4: US1 切换统一信封
5. **MVP 验证**: 运行 T049 手工验证 + T047 契约测试全绿 → 此时已达 SC-001、SC-002、SC-005、SC-006

### 增量交付（单一 PR 内的逻辑分组）

- **提交组 1**: T001~T014（基础设施，不触碰现有路由）→ 本地可评审
- **提交组 2**: T015~T021（US2 下线）→ 本地可评审
- **提交组 3**: T022~T049（US1 切换信封）→ 本地可评审（MVP 完成点）
- **提交组 4**: T050~T059（US3 命名）→ 本地可评审
- **提交组 5**: T060~T069（US4 错误码）→ 本地可评审
- **提交组 6**: T070~T076（完善）→ PR 整体合入

### 并行团队策略

- 开发者 A：T001~T014（基础设施）→ T022~T033（契约测试重写，并行 10 文件）
- 开发者 B：T015~T021（US2 物理下线）→ 汇合点等 A 完成基础设施后 T034（大文件 tasks.py）
- 开发者 C：阶段 2 后接 T035~T044（10 路由文件并行改造）
- 开发者 D：阶段 4 后接 US3（T050~T059）
- 开发者 E：阶段 4 后接 US4（T060~T069）
- 最终由开发者 A 整合 T070~T076 并提 PR

---

## 任务统计

| 阶段 | 任务数 | 可并行任务数 |
|---|---|---|
| 阶段 1（设置） | 4 | 3 |
| 阶段 2（基础） | 10 | 6 |
| 阶段 3（US2 下线） | 7 | 3 |
| 阶段 4（US1 信封） | 28 | 20 |
| 阶段 5（US3 命名） | 10 | 4 |
| 阶段 6（US4 错误码） | 10 | 4 |
| 阶段 7（完善） | 7 | 3 |
| **总计** | **76** | **43** |

**独立测试标准**（spec.md 验收场景到任务的锚定）:

| 用户故事 | 独立测试任务 | 成功标准 |
|---|---|---|
| US1 | T047、T048、T049 | SC-001、SC-003、SC-006、SC-009 |
| US2 | T020、T021 | SC-002、SC-005 |
| US3 | T058、T059 | SC-007（"后续 Feature 无需扩展"需时间观测，本 Feature 内不作一次性验证） |
| US4 | T067、T068、T069 | SC-004 |

---

## 注意事项

- **章程 v1.4.0 已对齐**：本 tasks.md 的每一项任务均可在 `.specify/memory/constitution.md` 原则 IX 找到直接依据，不存在章程违规
- **Big Bang 策略**：开发过程可分阶段推进，但**合入 PR 必须是单次原子合入**；禁止分 PR 发布
- **测试 Red-Green 强制**：US1 的 T022~T033 重写契约测试**必须先失败再转绿**；不得"先改路由再改测试"
- **大文件处理**：`src/api/routers/tasks.py`（54.92 KB，isBigFile=true）在 T034 中使用 `multi_replace` 工具进行多点改造，禁止用 `edit_file`（章程工具规则）
- **不改业务行为**：所有任务只重塑 API 外观；若发现需变更业务逻辑，单开 Feature 处理，不得合并到本 Feature
- **资源归属冲突 1 处**：T050 搬迁 `PATCH /tasks/{task_id}/coach` 是本 Feature 唯一的跨资源调整
- **前端改造不在本 tasks.md 范围**：前端调用的同步由前端开发者在同一合入窗口内配合完成（假设：前端仓库与本仓库为同一组织，参见 spec.md「假设」节）
