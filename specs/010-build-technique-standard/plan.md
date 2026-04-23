# 实施计划: 构建单项技术标准知识库

**分支**: `010-build-technique-standard` | **日期**: 2026-04-22 | **规范**: [spec.md](spec.md)
**输入**: 来自 `/specs/010-build-technique-standard/spec.md` 的功能规范

## 摘要

从各教练已提取的 ExpertTechPoint 中，按技术类别聚合（中位数+P25/P75），生成可版本化查询的技术标准（TechStandard + TechStandardPoint），供下游诊断比对使用。排除 conflict_flag=true 和 confidence<0.7 的数据点；每技术至少需要来自 2 位教练的数据才可构建多源标准，1 位教练可构建单源标准，0 位则跳过。

## 技术背景

**语言/版本**: Python 3.11+
**主要依赖**: FastAPI 0.111+, SQLAlchemy 2.0 asyncio, Alembic 1.13+, numpy（已有间接依赖）
**存储**: PostgreSQL，新增 2 张表（`tech_standards`, `tech_standard_points`）
**测试**: pytest（现有），需新增 contract/integration/unit 测试
**目标平台**: Linux 后端服务
**项目类型**: Web 服务（REST API）
**性能目标**: 单项标准构建 < 5s，查询响应 < 200ms（见 SC-002, SC-004）
**约束条件**: 仅手动触发刷新；只生成有数据维度；coach_count < 2 跳过构建
**规模/范围**: 21 类技术，预计每类 1-20 个维度，每维度 3-50 个技术点

## 章程检查

*门控: 必须在阶段 0 研究前通过。阶段 1 设计后重新检查。*

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 规范包含量化精准度指标（原则 VIII） | ✅ 通过 | SC-001~SC-006 均为可衡量指标；本功能为统计聚合（非 AI 模型），无"精准度"含义，成功标准以覆盖率/响应时间度量，符合章程精神 |
| 无前端实现任务混入（附加约束） | ✅ 通过 | 仅后端 API + 服务层 + 数据库，无前端 |
| 涉及 AI 模型（原则 VI）| ✅ N/A | 本功能为统计聚合，无 AI 模型推理 |
| 用户数据隐私（原则 VII）| ✅ 通过 | 技术标准为聚合统计数据，不含个人信息 |
| TDD 要求（原则 II）| ✅ 遵从 | 任务分解时测试先于实现 |
| YAGNI（原则 IV）| ✅ 遵从 | 无多余抽象；不内置调度 |

**阶段 1 设计后重检**: ✅ 数据模型（2 表）简洁，无过度设计；API 契约覆盖规范所有需求；复杂度在预期范围内。

## 项目结构

### 文档（此功能）

```
specs/010-build-technique-standard/
├── plan.md              # 此文件
├── spec.md              # 功能规范
├── research.md          # 阶段 0 研究结论
├── data-model.md        # 数据模型
├── quickstart.md        # 快速验证指南
├── contracts/
│   └── api-standards.md # API 契约
└── checklists/
    └── requirements.md  # 规范质量清单
```

### 源代码（新增文件）

```
src/
├── models/
│   └── tech_standard.py              # TechStandard + TechStandardPoint ORM 模型
├── services/
│   └── tech_standard_builder.py      # 聚合构建服务
└── api/
    └── routers/
        └── standards.py              # /api/v1/standards 路由

src/db/migrations/versions/
└── 0010_tech_standard.py             # 新表迁移

tests/
├── unit/
│   └── test_tech_standard_builder.py # 聚合逻辑单元测试
├── integration/
│   └── test_tech_standard_api.py     # API 端到端集成测试
└── contract/
    └── test_standards_contract.py    # API 契约测试
```

**结构决策**: 复用现有 `src/models/`, `src/services/`, `src/api/routers/` 结构，保持与 Feature-008 的一致性。

## 复杂度跟踪

> 无章程违规，本表为空。

---

## 实施阶段

### 阶段 0（已完成）: 研究

- [x] 确定聚合算法（中位数+P25/P75）
- [x] 确定 conflict_flag 处理（排除）
- [x] 确定数据不足阈值（coach_count < 2 跳过）
- [x] 确定缺失维度处理（只生成有数据维度）
- [x] 确定触发方式（仅手动）
- 输出: `research.md` ✅

### 阶段 1（已完成）: 设计与契约

- [x] 数据模型：`tech_standards` + `tech_standard_points` 两表设计
- [x] API 契约：4 个端点（build POST、build GET、standard GET、standards GET）
- [x] Quickstart 验证指南
- 输出: `data-model.md` ✅, `contracts/api-standards.md` ✅, `quickstart.md` ✅

### 阶段 2: 任务分解（由 /speckit.tasks 执行）

待 `/speckit.tasks` 命令生成 `tasks.md`。

**预期任务组**:

1. **数据库迁移**: 创建 `tech_standards` 和 `tech_standard_points` 表及索引
2. **ORM 模型**: `src/models/tech_standard.py`
3. **构建服务**: `src/services/tech_standard_builder.py`（聚合逻辑核心）
4. **API 路由**: `src/api/routers/standards.py` + 注册到 app
5. **单元测试**: 聚合逻辑（中位数/百分位数计算、过滤条件、版本化逻辑）
6. **集成测试**: API 端到端流程
7. **契约测试**: API 响应结构验证
