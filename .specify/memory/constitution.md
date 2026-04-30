<!--
同步影响报告
============

版本更改: 1.5.0 → 2.0.0

递增理由: MAJOR — 移除原则 IX 中既有强制规则「已下线接口台账（哨兵路由
  ENDPOINT_RETIRED + 双份台账）」，改为显式允许「直接物理删除下线代码、
  测试与契约」，追求简洁性与 YAGNI（原则 IV）对齐。撤销的是既有强制
  治理约束而非澄清或扩展，属于向后不兼容的治理层变更 → MAJOR 递增。

  设计权衡：
  - 代价：丢失旧客户端"友好替代路径提示"与"台账可追溯"两项能力；
    迁移期客户端调用老路径会收到 FastAPI 默认 404 而非专属 ENDPOINT_RETIRED
  - 收益：消除"下线接口仍占用路由 + 文档 + 测试 3 份重复维护成本"的持续
    漂移风险；下线 = 删除，规则单一
  - 兼容缓解：重大接口下线仍 SHOULD 在 spec.md「业务阶段映射」小段或
    Feature changelog 中记录前后迁移说明，但不再强制代码侧留痕

修改的原则:
  - 原则 IX「API接口规范统一」：删除「已下线接口台账」子段；映射表中
    「已下线接口」一行随之删除；错误码集中化条款的「只允许新增」约束
    保留不变

新增条款:
  - 无

删除的部分:
  - 原则 IX「已下线接口台账」子段（哨兵路由 + `_retired.py` + retirement-ledger.md
    双份台账强制要求）
  - 原则 IX 错误响应映射表「已下线接口 → 404 + ENDPOINT_RETIRED」行
  - 开发工作流「章程检查」(d) 中「下线接口保留 ENDPOINT_RETIRED 哨兵路由」条目

模板同步状态:
  ✅ .specify/templates/plan-template.md — 章程检查 API 规范验证要点中
     「已下线接口」条目已删除
  ✅ .specify/templates/spec-template.md — 无需修改（未引用下线机制）
  ✅ .specify/templates/tasks-template.md — 无需修改
  ✅ .codebuddy/commands/speckit.*.md — 均无需修改（无 ENDPOINT_RETIRED 引用）
  ✅ .codebuddy/rules/api.md — 「已下线接口哨兵路由」章节已删除
  ✅ docs/api-standardization-guide.md — 相关章节已删除
  ✅ docs/architecture.md — 路由模块表/错误码表中哨兵相关条目已清理
  ✅ docs/features.md — Feature-017 US2 的哨兵相关表述已删除
  ✅ docs/business-workflow.md — 无需修改（不涉及下线机制）

代码同步状态:
  ✅ src/api/routers/_retired.py — 文件整体删除
  ✅ src/api/main.py — build_retired_router 挂载代码已删除
  ✅ src/api/routers/knowledge_base.py — 老路径 ENDPOINT_RETIRED 哨兵
     处理器已删除
  ✅ src/api/errors.py — ENDPOINT_RETIRED 枚举 / 状态映射 / 默认消息
     三处登记已删除
  ✅ src/api/schemas/envelope.py — RetiredDetails 专用模型已删除

测试同步状态:
  ✅ tests/contract/test_retirement_contract.py — 文件整体删除
  ✅ tests/unit/api/test_errors.py — test_endpoint_retired_maps_to_404 已删除
  ✅ tests/unit/api/test_envelope.py — test_retired_details_* 三个用例已删除
  ✅ tests/contract/test_kb_per_category.py —
     test_approve_legacy_single_key_endpoint_retired 用例已删除

规范文档同步状态:
  ✅ specs/017-api-standardization/contracts/retirement-ledger.md — 文件删除
  ✅ specs/019-kb-per-category-lifecycle/contracts/retirement-ledger.md — 文件删除

延迟项:
  TODO(RATIFICATION_DATE): 确认本章程的原始采用日期；当前保留 2026-04-17。
-->

# 乒乓球AI智能教练系统 项目章程

## 核心原则

### I. 规范驱动开发

所有功能开发 MUST 从功能规范(`spec.md`)开始，在编写任何实现代码之前。
规范 MUST 包含：优先排序的用户故事、可衡量的成功标准以及明确的假设。
无规范的功能不得进入实现阶段——"先编码后记录"的方式被明确禁止。
每个功能 MUST 使用独立的功能分支(`###-feature-name` 格式)进行隔离开发。
规范中的用户故事 MUST 以后端服务/算法能力为中心表述，不得将前端交互作为验收前提。

### II. 测试优先(不可协商)

当功能规范要求测试时，TDD 循环 MUST 严格执行：编写测试 → 确认测试失败 → 实现代码 → 测试通过。
测试 MUST 在对应实现任务之前创建，且在实现开始前 MUST 处于失败状态。
需要合约测试(`tests/contract/`)的场景：新 API 接口、接口变更、AI 模型推理接口变更。
需要集成测试(`tests/integration/`)的场景：后端服务端到端流程、视频分析流水线、算法链路验证。
单元测试(`tests/unit/`)覆盖单一组件逻辑，包括动作识别算法、姿态估计模块和评分模型的单元验证。
AI 模型评估测试 MUST 包含基准数据集验证，以防止模型退化；此类测试不可省略。
算法精准度测试 MUST 量化输出误差边界（如关键点定位误差 `≤ N px`、分类准确率 `≥ X%`），
并作为 CI 质量门控的一部分——精准度低于基准的 PR 不得合并。
禁止为规范未要求的场景添加测试——测试仅在功能规范中明确要求时才包含。

### III. 增量交付

每个用户故事 MUST 能够独立实现、独立测试并独立演示，无需依赖其他未完成的故事。
P1 用户故事 MUST 构成可交付的 MVP；更高优先级编号的故事 SHOULD 在 P1 完成并验证后再开始。
实现任务 MUST 按阶段组织：设置 → 基础(阻塞前置) → 用户故事(按优先级) → 完善。
每个用户故事阶段完成后 MUST 执行独立验证，不允许跨越检查点继续推进。
AI 模型集成 MUST 先以 mock/stub 替代实现用户故事业务逻辑，再替换为真实模型推理——禁止将
模型可用性作为用户故事延迟交付的理由。
后端算法的验收演示 MUST 通过 API 调用或命令行工具完成，不依赖任何前端界面。

### IV. 简洁性与 YAGNI

实现 MUST 从满足当前规范的最简设计开始；不为假设的未来需求添加抽象或配置。
复杂性违规(如超出规范要求的层次、模式或依赖)MUST 在 `plan.md` 的复杂度跟踪表中说明理由。
禁止添加规范未要求的功能、错误处理场景或配置项——"以防万一"不构成充分理由。
重构 MUST 局限于当前任务范围；不在功能实现过程中清理无关代码。
AI 模型选型 MUST 优先选择满足精度要求的最轻量模型；引入大模型 MUST 在复杂度跟踪表中
提供精度提升与资源开销的权衡依据——精度不达标是引入更复杂模型的唯一正当理由。
禁止为前端集成、UI 适配或展示层需求编写任何后端代码；后端接口设计以算法正确性为首要目标。
接口下线 MUST 采用直接物理删除（路由代码 + 合约测试 + 契约文件一并删除），不保留哨兵路由、
台账文件或占位符——"以防旧客户端调用"不构成保留死代码的正当理由（客户端迁移在
Feature changelog 声明即可）。

### V. 可观测性与可调试性

所有服务和功能 MUST 包含结构化日志，覆盖关键操作路径和错误场景。
错误 MUST 通过结构化错误响应返回(API 场景)或 stderr(CLI/脚本场景)；禁止静默失败。
调试信息 MUST 不依赖外部工具即可获取——日志、错误消息和输出格式 MUST 对人类可读。
AI 推理结果 MUST 记录输入特征摘要、模型版本和置信度分数，以支持问题复现和模型调试。
算法中间结果(如关键点坐标、特征向量、分类概率)MUST 在 DEBUG 日志级别可输出，
以支持精准度问题的根因分析。
可观测性代码 MUST 与业务逻辑同步实现，不得作为事后补充的"完善"任务延迟处理。

### VI. AI 模型治理与可解释性

所有投入使用的 AI 模型 MUST 具有明确的版本标识，并在 `docs/models/` 或等效位置登记：
模型名称、版本、训练数据描述、精度指标、推理延迟基准。
模型更新 MUST 经过回归测试，与前一版本对比关键精度指标，不允许在精度回退的情况下上线。
向调用方返回的 AI 教练反馈 MUST 包含可解释的结构化依据字段（如动作类型、偏差维度、
置信度），禁止仅返回不可解释的标量评分。
模型推理 MUST 设置超时与降级策略——当推理超时或失败时，MUST 返回明确的错误码与降级说明，
而非静默丢弃结果。

### VII. 运动数据隐私与安全

用户运动视频、姿态数据和训练记录属于敏感个人数据，MUST 在存储和传输中加密处理。
用户数据的采集 MUST 在获得明确同意后方可进行；同意状态 MUST 可撤销且持久化记录。
训练数据和用户个人数据 MUST 严格隔离：用于模型训练的数据 MUST 经过匿名化或脱敏处理。
数据保留策略 MUST 在规范阶段明确定义；未在规范中说明保留期限的数据字段 MUST 标注为
`NEEDS CLARIFICATION`，不允许以"无限期"作为默认值。

### VIII. 后端算法精准度(不可妥协)

每个算法功能规范 MUST 在 `spec.md` 的成功标准中定义量化精准度指标，例如：
关键点检测误差上限、动作分类准确率下限、评分一致性(与人工标注的 Cohen's Kappa 等)。
未定义量化精准度指标的算法功能规范视为不完整，不得进入实现阶段。
精准度基准 MUST 在首次实现时通过基准测试建立，并持久化保存至 `docs/benchmarks/` 或
等效位置；后续所有模型迭代 MUST 与该基准对比。
算法优化迭代 MUST 优先提升精准度，其次才考虑推理速度；在精准度与速度存在权衡时，
MUST 在 `plan.md` 复杂度跟踪表中记录决策依据，不允许以"速度够用"为由接受精准度下降。
所有算法的输入数据质量假设(如帧率、分辨率、光照条件)MUST 在规范中显式声明；
在不满足输入质量假设时，算法 MUST 返回明确的质量不足错误，而非静默输出低质量结果。

### IX. API接口规范统一

所有 HTTP 接口 MUST 遵循统一的规范格式，确保系统内外部接口风格一致、可预测、可测试。

**版本前缀**: 所有接口 MUST 统一使用 `/api/v1/` 前缀；禁止使用 `/v1/`、`/api/`、
无版本号或其他自定义前缀。

**路由组织**: 每个路由文件 MUST 对应且仅对应一个资源（例如 `coaches.py`、`videos.py`、
`tasks.py`、`classifications.py`、`knowledge_base.py`、`standards.py`、`diagnosis.py`、
`teaching_tips.py`、`calibration.py`、`extraction_jobs.py` 等）；禁止在单个路由文件
中混搭不同资源的接口。新增资源 MUST 创建独立路由文件。

**分页参数**: 列表类接口的分页参数 MUST 统一为 `page`（整数，从 1 开始）+ `page_size`
（整数，默认 20，最大 100）；禁止使用 `offset/limit`、`pageNum/pageSize`、`skip/take`
等其他分页格式。超过最大值的 `page_size` MUST 返回 400 错误，而非静默裁剪。

**响应体格式（统一信封 v1.4.0）**: 所有 `/api/v1/**` 接口的响应体 MUST 匹配以下两种
互斥信封之一，由顶层 `success` 布尔位区分，禁止裸对象/裸数组/其他形态。

**成功信封**（`success=true`，不得出现 `error`）:
```
{
  "success": true,
  "data": <业务载荷>,        // 单对象 / 列表 / null；由路由的 response_model 泛型决定
  "meta": {                   // 仅列表/分页接口非空；非列表为 null 或省略
    "page": 1,                // 从 1 开始
    "page_size": 20,          // 默认 20，最大 100
    "total": 42               // 符合条件的全量记录数
  } | null
}
```

**错误信封**（`success=false`，不得出现 `data`/`meta`）:
```
{
  "success": false,
  "error": {
    "code": "TASK_NOT_FOUND",   // 来自 src/api/errors.py::ErrorCode 枚举的字符串值
    "message": "任务不存在",     // 面向开发者的可读消息
    "details": { ... } | null   // 可选结构化上下文（如 resource_id、field）
  }
}
```

实现约定:
- 成功响应 MUST 通过 `SuccessEnvelope[T]` 泛型 Pydantic 模型构造（见 `src/api/schemas/envelope.py`），
  禁止路由层手写 `return {"success": True, "data": ...}` dict
- 分页接口 MUST 使用 `page(items, page=, page_size=, total=)` 构造器；非分页接口使用 `ok(data)` 构造器
- `page_size` 超出 `[1, 100]` 范围 MUST 返回 400 + `INVALID_PAGE_SIZE`，禁止静默裁剪
- 禁止在顶层包装 `code/message/result` 等与本信封冲突的字段

**分层职责**: 路由层（`src/api/routers/`）MUST 仅负责参数校验与响应组装，禁止包含任何
业务逻辑；业务逻辑 MUST 集中在服务层（`src/services/`）。Celery 任务（`src/workers/`）
MUST 通过调用 service 层完成工作，禁止在 worker 中直接实现业务规则。

**错误响应映射（统一异常 v1.4.0）**: 服务层与路由层 MUST 统一抛出 `AppException(code, message, details)`
（定义于 `src/api/errors.py`），禁止直接抛 `HTTPException` 或返回错误字典。由 FastAPI 全局
异常处理器将 `AppException` 转为上述「错误信封」。映射规则:

- 请求体/查询参数校验失败（Pydantic `RequestValidationError`）→ 422 + `VALIDATION_FAILED`
- 资源不存在 → 404 + 资源专属 code（`TASK_NOT_FOUND`、`COACH_NOT_FOUND` 等）
- 状态/业务约束冲突 → 400 或 409 + 专属 code（`COACH_INACTIVE`、`KB_VERSION_NOT_DRAFT` 等）
- 通道容量 / 禁用 → 503 + `CHANNEL_QUEUE_FULL` / `CHANNEL_DISABLED`
- 上游依赖失败 → 502 + `LLM_/COS_/DB_/WHISPER_UPSTREAM_FAILED`
- 未预期异常 → 500 + `INTERNAL_ERROR`，且 MUST 记录 `logging.exception`

**错误码集中化**: `ErrorCode` 枚举 + `ERROR_STATUS_MAP`（code→HTTP 状态）+ `ERROR_DEFAULT_MESSAGE`
（code→默认消息）MUST 统一定义于 `src/api/errors.py`，作为**单一事实来源**。禁止在路由文件中
使用裸字符串错误码（如 `{"code": "FOO_BAR"}`）；所有新增 code MUST 同步更新 3 处映射表，并在
对应 Feature 的 `contracts/error-codes.md` 登记。已发布的错误码禁止改名或更换 HTTP 状态
（只允许新增）。

**接口下线**: 接口下线 MUST 采用直接物理删除路由代码、合约测试与契约文件；FastAPI 默认
404 `NOT_FOUND` 已足以告知客户端路径不可用，无需保留哨兵路由或台账文件。下线的迁移说明
SHOULD 在对应 Feature 的 `spec.md`「业务阶段映射」小段或 changelog 中记录一次性替代路径
提示，不再要求在代码或 spec contracts 目录下留痕（原则 IV 对齐）。

**接口契约与合约测试**: 新增或变更 API 接口 MUST 在 `specs/###-feature/contracts/` 下
提供接口契约（OpenAPI 片段或 Pydantic schema），并在 `tests/contract/` 中配套合约测试；
合约测试 MUST 在路由实现之前创建，且在实现开始前处于失败状态（遵循原则 II）。

**文档完整性**: 新增接口 MUST 在契约或规范中提供完整的请求/响应示例、分页行为说明、
以及至少 400/404/500 三类错误场景的示例响应。

### X. 业务流程对齐

所有系统设计与优化 MUST 显式对齐 `docs/business-workflow.md`（以下简称「业务流程文档」），
该文档为项目**业务执行流程的唯一权威参考**，定义三阶段（训练 TRAINING / 建标 STANDARDIZATION /
诊断 INFERENCE）与八个执行步骤（素材归集 / 视频预处理 / 技术分类 / KB 抽取 / 冲突审阅 /
KB 版本激活 / 技术标准构建 / 学员诊断）。任何与业务流程相关的工程活动 MUST 命中其中一个
已定义阶段/步骤，不允许在业务流程外引入旁路路径或平行流水线。

**Feature 规范必填段落**: 每个功能规范（`spec.md`）MUST 在「需求」节下新增「业务阶段映射」
小段，显式声明：
- **所属阶段**: `TRAINING` / `STANDARDIZATION` / `INFERENCE` 三选一；跨阶段功能 MUST
  拆成多个独立用户故事分别声明
- **所属步骤**: 业务流程文档 § 3–§ 5 中的八步骤名称之一（例如 `extract_kb`、`diagnose_athlete`），
  新增步骤 MUST 先扩展业务流程文档再进入 spec
- **DoD 引用**: 指向业务流程文档 § 2 阶段判据表对应行，作为功能完成判据
- **可观测锚点**: 指向业务流程文档 § 7 对应子节，声明本功能需落地的日志/指标/状态表

缺失「业务阶段映射」的规范视为不完整，MUST NOT 进入 `/speckit.plan` 阶段。

**章程级约束双向同步**: 下列任一变更 MUST 同步更新业务流程文档（由 `refresh-docs` skill
刷新或人工修订）：
- Celery 队列拓扑变化（新增/删除队列、worker 并发默认值调整）→ 同步业务流程文档 § 3.1 / § 5.1
- 状态机变化（`analysis_tasks.status` / `tech_knowledge_bases.status` / `pipeline_steps.status`
  枚举值增减）→ 同步 § 2 阶段 DoD、§ 4.3 状态机
- 结构化错误码前缀变化（`src/services/**/error_codes.py` 增删）→ 同步 § 7.4 错误码表
- 诊断评分公式变化（`diagnosis_scorer` 阈值/分段调整）→ 同步 § 5.3
- 单 active / 冲突门控等章程级约束变化 → 同步 § 4.2

未同步文档的代码变更 PR MUST 在章程检查中被标记为违规，要求在合并前补齐。

**优化活动必须命中三种杠杆**: 系统性能优化（时效性 / 准确性 / 成本）MUST 显式选择业务
流程文档 § 9 定义的三种杠杆之一：
- **运行时参数**（热配置，30 秒内生效，无需重启，如 `task_channel_configs`）
- **算法/模型**（需重启 worker，如 `POSE_BACKEND`、Whisper 模型大小）
- **规则/Prompt**（需重启 API，如 `tech_classification_rules.json`、LLM prompt）

跨杠杆组合或新增第四类杠杆 MUST 先扩展业务流程文档 § 9 并获批，不允许在 Feature 实现中
临时引入未登记的调参入口。

**回滚路径显式化**: 涉及 KB 版本激活、状态机切换、通道熔断等不可逆/高风险操作的 Feature
MUST 在 `plan.md` 中引用业务流程文档 § 10 的回滚剧本，或提出新剧本并扩展 § 10。禁止在
无回滚路径的情况下进入实现。

**理由**: 业务流程文档是运营、开发、SRE 三方的共同契约；将其提升为章程级权威参考可防止
"技术重构脱离业务语义"、"旁路流水线"、"未登记调参入口" 三类漂移，保证系统可运维性与
业务可追溯性。与原则 V（可观测）、原则 VI（模型治理）、原则 IX（API 规范）正交互补。

## 附加约束

**范围边界**: 本项目仅覆盖后端算法与服务实现，前端代码(Web 界面、移动端 UI、桌面客户端)
明确排除在外。规范、计划和任务中 MUST NOT 包含任何前端实现任务。后端 API 设计以算法
正确性和数据契约为准，不受前端框架或 UI 设计约束。

**分支命名**: 功能分支 MUST 使用 `###-feature-name` 或 `YYYYMMDD-HHMMSS-feature-name` 格式。

**文档完整性**: 每个功能 MUST 在 `specs/###-feature-name/` 下包含：`spec.md`(必需)、`plan.md`(必需)、
`tasks.md`(必需)；`research.md`、`data-model.md`、`contracts/`、`quickstart.md` 按需创建。
`spec.md` 的成功标准 MUST 包含量化精准度指标(原则 VIII 要求)。

**路径约定**（后端服务结构）:
- 标准后端: `src/`、`tests/`
- 多服务后端: `services/<service-name>/src/`、`services/<service-name>/tests/`
- 算法模块: `src/algorithms/`、`src/models/`、`src/pipelines/`
- 基准数据与评估: `docs/benchmarks/`、`data/eval/`(评估集，不含训练数据)
- 前端路径(`frontend/`、`web/`、`ios/`、`android/`)在本项目中 MUST NOT 创建

**AI/ML 约束**: AI 模型文件和权重 MUST 通过 Git LFS 或等效大文件存储管理，禁止直接提交至
Git 对象存储。模型推理依赖(如 ONNX Runtime、PyTorch 等)MUST 在 `plan.md` 技术背景中
明确版本锁定。实时推理场景 MUST 在规范中定义可接受的延迟上限(如 `p95 < 200ms`)。

**数据治理**: 训练数据集版本 MUST 与模型版本建立对应关系并记录在案。用于评估的测试集
MUST 与训练集严格隔离，禁止数据泄漏。精准度基准数据集 MUST 版本化管理，不允许静默替换。

**工具链**: 使用 speckit v0.5.0 提供的 9 个标准命令(`/speckit.specify`、`/speckit.plan`、
`/speckit.tasks`、`/speckit.implement` 等)驱动开发流程；禁止绕过规范工作流直接进入实现。

**Python 环境隔离**: 项目内所有功能 MUST 使用同一个由 `pyproject.toml` 管理的 Python 虚拟环境
（如 `uv venv` 或 `poetry env`）；禁止使用系统 Python 或 Conda 全局环境运行任何项目代码、
测试或构建步骤。新依赖 MUST 通过 `pyproject.toml` 声明并锁定版本，不允许直接 `pip install`
到系统环境。安装任何新 Python 包前 MUST 先激活项目虚拟环境（如 `source .venv/bin/activate`
或 `uv run`），MUST NOT 在未激活项目环境的情况下执行 `pip install`——在系统 Python 或
Conda 全局环境中安装包的行为被明确禁止。CI/CD 流水线 MUST 从 `pyproject.toml` 重建隔离
环境，确保可复现性。

**技术栈**: 语言与框架在功能规划阶段(`plan.md` 技术背景部分)确定；AI 推理框架选型 MUST
在章程检查时提供精度基准、延迟与资源占用的综合评估依据，精度权重高于速度权重。

## 开发工作流

**功能启动**: 每个功能 MUST 通过 `/speckit.specify` 创建规范(含量化精准度指标)，再通过
`/speckit.plan` 生成实施计划，然后通过 `/speckit.tasks` 分解任务，最后通过
`/speckit.implement` 执行——不允许跳过阶段。

**章程检查**: `plan.md` 中的章程检查部分 MUST 在阶段 0 研究前通过，并在阶段 1 设计后重新检查。
检查 MUST 验证：(a) 规范包含量化精准度指标(原则 VIII)；(b) 无前端实现任务混入范围；
(c) 涉及 AI 模型或用户数据的功能满足原则 VI 和 VII；(d) 新增或变更 HTTP 接口
满足原则 IX（统一版本前缀、资源化路由、标准分页、**统一响应信封 success/data/meta 或
success/error**、分层职责、**AppException + 集中化 ErrorCode 映射**、合约测试前置、
**接口下线直接物理删除而非保留哨兵**）；(e) 功能满足原则 X（**spec.md 含
「业务阶段映射」声明 phase/step/DoD/可观测锚点；队列/状态机/错误码/评分公式等章程级
约束变化与 `docs/business-workflow.md` 双向同步；优化活动命中 § 9 三种杠杆之一；
高风险操作引用 § 10 回滚剧本**）。任何违规 MUST 在继续之前记录并获批。

**代码审查**: 所有 PR MUST 验证章程合规性；算法变更 PR MUST 附精准度对比数据。
涉及模型推理或用户数据处理的 PR MUST 由至少一名了解 AI/隐私约束的审查者审核。

**提交规范**: SHOULD 在每个任务或逻辑组完成后提交；提交消息 SHOULD 引用相关任务 ID。

**质量门控**: 功能分支合并前 MUST 通过所有规范要求的测试，且精准度指标 MUST 达到或超过
规范定义的基准；`quickstart.md`(如存在) MUST 经过手工验证。AI 模型相关功能 MUST
通过模型回归测试且精准度不低于前版本后方可合并。

## 治理

本章程优先于所有其他开发实践和约定。

**修订程序**: 章程修订 MUST 使用 `/speckit.constitution` 命令执行，并遵循语义版本控制：
MAJOR 版本用于原则删除或不兼容的治理变更；MINOR 版本用于新原则或实质性扩展；
PATCH 版本用于澄清、措辞或拼写修正。

**版本控制**: 每次修订 MUST 更新版本号和修订日期；修订 SHOULD 附带说明变更动机的提交消息。

**合规审查**: 在每个功能的 `plan.md` 章程检查节点执行合规验证；不合规问题 MUST 在推进前解决。

**运行时指导**: AI 代理开发指导文件(如存在)由 `.specify/scripts/bash/update-agent-context.sh`
自动从活跃功能计划中生成；以该文件为运行时开发的权威参考。

**版本**: 2.0.0 | **批准日期**: 2026-04-17 | **最后修订**: 2026-04-30
