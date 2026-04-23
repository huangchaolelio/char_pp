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

### 文档（此功能）

```
specs/011-amateur-motion-diagnosis/
├── plan.md              # 此文件
├── spec.md              # 功能规范
└── tasks.md             # 任务分解
```

### 源代码（新增文件）

```
src/
├── models/
│   └── diagnosis_report.py           # DiagnosisReport + DiagnosisDimensionResult ORM 模型
├── services/
│   └── diagnosis_service.py          # DiagnosisService：主流程编排（提取→比对→评分→LLM→持久化）
│   └── diagnosis_scorer.py           # 纯函数：维度得分计算、综合评分、偏差级别判断（可单元测试）
│   └── diagnosis_llm_advisor.py      # LLM 改进建议生成（复用 LlmClient，可单独测试/mock）
└── api/
    └── routers/
        └── diagnosis.py              # POST /api/v1/diagnosis

src/db/migrations/versions/
└── 0011_diagnosis_report.py          # 新表迁移

tests/
├── unit/
│   └── test_diagnosis_scorer.py      # 评分逻辑单元测试（纯函数，无 DB）
│   └── test_diagnosis_llm_advisor.py # LLM advisor 单元测试（mock LlmClient）
├── integration/
│   └── test_diagnosis_api.py         # API 端到端集成测试（真实 DB）
└── contract/
    └── test_diagnosis_contract.py    # API 响应结构契约测试
```

**结构决策**: 复用现有 `src/models/`, `src/services/`, `src/api/routers/`, `tests/` 目录结构，与 Feature 010 保持一致。将评分逻辑和 LLM 调用拆分为独立模块，便于单元测试和未来替换。

## 复杂度跟踪

> 无章程违规，本表为空。

---

## 架构决策

### AD-001: 两张新表，不复用旧表

**决策**: 新建 `diagnosis_reports` 和 `diagnosis_dimension_results` 表，而非复用 `athlete_motion_analyses` + `deviation_reports`。

**理由**:
- 诊断报告面向非专业用户，不关联到 `AnalysisTask`（那是专家视频处理流程的入口）。
- 新数据模型语义更清晰：匿名请求 ID、综合评分、LLM 建议文本，与旧模型职责不重叠。
- 旧 `deviation_reports` 关联 `ExpertTechPoint`（KB 版本），新报告关联 `tech_standard_points`（标准版本）。

### AD-002: 标准来源为 tech_standards（Feature 010），不再查 ExpertTechPoint

**决策**: 维度比对的基准统一使用 `tech_standards`/`tech_standard_points` 的 active 记录（ideal/min/max），而非直接读 `expert_tech_points`。

**理由**:
- Feature 010 已将多教练数据聚合为更稳定的统计标准，用于诊断比旧的单点数据更可靠。
- 可追溯到使用的标准版本（standard_id + version），满足 SC-003 一致性要求。

### AD-003: 评分规则——基于标准范围的得分函数

**决策**:
- 维度偏差等级：值在 `[min, max]` 内为"达标"（得分 = 100）；超出范围但在 1.5 倍半宽内为"轻度偏差"（得分 = 60）；超出 1.5 倍半宽为"明显偏差"（得分 = 20）。
- 半宽定义：`half_width = (max - min) / 2`；`center = (max + min) / 2`；`distance = |measured - center|`。
- 综合评分 = 各维度得分的简单平均（等权），保留整数。
- 边界情况：单个维度测量值缺失时跳过该维度（不参与平均），分母为实际参与的维度数。

**具体得分区间**（可在 `diagnosis_scorer.py` 中调参）：

```
distance <= half_width                → 得分 100（达标）
half_width < distance <= 1.5 * half_width → 得分线性插值 [100, 60]（轻度偏差）
distance > 1.5 * half_width               → 得分线性插值 [60, 0]（明显偏差，随距离降低）
```

**理由**: 线性插值比区间跳变（100/60/20）更平滑，利于区分"刚刚偏出"和"大幅偏离"，满足 SC-001（教练视频应 ≥ 80）。

### AD-004: LLM 建议生成——逐维度调用，偏差维度才生成

**决策**: 仅对"轻度偏差"或"明显偏差"的维度调用 LLM 生成建议；达标维度直接记入优点列表（无需 LLM）。每个偏差维度独立构造 prompt，调用 `LlmClient.from_settings().chat()`（同步方式，在 asyncio 中通过 `run_in_executor` 包装）。

**理由**:
- 复用现有 `LlmClient`，零新基础设施。
- 达标维度无改进需求，不调用 LLM，节省 token 及延迟，助力 60 秒目标。
- 逐维度构造 prompt，建议具体指向偏差方向（偏高/偏低），满足 FR-007。

### AD-005: 视频指标提取——复用现有姿态分析管线

**决策**: 视频维度指标提取复用 Feature 001/002 的 `pose_estimator.py` + `tech_extractor.py` 管线，接受视频文件路径或 COS key（通过 `cos_client` 下载到临时文件）。不重新实现底层提取。

**说明**:
- 现有 `TechExtractor` 输出 `ExtractionResult`（含各维度 measured value），可直接用于诊断比对。
- `DiagnosisService` 编排：下载视频 → 提取关键帧 → 计算维度 → 比对标准 → 评分 → LLM 建议 → 持久化 → 返回报告。

### AD-006: 同步 POST，无轮询

**决策**: `POST /api/v1/diagnosis` 阻塞直到完整报告返回，HTTP 200 携带诊断结果。不提供任务状态轮询端点。

**理由**: spec 澄清明确，60 秒内同步返回可行（MediaPipe 提取 + LLM 几次调用）。

---

## 数据模型

### 表 1: `diagnosis_reports`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID PK | 唯一请求 ID（即报告 ID）|
| tech_category | VARCHAR(64) NOT NULL | 用户指定的技术类别 |
| standard_id | BIGINT FK→tech_standards.id | 使用的标准版本 ID |
| standard_version | INTEGER NOT NULL | 快照：标准版本号 |
| video_path | TEXT NOT NULL | 输入视频路径或 COS key |
| overall_score | FLOAT NOT NULL | 综合评分 0–100 |
| strengths_summary | TEXT | 优点摘要（达标维度名列表，JSON 文本）|
| created_at | TIMESTAMPTZ | 创建时间 |

索引：`idx_dr_tech_category ON (tech_category)`, `idx_dr_created_at ON (created_at DESC)`

### 表 2: `diagnosis_dimension_results`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGSERIAL PK | |
| report_id | UUID FK→diagnosis_reports.id CASCADE | |
| dimension | VARCHAR(128) NOT NULL | 维度名称（如 elbow_angle）|
| measured_value | FLOAT NOT NULL | 用户测量值 |
| ideal_value | FLOAT NOT NULL | 标准理想值 |
| standard_min | FLOAT NOT NULL | 标准下界（P25）|
| standard_max | FLOAT NOT NULL | 标准上界（P75）|
| unit | VARCHAR(32) | 单位 |
| score | FLOAT NOT NULL | 该维度得分 0–100 |
| deviation_level | VARCHAR(20) NOT NULL | ok / slight / significant |
| deviation_direction | VARCHAR(10) | above / below / none |
| improvement_advice | TEXT | LLM 生成的改进建议（仅偏差维度有值）|

唯一约束：`uq_ddr_report_dimension ON (report_id, dimension)`
索引：`idx_ddr_report ON (report_id)`

---

## 实施阶段

### 阶段 0（已完成）: 规范分析与澄清

- [x] 确认同步处理模式（FR-011）
- [x] 确认匿名模式（US4 推迟）
- [x] 确认评分规则（等权均值，线性插值）
- [x] 确认偏差判断阈值（1.5 倍半宽）
- [x] 确认 LLM 建议生成方式（复用 LlmClient）
- [x] 确认技术类别必填
- 输出: 本 plan.md ✅

### 阶段 1: 基础设施（阻塞前置条件）

**目的**: 新表迁移 + ORM 模型，所有用户故事的共同依赖

**⚠️ 关键**: 完成后才能开始用户故事实施

- T002 数据库迁移：创建 `diagnosis_reports` + `diagnosis_dimension_results` 表
- T003 ORM 模型：`DiagnosisReport` + `DiagnosisDimensionResult` in `src/models/diagnosis_report.py`
- T004 导出：更新 `src/models/__init__.py`
- T005 验证迁移：执行 `alembic upgrade head`

### 阶段 2: 核心评分逻辑（US1 + US2 基础，可纯单元测试）

**目的**: 不依赖 DB 的纯函数评分逻辑，最先实施并通过测试

- T006 编写 `test_diagnosis_scorer.py`（TDD 先行）
- T007 实施 `src/services/diagnosis_scorer.py`：偏差等级、维度得分、综合评分

### 阶段 3: LLM 建议生成（可 mock 单元测试）

**目的**: LLM advisor 模块，独立于主流程可测试

- T008 编写 `test_diagnosis_llm_advisor.py`（mock LlmClient）
- T009 实施 `src/services/diagnosis_llm_advisor.py`：prompt 构造 + LlmClient 调用

### 阶段 4: 诊断主服务（US1 + US2 核心流程）

**目的**: `DiagnosisService` 编排完整诊断流程

- T010 编写诊断服务集成测试（TDD 先行）
- T011 实施 `src/services/diagnosis_service.py`
- T012 实施 `src/api/routers/diagnosis.py` + 注册路由
- T013 编写契约测试

### 阶段 5: 教练视频验证（US3）

**目的**: 用已有教练视频验证系统基准可信度（SC-001）

- T014 编写教练视频验证集成测试

### 阶段 6: 错误处理与边界情况

**目的**: 无标准返回错误、视频无效处理

- T015 补充错误路径单元测试
- T016 确认 FR-008 错误响应实现

### 阶段 7: 收尾

**目的**: 注册路由、日志、quickstart 手工验证

- T017 注册 `diagnosis_router` 到 `src/api/main.py`
- T018 quickstart 手工 curl 验证

---

## 依赖关系

```
T001（确认目录）
  └→ T002（迁移）→ T003（ORM）→ T004（导出）→ T005（验证迁移）
                                                    └→ T011（DiagnosisService）
T006（scorer 测试）→ T007（scorer 实现）─────────────────↑
T008（advisor 测试）→ T009（advisor 实现）────────────────↑
T010（服务集成测试）→ T011 → T012（路由）→ T013（契约测试）
T014（教练视频验证）依赖 T012
T015, T016 依赖 T011
T017 依赖 T012
T018 依赖 T017
```

## API 契约

### POST /api/v1/diagnosis

**请求**:
```json
{
  "tech_category": "forehand_topspin",     // 必填，ActionType 枚举值
  "video_path": "cos://bucket/path.mp4"   // 必填，COS key 或本地路径
}
```

**响应 200**:
```json
{
  "report_id": "uuid",
  "tech_category": "forehand_topspin",
  "standard_id": 1,
  "standard_version": 2,
  "overall_score": 85.0,
  "strengths": ["elbow_angle", "contact_timing"],
  "dimensions": [
    {
      "dimension": "elbow_angle",
      "measured_value": 92.3,
      "ideal_value": 95.0,
      "standard_min": 85.0,
      "standard_max": 105.0,
      "unit": "°",
      "score": 100.0,
      "deviation_level": "ok",
      "deviation_direction": "none",
      "improvement_advice": null
    },
    {
      "dimension": "swing_trajectory",
      "measured_value": 0.45,
      "ideal_value": 0.65,
      "standard_min": 0.55,
      "standard_max": 0.80,
      "unit": "ratio",
      "score": 42.0,
      "deviation_level": "significant",
      "deviation_direction": "below",
      "improvement_advice": "您的挥拍轨迹偏短（0.45），理想值为 0.65。建议增大引拍幅度..."
    }
  ],
  "created_at": "2026-04-23T10:00:00Z"
}
```

**响应 422**: tech_category 不合法
```json
{"detail": [{"loc": ["body", "tech_category"], "msg": "...", "type": "value_error"}]}
```

**响应 404**: 技术类别无 active 标准
```json
{"error": "standard_not_found", "detail": "No active standard for tech_category: xxx"}
```

**响应 400**: 视频无法提取有效动作
```json
{"error": "extraction_failed", "detail": "No valid action segments detected in video"}
```

## 注意事项

- 评分线性插值系数（100/60/0 区间）在 `diagnosis_scorer.py` 顶部定义为常量，便于调参。
- LLM 调用使用 `asyncio.get_event_loop().run_in_executor(None, ...)` 包装同步 `LlmClient.chat()`，保持 FastAPI 异步兼容。
- 视频处理通过临时目录（`tempfile.mkdtemp()`）管理，处理完毕后清理。
- 60 秒时限内，LLM 调用数量 = 偏差维度数（通常 1–4 次），每次 ≤ 10 秒，可行。
- 标准缺失（FR-008）在服务层检查，不进入提取流程。
