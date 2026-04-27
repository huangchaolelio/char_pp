# 阶段 0 研究: API 统一规范化 — 现状盘点与决策验证

**日期**: 2026-04-27
**分支**: `017-api-standardization`
**目的**: 为 plan.md 中的全量改造提供"精确到端点级别"的事实依据；澄清决策（Big Bang、无兼容、404+ENDPOINT_RETIRED、success 信封）在当前代码库中的具体落地点。

## 1. 路由端点全量盘点

基于 `grep '@router.(get|post|patch|delete|put)(' src/api/routers/` 的实测结果（共 50 条装饰器匹配 + `videos.py` 之前 `@router.get('', ...)` 裸路径）。

### 1.1 保留路由（Big Bang 合入日切换到新信封）

| 文件 | 端点数 | 代表路径 | 信封改造点 |
|---|---|---|---|
| `tasks.py` **（大文件，54.92 KB）** | 15 | `/tasks`、`/tasks/cos-videos`、`/tasks/{task_id}`、`/tasks/{task_id}/result`、`/tasks/classification`、`/tasks/kb-extraction`、`/tasks/diagnosis`、`/tasks/kb-extraction/batch` | `response_model` 改泛型 + `raise HTTPException` 全部替换为 `raise AppException(code=...)` |
| `coaches.py` | 6 | `/coaches`、`/coaches/{coach_id}`、`/tasks/{task_id}/coach` | 同上；`/tasks/{task_id}/coach` 归属问题见 §2 |
| `classifications.py` | 5 | `/classifications`、`/classifications/scan`、`/classifications/{id}` | 同上 |
| `teaching_tips.py` | 4 | `/teaching-tips`、`/teaching-tips/{tip_id}`、`POST /teaching-tips`（合成） | 同上 |
| `videos.py`（4 个端点） | 4 | `/classifications`、`/classifications/refresh`、`/classifications/{cos_object_key:path}`、`/classifications/batch-submit` | **全 4 个端点下线**（见 §1.2）；文件改为纯哨兵 |
| `extraction_jobs.py` | 3 | `/extraction-jobs`、`/extraction-jobs/{job_id}`、`POST /extraction-jobs/{job_id}/rerun` | 同上 |
| `knowledge_base.py` | 3 | `/knowledge-base/versions`、`/knowledge-base/{version}`、`/knowledge-base/{version}/approve` | 同上 |
| `standards.py` | 3 | `/standards`、`/standards/{tech_category}`、`POST /standards/build` | 路径前缀不统一（`""` 裸路径），FR-011 强制修正 |
| `calibration.py` | 2 | `/calibration/tech-points`、`/calibration/teaching-tips` | 同上 |
| `admin.py` | 2 | `/admin/channels/{task_type}` 等 | 同上；注意认证逻辑内 `ADMIN_TOKEN_*` 错误码归类 |
| `task_channels.py` | 2 | `/task-channels/*` | 同上 |
| `diagnosis.py` | 1 | `POST /diagnosis` | **整个端点下线**（见 §1.2），文件改为单条哨兵 |
| `video_preprocessing.py` | 1 | `GET /video-preprocessing/{job_id}` | T004 盘点结果（见下）：装饰器采用裸路径形式，`APIRouter(tags=[...])` 无 `prefix`；`main.py::include_router(prefix="/api/v1")` 单点拼接资源段 |

#### T004 盘点结果（video_preprocessing.py）

- **文件**：`src/api/routers/video_preprocessing.py`（86 行，2.68 KB）
- **APIRouter 声明**：`router = APIRouter(tags=["video-preprocessing"])` — 不含 `prefix`
- **端点**：仅 1 个 — `@router.get("/video-preprocessing/{job_id}", response_model=PreprocessingJobResponse, ...)`
- **grep 正则未命中的原因**：先前 `grep '^@router\.' ` 虽命中该行（29 行），但路径段 `/video-preprocessing/...` 直接写在装饰器字符串首参，不符合"路由文件内 `APIRouter(prefix="/<resource>")` 只声明资源段"的章程 v1.4.0 约定
- **US1 改造点**（T044）：`response_model=PreprocessingJobResponse` → `SuccessEnvelope[PreprocessingJobResponse]`；`HTTPException(404, "video_preprocessing job not found")` → `AppException(ErrorCode.PREPROCESSING_JOB_NOT_FOUND)`（错误码在 data-model.md §2 中已登记 `PREPROCESSING_JOB_NOT_FOUND`，若缺失则补充）
- **US3 改造点**（T052/T053）：将 `APIRouter(tags=[...])` 改为 `APIRouter(prefix="/video-preprocessing", tags=[...])`，装饰器改为 `@router.get("/{job_id}", ...)`，由 `main.py::include_router(router, prefix="/api/v1")` 单点拼接

**合计**: ≈ 50 个装饰器端点；下线 5 个，保留 ≈ 45 个。

### 1.2 待下线端点（哨兵路由，返回 404+ENDPOINT_RETIRED）

完全对齐 spec.md FR-009 清单：

| 旧端点 | 替代路径（successor） | 位置 |
|---|---|---|
| `POST /api/v1/tasks/expert-video` | `POST /api/v1/tasks/classification` + `POST /api/v1/tasks/kb-extraction`（两步独立提交） | `tasks.py:260` |
| `POST /api/v1/tasks/athlete-video` | `POST /api/v1/tasks/diagnosis` | `tasks.py:400` |
| `GET /api/v1/videos/classifications` | `GET /api/v1/classifications` | `videos.py:105` |
| `POST /api/v1/videos/classifications/refresh` | `POST /api/v1/classifications/scan` | `videos.py:138` |
| `PATCH /api/v1/videos/classifications/{cos_object_key:path}` | `PATCH /api/v1/classifications/{id}` | `videos.py:214` |
| `POST /api/v1/videos/classifications/batch-submit` | `POST /api/v1/tasks/kb-extraction/batch` | `videos.py:267` |
| `POST /api/v1/diagnosis` | `POST /api/v1/tasks/diagnosis`（异步，需轮询 `GET /tasks/{task_id}`） | `diagnosis.py:78` |

**合计**: 7 条端点下线（spec.md 宏观写 6 条，精确到方法+路径后是 7 条；两处 `/videos/classifications` 通配符展开成 2 条）。

## 2. 资源归属冲突（Phase 1 需在 data-model.md 定版）

**冲突点**: `coaches.py` 第 164 行定义 `PATCH /api/v1/tasks/{task_id}/coach`，即"教练"路由文件里注册了"任务"资源的路径。违反宪法原则 IX「每个路由文件对应一个资源，禁止混搭不同资源」。

**决策**: 将 `PATCH /tasks/{task_id}/coach` 搬迁至 `tasks.py`；`coaches.py` 只保留 `/coaches/*` 端点。搬迁属路由层调整、无业务逻辑变更，作为本 Feature tasks.md 的一个 P2 任务落地。

## 3. 错误码现状清点（基于 `grep '"code":\s*"[A-Z_]+"'`）

当前散落在 6 个路由文件中的**裸字符串错误码**共 **24 个**（去重后）：

| 错误码 | HTTP 状态 | 使用路径 | 集中枚举后的归类 |
|---|---|---|---|
| `JOB_NOT_FOUND` | 404 | `extraction_jobs.py` | 资源不存在类 |
| `INVALID_STATUS` | 400 | `extraction_jobs.py` | 状态校验类 |
| `JOB_NOT_FAILED` | 400 | `extraction_jobs.py` | 状态校验类 |
| `INTERMEDIATE_EXPIRED` | 410 | `extraction_jobs.py` | 过期类 |
| `ADMIN_TOKEN_NOT_CONFIGURED` | 500 | `admin.py` | 配置错误类 |
| `ADMIN_TOKEN_INVALID` | 401 | `admin.py` | 认证类 |
| `INVALID_INPUT` | 400 | `admin.py`、`tasks.py` | 输入校验类（可并入 `VALIDATION_FAILED`） |
| `INVALID_ACTION_TYPE` | 400 | `tasks.py` | 状态校验类 |
| `COS_OBJECT_NOT_FOUND` | 404 | `tasks.py` | 资源不存在类 |
| `VIDEO_TOO_LONG` | 400 | `tasks.py` | 业务约束类 |
| `COACH_NOT_FOUND` | 404 | `tasks.py`、`coaches.py`、`teaching_tips.py` | 资源不存在类（**跨 3 文件复用，验证集中化价值**） |
| `COACH_INACTIVE` | 400 | `tasks.py`、`coaches.py` | 业务约束类 |
| `MISSING_VIDEO` | 400 | `tasks.py` | 输入校验类 |
| `UPLOAD_FAILED` | 500 | `tasks.py` | 上游失败类（可并入 `COS_UPSTREAM_FAILED`） |
| `TASK_NOT_FOUND` | 404 | `tasks.py`、`teaching_tips.py` | 资源不存在类 |
| `TASK_NOT_READY` | 409 | `tasks.py`、`teaching_tips.py` | 状态校验类 |
| `BATCH_TOO_LARGE` | 400 | `tasks.py` | 输入校验类 |
| `CLASSIFICATION_REQUIRED` | 400 | `tasks.py` | 业务约束类 |
| `COS_KEY_NOT_CLASSIFIED` | 400 | `tasks.py` | 业务约束类 |
| `CHANNEL_QUEUE_FULL` | 503 | `tasks.py` | 容量类 |
| `CHANNEL_DISABLED` | 503 | `tasks.py` | 容量类 |
| `KB_VERSION_NOT_FOUND` | 404 | `knowledge_base.py` | 资源不存在类 |
| `KB_VERSION_NOT_DRAFT` | 400 | `knowledge_base.py` | 状态校验类 |
| `CONFLICT_UNRESOLVED` | 409 | `knowledge_base.py` | 业务约束类 |
| `TIP_NOT_FOUND` | 404 | `teaching_tips.py` | 资源不存在类 |
| `WRONG_TASK_TYPE` | 400 | `teaching_tips.py` | 业务约束类 |
| `NO_AUDIO_TRANSCRIPT` | 400 | `teaching_tips.py` | 业务约束类 |
| `COACH_NAME_CONFLICT` | 409 | `coaches.py` | 冲突类 |
| `COACH_ALREADY_INACTIVE` | 409 | `coaches.py` | 状态校验类 |

**新增枚举值**（本 Feature 首次引入）：

| 错误码 | HTTP 状态 | 触发场景 |
|---|---|---|
| `ENDPOINT_RETIRED` | 404 | FR-006 哨兵路由返回 |
| `VALIDATION_FAILED` | 422 | FastAPI `RequestValidationError` 统一归类 |
| `INVALID_ENUM_VALUE` | 400 | FR-013 枚举参数非法值 |
| `INVALID_PAGE_SIZE` | 400 | `page_size > 100` 或 ≤ 0 |
| `INTERNAL_ERROR` | 500 | FR-017 未预期异常兜底 |
| `NOT_FOUND` | 404 | FastAPI 未匹配路由（非业务下线） |
| `LLM_UPSTREAM_FAILED` | 502 | FR-018 上游归类 |
| `COS_UPSTREAM_FAILED` | 502 | FR-018（替代 `UPLOAD_FAILED`） |
| `DB_UPSTREAM_FAILED` | 502 | FR-018 |
| `WHISPER_UPSTREAM_FAILED` | 502 | FR-018（替代 `NO_AUDIO_TRANSCRIPT` 中由 Whisper 失败触发的场景） |

**合计**: 枚举初版 **29 + 10 = 约 35 个**，与 plan.md 中估算的"25–30 个"略有上浮，属正常偏差。实际 tasks.md 以本研究表为权威清单。

## 4. 现状响应格式清点

**三种并存的成功响应形态**（spec.md 用户故事 1 指出）：
1. **裸对象**: `coaches.py` 的 `list[CoachResponse]`、`tasks.py` 的 `TaskListResponse`（FastAPI 的 `response_model` 直接返回业务字段）
2. **`{data,total}` 包装**: `tasks.py` 的 `GET /tasks` 响应模型是 `TaskListResponse = {"data": [...], "total": N}`
3. **自定义 envelope**: `tasks.py` 的 `POST /tasks/classification` 返回 `{"accepted":[...], "rejected":[...], "items":[...], "channel": {...}}`

**错误响应形态**：
1. `HTTPException(detail="字符串")`: FastAPI 默认形态
2. `HTTPException(detail={"code":"...","message":"...","details":{...}})`: 多数业务错误
3. `HTTPException(detail={"error":{"code":"...","message":"..."}})`: `tasks.py:1064` 等处的"错误外包一层 error"形态

改造后统一为: 成功 `{"success":true,"data":...,"meta":...}`、错误 `{"success":false,"error":{...}}`。**三种成功形态与三种错误形态全部归一**。

## 5. 澄清决策验证（在代码库中的可行性）

| 澄清项 | 可行性 | 关键依据 |
|---|---|---|
| Big Bang 一次性切换 | ✅ 可行 | 14 个路由文件均在本 repo 内、契约测试由 `tests/contract/` 全量覆盖、前端调用在本 repo 外但同一组织、无第三方 SDK |
| 无兼容（直接下线） | ✅ 可行 | `diagnosis.py`（同步版）与 `/tasks/diagnosis`（异步版）在代码中**完全独立实现**，下线旧版不影响新版；`expert-video`/`athlete-video` 的 Celery 任务路由也独立于新接口 |
| 404 + ENDPOINT_RETIRED | ✅ 可行 | FastAPI 支持对路径保留方法+路径定义并在 handler 抛异常，全局异常处理器捕获后返回 404 + 统一信封；测试通过 `TestClient` 直接验证 |
| success 布尔位信封 | ✅ 可行 | Pydantic v2 `Generic[T]` 原生支持；`/openapi.json` 可正确展开泛型；但泛型在 Pydantic v2 与 FastAPI 结合时需 `Generic[DataT], BaseModel` 多继承 + `model_config = ConfigDict(arbitrary_types_allowed=False)` 定型（Phase 1 quickstart.md 给出示例） |

## 6. 章程冲突的确认

本研究阶段再次确认：章程 v1.3.1 原则 IX 明文「响应体统一包装：(a) 单实体直接返回；(b) 分页 `{data, total}`」与本 Feature 的「顶层 `success` 布尔位 + 互斥字段」直接冲突。**plan.md 章程检查节点已登记并提出处置方案 A（先修订章程到 v1.4.0）**。研究阶段未发现其他隐蔽冲突。

## 7. 对 Phase 1 的输入

本研究明确了：
- 精确到行号的下线端点清单（§1.2）→ 写入 `contracts/retirement-ledger.md`
- 集中错误码枚举的初版 35 个值（§3）→ 写入 `contracts/error-codes.md`
- ResponseEnvelope 泛型 Pydantic 模型的必要性（§5）→ 写入 `data-model.md`
- 资源归属冲突 1 处（§2）→ 作为 tasks.md 子任务
- `video_preprocessing.py` 路由注册形态未确认 1 处 → 作为 tasks.md 子任务
