# 实施计划: 教练视频技术分类数据库

**分支**: `008-coach-tech-classification` | **日期**: 2026-04-21 | **规范**: [spec.md](spec.md)
**输入**: 来自 `/specs/008-coach-tech-classification/spec.md` 的功能规范

## 摘要

扫描 COS `COS_VIDEO_ALL_COCAH` 路径下所有教练课程视频，基于关键词规则（优先）+ LLM 兜底（venus_proxy）对每个视频进行精细乒乓球技术分类，并将结果持久化到新建的 `coach_video_classifications` 表。提供 REST API 支持查询、统计、人工修正。本功能不触发知识库提取。

## 技术背景

**语言/版本**: Python 3.11  
**主要依赖**: FastAPI 0.111+、SQLAlchemy 2.0+、Celery 5.4+、cos-python-sdk-v5、requests（venus_proxy）  
**存储**: PostgreSQL（新表 `coach_video_classifications`，迁移 0009）  
**测试**: pytest 8.2+、pytest-asyncio、httpx  
**目标平台**: Linux 服务器（Tesla T4，coaching conda 环境）  
**项目类型**: 后端 Web 服务（FastAPI + Celery）  
**性能目标**: 全量扫描 500 视频 ≤ 5 分钟；查询接口 p95 ≤ 500ms  
**约束条件**: 不触发视频内容分析；LLM 仅处理规则未命中记录（约 10%）  
**规模/范围**: ~600 个视频文件，20 个教练课程系列  

## 章程检查

*门控: 必须在阶段 0 研究前通过。阶段 1 设计后重新检查。*

| 原则 | 检查项 | 状态 |
|------|--------|------|
| I. 规范驱动 | spec.md 存在且含量化精准度指标（SC-002 ≥ 90%） | ✅ |
| II. 测试优先 | 集成测试（扫描流程端到端）、单元测试（分类器）在实现前定义 | ✅ 计划中 |
| III. 增量交付 | P1（扫描+分类入库）可独立测试和演示 | ✅ |
| IV. YAGNI | 无定时调度、无前端、无额外抽象 | ✅ |
| V. 可观测性 | 每条分类记录含 source+confidence；扫描日志含 inserted/skipped/error 计数 | ✅ |
| VI. AI 模型治理 | LLM 分类标注 source=llm + confidence；置信度 < 0.5 降级为 unclassified | ✅ |
| VII. 数据隐私 | 无用户个人数据；仅处理教练课程视频元数据 | ✅ N/A |
| VIII. 精准度指标 | SC-002: 人工抽检 20 条准确率 ≥ 90%（rule ≥ 95%，llm ≥ 85%） | ✅ |
| 范围边界 | 无前端任务；后端 API + Celery task | ✅ |
| Python 环境 | 使用 coaching conda 环境，通过 pyproject.toml 管理依赖 | ✅ |

**阶段 1 设计后重检**: ✅ data-model.md 和 contracts/api.md 符合章程要求，无违规项。

## 项目结构

### 文档（此功能）

```
specs/008-coach-tech-classification/
├── plan.md              # 此文件
├── spec.md              # 功能规范
├── research.md          # 技术决策（阶段 0）
├── data-model.md        # 数据模型（阶段 1）
├── contracts/
│   └── api.md           # API 契约（阶段 1）
└── tasks.md             # 任务分解（/speckit.tasks 生成）
```

### 源代码（新增/修改文件）

```
src/
├── models/
│   └── coach_video_classification.py    # 新增 ORM 模型
├── services/
│   ├── tech_classifier.py               # 新增 关键词规则+LLM 分类器
│   └── cos_classification_scanner.py    # 新增 COS 扫描器
├── workers/
│   └── classification_task.py           # 新增 Celery scan task
├── api/
│   └── routers/
│       ├── classifications.py           # 新增 路由
│       └── __init__.py                  # 修改 注册新路由
│   └── schemas/
│       └── classification.py            # 新增 Pydantic schemas
└── db/
    └── migrations/versions/
        └── 0009_coach_video_classifications.py  # 新增 迁移

config/
├── coach_directory_map.json             # 新增 目录名→教练名映射
└── tech_classification_rules.json       # 新增 关键词规则

tests/
├── unit/
│   └── test_tech_classifier.py          # 新增 分类器单元测试
└── integration/
    └── test_classification_scan.py      # 新增 扫描流程集成测试
```

## 实现阶段

### 阶段 Setup: 数据库迁移 + 配置文件

1. 创建迁移文件 `0009_coach_video_classifications.py`
2. 创建 `config/coach_directory_map.json`（20 个教练映射）
3. 创建 `config/tech_classification_rules.json`（21 类规则）
4. 运行迁移验证表结构

### 阶段 Core P1: 扫描 + 分类核心逻辑

1. `src/models/coach_video_classification.py` — ORM 模型
2. `src/services/tech_classifier.py` — `TechClassifier`（规则+LLM）
3. `src/services/cos_classification_scanner.py` — `CosClassificationScanner`（全量/增量）
4. `src/workers/classification_task.py` — Celery `@shared_task`

**测试优先**（对应 TDD）:
- `tests/unit/test_tech_classifier.py` — 覆盖关键词命中、多技术标签、LLM 兜底

### 阶段 Core P2: API 接口

1. `src/api/schemas/classification.py` — Pydantic 请求/响应模型
2. `src/api/routers/classifications.py` — 5 个端点
3. `src/api/main.py` — 注册新路由

**测试优先**:
- `tests/integration/test_classification_scan.py` — 扫描触发→入库→查询端到端

### 阶段 Polish: 验证与文档

1. 人工抽检 20 条记录，验证 SC-002（准确率 ≥ 90%）
2. 性能验证：全量扫描耗时，查询响应时间

## 复杂度跟踪

| 决策 | 理由 |
|------|------|
| LLM 兜底分类 | 纯规则 unclassified 率预估 > 5%，不满足 SC-001；venus_proxy 已有基础设施，增加复杂度可控 |
| 独立分类表（不扩展现有表） | 职责隔离，避免与 analysis_tasks/video_classifications 耦合 |

## 依赖与前提

- COS 凭证已在 `.env` 中配置（`COS_SECRET_ID/KEY/REGION/BUCKET`）
- Venus proxy 已在 `.env` 中配置（`venus_token/venus_base_url/venus_model`）
- 数据库迁移 0008 已执行（coaching 环境）
- coaching conda 环境已激活
