# 实施计划: 任务处理管道重新设计与提交限流

**分支**: `013-task-pipeline-redesign` | **日期**: 2026-04-24 | **规范**: `specs/013-task-pipeline-redesign/spec.md`
**输入**: 来自 `/specs/013-task-pipeline-redesign/spec.md` 的功能规范

## 摘要

将现有 2 类任务（`expert_video` / `athlete_video`）重构为 3 类职责单一的任务（`video_classification` / `kb_extraction` / `athlete_diagnosis`），每类使用独立的 Celery 队列、独立 Worker 进程、独立通道配额与并发控制，彻底解决当前「批量 expert_video 阻塞单条任务」的痛点。同时提供统一的单条/批量提交 API（含限流 429 与批量上限 400）、数据重置能力（保留核心资产）以及通道状态监控。

**技术方法**：基于现有 Celery + Redis + FastAPI + PostgreSQL 栈，通过「多队列静态路由 + 多 Worker 进程隔离 + 数据库状态权威计数」实现任务类型解耦；新增 `task_channel_configs` 配置表驱动通道容量与并发。历史任务数据在重置操作时删除，task_type 枚举重建为 3 值枚举（不保留旧值）。

## 技术背景

**语言/版本**: Python 3.11（项目虚拟环境 `/opt/conda/envs/coaching`）
**主要依赖**: FastAPI 0.111+、SQLAlchemy 2.0 asyncio、Celery 5.4+、Redis（asyncio client）、kombu、asyncpg、Alembic、Pydantic v2
**存储**: PostgreSQL（asyncpg）、Redis（Celery broker + result backend）、腾讯云 COS（视频原文件）
**测试**: pytest（unit / integration / contract），项目虚拟环境 `python3.11 -m pytest`
**目标平台**: Linux 服务器（GPU 可选，用于 kb_extraction / athlete_diagnosis 的 Whisper 与姿态估计）
**项目类型**: 后端服务（单一后端，无前端）
**性能目标**:
  - 单条任务从提交到 processing 的 P95 等待 ≤ 5 秒（SC-001）
  - 批量 100 条提交响应 ≤ 2 秒（SC-003）
  - 三类并发总吞吐量相比旧方案提升 ≥ 50%（SC-007）
**约束条件**:
  - 硬超时 420s 仍未完成则自动标记 failed（FR-013）
  - 数据初始化 1 分钟内完成（SC-005）
  - 一类通道满不得影响其他通道 >10% 延迟劣化（SC-002）
**规模/范围**:
  - 任务表重建后从 0 条记录开始；视频总量约 1015 条（coach_video_classifications）
  - 通道默认配额：分类 5 / 知识库 50 / 诊断 20；并发：分类 1 / 知识库 2 / 诊断 2
  - 批量单次上限 100 条（BATCH_MAX_SIZE）

## 章程检查

*门控: 必须在阶段 0 研究前通过. 阶段 1 设计后重新检查。*

| 原则 | 检查项 | 结论 |
|------|--------|------|
| I. 规范驱动开发 | spec.md 已含用户故事、成功标准、假设、澄清记录（5 条） | ✅ 通过 |
| II. 测试优先 | 本功能为基础设施/流程重构，新 API 契约需合约测试（contract/），限流与幂等逻辑需集成测试 | ✅ 覆盖于阶段 1 契约 |
| III. 增量交付 | 5 个用户故事按 P1（US1/US2/US3）→ P2（US4/US5）分层，每个可独立验证 | ✅ 通过 |
| IV. 简洁性与 YAGNI | 不引入新中间件；复用 Celery/Redis；不做优先级队列等未要求特性 | ✅ 通过 |
| V. 可观测性 | FR-018 通道状态查询接口；沿用结构化 logging；错误码明确（429/409/400 已定义） | ✅ 通过 |
| VI. AI 模型治理 | 本功能不新增 AI 模型；复用既有 Whisper / 姿态估计；推理逻辑保留 | ✅ 不适用 |
| VII. 数据隐私 | `video_storage_uri` 已使用 `EncryptedString`；本次不改动加密 | ✅ 通过 |
| VIII. 算法精准度 | 本功能为管道/流量控制，不涉及算法精度指标；原有 Whisper/姿态估计精度保持 | ✅ 不适用 |
| 范围边界（后端） | 纯后端接口与 Celery 工程改造，无前端代码 | ✅ 通过 |
| Python 环境隔离 | 使用项目 `/opt/conda/envs/coaching`（与 CODEBUDDY.md 一致，项目约定） | ✅ 通过（项目历史虚拟环境惯例，非 uv，遵循既有规范） |
| 路径约定 | 复用 `src/api/routers/`、`src/services/`、`src/workers/`、`src/models/` | ✅ 通过 |

**门控结论**: 通过，可进入阶段 0。

## 项目结构

### 文档（此功能）

```
specs/013-task-pipeline-redesign/
├── plan.md              # 此文件
├── spec.md              # 功能规范（已有）
├── research.md          # 阶段 0 输出
├── data-model.md        # 阶段 1 输出
├── quickstart.md        # 阶段 1 输出
├── contracts/           # 阶段 1 输出（OpenAPI 片段）
│   ├── task_submit.yaml
│   ├── task_submit_batch.yaml
│   ├── channel_status.yaml
│   └── data_reset.yaml
├── checklists/
│   └── requirements.md  # 已有
└── tasks.md             # 阶段 2 输出（/speckit.tasks 生成，非本阶段）
```

### 源代码（仓库根目录）

```
src/
├── api/
│   ├── routers/
│   │   ├── tasks.py                     # 改造：新增 3 个提交端点 + 批量端点
│   │   ├── task_channels.py             # 新增：通道状态查询
│   │   └── admin.py                     # 新增：数据重置（需要 confirmation token）
│   └── schemas/
│       ├── classification_task.py       # 新增
│       ├── kb_extraction_task.py        # 新增
│       ├── diagnosis_task.py            # 新增（复用部分 diagnosis schema）
│       ├── task_submit.py               # 新增：统一提交响应 SubmissionResult
│       └── task_channel.py              # 新增：通道状态 schema
├── models/
│   ├── analysis_task.py                 # 改造：TaskType 枚举值重建
│   └── task_channel_config.py           # 新增：通道配置表
├── services/
│   ├── task_channel_service.py          # 新增：容量/并发配额管理 + 计数
│   ├── task_submission_service.py       # 新增：单条/批量提交 + 限流 + 幂等
│   ├── task_reset_service.py            # 新增：数据重置（带 confirmation token）
│   ├── classification_service.py        # 改造：拆出单条分类逻辑
│   ├── kb_extraction_service.py         # 新增：从 expert_video_task 拆分出知识点提取
│   └── diagnosis_service.py             # 既有：沿用
├── workers/
│   ├── celery_app.py                    # 改造：队列定义为 3 + 1（内部扫描）
│   ├── classification_task.py           # 改造：新增 classify_video 单条任务；保留 scan_cos_videos
│   ├── kb_extraction_task.py            # 新增：从 expert_video_task 拆分
│   ├── athlete_diagnosis_task.py        # 新增：从 athlete_video_task 拆分并重命名
│   ├── orphan_recovery.py               # 新增：Worker 启动时回收 orphan processing 任务（FR-014）
│   ├── expert_video_task.py             # 删除（旧类型）
│   └── athlete_video_task.py            # 删除（旧类型）；cleanup_expired_tasks 迁移到通用位置
└── db/
    └── migrations/versions/
        └── 0012_task_pipeline_redesign.py  # 新增：重建 TaskType 枚举 + 新增 task_channel_configs + 数据清理 DDL

tests/
├── contract/
│   ├── test_task_submit_single.py       # 新增
│   ├── test_task_submit_batch.py        # 新增
│   ├── test_channel_status.py           # 新增
│   └── test_data_reset.py               # 新增
├── integration/
│   ├── test_task_pipeline_isolation.py  # 新增：三类通道互不干扰（US3）
│   ├── test_task_throttling.py          # 新增：限流 + 批量上限（US2）
│   ├── test_task_idempotency.py         # 新增：重复提交幂等（边界）
│   ├── test_kb_classification_gate.py   # 新增：kb 需前置分类（FR-004a）
│   └── test_orphan_recovery.py          # 新增：Worker 崩溃重启回收 orphan（FR-014）
└── unit/
    ├── test_task_channel_service.py     # 新增
    └── test_task_submission_service.py  # 新增
```

**结构决策**: 遵循项目既有分层（`src/api/routers` + `src/api/schemas` + `src/services` + `src/workers` + `src/models`），单一后端项目，无前端代码；新任务类型每类一个 worker 文件，service 层按类拆分，避免共享可变状态（FR-004）。

## 复杂度跟踪

> 本次改造无超出规范要求的复杂度，复杂度跟踪表留空。

| 违规 | 为什么需要 | 拒绝更简单替代方案的原因 |
|------|-----------|------------------------|
| —    | —         | —                      |
