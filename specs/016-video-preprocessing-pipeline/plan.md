# 实施计划: 视频预处理流水线

**分支**: `016-video-preprocessing-pipeline` | **日期**: 2026-04-25 | **规范**: [spec.md](./spec.md)
**输入**: 来自 `/specs/016-video-preprocessing-pipeline/spec.md` 的功能规范

## 摘要

在知识库提取（Feature-014/015）前新增一个**视频预处理阶段**：下载 → probe/validate → 转码标准化 → 按 180s 切分 → 流式并发上传 COS + 同步产出整段 16 kHz mono WAV。产物按 job_id 隔离写入 `preprocessed/{cos_key}/jobs/{job_id}/`。KB 提取流水线（`pose_analysis` / `audio_transcription` / `visual_kb_extract` / `audio_kb_extract`）改造为消费预处理产物：按分段迭代推理、直接复用预置 WAV、强制 CPU Whisper，彻底避免 Feature-015 烟测暴露的 pose / Whisper OOM。本地预处理产物保留 24h 供同一视频后续 KB 提取任务复用（先 COS 存在性校验再本地读），24h 后由 `cleanup_intermediate_artifacts` 周期任务扫除。新增 `preprocessing` 为第五个任务通道（Feature-013 通道模型复用）。

## 技术背景

**语言/版本**: Python 3.11+（项目虚拟环境 `/opt/conda/envs/coaching/bin/python3.11`；禁止使用系统 3.9）
**主要依赖**:
- FastAPI 0.111+ / uvicorn 0.29+（新路由 `/tasks/preprocessing` + `/tasks/preprocessing/batch` + `/video-preprocessing/{job_id}`）
- SQLAlchemy 2.0+ async + asyncpg（两张新表 + 一个扩展列）
- Alembic（迁移 `0014_video_preprocessing_pipeline.py`）
- Celery 5.4+ + Redis（新 `preprocessing` 队列，独立 Worker）
- ffmpeg + ffprobe（转码 / 分段 / 音频提取，通过 subprocess 直调；沿用 `src.services.audio_extractor` 的 ffmpeg 封装风格）
- 腾讯云 COS SDK 1.9.30+（`src.services.cos_client`，新增 head_object / delete_object 调用）
- 现有 `src.services.video_validator`（probe 阶段复用，保留兜底）
- 现有 `src.services.speech_recognizer` + Whisper 20231117（强制 `WHISPER_DEVICE=cpu`）
- 现有 `src.services.pose_estimator`（单段推理，不改算法；调用上层按分段迭代）
- `concurrent.futures.ThreadPoolExecutor(max_workers=2)`（并发上传，沿用 Feature-007 commit `8713543` 实证模式）

**存储**:
- PostgreSQL：新增 `video_preprocessing_jobs` / `video_preprocessing_segments`；扩展 `coach_video_classifications` 新增 `preprocessed: bool`
- COS：新增前缀 `preprocessed/{original_cos_key}/jobs/{job_id}/seg_NNNN.mp4` + `audio.wav`（与原视频树隔离）
- 本地文件系统：`${EXTRACTION_ARTIFACT_ROOT}/preprocessing/{job_id}/`（默认 `/tmp/coaching-advisor/jobs/preprocessing/{job_id}/`），保留 24h 作为温缓存

**测试**: pytest 8.0+；分 unit / integration / contract 三层（沿用 Feature-014/015 目录结构）
**目标平台**: Linux 服务器（pod memcg 限制 64 GB，必须避免 torch CUDA 全虚地址占用）
**项目类型**: 后端服务（单一 src/ 结构，无前端）
**性能目标**:
- 单次预处理任务耗时 ≤ 原视频时长 × 5（SC-006）
- 同一视频第 2 次 KB 提取耗时相比第 1 次降低 ≥ 30%（SC-003）
- `pose_analysis` 单段峰值 RSS < 原视频整体处理峰值的 50%（SC-002）
- 分段时长误差 < 1 秒、累计时长误差 < 原时长 1%（SC-005）

**约束条件**:
- 内存硬上限：pod memcg 64 GB；Whisper CPU small ≈ 1–2 GB RSS，pose 单段（180s × 30fps）预算 < 20 GB RSS
- 不得引入 GPU CUDA 初始化（Feature-015 烟测实证 torch CUDA 占 58 GB 虚地址）
- 不引入显式文件锁（FR-005e 依赖 POSIX 已打开句柄语义）
- 预处理失败率 ≤ 5%（SC-004，排除源编码不支持）
- 100% 失败带结构化错误前缀（SC-007）

**规模/范围**:
- 覆盖 `coach_video_classifications` 全量（~1015 个视频 / 21 类技术）
- 默认分段 180s，典型 10 分钟视频 → 4 分段；典型 30 分钟视频 → 10 分段
- 预处理通道默认 3 并发 / 队列 20（可热更新）

## 章程检查

*门控: 必须在阶段 0 研究前通过. 阶段 1 设计后重新检查.*

### 基于章程 v1.2.2 的逐原则检查

| 原则 | 状态 | 证据 |
|------|------|------|
| **I. 规范驱动开发** | ✅ PASS | 先有 spec.md（2 轮 Clarify，8 条澄清全部解决）；本 plan.md 完全从 spec.md 派生；分支名 `016-video-preprocessing-pipeline` 符合 `###-feature-name` 约定；US1-US4 以后端服务能力为中心表述，无前端验收前提 |
| **II. 测试优先（不可协商）** | ✅ PASS | 契约测试（`tests/contract/test_preprocessing_api.py`）覆盖 3 个新 API；集成测试覆盖 US1/US2 端到端；单元测试覆盖 probe/split/upload/consume-from-cos/consume-from-local 五个关键路径；所有测试先建立失败基线再实施 |
| **III. 增量交付** | ✅ PASS | 4 个 US 按 P1/P1/P2/P3 优先级独立可交付；US1 MVP（预处理主流水线）完成即可单独演示；US2（KB 提取消费）依赖 US1 但可独立测试 |
| **IV. 简洁性与 YAGNI** | ✅ PASS | 不新增 DAG orchestrator 架构（复用 Feature-014）；不新增 retry_policy 层（复用）；不引入显式锁/verify sweep（spec 澄清明确拒绝）；4 个 executor 仅改"数据入口"不改算法 |
| **V. 可观测性与可调试性** | ✅ PASS | 8 类结构化错误前缀（VIDEO_DOWNLOAD_FAILED: 等）覆盖所有失败阶段；`pipeline_steps.output_summary` 记录 `segments_processed/segments_skipped`；`video_preprocessing_jobs.original_meta_json` + `target_standard_json` 完整记录推理输入特征 |
| **VI. AI 模型治理与可解释性** | ⚠️ 借用现有治理 | 本 Feature 不引入新 AI 模型；Whisper / YOLOv8 / MediaPipe 均已在 Feature-015 的 `docs/models/` 登记；仅改变调用方式（CPU 强制 + 分段推理）不涉及版本或精度回归 |
| **VII. 运动数据隐私与安全** | ✅ PASS | 预处理产物与原视频同属 COS 同桶同加密策略；不产生新用户数据类型；保留期 24h（本地）/ 永久（COS）与 `coach_video_classifications` 现有保留策略一致 |
| **VIII. 后端算法精准度（不可妥协）** | ✅ PASS | SC-002/003/005/006 均为量化指标；`target_fps=30` + `target_short_side=720` 在 spec "假设" 中显式声明为输入质量契约；`validate_video` 门禁 + `pose_analysis` 兜底双重保障（FR-002a） |

### 附加约束检查

| 约束 | 状态 | 说明 |
|------|------|------|
| 范围边界（无前端） | ✅ PASS | 零前端代码，纯后端 + API + DB |
| 分支命名 | ✅ PASS | `016-video-preprocessing-pipeline` |
| 文档完整性 | ✅ PASS | 本 plan.md 覆盖必需项；research.md / data-model.md / contracts/ / quickstart.md 将在阶段 0-1 产出 |
| 路径约定 | ✅ PASS | 沿用 `src/` + `tests/` 单一结构；无 frontend/web/ios/android 目录 |
| AI/ML 约束 | ✅ PASS | 无新模型权重文件；Whisper 版本 `20231117` 已锁定 |
| Python 环境隔离 | ✅ PASS | 统一使用 `/opt/conda/envs/coaching/bin/python3.11`；新依赖（如有）通过 `pyproject.toml` 声明 |

**门控结论**: ✅ **全部通过**，可进入阶段 0 研究。无违规，**复杂度跟踪表留空**。

## 项目结构

### 文档（此功能）

```
specs/016-video-preprocessing-pipeline/
├── plan.md              # 此文件
├── spec.md              # /speckit.specify + /speckit.clarify×2 输出
├── research.md          # 阶段 0 输出（本次生成）
├── data-model.md        # 阶段 1 输出（本次生成）
├── quickstart.md        # 阶段 1 输出（本次生成）
├── contracts/           # 阶段 1 输出（本次生成）
│   ├── submit_preprocessing.md         # POST /api/v1/tasks/preprocessing
│   ├── submit_preprocessing_batch.md   # POST /api/v1/tasks/preprocessing/batch
│   └── get_preprocessing_job.md        # GET  /api/v1/video-preprocessing/{job_id}
├── checklists/
│   └── requirements.md  # 已在 clarify 阶段生成
└── tasks.md             # 阶段 2 输出（/speckit.tasks 命令产出，不在本次范围）
```

### 源代码（仓库根目录）

**结构决策**: 单一项目结构（`src/` + `tests/`），完全沿用 Feature-013 / 014 / 015 的代码分层：

```
src/
├── api/
│   ├── routers/
│   │   └── tasks.py                        # 扩展：新增 POST /tasks/preprocessing 和 /batch
│   │   └── video_preprocessing.py          # 新增：GET /video-preprocessing/{job_id}
│   └── schemas/
│       └── preprocessing.py                # 新增：Pydantic 请求/响应模型
├── models/
│   ├── video_preprocessing_job.py          # 新增 ORM
│   ├── video_preprocessing_segment.py      # 新增 ORM
│   └── coach_video_classification.py       # 扩展：新增 preprocessed: bool 列
├── services/
│   ├── preprocessing/                      # 新增子包
│   │   ├── __init__.py
│   │   ├── orchestrator.py                 # 预处理主流程（probe→transcode→split→upload）
│   │   ├── video_probe.py                  # ffprobe 元数据采集 + validate_video 调用
│   │   ├── video_transcoder.py             # ffmpeg 标准化转码
│   │   ├── video_splitter.py               # 流式分段（segmenter）
│   │   ├── audio_exporter.py               # 整段 16 kHz mono WAV 提取
│   │   ├── cos_uploader.py                 # ThreadPool 并发上传封装
│   │   └── error_codes.py                  # 8 类 VIDEO_* / SEGMENT_* / AUDIO_* 错误常量
│   ├── preprocessing_service.py            # DB 层 CRUD + force/superseded 逻辑
│   └── kb_extraction_pipeline/step_executors/
│       ├── download_video.py               # 改造：读 segments 表，顺序下载到本地 + 本地优先
│       ├── pose_analysis.py                # 改造：按分段迭代 estimate_pose + 累积 frames
│       ├── audio_transcription.py          # 改造：直接从 COS 拉 audio.wav 喂 Whisper
│       ├── visual_kb_extract.py            # 不变
│       ├── audio_kb_extract.py             # 不变
│       └── merge_kb.py                     # 不变
├── workers/
│   ├── celery_app.py                       # 扩展：注册新队列 preprocessing
│   ├── preprocessing_task.py               # 新增：preprocess_video Celery 任务
│   ├── kb_extraction_task.py               # 不改
│   └── housekeeping_task.py                # 扩展：cleanup_intermediate_artifacts 覆盖 preprocessing/{job_id}/
├── db/migrations/versions/
│   └── 0014_video_preprocessing_pipeline.py  # 新增：两张新表 + 一列扩展
├── config.py                               # 扩展：新增 5 个配置项（见下）

tests/
├── contract/
│   └── test_preprocessing_api.py           # 新增：3 个新 API 的契约测试
├── integration/
│   ├── test_preprocessing_end_to_end.py    # 新增：US1 端到端
│   └── test_kb_extraction_with_preprocessed.py  # 新增：US2 端到端
└── unit/
    ├── test_video_probe.py                 # probe + validate_video 门禁
    ├── test_video_splitter.py              # 流式分段正确性 + 分段时长误差
    ├── test_cos_uploader.py                # ThreadPool 并发上传
    ├── test_preprocessing_service.py       # force=true superseded + 本地 TTL 清理
    ├── test_pose_analysis_segmented.py     # 分段迭代内存峰值
    └── test_audio_transcription_from_cos.py  # COS audio.wav 直接读取
```

新增配置项（`src/config.py` 追加到 `Settings` 类）：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `video_preprocessing_segment_duration_s` | 180 | 分段秒数阈值（Feature-007 实证值） |
| `video_preprocessing_target_fps` | 30 | 标准化目标帧率 |
| `video_preprocessing_target_short_side` | 720 | 标准化目标短边像素 |
| `preprocessing_local_retention_hours` | 24 | 本地产物保留时长（统一成功/失败） |
| `preprocessing_upload_concurrency` | 2 | ThreadPoolExecutor max_workers（Feature-007 实证值） |

## 复杂度跟踪

> **仅在章程检查有必须证明的违规时填写**

✅ 本次规划章程检查零违规，**复杂度跟踪表留空**。
