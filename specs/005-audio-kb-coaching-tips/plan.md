# 实施计划: Feature-005 音频技术要点提炼与教学建议知识库

**分支**: `005-audio-kb-coaching-tips` | **日期**: 2026-04-20 | **规范**: [spec.md](spec.md)
**输入**: 来自 `/specs/005-audio-kb-coaching-tips/spec.md` 的功能规范

## 摘要

在 Feature-002 已有音频转录（Whisper）的基础上，增加 LLM（GPT）摘要层：
- **新实体**: `TeachingTip` 表，存储从转录文本提炼的教学建议条目
- **新服务**: `TeachingTipExtractor`，调用 GPT API 判断是否含技术讲解并提炼要点
- **新 Worker 步骤**: `expert_video_task.py` 完成音频转录后自动触发提炼
- **新 API 端点**: CRUD 管理 TeachingTip + 重新触发提炼
- **扩展 AdviceGenerator**: 为运动员改进建议附加匹配的教学建议文字
- **验证基准**: 第06节正手攻球（216句转录）→ ≥3 条教学建议条目

## 技术背景

**语言/版本**: Python 3.11（coaching conda 环境）
**主要依赖**: FastAPI、SQLAlchemy 2.0 async、Celery、openai>=1.0（新增）、whisper（已有）
**存储**: PostgreSQL（async via asyncpg），新建 teaching_tips 表
**测试**: pytest + pytest-asyncio；mock LLM 响应（避免测试调用真实 API）
**目标平台**: Linux 服务器（同现有服务）
**性能目标**: 单次 LLM 提炼 ≤30 秒（SC-005），无转录时间
**约束条件**: LLM API 调用需设置 30s 超时；失败时降级为空结果，不中断视频处理
**规模/范围**: 120 个教练视频，每视频约 50-300 句转录，预期每视频产出 3-10 条教学建议

## 章程检查

**阶段 0 前检查**（必须通过）:

| 原则 | 检查项 | 状态 |
|------|--------|------|
| I (规范驱动) | spec.md 已完成，含量化成功标准 | ✅ |
| II (TDD) | TeachingTipExtractor 单元测试先于实现；API 合约测试 | ✅ 计划中 |
| III (增量交付) | US1（提炼+存储）→ US2（建议集成）→ US3（CRUD）独立可测 | ✅ |
| IV (YAGNI) | 不引入 TechPhase DB enum，用字符串；不新建 CoachingAdvice 字段 | ✅ |
| V (可观测性) | LLM 调用记录 model_version、token 数、耗时、prompt hash | ✅ 计划中 |
| VI (AI 治理) | GPT 模型版本通过配置项锁定（OPENAI_MODEL）；30s 超时+降级 | ✅ |
| VII (数据隐私) | 发给 GPT API 的是转录文本（非视频/姿态），无用户个人数据 | ✅ |
| VIII (精准度) | SC-001: ≥3条/视频；SC-004: 零噪音（纯示范视频）| ✅ |
| 范围边界 | 纯后端；无前端任务 | ✅ |
| Python 环境 | 使用 coaching conda 环境；新依赖通过 pyproject.toml 添加 | ✅ |

**阶段 1 后重新检查**: 数据模型无 knowledge_base_version 关联，通过 task_id 溯源 ✅

## 项目结构

### 文档（此功能）

```
specs/005-audio-kb-coaching-tips/
├── plan.md              ← 此文件
├── spec.md
├── research.md          ← 已生成
├── data-model.md        ← 已生成
├── contracts/
│   └── teaching-tips-api.md  ← 已生成
└── tasks.md             ← /speckit.tasks 生成
```

### 源代码变更

```
src/models/
├── teaching_tip.py              【新建】TeachingTip ORM 模型
└── __init__.py                  【修改】注册 TeachingTip

src/db/migrations/versions/
└── 0006_teaching_tips.py        【新建】Alembic 迁移

src/services/
├── teaching_tip_extractor.py    【新建】LLM 提炼服务
└── advice_generator.py          【修改】附加 TeachingTip 到改进建议

src/api/schemas/
└── teaching_tip.py              【新建】Pydantic schemas

src/api/routers/
├── teaching_tips.py             【新建】3 个 CRUD 端点 + extract-tips 端点
└── tasks.py                     【修改】GET result 附加 teaching_tips 字段

src/api/main.py                  【修改】注册 teaching_tips router

src/workers/
└── expert_video_task.py         【修改】在音频转录后自动触发提炼

src/config.py                    【修改】添加 OPENAI_API_KEY、OPENAI_MODEL 配置

tests/unit/
└── test_teaching_tip_extractor.py  【新建】mock LLM，测试提炼逻辑

tests/contract/
└── test_teaching_tips_api.py    【新建】API 合约测试
```

## 实施阶段

### 阶段 1 — 基础设施（阻塞前置）

| 任务 | 内容 |
|------|------|
| T001 | 创建 `TeachingTip` ORM 模型（teaching_tip.py） |
| T002 | 注册模型到 `src/models/__init__.py` |
| T003 | 创建 Alembic 迁移 `0006_teaching_tips.py` |
| T004 | 在 `src/config.py` 添加 `OPENAI_API_KEY`、`OPENAI_MODEL`（默认 gpt-4o-mini）配置 |
| T005 | 在 `pyproject.toml` 添加 `openai>=1.0` 依赖并安装 |

### 阶段 2 — US1：提炼服务（P1）

| 任务 | 内容 |
|------|------|
| T006 | **[TDD]** 编写 `test_teaching_tip_extractor.py`：mock LLM，覆盖"含技术讲解"、"无技术讲解"、"LLM 超时降级"3个场景 |
| T007 | 实现 `TeachingTipExtractor`（teaching_tip_extractor.py）：LLM 判断+提炼，输出 TeachingTip 列表 |
| T008 | 修改 `expert_video_task.py`：在音频转录成功后自动调用提炼，失败时降级（不阻断主流程） |
| T009 | 验证：用第06节正手攻球 task_id 调用 extract-tips 端点，确认 ≥3 条条目 |

### 阶段 3 — US2：运动员建议集成（P1）

| 任务 | 内容 |
|------|------|
| T010 | 修改 `AdviceGenerator.generate()`：按 action_type 查询 TeachingTip，human 优先，最多 3 条 |
| T011 | 修改 API schemas：`CoachingAdviceItem` 新增 `teaching_tips` 字段 |
| T012 | 修改 `tasks.py` 结果端点：填充 `teaching_tips` 列表 |
| T013 | **[合约测试]** 验证 GET /tasks/{id}/result 返回 teaching_tips 字段格式正确 |

### 阶段 4 — US3 + API（P2）

| 任务 | 内容 |
|------|------|
| T014 | 创建 `src/api/schemas/teaching_tip.py`（Request/Response schemas） |
| T015 | 创建 `src/api/routers/teaching_tips.py`：GET list、PATCH、DELETE、POST extract-tips |
| T016 | 注册 router 到 `main.py` |
| T017 | FR-008：实现 `POST /tasks/{task_id}/extract-tips` 逻辑（删旧auto保留human，重新写入） |

### 阶段 5 — 收尾

| 任务 | 内容 |
|------|------|
| T018 | 结构化日志：TeachingTipExtractor 记录 model_version、tokens、elapsed_ms、tip_count |
| T019 | 运行完整测试套件，确认无回归 |
| T020 | 端到端验证：第06节正手攻球 → 提炼 → 运动员建议含文字指导 |

## 复杂度跟踪

| 违规 | 为什么需要 | 拒绝更简单替代方案的原因 |
|------|-----------|--------------------------|
| 新增 LLM API 依赖（openai） | 自然语言教学建议无法用正则覆盖 | 正则只能提取数值参数，无法理解语义性指导描述（如"保持放松"） |
