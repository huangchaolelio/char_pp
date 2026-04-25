---
alwaysApply: true
---

# 文件组织规则

- **新脚本/分析文件**：放 `specs/NNN-xxx/scripts/` 或 `specs/NNN-xxx/research.md`，**禁止散落项目根目录**
- **临时调试文件**：只用 `/tmp/`，不提交 git
- **新 Feature 规范**：遵循 `specs/speckit.constitution.md` 结构（spec.md + plan.md + data-model.md + tasks.md）
- **文档**：放 `docs/`，用 `/refresh-docs` skill 自动更新

# 新 Feature 编号规则

下一个 Feature 编号为 `015`（001–014 均已完成）。Feature 目录命名格式：`specs/NNN-kebab-case-name/`

# 目录职责速查

| 目录 | 职责 |
|------|------|
| `src/api/routers/` | HTTP 路由，参数校验，响应组装 |
| `src/api/schemas/` | Pydantic 请求/响应模型 |
| `src/models/` | SQLAlchemy ORM 表定义 |
| `src/services/` | 核心业务逻辑 |
| `src/workers/` | Celery 异步任务 |
| `src/db/migrations/` | Alembic 迁移文件 |
| `config/` | 静态映射配置（JSON） |
| `src/config/` | 运行时规则配置（YAML/JSON） |
| `specs/` | Feature 规范文档 |
| `docs/` | 技术架构与产品功能文档 |
| `tests/` | unit / integration / contract 测试 |
