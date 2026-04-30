---
name: refresh-docs
description: 当 specs 功能执行完成时，刷新项目文档（docs/architecture.md、docs/features.md、docs/business-workflow.md）
allowed-tools: Read, Write, Bash, Grep, Glob
context: fork
---

## 触发时机
当任意 specs/NNN-*/PLAN.md 完成实现后调用，或手动执行 `/refresh-docs` 刷新项目文档。

## 当前 specs 目录结构
!`ls specs/`

## 文档职责分工（三份核心文档，禁止混淆）

| 文档 | 视角 | 受众 |
|------|------|------|
| `docs/architecture.md` | **技术架构**：分层、依赖、数据流、路由汇总 | 开发 / SRE |
| `docs/features.md` | **功能清单**：Feature-001 ~ 最新的状态与指标记账 | PM / 开发 |
| `docs/business-workflow.md` | **业务执行流程规范**：三阶段八步骤、DoD、可观测、优化杠杆 | 运营 / SRE / PM |

## 执行步骤

1. **收集功能状态**：读取所有 `specs/*/PLAN.md`（或 `plan.md`），提取每个 Feature 的标题、状态、核心功能描述
2. **收集 API 路由**：读取 `src/api/routers/` 下所有 `.py` 文件，提取路由端点、方法、描述
3. **收集数据模型**：读取 `src/models/` 下所有 `.py` 文件，提取模型类和字段
4. **收集技术配置**：读取 `src/config/` 下配置文件，了解技术栈参数
5. **更新 `docs/architecture.md`**：
   - 系统概述（项目目标、技术栈）
   - 服务架构（FastAPI、Celery Worker、PostgreSQL、Redis、COS）
   - 数据模型关系图（文本形式）
   - API 路由汇总表
   - 存储配置说明
6. **更新 `docs/features.md`**：
   - 列出所有已实现的 Feature（Feature-001 ~ 最新），每条包含：状态、功能描述、核心 API、技术指标
   - 更新每个 Feature 的实际完成状态
7. **更新 `docs/business-workflow.md`**（业务流程视角，不与 architecture.md 重复）：
   - 仅在以下触发条件之一命中时才修改该文档，其余情况只更新顶部时间戳：
     * **阶段变化**：三阶段（训练 / 建标 / 诊断）中新增或删除步骤（如新增预处理子步骤、新增诊断后置任务）
     * **队列/通道变化**：`task_channel_configs` 种子、Celery 队列清单、Worker 并发默认值发生变化 → 同步 § 3.1 / § 5.1 / § 7 表格
     * **状态机/DoD 变化**：`analysis_tasks.status`、`tech_knowledge_bases.status`、`pipeline_steps.status` 枚举值增减 → 同步 § 2 阶段判据 / § 4.3 状态机
     * **错误码前缀变化**：`src/services/**/error_codes.py` 新增/删除结构化前缀 → 同步 § 7.4 表格
     * **诊断评分公式变化**：`diagnosis_scorer` 阈值/分段调整 → 同步 § 5.3
     * **单 active / 冲突门控等章程级约束变化** → 同步 § 4.2
   - 修改时严格保持章节编号（§ 1 ~ § 11）稳定；新增阶段或步骤时在对应章节内扩展，不新增顶层章节
   - 从 `pipeline_steps` 实际步骤名、`error_codes.py` 实际前缀、`task_channel_configs` 迁移默认值抽取，禁止臆造
8. **更新文档顶部时间戳**：在三份文档的第一行更新「最后更新：YYYY-MM-DD」

## 注意事项
- 优先以代码实际状态为准，而非 PLAN.md 描述
- 如果某个 Feature 在 PLAN.md 中标记为完成但代码不存在，标记为「计划中」
- docs/ 目录下的文档以中文为主
- **业务流程文档稳态优先**：`business-workflow.md` 的结构稳定性高于完整性——没有明确触发条件就只刷时间戳；任何章节编号调整必须同步更新 § 11 交叉索引块与本 skill
- **不重复 architecture.md**：业务流程文档只描述"业务为何/何时执行"，技术实现/分层/依赖一律交给 architecture.md
