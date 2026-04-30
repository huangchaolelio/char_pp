# 实施计划: [FEATURE]

**分支**: `[###-feature-name]` | **日期**: [DATE] | **规范**: [link]
**输入**: 来自 `/specs/[###-feature-name]/spec.md` 的功能规范

**注意**: 此模板由 `/speckit.plan` 命令填充. 执行工作流程请参见 `.specify/templates/plan-template.md`.

## 摘要

[从功能规范中提取: 主要需求 + 研究得出的技术方法]

## 技术背景

<!--
  需要操作: 将此部分内容替换为项目的技术细节.
  此处的结构以咨询性质呈现, 用于指导迭代过程.
-->

**语言/版本**: [例如: Python 3.11, Swift 5.9, Rust 1.75 或 NEEDS CLARIFICATION]
**主要依赖**: [例如: FastAPI, UIKit, LLVM 或 NEEDS CLARIFICATION]
**存储**: [如适用, 例如: PostgreSQL, CoreData, 文件 或 N/A]
**测试**: [例如: pytest, XCTest, cargo test 或 NEEDS CLARIFICATION]
**目标平台**: [例如: Linux 服务器, iOS 15+, WASM 或 NEEDS CLARIFICATION]
**项目类型**: [例如: 库/cli/web 服务/移动应用/编译器/桌面应用 或 NEEDS CLARIFICATION]
**性能目标**: [领域特定, 例如: 1000 请求/秒, 10k 行/秒, 60 fps 或 NEEDS CLARIFICATION]
**约束条件**: [领域特定, 例如: <200ms p95, <100MB 内存, 离线可用 或 NEEDS CLARIFICATION]
**规模/范围**: [领域特定, 例如: 10k 用户, 1M 行代码, 50 个屏幕 或 NEEDS CLARIFICATION]

## 章程检查

*门控: 必须在阶段 0 研究前通过. 阶段 1 设计后重新检查. *

**章程合规验证**:
- ✅ 规范包含量化精准度指标(原则 VIII)
- ✅ 无前端实现任务混入范围(附加约束)
- ✅ 涉及 AI 模型的功能满足原则 VI(AI 模型治理)
- ✅ 涉及用户数据的功能满足原则 VII(隐私与安全)
- ✅ API 接口设计符合原则 IX(接口规范统一)
- ✅ 功能与业务流程对齐符合原则 X(spec.md 含「业务阶段映射」；队列/状态机/错误码等章程级约束变化已双向同步 `docs/business-workflow.md`；优化活动命中 § 9 三种杠杆之一；高风险操作引用 § 10 回滚剧本)

**API 接口规范验证要点（原则 IX，v1.4.0 统一信封）**:
- 版本前缀统一使用 `/api/v1/`
- 路由按资源划分，每个路由文件对应一个资源，禁止混搭
- 分页参数统一：`page`(从 1 开始) + `page_size`(默认 20，最大 100)；越界返回 400 + `INVALID_PAGE_SIZE`
- **响应体统一信封**（互斥两选一，由顶层 `success` 布尔位区分）：
  - 成功：`{"success": true, "data": <载荷>, "meta": {page,page_size,total} | null}`，MUST 通过 `SuccessEnvelope[T]` 泛型构造
  - 错误：`{"success": false, "error": {"code","message","details"}}`，MUST 由全局异常处理器从 `AppException` 转换
- 分层职责：路由层仅做参数校验与响应组装，业务逻辑归 `src/services/`
- **错误响应统一**：服务层/路由层 MUST 抛 `AppException(ErrorCode.XXX)`；禁止直接抛 `HTTPException` 或返回错误字典
  - 422 `VALIDATION_FAILED` / 404 资源专属 code / 400 \| 409 状态冲突 / 503 队列容量 / 502 上游失败 / 500 `INTERNAL_ERROR`（含 `logging.exception`）
- **错误码集中化**：`ErrorCode` 枚举 + `ERROR_STATUS_MAP` + `ERROR_DEFAULT_MESSAGE` 单一事实来源于 `src/api/errors.py`，新增必须同步 3 张表 + `contracts/error-codes.md`
- **已下线接口**：保留哨兵路由返回 404 + `ENDPOINT_RETIRED`（`details.successor` + `migration_note`），禁止物理删除；台账登记于 `_retired.py::RETIREMENT_LEDGER` + `contracts/retirement-ledger.md`
- 新增/变更接口在 `contracts/` 下提供契约，并先于实现创建 `tests/contract/` 合约测试

**业务流程对齐验证要点（原则 X，v1.5.0）**:
- **权威参考**: `docs/business-workflow.md` 为业务执行流程唯一权威参考，三阶段 TRAINING / STANDARDIZATION / INFERENCE 八步骤为硬约束
- **spec.md 必须声明**: 「业务阶段映射」小段——所属阶段、所属步骤（八步骤之一或新扩展的步骤）、DoD 引用（§ 2 阶段判据表对应行）、可观测锚点（§ 7 对应子节）
- **章程级约束双向同步**: 以下变更 MUST 同步更新 `docs/business-workflow.md` 对应章节，否则 PR 视为违规
  - Celery 队列拓扑（新增/删除队列、worker 并发默认值）→ § 3.1 / § 5.1 / § 7 表格
  - 状态机枚举（`analysis_tasks.status` / `tech_knowledge_bases.status` / `pipeline_steps.status`）→ § 2 DoD / § 4.3 状态机
  - 结构化错误码前缀（`src/services/**/error_codes.py`）→ § 7.4 错误码表
  - 诊断评分公式（`diagnosis_scorer` 阈值/分段）→ § 5.3
  - 章程级约束（单 active、冲突门控）→ § 4.2
- **优化活动必须命中三种杠杆**: 性能优化（时效性 / 准确性 / 成本）MUST 显式选择 § 9 定义的三种杠杆之一——运行时参数（如 `task_channel_configs` 热配置）/ 算法与模型 / 规则与 Prompt；跨杠杆组合或新增第四类杠杆 MUST 先扩展 § 9 并获批
- **高风险操作引用回滚剧本**: 涉及 KB 版本激活、状态机切换、通道熔断等不可逆操作的 Feature MUST 在 `plan.md` 引用 § 10 回滚剧本或新增剧本；无回滚路径不得进入实现
- **spec.md 缺失「业务阶段映射」视为不完整**: MUST NOT 进入 `/speckit.plan` 阶段

任何违规 MUST 在继续之前记录并获批。

## 项目结构

### 文档(此功能)

```
specs/[###-feature]/
├── plan.md              # 此文件 (/speckit.plan 命令输出)
├── research.md          # 阶段 0 输出 (/speckit.plan 命令)
├── data-model.md        # 阶段 1 输出 (/speckit.plan 命令)
├── quickstart.md        # 阶段 1 输出 (/speckit.plan 命令)
├── contracts/           # 阶段 1 输出 (/speckit.plan 命令)
└── tasks.md             # 阶段 2 输出 (/speckit.tasks 命令 - 非 /speckit.plan 创建)
```

### 源代码(仓库根目录)
<!--
  需要操作: 将下面的占位符树结构替换为此功能的具体布局.
  删除未使用的选项, 并使用真实路径(例如: apps/admin, packages/something)扩展所选结构.
  交付的计划不得包含选项标签.
-->

```
# [如未使用请删除] 选项 1: 单一项目(默认)
src/
├── models/
├── services/
├── cli/
└── lib/

tests/
├── contract/
├── integration/
└── unit/

# [如未使用请删除] 选项 2: Web 应用程序(检测到"前端" + "后端"时)
backend/
├── src/
│   ├── models/
│   ├── services/
│   └── api/
└── tests/

frontend/
├── src/
│   ├── components/
│   ├── pages/
│   └── services/
└── tests/

# [如未使用请删除] 选项 3: 移动端 + API(检测到 "iOS/Android" 时)
api/
└── [同上后端结构]

ios/ 或 android/
└── [平台特定结构: 功能模块, UI 流程, 平台测试]
```

**结构决策**: [记录所选结构并引用上面捕获的真实目录]

## 复杂度跟踪

> **仅在章程检查有必须证明的违规时填写**

| 违规 | 为什么需要 | 拒绝更简单替代方案的原因 |
|-----------|------------|-------------------------------------|
| [例如: 第 4 个项目] | [当前需求] | [为什么 3 个项目不够] |
| [例如: 仓储模式] | [特定问题] | [为什么直接数据库访问不够] |
