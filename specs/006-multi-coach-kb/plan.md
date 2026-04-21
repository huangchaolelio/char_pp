# 实施计划: 多教练知识库提炼与技术校准

**分支**: `006-multi-coach-kb` | **日期**: 2026-04-21 | **规范**: [spec.md](./spec.md)
**输入**: 来自 `/specs/006-multi-coach-kb/spec.md` 的功能规范

## 摘要

为乒乓球AI教练系统引入多教练知识库能力：

1. **Coach 实体**：独立 `coaches` 表，姓名全局唯一（DB UNIQUE 约束），支持软删除
2. **教练关联**：在 `analysis_tasks` 上新增 `coach_id` 外键；`expert_tech_points` 和 `teaching_tips` 通过 task JOIN 获得教练信息（不冗余存储）
3. **按教练过滤查询**：现有知识库查询接口扩展 `coach_id` 可选过滤参数
4. **校准对比接口**：传入 `action_type + dimension`，精确返回该维度下各教练的 param_min/ideal/max 对比
5. **教练 CRUD API**：创建/修改/软删除/查询活跃教练列表
6. **向后兼容**：历史无 coach_id 的任务和知识库数据正常可用，`coach_id=NULL` 视为"未指定"

## 技术背景

**语言/版本**: Python 3.11（项目要求，使用 `/opt/conda/envs/coaching/bin/python3.11`）
**主要依赖**: FastAPI 0.111+, SQLAlchemy 2.0+ (asyncio), Alembic 1.13+, Pydantic v2
**存储**: PostgreSQL（asyncpg 驱动）
**测试**: pytest 8.2+ / pytest-asyncio 0.23+
**目标平台**: Linux 服务器（后端 API + Celery worker）
**项目类型**: Web 服务（REST API）
**性能目标**: 校准接口 p95 < 200ms（教练数量预期 ≤ 20 人，数据规模小）
**约束条件**: 无前端代码；Coach 实体由管理员人工维护；不自动识别教练身份
**规模/范围**: 预期教练数量 ≤ 20，每位教练数十个任务，tech_points 数百条

## 章程检查

*门控: 必须在阶段 0 研究前通过。阶段 1 设计后重新检查。*

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 规范包含用户故事和可衡量成功标准 | ✅ 通过 | spec.md 含 4 个用户故事、5 条 SC |
| 无前端实现任务混入 | ✅ 通过 | 全部为后端 API + DB 任务 |
| 量化精准度指标（原则 VIII） | ✅ 通过 | 本功能无 AI 模型推理，不适用精准度基准要求；SC-002/SC-003 提供 100% 数据正确性指标 |
| 测试优先（原则 II） | ✅ 计划 | 合约测试和集成测试将在实现任务前创建 |
| 复杂性违规 | ✅ 无违规 | Coach 实体设计最简（单表 + FK），无额外抽象层 |
| AI 模型治理（原则 VI）| N/A | 本功能不引入新 AI 模型 |
| 用户数据隐私（原则 VII）| ✅ 通过 | 教练姓名为非敏感数据，无需加密 |
| Python 环境隔离 | ✅ 遵守 | 使用 coaching conda 环境，pyproject.toml 管理依赖 |

**阶段 1 设计后重检**: 见本文档末尾

## 项目结构

### 文档（此功能）

```
specs/006-multi-coach-kb/
├── plan.md              # 此文件
├── research.md          # 阶段 0 输出
├── data-model.md        # 阶段 1 输出
├── contracts/
│   └── coaches-api.md   # 阶段 1 输出
└── tasks.md             # 阶段 2 输出（/speckit.tasks 命令创建）
```

### 源代码（新增/修改文件）

```
src/
├── models/
│   └── coach.py                          # 新增：Coach ORM 模型
├── api/
│   ├── routers/
│   │   ├── coaches.py                    # 新增：Coach CRUD 路由
│   │   └── calibration.py               # 新增：校准对比路由
│   └── schemas/
│       └── coach.py                      # 新增：Coach Pydantic schema
├── db/
│   └── migrations/versions/
│       └── 0007_multi_coach_kb.py        # 新增：DB 迁移
└── api/main.py                           # 修改：注册新路由

tests/
├── unit/
│   ├── test_coaches_router.py            # 新增
│   └── test_calibration_router.py        # 新增
├── integration/
│   └── test_coach_kb_pipeline.py         # 新增
└── contract/
    └── test_coaches_api.py               # 新增
```

**结构决策**: 沿用现有单一项目结构（选项 1），在 `src/` 下扩展。

## 复杂度跟踪

> 无违规，无需填写。

## 阶段 1 设计后重检

*于实现完成后（2026-04-21）补充*

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 设计与规范一致 | ✅ 通过 | Coach 实体、API 契约与 spec.md + data-model.md 完全对齐 |
| 向后兼容性 | ✅ 通过 | `coach_id=NULL` 历史数据全量通过回归（230 tests passed） |
| 测试优先（原则 II）| ✅ 通过 | T010/T016/T019/T024 均先写合约测试确认失败再实现 |
| 复杂度无增长 | ✅ 通过 | 无额外抽象层；calibration 路由直接 JOIN，无中间服务层 |
| 迁移可逆 | ✅ 通过 | 0007 迁移含完整 downgrade()，经验证可执行 |
| 全量回归 | ✅ 通过 | `pytest tests/ — 230 passed, 0 failed`（2026-04-21） |
