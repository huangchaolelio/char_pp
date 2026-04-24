---
name: refresh-docs
description: 当 specs 功能执行完成时，刷新项目文档（docs/architecture.md 和 docs/features.md）
allowed-tools: Read, Write, Bash, Grep, Glob
context: fork
---

## 触发时机
当任意 specs/NNN-*/PLAN.md 完成实现后调用，或手动执行 `/refresh-docs` 刷新项目文档。

## 当前 specs 目录结构
!`ls specs/`

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
7. **更新文档顶部时间戳**：在两个文档的第一行更新「最后更新：YYYY-MM-DD」

## 注意事项
- 优先以代码实际状态为准，而非 PLAN.md 描述
- 如果某个 Feature 在 PLAN.md 中标记为完成但代码不存在，标记为「计划中」
- docs/ 目录下的文档以中文为主
