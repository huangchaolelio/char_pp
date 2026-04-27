# 功能规范: API 接口统一规范化与遗留接口下线

**功能分支**: `017-api-standardization`
**创建时间**: 2026-04-27
**状态**: 草稿
**输入**: 用户描述: "对当前的API接口优化统一规范化，接口返回格式要统一，旧的重复接口直接下线掉"

## 澄清

### 会话 2026-04-27

- Q: 信封改造的发布策略？ → **A: Big Bang 一次性切换**——所有保留接口的 response_model、契约测试、前端调用、错误信封在单个合入窗口内原子切换，不采用分批滚动/双写兼容；旧接口同日下线。
- Q: 旧接口下线时是否保留兼容层、废弃期、迁移指南阻塞？ → **不考虑兼容历史，旧接口直接下线**。不设废弃期、不设双写、不设 `Deprecation`/`Sunset` 响应头、不因"新接口非 1:1 平移"而阻塞下线；调用方自行按新接口改造。
- Q: 已下线接口的 HTTP 状态码？ → **A: 404 Not Found + 响应体 `error.code=ENDPOINT_RETIRED`**，在 `error.details.successor` 字段给出替代路径；与 FastAPI 未匹配路由的默认 404 区分靠响应体里的错误码，而非物理删除路由。
- Q: 成功/错误统一信封的最终字段形态？ → **B: 顶层带 `success` 布尔位**。成功：`{"success": true, "data": <业务>, "meta": {...}|null}`；错误：`{"success": false, "error": {"code": "...", "message": "...", "details": {...}|null}}`。前端以 `body.success` 分流，字段互斥不共存。

## 用户场景与测试 *(必填)*

<!--
  API 规范化面向内部开发者/前端/SDK 使用者/运维四类"用户"。
  下述故事按对系统一致性与稳定性的价值排序。
-->

### 用户故事 1 - 统一响应信封让前端 / SDK 不再做"按接口猜格式"的适配 (优先级: P1)

当前前端调用不同接口时，需要同时处理至少三种返回形态：裸对象（如 `/coaches`）、列表 + total 包裹（如 `/tasks`）、自定义 envelope（如 `/tasks/classification` 的 `accepted/rejected/items/channel`），错误又散落在 `{"detail": ...}` 和 `{"error": {"code", "message", "details"}}` 两种结构之间。前端开发者希望所有 `/api/v1/**` 接口在成功与失败两条路径上都使用统一、稳定、可预测的响应信封，使一次编写的错误/分页/元信息处理逻辑能覆盖全部接口。

**优先级原因**: 响应格式是所有调用方最先感知的契约，不统一会直接放大每一个下游 bug 的修复成本，是本 Feature 的"地基"。

**独立测试**: 挑选三组接口（单资源 GET、列表分页 GET、异步任务 POST），用同一份通用响应解析器逐个调用即可完成验证——解析器无需为任何接口做特殊分支。

**验收场景**:

1. **给定** 任一 `GET /api/v1/**` 成功返回，**当** 调用方用统一解析器读取时，**那么** 能稳定取到业务数据字段（列表 / 对象 / 空对象）以及可选的元数据（分页、计数），无需针对某个接口做 if-else 分支。
2. **给定** 任一接口触发业务错误（400 / 404 / 409 / 422），**当** 调用方读取响应体时，**那么** 能从同一字段读取到错误码（稳定字符串枚举）、可读错误消息、可选上下文详情三要素。
3. **给定** 任一分页列表接口，**当** 调用方使用 `page` / `page_size` 查询时，**那么** 响应 `meta` 字段中总是能取到 `total`、`page`、`page_size` 三个分页元信息字段，且字段名跨接口一致。

---

### 用户故事 2 - 下线遗留重复接口，只保留单一权威入口 (优先级: P1)

系统历经 16 个 Feature 迭代，同一能力出现了多个路径并存的情况：`/api/v1/tasks/expert-video` / `/api/v1/tasks/athlete-video`（Feature-001 旧接口）与 `/api/v1/tasks/classification`、`/api/v1/tasks/kb-extraction`、`/api/v1/tasks/diagnosis`（Feature-013 新接口）功能重叠；`/api/v1/videos/classifications*` 与 `/api/v1/classifications*` 双轨并存；`/api/v1/videos/classifications/batch-submit` 与 `/api/v1/tasks/kb-extraction/batch` 重复；`/api/v1/diagnosis`（同步）与 `/api/v1/tasks/diagnosis`（异步）重复。API 使用者希望每项能力只暴露**一个**权威入口，避免"不知道该调哪个"、"调了旧接口踩到旧返回格式"的困扰。

**优先级原因**: 重复入口是后续规范化工作的绊脚石——若新旧并存，响应信封统一就必须同时适配两套契约，工作量翻倍且语义割裂。必须与 P1 响应信封统一同步完成。

**独立测试**: 用脚本枚举全部 `/api/v1/**` 路由并按资源分组，每组只保留**一个**具备该能力的权威路径；对已下线路径发起请求，必须全部返回下线约定的状态（404 + `ENDPOINT_RETIRED`），不再路由到旧处理器。

**验收场景**:

1. **给定** 新接口 `/api/v1/tasks/classification`、`/api/v1/tasks/kb-extraction`、`/api/v1/tasks/diagnosis` 已具备 Feature-013/014 的全部能力，**当** 下线 `/api/v1/tasks/expert-video`、`/api/v1/tasks/athlete-video` 后调用旧路径，**那么** 请求直接返回 404 + 错误信封 `error.code=ENDPOINT_RETIRED`，不再进入旧处理器逻辑。
2. **给定** `/api/v1/classifications` 作为 COS 分类的权威资源（Feature-008），**当** 下线 `/api/v1/videos/classifications`、`/api/v1/videos/classifications/{cos_object_key}`、`/api/v1/videos/classifications/batch-submit` 后调用旧路径，**那么** 返回 404 + `ENDPOINT_RETIRED`，且 `error.details.successor` 指向对应新路径。
3. **给定** `/api/v1/tasks/diagnosis` 提供异步诊断能力，**当** 下线 `/api/v1/diagnosis`（同步 60s 版本）后，**那么** 诊断能力仅通过异步任务通道暴露，调用方通过 `GET /api/v1/tasks/{task_id}` 轮询结果；同步与异步的时序差异由调用方自行承担，不提供兼容薄层。
4. **给定** 本 Feature 合入窗口（单个合入日），**当** 合入完成时，**那么** 保留接口全部原子切换到新信封，下线接口同日起返回 404 + `ENDPOINT_RETIRED`，不存在"两种格式并存"的中间状态。

---

### 用户故事 3 - 资源路径、命名与分页参数跨接口一致 (优先级: P2)

当前路径命名与参数命名跨模块不一致：有的用连字符（`/knowledge-base`、`/teaching-tips`），有的用下划线或混写；有的前缀通过 `APIRouter(prefix=...)` 定义，有的在每个装饰器里重复写死；分页参数有用 `page/page_size`、有用 `limit/offset`、有裸列表不分页；枚举值（如 `tech_category`）在查询参数里有时大小写敏感、有时不。API 使用者希望所有资源路径、参数命名、大小写规则、枚举取值形成一套**跨模块一致**的约定，看到任一接口就能推断相邻接口的形态。

**优先级原因**: 命名与参数约定一旦随 P1、P2 固化，后续新增 Feature 就能按约定复用；反之若 P1、P2 完成但命名仍不一致，规范化仍是"半成品"。排在 P2 因它依赖 P1 先确定响应信封。

**独立测试**: 编写一份 API 命名约定 lint 规则（资源路径为 kebab-case 复数名词、分页统一 `page`/`page_size`、枚举小写下划线），对所有保留路由跑一遍，应 0 违规。

**验收场景**:

1. **给定** 所有保留路由，**当** 运行命名约定检查时，**那么** 资源段均为 kebab-case 复数名词、ID 段统一使用 `{resource_id}` 形式、动作型子路径（如 `/refresh`、`/approve`）均为动词性 kebab-case。
2. **给定** 全部列表型接口，**当** 调用方用 `?page=N&page_size=M` 查询时，**那么** 所有接口均接受并返回与 `page` / `page_size` 一致的分页字段；原先使用 `limit/offset` 的接口在本 Feature 合入窗口内一次性切换为 `page/page_size`，不保留兼容参数。
3. **给定** 全部接收枚举值的查询参数（如 `tech_category`、`status`、`task_type`），**当** 用任意大小写输入时，**那么** 服务端统一按小写下划线规范化并匹配，非法枚举返回统一错误码 `INVALID_ENUM_VALUE`。

---

### 用户故事 4 - 错误码枚举集中化，异常到 HTTP 状态映射统一 (优先级: P2)

服务层当前存在三种错误表达：直接抛 `ValueError`（被路由统一转 400）、抛自定义异常（`NotFoundException` 转 404）、在路由里手动构造 `HTTPException(detail={"error": {"code": ..., "message": ..., "details": ...}})`。错误码（如 `CLASSIFICATION_REQUIRED`、`BATCH_TOO_LARGE`、`COS_KEY_NOT_CLASSIFIED`、`CHANNEL_QUEUE_FULL`）散落在多个路由文件里，既无集中枚举也没文档。API 使用者希望有一份**集中的错误码清单**以及一套"服务层异常 → HTTP 状态 + 错误码"的统一映射。

**优先级原因**: 错误语义统一显著提升调用方的容错代码质量，但它可以在 P1 响应信封落地后再做内部映射，不阻塞最小闭环。

**独立测试**: 将所有错误码收集到单一枚举来源，运行测试覆盖每个枚举值至少有一条路由会返回它；调用方 SDK 只需导入该枚举即可识别所有业务错误。

**验收场景**:

1. **给定** 规范化后的错误码集中枚举，**当** 任一接口在业务错误路径上返回时，**那么** 错误码来自该枚举，不再出现"只在一个文件里字符串字面量硬编码"的情况。
2. **给定** 未预期异常发生，**当** 请求返回 500 时，**那么** 响应信封仍符合 P1 的统一错误格式，错误码为稳定 `INTERNAL_ERROR`，且服务端日志带 traceback。
3. **给定** 新接口开发者未使用集中枚举，**当** 通过 CI / 测试扫描时，**那么** 能检测出裸字符串错误码并阻断合入。

---

### 边界情况

- 调用方调用已下线路径时：立即返回 404 + 统一错误信封，`error.code=ENDPOINT_RETIRED`，`error.details.successor` 指向替代路径，避免静默失败；不提供任何形式的兼容转发。
- 旧接口与新接口行为在细节上不完全等价（例如 `/api/v1/diagnosis` 同步 60s 返回完整结果 vs. `/api/v1/tasks/diagnosis` 异步返回 task_id）：按澄清决策**直接下线**，不提供兼容薄层、不阻塞下线；语义差异在本规范的 FR-009 清单中作为备注保留，由调用方按新接口改造。
- 统一响应信封改造改动 `response_model` 后，现有集成测试（`tests/integration/`、`tests/contract/`）会批量失败：按 Big Bang 策略，在同一合入窗口内先更新契约测试再切换路由实现，合入前必须全绿。
- 部分接口返回非 JSON 数据（如任务结果下载、视频文件 URL）或返回二进制：这些应列为"明确豁免"，豁免清单进入规范本身而非隐式处理。
- 某些错误来自第三方依赖（LLM、COS、Whisper）：映射到统一错误码时应保留上游错误类别（`LLM_UPSTREAM_FAILED`、`COS_UPSTREAM_FAILED`），不要吞掉根因。
- 分页参数 `page_size` 上限如何执行：超过 100 时是截断到 100 还是返回 `INVALID_PAGE_SIZE`——须在规范中定稿为单一行为。

## 需求 *(必填)*

### 功能需求

**响应格式统一（US1）**

- **FR-001**: 所有 `/api/v1/**` 接口的成功响应必须采用统一信封：顶层字段 `success: true`、`data: <业务载荷>`（单对象 / 列表 / 空对象 / `null`）、`meta: {page, page_size, total} | null`（仅列表场景非空）；字段命名跨所有资源保持一致。
- **FR-002**: 所有 `/api/v1/**` 接口的错误响应必须采用统一信封：顶层字段 `success: false`、`error: {code, message, details}`，其中 `code` 为稳定字符串枚举值（来自 FR-015 的集中枚举）、`message` 为面向开发者的可读消息、`details` 为 `object | null`（可选上下文，如非法参数取值、上游错误摘要、替代路径等）。成功/错误字段互斥：成功响应不得出现 `error`，错误响应不得出现 `data` / `meta`。
- **FR-003**: 系统必须保证列表型响应始终提供 `meta.page`、`meta.page_size`、`meta.total` 三项分页元信息，即使该接口仅支持全量返回（此时 `meta.page=1`、`meta.page_size=meta.total`）。
- **FR-004**: 系统必须对"非 JSON 返回"（文件下载、重定向等）维护一份豁免清单并在规范中显式记录，不强行套用 JSON 信封。

**遗留接口下线（US2）**

- **FR-005**: 系统必须维护一份"权威接口 vs 已下线接口"的对照表（RetirementLedger），明确列出每个被下线接口的替代路径与语义差异备注；该对照表同时作为下线 404 响应中 `error.details.successor` 字段的数据来源。
- **FR-006**: 系统必须对已下线的接口返回 **HTTP 404** + FR-002 统一错误信封，`error.code=ENDPOINT_RETIRED`，`error.details.successor` 给出替代路径；物理路由保留为"哨兵路由"仅用于返回该错误，不得进入旧处理器逻辑。
- **FR-007**: 系统**不设废弃宽限期**。合入日即下线日：保留接口切换到新信封的同一合入窗口内，所有待下线接口立即返回 `ENDPOINT_RETIRED`，不发布 `Deprecation` / `Sunset` / `Link: successor-version` 等废弃响应头，不提供双写/兼容薄层。
- **FR-008**: 本 Feature 采用 Big Bang 合入策略：新信封改造、新错误码枚举、旧接口下线、契约测试更新、前端调用更新必须在**单个合入窗口**内原子完成；合入前需在 PR 内完成全量契约测试绿灯验证，但不作为"逐接口阻塞"的门槛——即调用模式非 1:1 平移的接口（如同步→异步、单步→两步）也在本次合入中直接下线，不延后。
- **FR-009**: 待下线接口清单（本 Feature 一次性下线，无遗留）必须至少包含：
  - `POST /api/v1/tasks/expert-video` → 替代 `POST /api/v1/tasks/classification` + `POST /api/v1/tasks/kb-extraction`（调用方需改为两次独立提交，自行串联）
  - `POST /api/v1/tasks/athlete-video` → 替代 `POST /api/v1/tasks/diagnosis`
  - `GET/POST/PATCH /api/v1/videos/classifications*` → 替代 `/api/v1/classifications*`
  - `POST /api/v1/videos/classifications/batch-submit` → 替代 `POST /api/v1/tasks/kb-extraction/batch`
  - `POST /api/v1/videos/classifications/refresh` → 替代 `POST /api/v1/classifications/scan`
  - `POST /api/v1/diagnosis`（同步） → 替代 `POST /api/v1/tasks/diagnosis`（异步，调用方需改为提交任务 + 轮询 `GET /api/v1/tasks/{task_id}` 获取结果）

**路径与参数一致性（US3）**

- **FR-010**: 所有保留路由的资源段必须使用 kebab-case 复数名词；动词型子路径使用 kebab-case 动词。
- **FR-011**: 所有路由前缀必须由 `APIRouter(prefix="/api/v1/<resource>")` 单点定义，禁止在装饰器路径里重复前缀拼接。
- **FR-012**: 所有列表接口统一使用 `page`（从 1 开始，默认 1）与 `page_size`（默认 20，最大 100）两个查询参数；原先使用 `limit` / `offset` 的接口在本 Feature 合入窗口内直接切换，不保留兼容参数。
- **FR-013**: 所有枚举型查询参数在服务端统一按小写下划线规范化；非法值返回统一错误码 `INVALID_ENUM_VALUE`，`details` 含合法取值列表。
- **FR-014**: 所有资源 ID 路径段统一使用 `{resource_id}` 形式（如 `{task_id}`、`{coach_id}`）；禁止无命名的 `{id}`。

**错误语义统一（US4）**

- **FR-015**: 系统必须提供一个集中的错误码枚举来源（单一定义文件），所有路由与服务层引用该枚举，禁止在业务代码中用字符串字面量表达错误码。
- **FR-016**: 系统必须建立"服务层异常 → HTTP 状态 + 错误码"的统一映射层，路由层不再手写 `HTTPException(detail={"error": ...})`；映射层是异常到响应信封的唯一出口。
- **FR-017**: 系统必须对未预期异常（非业务异常）统一返回 500 + 错误码 `INTERNAL_ERROR`，服务端日志记录 traceback；响应体不得泄露栈信息。
- **FR-018**: 系统必须对来自上游依赖的错误统一归类为 `LLM_UPSTREAM_FAILED`、`COS_UPSTREAM_FAILED`、`DB_UPSTREAM_FAILED`、`WHISPER_UPSTREAM_FAILED` 等稳定错误码，保留上游错误摘要在 `details`。

**文档与契约同步**

- **FR-019**: OpenAPI 文档（FastAPI 自动生成的 `/docs`、`/openapi.json`）必须反映新的统一信封、路径、错误码，与代码保持单一数据源一致。
- **FR-020**: 所有路由的契约测试（`tests/contract/`）必须覆盖成功信封、错误信封、分页字段、下线响应四类断言，新增接口默认继承。
- **FR-021**: `docs/` 下必须产出一份 API 规范化指南，列出信封结构、错误码清单、Big Bang 下线流程与 RetirementLedger 台账，后续新增 Feature 遵循该文档。

### 关键实体 *(如果功能涉及数据则包含)*

- **RetirementLedger（下线接口台账）**: 每条记录表示一个已下线接口的元数据，属性包括"旧路径"、"旧方法"、"替代路径（successor）"、"语义差异说明"、"合入下线日"；同时作为 FR-006 哨兵路由返回 `error.details.successor` 字段的数据来源，可用 Markdown 表 + 服务端常量字典双向同步维护。
- **ResponseEnvelope（统一响应信封）**: 成功/错误互斥的顶层结构。成功形态 `{"success": true, "data": <业务载荷>, "meta": {"page": int, "page_size": int, "total": int} | null}`；错误形态 `{"success": false, "error": {"code": str, "message": str, "details": object | null}}`。前端以 `body.success` 字段做分流判断，不再基于 HTTP 状态码或字段存在性猜测。
- **错误码枚举（ErrorCode）**: 跨路由共享的稳定字符串枚举，属性包括"错误码常量名"、"默认消息"、"对应 HTTP 状态"；它是代码与文档的单一事实来源。

## 成功标准 *(必填)*

### 可衡量的结果

- **SC-001**: 前端 / SDK 调用任何 `/api/v1/**` 接口时，可使用同一通用响应解析函数（不超过 30 行代码）完成响应取值，以 `body.success` 分流，无需按接口写特判分支。
- **SC-002**: 下线首批至少 6 条重复接口（见 FR-009 清单）后，`/api/v1/**` 下同一能力的权威入口数量等于 1，即枚举所有路由并按能力分组时，每组仅保留一条。
- **SC-003**: 全量契约测试套件中，有 100% 的列表接口测试断言包含 `meta.total`、`meta.page`、`meta.page_size` 三个字段且字段名一致。
- **SC-004**: 全量错误码集中枚举定义完成后，代码扫描检出的"裸字符串错误码"数量为 0。
- **SC-005**: 合入日起调用任一已下线接口，100% 返回 HTTP 404 + `{"success": false, "error": {"code": "ENDPOINT_RETIRED", ...}}`，且 `error.details.successor` 字段可被调用方自动跳转使用。
- **SC-006**: 合入日后，保留接口与下线接口的响应形态不存在中间态：不会出现"部分接口用旧格式、部分用新格式"的并存窗口；用 HTTP 客户端批量抓取全量保留路由可验证 100% 顶层含 `success` 布尔位。
- **SC-007**: 新规范落地后 30 天内，新增 Feature（Feature-018+）无需对自定义响应信封做任何扩展即可直接复用；若出现扩展需求，计为规范不完备，需回头迭代。
- **SC-008**: `docs/` 下输出的 API 规范化指南能在 10 分钟内让新成员独立完成一个"创建资源 + 返回统一信封"的示例路由，且新示例开箱通过契约测试。
- **SC-009**: OpenAPI 文档中，所有保留接口的 `responses` 定义均引用统一信封 schema（成功 + 错误），引用率为 100%。

## 假设

- **发布策略为 Big Bang 一次性切换**: 所有保留接口的 response_model、契约测试、前端调用、错误信封在单个合入窗口内原子切换到新信封；同一窗口内旧接口改为返回 `ENDPOINT_RETIRED`。不采用分批滚动、不采用双写兼容、不保留任何过渡格式。回滚粒度为整 PR 级别。
- **不考虑历史兼容**: 不提供任何废弃宽限期、`Deprecation` / `Sunset` 响应头、双写层、兼容转发路由；调用方按新接口一次性改造完成。同步→异步、单步→两步等调用模式差异由调用方承担改造成本。
- **下线方式固定为 HTTP 404 + `ENDPOINT_RETIRED` 错误码**: 使用 404 而非 410，理由是保持与路由未匹配行为一致、降低前端错误处理复杂度；但响应体中的 `error.code=ENDPOINT_RETIRED` 与常规 `NOT_FOUND` 区分，并在 `error.details.successor` 给出替代路径。实现方式是保留"哨兵路由"（方法+路径不变，处理器改为抛 `ENDPOINT_RETIRED`），不物理删除路由文件条目以便保持可审计性。
- **统一信封形态为"顶层 `success` 布尔位 + 互斥字段"**: 成功 `{success:true,data,meta}` / 错误 `{success:false,error}`，成功响应不得出现 `error`，错误响应不得出现 `data`/`meta`；前端解析统一以 `body.success` 分流。
- **`page_size` 上限默认 100，超过则返回 `INVALID_PAGE_SIZE` 而非截断**: 与项目规则中"最大 100"保持一致；明确行为避免"调了 1000 但只返回 100"的静默语义丢失。
- **非 JSON 豁免清单**: 文件下载类接口（若存在）与静态资源视为豁免；当前系统范围内经盘点暂无硬豁免，若后续新增需在规范指南中显式记录。
- **向下游调用方的通知义务**: 前端团队、运维脚本、集成测试由本仓库同一 repo 内维护，因此本 Feature 在 Big Bang 合入窗口内同步修改它们全部；不存在外部 SDK / 第三方调用方。
- **不改变业务行为**: 本 Feature 仅重塑 API 外观（路径、信封、错误码、参数命名），不变更任何业务逻辑、数据库 schema、异步任务管道。若发现两接口语义本质不同（如同步 vs 异步诊断），按澄清决策直接下线旧接口，调用方改用新接口。
- **认证与权限不在本次规范化范围**: 当前系统未显式要求认证改造，假设维持现状；若后续引入统一鉴权，单开 Feature 处理。
- **任务结果返回结构（`GET /tasks/{task_id}/result`）的内部业务字段保持不变**: 仅把外层信封替换为统一结构（`{success:true, data: <原有业务对象>, meta:null}`），内部业务字段命名、嵌套层级、类型全部不变；前端只需替换外层解析逻辑。
- **现有的 `APIRouter(prefix=...)` 不一致（有的设前缀、有的装饰器写死）**: 本 Feature 统一改为"路由文件内只设资源前缀，版本前缀 `/api/v1` 在 `app.include_router` 处拼接"的单一模式。

