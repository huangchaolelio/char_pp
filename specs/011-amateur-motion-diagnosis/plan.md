# 实施计划: 非专业选手动作诊断与评分

**分支**: `011-amateur-motion-diagnosis` | **日期**: 2026-04-23 | **规范**: [spec.md](spec.md)
**输入**: 来自 `/specs/011-amateur-motion-diagnosis/spec.md` 的功能规范

## 摘要

用户提交视频和指定技术类别，系统通过现有姿态分析管线提取动作维度指标，与 Feature 010 构建的 `tech_standards` active 标准逐维度比对，计算各维度得分及综合评分，调用现有 LLM 集成（`LlmClient`）动态生成改进建议，将完整诊断报告持久化后同步返回。MVP 阶段匿名模式（唯一请求 ID，无用户账户）。

## 技术背景

**语言/版本**: Python 3.11+
**主要依赖**: FastAPI 0.111+, SQLAlchemy 2.0 asyncio, Alembic 1.13+, Pydantic v2, MediaPipe/YOLOv8（姿态估计，现有）
**存储**: PostgreSQL，新增 2 张表（`diagnosis_reports`, `diagnosis_dimension_results`）
**测试**: pytest（现有 TDD 模式），含 contract/integration/unit 三层测试
**目标平台**: Linux 后端服务
**项目类型**: Web 服务（REST API）
**性能目标**: 端到端处理 ≤ 60 秒（SC-002）；教练视频评分 ≥ 80 分（SC-001）
**约束条件**: 同步 POST 阻塞返回；用户必填技术类别；匿名模式（无用户 ID）

## 章程检查

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 规范包含量化精准度指标（原则 VIII） | ✅ 通过 | SC-001~SC-005 均为可衡量指标 |
| 无前端实现任务混入（附加约束） | ✅ 通过 | 仅后端 API + 服务层 + 数据库 |
| 涉及 LLM（原则 VI） | ✅ 通过 | 复用现有 LlmClient，不引入新模型 |
| 用户数据隐私（原则 VII） | ✅ 通过 | 匿名模式，无用户账户，仅存诊断数据 |
| TDD 要求（原则 II） | ✅ 遵从 | 测试先于实现 |
| YAGNI（原则 IV） | ✅ 遵从 | 不实现历史查询（US4 推迟）；无轮询端点 |
| 同步处理约束 | ✅ 确认 | FR-011：POST 阻塞直到报告生成，无异步任务队列 |

## 项目结构

**文档（此功能）**

```
specs/011-amateur-motion-diagnosis/
├── plan.md              # 此文件
├── spec.md              # 功能规范
├── tasks.md             # 任务分解
├── research.md          # 设计决策研究
├── data-model.md        # 数据模型
├── quickstart.md        # 开发者快速启动
└── contracts/
    └── diagnosis_api.md # API 接口契约
```

**源代码（新增文件）**

```
src/
├── models/
│   └── diagnosis_report.py           # DiagnosisReport + DiagnosisDimensionResult ORM 模型
├── services/
│   ├── diagnosis_service.py          # DiagnosisService：主流程编排（提取→比对→评分→LLM→持久化）
│   ├── diagnosis_scorer.py           # 纯函数：维度得分计算、综合评分、偏差级别判断（可单元测试）
│   └── diagnosis_llm_advisor.py      # LLM 改进建议生成（复用 LlmClient，可单独测试/mock）
└── api/
    └── routers/
        └── diagnosis.py              # POST /api/v1/diagnosis

src/db/migrations/versions/
└── 0011_diagnosis_report.py          # 新表迁移

tests/
├── unit/
│   ├── test_diagnosis_scorer.py      # 评分逻辑单元测试（27 tests）
│   ├── test_diagnosis_llm_advisor.py # LLM advisor 单元测试（11 tests）
│   ├── test_diagnosis_service.py     # DiagnosisService 单元测试（8 tests）
│   ├── test_diagnosis_service_cleanup.py # 临时文件清理测试（3 tests）
│   ├── test_migration_011.py         # Migration schema 验证（7 tests）
│   └── test_diagnosis_model.py       # ORM 模型单元测试（10 tests）
├── integration/
│   └── test_diagnosis_api.py         # API 端到端集成测试（9 tests）
└── contract/
    └── test_diagnosis_contract.py    # API 响应结构契约测试（17 tests）
```

## 架构决策

### AD-001: 独立表设计

**决策**: 新建 `diagnosis_reports` 和 `diagnosis_dimension_results` 表，不复用 `athlete_motion_analyses` / `deviation_reports`。

**理由**: 现有表与 `AnalysisTask`（专家视频流程）强耦合，字段语义不同。新表为匿名用户诊断优化，无需任务状态机。

### AD-002: 同步 API

**决策**: 单次 POST 请求同步返回结果（60 秒超时）。

**理由**: 澄清 Q1 用户选择了同步处理，避免任务队列复杂性。

### AD-003: 评分算法

线性插值，基于半宽：

```
half_width = (max - min) / 2
center = (min + max) / 2
distance = |measured - center|

if distance <= half_width:           → ok,          score = 100
if half_width < d <= 1.5 * hw:       → slight,      score = linear [100, 60]
if distance > 1.5 * half_width:      → significant,  score = linear [60, 0]
```

**方向**: `measured > center` → above; `measured < center` → below; `measured == center` → none

**特殊情况**: 当 `min == max`（半宽为 0）：若 measured == value → ok，score=100；否则 → significant，score=0

### AD-004: LLM 建议生成

**决策**: 使用现有 `LlmClient.from_settings()` 调用 LLM（通过 run_in_executor 异步化）。

**输入**: 维度名、实测值、标准范围、偏差等级、技术类别

**降级策略**: LLM 调用失败 → 返回模板建议（不中断整个诊断）

### AD-005: 视频本地化

**决策**: 支持两种路径格式：
- `cos://bucket/key` → 下载到临时文件（复用 cos_client）
- 绝对本地路径或存在的文件 → 直接使用

**清理**: `finally` 块删除临时文件

### AD-006: 数据持久化

**决策**: 使用现有 `AsyncSession` 通过 FastAPI 依赖注入。诊断成功后 commit；API 层处理异常并回滚。

## 数据模型

### diagnosis_reports

| 列 | 类型 | 约束 |
|----|------|------|
| id | UUID | PK, gen_random_uuid() |
| tech_category | VARCHAR(64) | NOT NULL |
| standard_id | BIGINT | FK → tech_standards(id) RESTRICT |
| standard_version | INTEGER | NOT NULL |
| video_path | TEXT | NOT NULL |
| overall_score | FLOAT | NOT NULL |
| strengths_summary | TEXT | nullable |
| created_at | TIMESTAMPTZ | DEFAULT NOW() |

### diagnosis_dimension_results

| 列 | 类型 | 约束 |
|----|------|------|
| id | BIGINT | PK, Identity |
| report_id | UUID | FK → diagnosis_reports(id) CASCADE |
| dimension | VARCHAR(128) | NOT NULL |
| measured_value | FLOAT | NOT NULL |
| ideal_value | FLOAT | NOT NULL |
| standard_min | FLOAT | NOT NULL |
| standard_max | FLOAT | NOT NULL |
| unit | VARCHAR(32) | nullable |
| score | FLOAT | NOT NULL |
| deviation_level | VARCHAR(20) | CHECK IN ('ok','slight','significant') |
| deviation_direction | VARCHAR(10) | CHECK IN ('above','below','none') OR NULL |
| improvement_advice | TEXT | nullable |

## API 契约

**POST /api/v1/diagnosis**

请求: `{"tech_category": "forehand_topspin", "video_path": "cos://bucket/path.mp4"}`

响应 200: `{"report_id": "uuid", "tech_category": "...", "standard_id": 1, "standard_version": 2, "overall_score": 85.0, "strengths": [...], "dimensions": [...], "created_at": "..."}`

响应 422: 无效 tech_category
响应 404: 无 active 标准（error: standard_not_found）
响应 400: 视频无法提取（error: extraction_failed）
响应 500: 未知错误（error: internal_error）

## 实施状态

**已完成** (2026-04-23):
- ✅ Alembic migration 0011
- ✅ ORM 模型
- ✅ 评分算法（diagnosis_scorer.py）
- ✅ LLM 建议生成（diagnosis_llm_advisor.py）
- ✅ 诊断主服务（diagnosis_service.py）
- ✅ API 路由（diagnosis.py）
- ✅ 路由注册（main.py）
- ✅ 92 tests 全部通过

**待人工验收**:
- T028: curl 冒烟测试 + SC-002 ≤60s 计时验证
- T029: 教练视频 SC-001 ≥80 分（阻塞于完整 pipeline）
