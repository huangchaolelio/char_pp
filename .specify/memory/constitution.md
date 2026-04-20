<!--
同步影响报告
============

版本更改: 1.2.0 → 1.2.1

递增理由: PATCH — 在附加约束 → 工具链与 Python 环境部分新增一条具体操作约定：
  所有功能必须使用统一的项目 Python 环境（pyproject.toml 管理），禁止使用系统 Python。
  属于对现有工具链约束的澄清扩充，无新原则，无不兼容治理变更。

修改的原则:
  - 无

新增条款:
  - 附加约束 → 工具链：新增"Python 环境隔离"约定

删除的部分:
  - 无

模板同步状态:
  ✅ .specify/templates/plan-template.md — 无需修改
  ✅ .specify/templates/spec-template.md — 无需修改
  ✅ .specify/templates/tasks-template.md — 路径约定保持 `src/`, `tests/`，与本约定兼容
  ✅ .codebuddy/commands/speckit.constitution.md — 无过时引用
  ✅ .codebuddy/commands/speckit.plan.md — 无需修改
  ✅ .codebuddy/commands/speckit.specify.md — 无需修改
  ✅ .codebuddy/commands/speckit.tasks.md — 无需修改
  ✅ .codebuddy/commands/speckit.implement.md — 无需修改
  ✅ .codebuddy/commands/speckit.analyze.md — 无需修改
  ✅ .codebuddy/commands/speckit.clarify.md — 无需修改
  ✅ .codebuddy/commands/speckit.checklist.md — 无需修改
  ✅ .codebuddy/commands/speckit.taskstoissues.md — 无需修改

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
到系统环境。CI/CD 流水线 MUST 从 `pyproject.toml` 重建隔离环境，确保可复现性。

**技术栈**: 语言与框架在功能规划阶段(`plan.md` 技术背景部分)确定；AI 推理框架选型 MUST
在章程检查时提供精度基准、延迟与资源占用的综合评估依据，精度权重高于速度权重。

## 开发工作流

**功能启动**: 每个功能 MUST 通过 `/speckit.specify` 创建规范(含量化精准度指标)，再通过
`/speckit.plan` 生成实施计划，然后通过 `/speckit.tasks` 分解任务，最后通过
`/speckit.implement` 执行——不允许跳过阶段。

**章程检查**: `plan.md` 中的章程检查部分 MUST 在阶段 0 研究前通过，并在阶段 1 设计后重新检查。
检查 MUST 验证：(a) 规范包含量化精准度指标(原则 VIII)；(b) 无前端实现任务混入范围；
(c) 涉及 AI 模型或用户数据的功能满足原则 VI 和 VII。任何违规 MUST 在继续之前记录并获批。

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

**版本**: 1.2.1 | **批准日期**: 2026-04-17 | **最后修订**: 2026-04-20
