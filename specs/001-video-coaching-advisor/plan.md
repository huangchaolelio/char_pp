# 实施计划: 视频教学分析与专业指导建议

**分支**: `001-video-coaching-advisor` | **日期**: 2026-04-17 | **规范**: [spec.md](./spec.md)
**输入**: 来自 `/specs/001-video-coaching-advisor/spec.md` 的功能规范

## 摘要

本功能构建乒乓球AI智能教练后端服务，主要需求：

1. **专家视频知识提取**（US1 P1）：通过 COS 接收教练教学视频，提取结构化技术要点，构建版本化知识库，经专家审核后激活
2. **运动员偏差诊断**（US2 P2）：接收运动员上传视频，与激活知识库定量比对，输出偏差报告（偏差值/方向/置信度/稳定性标注）
3. **结构化指导建议**（US3 P3）：基于偏差报告生成按影响程度排序的可操作建议，关联专家标准值

**技术方法**（research.md 研究结论）：
- 姿态估计：**双后端架构**——GPU 环境优先使用 YOLOv8-pose（ultralytics 8.4.39），CPU fallback 使用 MediaPipe Pose 0.10.33
- 视频处理：OpenCV 4.13.0 逐帧读取 + FFmpeg 元数据提取
- 异步任务：Celery 5.6.3 + Redis，任务超时 6 分钟（soft_time_limit=360s，与 SC-004 对齐）
- 存储：PostgreSQL（结构化数据）+ 腾讯云 COS SDK（专家视频源）
- API：FastAPI 0.136.0 + Pydantic v2
- 数据保留：12 个月 + 用户主动软删除；video_storage_uri AES-256-GCM 列加密

## 技术背景

**语言/版本**: Python 3.11.15（`/opt/conda/envs/coaching/bin/python3.11`，`requires-python = ">=3.11"`）
**主要依赖**:
- FastAPI 0.136.0 + uvicorn（HTTP 服务）
- Celery 5.6.3 + redis 6.4.0（异步任务队列）
- SQLAlchemy 2.0.49 async + asyncpg（ORM + 异步数据库驱动）
- Alembic 1.18.4（数据库迁移）
- Pydantic 2.13.2（数据验证与序列化）
- **PyTorch 2.4.1 + CUDA 12.1 build**（GPU 推理，MKL 2022.1.0）
- **ultralytics 8.4.39**（YOLOv8-pose，GPU 后端）
- **mediapipe 0.10.33**（CPU fallback 后端）
- opencv-python 4.13.0（视频帧读取）
- cos-python-sdk-v5（腾讯云 COS 客户端）
- psycopg2 2.9.11（PostgreSQL 同步驱动，Alembic 迁移用）

**存储**:
- PostgreSQL（结构化数据：任务/知识库/偏差报告/建议）
- Redis（Celery broker + backend）
- 腾讯云 COS（专家教练视频文件，cos-python-sdk-v5）
- 本地临时目录 `/tmp/coaching-advisor/`（视频处理中间文件，处理后即清除）

**测试**: pytest 9.0.3 + pytest-asyncio（asyncio_mode=auto）+ httpx 0.28.1（ASGI 测试客户端）
**目标平台**: Linux 服务器（Tesla T4 GPU，驱动 535.161.07，CUDA 12.1 可用）
**项目类型**: 后端 Web 服务 + 异步任务处理器（FastAPI + Celery Worker）
**性能目标**:
- 单段视频（≤5 分钟）从提交到结果可获取 **≤5 分钟**（SC-004）
- Celery 任务 soft_time_limit = 360s（6 分钟，SC-004 ≤5 分钟 + 1 分钟调度/IO 缓冲；480s 为历史值，已收紧以与 SC-004 对齐）
- 姿态估计：YOLOv8 GPU 批处理 16 帧/批；MediaPipe CPU 单帧 <30ms
**约束条件**:
- 输入视频最低帧率 15fps，最低分辨率 854×480（低于此拒绝）
- 置信度阈值 0.7（低于此标注 is_low_confidence=true）
- 知识库同时只能有一个 active 版本
- 所有凭证通过环境变量注入，禁止硬编码
**规模/范围**: v1 仅支持 2 类动作（forehand_topspin / backhand_push）；后端纯算法服务，无前端界面

## 章程检查

*门控: 必须在阶段 0 研究前通过。阶段 1 设计后重新检查。*

### 初始检查（阶段 0 前）

| 原则 | 检查项 | 状态 |
|------|--------|------|
| I. 规范驱动 | spec.md 包含优先排序用户故事（P1/P2/P3）、可衡量成功标准（SC-001~006）、明确假设 | ✅ 通过 |
| I. 规范驱动 | 用户故事以后端服务/算法能力为中心，不含前端验收前提 | ✅ 通过 |
| II. 测试优先 | spec.md 要求契约/集成/单元/基准测试，均在 tasks.md 中有对应任务（T047~T055） | ✅ 通过 |
| II. 测试优先 | SC-001/SC-002 精准度指标有量化值（≥90% / ≥85%），有基准测试任务（T047） | ✅ 通过 |
| IV. 简洁性 | 双后端（YOLOv8 + MediaPipe）引入了额外复杂度，见复杂度跟踪表 | ✅ 已记录 |
| VI. 模型治理 | AI 模型版本已在本 plan.md 技术背景中明确锁定 | ✅ 通过 |
| VII. 数据隐私 | video_storage_uri AES-256-GCM 加密（T043）；12 个月数据保留（T042）；章程隐私要求 | ✅ 通过 |
| VIII. 精准度 | spec.md SC-001/SC-002 包含量化精准度指标；基准数据集任务（T044~T047）已规划 | ✅ 通过 |
| 范围边界 | 后端纯算法服务，无前端任务，无前端路径（frontend/、web/）创建 | ✅ 通过 |
| AI/ML 约束 | 模型推理依赖（PyTorch 2.4.1/ultralytics 8.4.39/mediapipe 0.10.33）版本锁定 | ✅ 通过 |
| 数据治理 | 评估基准数据集与训练数据严格隔离；基准数据版本化管理（docs/benchmarks/） | ✅ 通过 |

### 重新检查（阶段 1 设计后）

| 原则 | 检查项 | 状态 |
|------|--------|------|
| I. 规范驱动 | data-model.md 6 个实体完整定义，与 spec.md 关键实体一一对应 | ✅ 通过 |
| II. 契约测试 | contracts/api.md 定义所有 8 个 API 端点，T052 契约测试覆盖 | ✅ 通过 |
| VI. 模型可解释性 | API 响应包含 confidence/reliability_level/deviation_direction 等可解释字段 | ✅ 通过 |
| VII. 数据隐私 | 运动员视频不持久化存储，仅临时目录处理后即清除；视频路径列加密 | ✅ 通过 |

**章程检查结论：所有门控条件通过，无严重违规，可进入实施阶段。**

## 项目结构

### 文档（此功能）

```
specs/001-video-coaching-advisor/
├── plan.md              # 此文件（/speckit.plan 命令输出）
├── spec.md              # 功能规范
├── research.md          # 阶段 0 输出（已完成）
├── data-model.md        # 阶段 1 输出（已完成）
├── quickstart.md        # 阶段 1 输出（已完成）
├── contracts/           # 阶段 1 输出（已完成）
│   └── api.md
└── tasks.md             # 阶段 2 输出（已完成，56 个任务）
```

### 源代码（仓库根目录）

```
src/
├── api/
│   ├── main.py                     # FastAPI 应用入口
│   ├── routers/
│   │   ├── tasks.py                # POST/GET/DELETE /tasks/*
│   │   └── knowledge_base.py       # GET/POST /knowledge-base/*
│   └── schemas/
│       ├── task.py                 # 请求/响应 Pydantic 模型
│       └── knowledge_base.py
├── models/
│   ├── analysis_task.py            # AnalysisTask ORM
│   ├── expert_tech_point.py        # ExpertTechPoint ORM
│   ├── tech_knowledge_base.py      # TechKnowledgeBase ORM
│   ├── athlete_motion_analysis.py  # AthleteMotionAnalysis ORM（US2）
│   ├── deviation_report.py         # DeviationReport ORM（US2）
│   └── coaching_advice.py          # CoachingAdvice ORM（US3）
├── services/
│   ├── cos_client.py               # COS 下载封装
│   ├── video_validator.py          # 视频质量门控
│   ├── pose_estimator.py           # 双后端姿态估计（YOLOv8/MediaPipe）
│   ├── action_segmenter.py         # 动作片段分割
│   ├── action_classifier.py        # 规则分类器 v1
│   ├── tech_extractor.py           # 技术要点提取
│   ├── knowledge_base_svc.py       # 知识库版本管理
│   ├── deviation_analyzer.py       # 偏差计算（US2）
│   └── advice_generator.py         # 建议生成（US3）
├── workers/
│   ├── celery_app.py               # Celery 配置 + Beat 调度
│   ├── expert_video_task.py        # Celery 任务：专家视频处理
│   └── athlete_video_task.py       # Celery 任务：运动员视频处理
├── db/
│   ├── session.py                  # SQLAlchemy 异步引擎 + AsyncSession
│   └── migrations/
│       ├── env.py
│       └── versions/
│           └── 0001_initial_schema.py
└── config.py                       # pydantic-settings 配置单例

tests/
├── unit/
│   ├── conftest.py
│   ├── test_cos_client.py
│   ├── test_video_validator.py
│   ├── test_deviation_analyzer.py
│   ├── test_advice_generator.py
│   ├── test_tasks_router.py
│   └── test_knowledge_base_router.py
├── integration/
│   ├── test_expert_pipeline.py
│   ├── test_athlete_pipeline.py
│   └── test_data_retention.py
├── contract/
│   └── test_api_contracts.py
└── benchmarks/
    └── test_accuracy_benchmarks.py

docs/
└── benchmarks/
    ├── README.md
    ├── expert_annotation_v1.json   # SC-001 人工标注基准
    └── deviation_annotation_v1.json # SC-002 人工标注基准
```

**结构决策**: 选用单一后端服务结构（选项 1），符合章程范围边界（纯后端，无 frontend/ 路径）。
目录已按 `src/api/`、`src/models/`、`src/services/`、`src/workers/`、`src/db/` 组织，
与章程路径约定（`src/`、`tests/`、`src/algorithms/`等）完全一致。

## 复杂度跟踪

> 章程 IV 要求：超出规范最简需求的复杂度决策必须在此记录，说明精度或业务需求理由。

| 违规 | 为什么需要 | 拒绝更简单替代方案的原因 |
|------|------------|--------------------------|
| **双后端姿态估计（YOLOv8 + MediaPipe）** | research.md 原始选型为 MediaPipe CPU-only。实施阶段发现 Tesla T4 GPU 可用（14.6 GB VRAM），YOLOv8-pose 在 GPU 上批处理精度显著优于 MediaPipe（尤其在快速运动帧），直接影响 SC-001（≥90% 维度覆盖率）和 SC-002（≥85% 偏差一致率）能否达标 | 精度是唯一正当理由（章程 IV）：YOLOv8 GPU 推理的 17 关键点检测在运动视频中的精度高于 MediaPipe 33 关键点（MediaPipe 针对静态或慢速场景优化）；MediaPipe 保留为 CPU fallback，保证无 GPU 环境下仍可运行；无 GPU 则单后端 MediaPipe，复杂度降为最简 |
| **AES-256-GCM 应用层列加密（video_storage_uri）** | 章程 VII 要求用户运动视频路径等敏感数据 MUST 在存储中加密处理 | PostgreSQL 透明数据加密（TDE）在当前部署环境未启用；应用层 SQLAlchemy TypeDecorator 封装 AES-256-GCM 是满足章程 VII 合规要求的最小实现，不依赖数据库配置 |
| **Celery Beat 定时清理任务** | 章程 VII + spec.md SC-006：数据保留策略 MUST 自动执行，不能依赖手工操作 | 简单 cron job 需要额外系统配置；Celery Beat 与已有 Celery worker 共享基础设施，零额外依赖 |

## 阶段概览

| 阶段 | 目标 | 状态 |
|------|------|------|
| 0. 研究 | 技术选型决策（姿态估计框架、存储、队列） | ✅ 完成 → research.md |
| 1. 设计 | 数据模型、API 契约、快速入门文档 | ✅ 完成 → data-model.md / contracts/ / quickstart.md |
| 2. 任务分解 | 56 个实现任务，含并行标记和故事映射 | ✅ 完成 → tasks.md |
| 3. 实施 US1 | 专家视频处理 → 知识库提取 → 审核激活（T001~T030） | ✅ 完成 |
| 4. 实施 US2 | 运动员偏差分析（T031~T037，T050） | ✅ 完成 |
| 5. 实施 US3 | 指导建议生成（T038~T041，T051） | ✅ 完成 |
| 6. 完善 | 数据保留/加密/基准测试/契约测试/集成测试（T042~T056） | ✅ 完成（基准数据集 T045/T046 占位，待人工标注填充） |

## 精准度基准计划

（章程 VIII 要求：精准度基准 MUST 在首次实现时建立）

| 成功标准 | 指标 | 基准数据集 | 验证任务 | 状态 |
|----------|------|-----------|---------|------|
| SC-001 | 技术维度覆盖率 ≥ 90% | docs/benchmarks/expert_annotation_v1.json | T047（tests/benchmarks/test_accuracy_benchmarks.py） | ⚠️ 数据集待填充（需人工标注视频） |
| SC-002 | 偏差一致率 ≥ 85% | docs/benchmarks/deviation_annotation_v1.json | T047 | ⚠️ 数据集待填充（需标注偏差视频） |
| SC-004 | 单视频处理 ≤ 5 分钟 | 端到端实测 | quickstart.md 手工验证 | ⚠️ 无自动化测试，需手工验证 |
| SC-005 | 拒绝率 ≥ 99% | unit test 逻辑验证 | T049（test_video_validator.py） | ✅ 逻辑路径已覆盖 |

**注**: SC-001/SC-002 benchmark 测试（T047）因无标注视频数据而被 skip（`@pytest.mark.benchmark`），
一旦提供标注数据集即可自动运行。SC-003（专家评分 ≥4/5）为人工评审指标，不纳入自动化 CI。

## 模型治理记录

（章程 VI 要求：所有 AI 模型 MUST 具有明确版本标识）

| 模型 | 版本 | 用途 | 推理延迟基准 | 精度指标 |
|------|------|------|------------|---------|
| YOLOv8-pose（ultralytics） | 8.4.39（`yolov8n-pose.pt`） | GPU 姿态估计主后端 | <15ms/帧（Tesla T4，batch=16） | COCO val AP_pose ≈ 50.4（yolov8n-pose 官方） |
| MediaPipe Pose | 0.10.33 | CPU fallback 姿态估计 | <30ms/帧（CPU） | 33 关键点，运动场景 visibility 阈值 0.5 |

**降级策略**: `_detect_backend("auto")` — 优先 YOLOv8（`torch.cuda.is_available()` + ultralytics 可导入），
否则 fallback MediaPipe；两者均不可用时抛出 `RuntimeError`。

**模型文件管理**: `*.pt`、`*.pth`、`*.onnx`、`*.weights` 已加入 `.gitignore`，
不直接提交 Git 对象存储；YOLOv8 模型首次运行时由 ultralytics 自动下载。
