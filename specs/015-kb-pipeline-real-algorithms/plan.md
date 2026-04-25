# 实施计划: Feature-014 知识库提取流水线 — 真实算法接入

**分支**: `015-kb-pipeline-real-algorithms` | **日期**: 2026-04-25 | **规范**: [spec.md](./spec.md)
**输入**: 来自 `/specs/015-kb-pipeline-real-algorithms/spec.md` 的功能规范

## 摘要

Feature-014 交付了 DAG 编排 + 并行 + 冲突分离 + 重跑 + 通道兼容的**完整骨架**，但 4 个 step executor（`pose_analysis` / `audio_transcription` / `visual_kb_extract` / `audio_kb_extract`）仍是 scaffold——读写空 artifact，产出 `note="scaffold_output_pending_feature014_us2_implementation"`。本 Feature **只做接线**：把 scaffold 替换为 Feature-002 已有模块（`pose_estimator` / `speech_recognizer` / `action_segmenter` / `action_classifier` / `tech_extractor` / `transcript_tech_parser` / `llm_client`）的真实调用。

核心工作集中在 4 个 executor 文件 + 3–5 个参考视频回归测试 + 1 份 verification.md。**零数据库迁移、零新依赖、零算法改进**——只是把已有乐高积木按 Feature-014 定义的 artifact 契约接起来。

## 技术背景

**语言/版本**: Python 3.11（`/opt/conda/envs/coaching`，与 Feature-014 一致）
**主要依赖**（全部已存在，本 Feature 不新增）:
- `ultralytics>=8.0.0`（YOLOv8-pose 可选，`pose_backend=auto` 时优先）
- `mediapipe>=0.10.14`（CPU fallback）
- `openai-whisper==20231117`（本地语音识别）
- `openai>=1.0.0`（LLM HTTP 客户端，复用于 Venus Proxy）
- `tenacity>=8.2.0`（由 Feature-014 引入，已可用于 I/O 重试）
- `opencv-python-headless`（视频质量预检）

**存储**: 
- PostgreSQL 无迁移（schema 在 Feature-014 已定）
- Worker 本地 FS `/tmp/coaching-advisor/jobs/{job_id}/`：新增 `pose.json`（真实 33 关键点 × 帧数）+ `transcript.json`（真实 Whisper 输出）

**测试**: pytest + pytest-asyncio；3–5 个参考视频集用于回归（由运维提供 fixture 目录，本仓库不入 git）

**目标平台**: Linux 服务器（Docker / 虚拟机），与 Feature-014 同宿主

**项目类型**: Web 服务（后端算法 + API）

**性能目标**（源自 spec SC）:
- SC-001: 一段 ≥10s 视频 → visual 条目 ≥2 + audio 条目 ≥1（有讲解时）
- SC-002: 10 分钟视频 **总耗时 ≤ Feature-002 旧流程 × 90%**（Feature-014 `SC-003` 实证）
- SC-003: 参考视频集每个视频条目数 ∈ 预定义范围（5–30 典型），0 异常（替代旧 SC-007）
- SC-005: 视觉路批次成功率 ≥95%（N=10）
- SC-006: 音频路 LLM 抽取批次成功率 ≥85%（有讲解子集）

**约束条件**（源自 spec clarifications）:
- 视频质量不达标立即 fail fast（FR-006），错误码前缀 `VIDEO_QUALITY_REJECTED:`
- artifact JSON 无 schema 版本，下游容错解析
- LLM prompt 完全复用 `transcript_tech_parser`，只追加说明不改原格式
- 视觉路是关键路径；音频路允许失败（降级合并）
- 复用 Feature-014 的重试策略（I/O 3 次 × 30s，CPU 不重试）

**规模/范围**:
- 4 个 executor 文件实质改动（`src/services/kb_extraction_pipeline/step_executors/`）
- 1 个新合并辅助（`merge_kb.py` 处理 `visual+audio` 条目的 action_type 从 F-002 算法派生）
- 1 个参考视频集回归脚本（`scripts/run_reference_regression.py`）
- 0 新模型、0 新迁移、0 新路由

## 章程检查

*门控: 必须在阶段 0 研究前通过。阶段 1 设计后重新检查。*

### I. 规范驱动开发 ✅

- `specs/015-kb-pipeline-real-algorithms/spec.md` 已通过 clarify 会话，5 条 Clarifications 集成
- 功能分支 `015-kb-pipeline-real-algorithms` 符合命名规范
- 所有 4 个用户故事以后端算法能力为中心（visual KB 产出 / audio KB 产出 / 条目数回归 / 耗时对比），无前端依赖

### II. 测试优先 ✅

沿用 Feature-014 的 TDD 实践：
- **参考视频集集成测试**（`tests/integration/test_real_algorithms_regression.py`）：US3 覆盖
- **视觉路端到端**（`tests/integration/test_visual_kb_real.py`）：US1 覆盖；用小体积 fixture 视频
- **音频路端到端**（`tests/integration/test_audio_kb_real.py`）：US2 覆盖
- **视频质量预检单元**（`tests/unit/test_video_quality_gate.py`）：FR-006 覆盖
- **容错 artifact 解析单元**（`tests/unit/test_artifact_parsers.py`）：FR-002 / FR-007 覆盖

AI 模型精度验证：复用 Feature-002 已建立的基准（`docs/benchmarks/`），本 Feature 不引入新模型、不调整阈值。

### III. 增量交付 ✅

4 个 US 可独立交付：
- **US1 MVP**（视觉路真实）：可独立演示"视频 → 真实 ExpertTechPoint 条目"
- **US2**（音频路真实）：在 US1 基础上加音频增强
- **US3**（参考视频回归）：跨 executor 的回归保障
- **US4**（10 分钟视频耗时验证）：性能实证

实现任务按"executor 逐个替换 + 每次替换配对测试"推进。

### IV. 简洁性与 YAGNI ✅

- **完全不改 Feature-014 架构**（orchestrator / merger / retry_policy / session 管理全部复用）
- **完全不动 Feature-002 算法模块**（tech_extractor / pose_estimator 等原样调用）
- **不新增包依赖**
- **不新增数据库字段**
- LLM prompt 沿用，只做小修补（Q5 决策）
- artifact JSON 不做版本化（Q4 决策）

### V. 可观测性 ✅

每个 executor 的 `output_summary` 必须暴露真实算法后端（FR-014）：
- `pose_analysis`：`backend=yolov8|mediapipe`、`fps`、`resolution`、`keypoints_frame_count`
- `audio_transcription`：`whisper_model`、`language_detected`、`snr_db`、`quality_flag`、`transcript_chars`
- `audio_kb_extract`：`llm_model`（venus-xxx / gpt-xxx）、`kb_items_count`

结构化错误码（FR-006, FR-016）：失败时 `error_message` 以 `{COMPONENT}:` 前缀开头（`VIDEO_QUALITY_REJECTED:` / `WHISPER_LOAD_FAILED:` / `LLM_UNCONFIGURED:` / `LLM_JSON_PARSE:` / `POSE_NO_KEYPOINTS:`）。

### VI. AI 模型治理 ✅

- 本 Feature 不引入新模型、不改阈值
- Whisper 模型版本受 `.env::WHISPER_MODEL` 控制
- 姿态后端受 `.env::POSE_BACKEND` 控制（`auto|yolov8|mediapipe`）
- LLM 优先级受 `.env::VENUS_TOKEN`/`OPENAI_API_KEY` 驱动
- 所有模型推理复用 Feature-002 已验证的封装

### VII. 数据隐私 ✅

- 教练视频为系统内部数据，非用户个人视频
- 中间 artifact 保留期沿用 Feature-014（success 24h / failed 7d）
- 无新增用户数据采集

### VIII. 后端算法精准度 ✅

SC-003/SC-005/SC-006 为可量化指标：
- 参考视频集条目数范围回归（SC-003）
- 视觉路批次成功率 ≥95%（SC-005）
- 音频路批次成功率 ≥85%（SC-006）

复用 Feature-002 建立的精度基准；本 Feature 的成功标准是**对等保持**而非新标定。

### 附加约束 — Python 环境隔离 ✅

- 所有 pip/pytest/python 调用使用 `/opt/conda/envs/coaching` 虚拟环境
- 不向系统 Python 或 Conda 全局环境安装包
- 本 Feature 不新增依赖，无包安装操作

**门控结论：✅ 通过。无章程违规，无需填写复杂度跟踪表。**

## 项目结构

### 文档（此功能）

```
specs/015-kb-pipeline-real-algorithms/
├── plan.md              # 此文件
├── spec.md              # 功能规范（已通过 clarify，5 条 Clarifications）
├── research.md          # 阶段 0 输出（见下）
├── data-model.md        # 阶段 1 输出（artifact JSON 字段 + KB item dict 结构）
├── quickstart.md        # 阶段 1 输出（参考视频集准备 + 回归运行步骤）
├── checklists/
│   └── requirements.md  # 已完成（16/16 通过）
├── scripts/
│   └── run_reference_regression.py  # US3 参考视频回归脚本
└── tasks.md             # 阶段 2 输出（/speckit.tasks 创建，本命令不创建）
```

### 源代码（仓库根目录）

**没有新文件**，只改以下 4 个 executor：

```
src/services/kb_extraction_pipeline/step_executors/
├── pose_analysis.py          # 改造：接入 pose_estimator.estimate_pose + 视频质量预检
├── audio_transcription.py    # 改造：接入 speech_recognizer.SpeechRecognizer
├── visual_kb_extract.py      # 改造：接入 action_segmenter + action_classifier + tech_extractor
└── audio_kb_extract.py       # 改造：接入 transcript_tech_parser + llm_client
```

**复用（零修改）**：
```
src/services/
├── pose_estimator.py         # Feature-002 (MediaPipe + YOLOv8 双后端)
├── speech_recognizer.py      # Feature-002 (Whisper)
├── action_segmenter.py       # Feature-002
├── action_classifier.py      # Feature-002
├── tech_extractor.py         # Feature-002 (姿态 → 4 维度)
├── transcript_tech_parser.py # Feature-002 (LLM 抽取)
├── llm_client.py             # Feature-002 (Venus → OpenAI)
├── video_validator.py        # Feature-002 (视频质量预检)
├── kb_extraction_pipeline/
│   ├── orchestrator.py       # Feature-014，不改
│   ├── merger.py             # Feature-014，不改（F14KbMerger 逻辑已正确）
│   └── retry_policy.py       # Feature-014，不改
```

**测试新增**：
```
tests/
├── unit/
│   ├── test_video_quality_gate.py     # FR-006 预检逻辑单元
│   └── test_artifact_parsers.py       # FR-002/FR-007 容错解析单元
└── integration/
    ├── test_visual_kb_real.py         # US1 端到端（小 fixture 视频）
    ├── test_audio_kb_real.py          # US2 端到端
    └── test_real_algorithms_regression.py  # US3 参考视频集回归
```

**结构决策**: 保持 Feature-014 已有结构不变；本 Feature 只动 4 个 executor 文件 + 加测试 + 加一个 spec 脚本。章程 IV（YAGNI）驱动的最小干预。

## 复杂度跟踪

> 无章程违规，无需填写复杂度跟踪表。
