# 实施计划: 全量任务查询接口

**分支**: `012-task-query-all` | **日期**: 2026-04-23 | **规范**: [spec.md](spec.md)
**输入**: 来自 `/specs/012-task-query-all/spec.md` 的功能规范

## 摘要

新增 `GET /api/v1/tasks` 列表查询端点，支持分页（offset-based）、5 维筛选（status/task_type/coach_id/created_after/created_before）和 2 字段排序（created_at/completed_at，NULLS LAST）。同时扩展现有 `GET /api/v1/tasks/{task_id}`，在响应体中追加 `summary` 字段，包含所有关联实体的聚合统计（技术点数、转录状态、语义分段数、动作分析数、偏差数、建议数）。纯查询功能，无数据库结构变更。

## 技术背景

**语言/版本**: Python 3.11
**主要依赖**: FastAPI 0.100+、SQLAlchemy 2.0 asyncio、Pydantic v2
**存储**: PostgreSQL（asyncpg 驱动）
**测试**: pytest + httpx（AsyncClient）
**目标平台**: Linux 服务器，内网 API
**项目类型**: Web 服务（REST API 扩展）
**性能目标**: 列表查询（page_size=50，10k 条数据）< 1s；详情查询 < 2s
**约束条件**: 无 DB schema 变更；不新增依赖；复用现有 ORM 关系
**规模/范围**: 当前任务表约数千条，短期预计不超过 10k

## 章程检查

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 规范包含量化成功标准（原则 VIII） | ✓ | SC-001/002/003/004 已定义（原则 VIII 仅针对算法精准度，本功能为查询 API，不适用精准度指标，性能指标已覆盖） |
| 无前端实现任务（范围约束） | ✓ | 纯后端 API |
| 不涉及 AI 模型（原则 VI） | ✓ | 纯 DB 查询 |
| 不写入用户数据（原则 VII） | ✓ | 只读接口 |
| 无复杂性违规（原则 IV） | ✓ | 无新抽象层，直接扩展现有 router/schema |
| 分支命名格式正确 | ✓ | `012-task-query-all` |

## 项目结构

### 文档（此功能）

```
specs/012-task-query-all/
├── plan.md              ← 此文件
├── spec.md              ← 功能规范
├── research.md          ← 阶段 0 输出
├── data-model.md        ← 阶段 1 输出
├── quickstart.md        ← 阶段 1 输出
├── contracts/
│   └── task_list_api.md ← API 契约
├── checklists/
│   └── requirements.md  ← 规范质量清单
└── tasks.md             ← 阶段 2 输出（/speckit.tasks 创建）
```

### 源代码（修改/新增文件）

```
src/
├── api/
│   ├── routers/
│   │   └── tasks.py          # 新增 GET /tasks 端点；扩展 GET /tasks/{task_id}
│   └── schemas/
│       └── task.py           # 新增 TaskListItemResponse、TaskListResponse、TaskSummary；
│                             # 扩展 TaskStatusResponse（追加 summary 字段）
└── services/
    └── task_query_service.py # 新增：列表查询 + 详情摘要聚合逻辑（可选，视复杂度决定）

tests/
├── contract/
│   └── test_task_list_api.py  # GET /tasks 契约测试
└── integration/
    └── test_task_list.py      # 分页、筛选、排序端到端集成测试
```

**结构决策**: 单一项目结构（选项 1），所有新代码集中在 `src/api/` 下扩展。若查询逻辑超过 80 行，抽取到 `src/services/task_query_service.py`；否则直接在 router 中实现。

## 复杂度跟踪

> 无章程违规，此表为空。
