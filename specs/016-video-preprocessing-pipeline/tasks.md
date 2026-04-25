---
description: "Feature-016 视频预处理流水线实施任务清单"
---

# 任务: 视频预处理流水线

**输入**: `/specs/016-video-preprocessing-pipeline/` 下的设计文档
**前置条件**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅, quickstart.md ✅

**测试**: 本功能规范的 SC-001~007 和用户故事的"独立测试"节明确要求测试覆盖；章程 II 原则（测试优先）强制契约测试 + 集成测试 + 关键单元测试。

**组织结构**: 任务按用户故事分组（US1 P1 MVP → US2 P1 → US3 P2 → US4 P3）。

## 格式: `[ID] [P?] [Story] 描述`
- **[P]**: 可并行运行（不同文件 / 无依赖）
- **[Story]**: US1 | US2 | US3 | US4（spec.md 用户故事）
- 任务描述包含确切文件绝对路径

## 路径约定
- 单一后端项目（章程"路径约定"）
- 源代码根：`/data/charhuang/char_ai_coding/charhuang_pp_cn/src/`
- 测试根：`/data/charhuang/char_ai_coding/charhuang_pp_cn/tests/`

---

## 阶段 1: 设置（共享基础设施）

**目的**: 项目基础设施就位，为所有用户故事共享。

- [ ] T001 新增 5 个配置项到 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/config.py` 的 `Settings` 类末尾：`video_preprocessing_segment_duration_s: int = 180`、`video_preprocessing_target_fps: int = 30`、`video_preprocessing_target_short_side: int = 720`、`preprocessing_local_retention_hours: int = 24`、`preprocessing_upload_concurrency: int = 2`；同步更新 `CODEBUDDY.md` 的"核心配置"章节追加这 5 项说明
- [ ] T002 [P] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/workers/celery_app.py` 的 `task_queues` 中注册新队列 `preprocessing`（Kombu Queue），并在 `task_routes` 中添加路由 `src.workers.preprocessing_task.preprocess_video → preprocessing`
- [ ] T003 [P] 新建 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/services/preprocessing/__init__.py`（空包占位）和 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/services/preprocessing/error_codes.py`，后者定义 8 类错误常量：`VIDEO_DOWNLOAD_FAILED`、`VIDEO_PROBE_FAILED`、`VIDEO_QUALITY_REJECTED`、`VIDEO_CODEC_UNSUPPORTED`、`VIDEO_TRANSCODE_FAILED`、`VIDEO_SPLIT_FAILED`、`VIDEO_UPLOAD_FAILED`、`AUDIO_EXTRACT_FAILED`、`SEGMENT_MISSING`、`AUDIO_MISSING`，以及 `format_error(code, detail)` 辅助函数

---

## 阶段 2: 基础（阻塞前置）

**目的**: 所有用户故事都依赖的数据模型、迁移、Schema、通道配置。**在此阶段完成前不得启动 US1~US4**。

- [ ] T004 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/models/video_preprocessing_job.py` 创建 `VideoPreprocessingJob` ORM 模型（完整字段、约束、partial unique index — 按 data-model.md §1 实现；使用 `Mapped[]` + `mapped_column`；`status` 枚举用 `String(16)` + CheckConstraint）
- [ ] T005 [P] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/models/video_preprocessing_segment.py` 创建 `VideoPreprocessingSegment` ORM 模型（按 data-model.md §2：FK CASCADE DELETE、(job_id, segment_index) UNIQUE、时间与大小 CHECK）
- [ ] T006 扩展 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/models/coach_video_classification.py`：新增列 `preprocessed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)`；新增 `Index("idx_cvclf_preprocessed", "preprocessed")`
- [ ] T007 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/db/migrations/versions/0014_video_preprocessing_pipeline.py` 写 Alembic 迁移：创建两张新表、扩展 `coach_video_classifications.preprocessed` + 索引、更新 `task_channel_configs.ck_tcc_channel_type` 容纳 `'preprocessing'`、种子插入 `('preprocessing', 3, 20)`（完整 upgrade/downgrade，对齐 data-model.md §8）
- [ ] T008 [P] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/api/schemas/preprocessing.py` 定义 Pydantic v2 请求/响应 schema：`PreprocessingSubmitRequest`、`PreprocessingBatchSubmitRequest`、`PreprocessingSubmitItem`、`PreprocessingSubmitResponse`、`PreprocessingBatchSubmitResponse`、`PreprocessingJobResponse`、`PreprocessingSegmentView`、`PreprocessingAudioView`、`PreprocessingOriginalMeta`、`PreprocessingTargetStandard`（字段与 contracts/ 三个文件一致；使用 `model_config = ConfigDict(from_attributes=True)`）
- [ ] T009 [P] 运行迁移并验证：`cd /data/charhuang/char_ai_coding/charhuang_pp_cn && alembic upgrade head`；用 `psql -c "\d video_preprocessing_jobs"` 和 `\d video_preprocessing_segments` 核对字段、约束、索引完整

**检查点**: 数据库就绪；Schema 类型就绪；可以开始 US1 实施

---

## 阶段 3: 用户故事 1 - 大视频自动分段预处理 (优先级: P1) 🎯 MVP

**目标**: 运维触发预处理任务 → 系统下载 → probe → 转码标准化 → 按 180s 分段 → 并发上传 COS + 产出 WAV → 写 DB 映射表 → `coach_video_classifications.preprocessed=true`。

**独立测试** (US1 验收场景): 对一个时长 600 秒、已分类的教练视频提交 `POST /api/v1/tasks/preprocessing` → 轮询 `GET /api/v1/video-preprocessing/{job_id}` 达到 `status='success'` → DB 中 `video_preprocessing_jobs` 有 1 行 + `video_preprocessing_segments` 有 4 行（`ceil(600/180)`）→ COS 对应对象可下载 → 分段时长误差 < 1 秒 → `coach_video_classifications.preprocessed=true`。

### US1 测试（TDD 先行）⚠️

> 先写，确保运行失败；然后再实施

- [ ] T010 [P] [US1] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/tests/contract/test_preprocessing_api.py` 写契约测试（TestClient）：覆盖 `contracts/submit_preprocessing.md` 的 C1-C7 + `contracts/get_preprocessing_job.md` 的 C1-C6；使用 monkeypatch 拦截 Celery `.delay()` 只验证 API 层契约，不触发真实 Worker
- [ ] T011 [P] [US1] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/tests/unit/test_video_probe.py` 写单元测试：覆盖 `probe_and_validate()` 成功路径（返回 VideoMeta）+ fps 不达标抛 `VIDEO_QUALITY_REJECTED:` + 无法解码抛 `VIDEO_PROBE_FAILED:`（使用 fixtures 下的测试视频，或 monkeypatch `subprocess.run`）
- [ ] T012 [P] [US1] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/tests/unit/test_video_splitter.py` 写单元测试：验证 `split()` 对 600s 原视频产出 4 段，每段时长误差 < 1 秒、累计误差 < 原时长 1%（SC-005）；短视频 < 180s 产出 1 段
- [ ] T013 [P] [US1] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/tests/unit/test_cos_uploader.py` 写单元测试：ThreadPoolExecutor(max_workers=2) 并发上传 10 段（mock cos_client）、单段上传失败触发 3×30s 重试、重试用尽抛 `VIDEO_UPLOAD_FAILED:`
- [ ] T014 [P] [US1] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/tests/unit/test_preprocessing_service.py` 写单元测试：`create_job(force=False, 已有 success)` 返回已有 job + reused=True；`create_job(force=True, 已有 success)` 把旧 job 置 superseded + 删除旧 COS 对象 + 创建新 running job
- [ ] T015 [US1] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/tests/integration/test_preprocessing_end_to_end.py` 写集成测试：对真实短视频（fixture 或小样本）跑完整 pipeline，验证 DB 记录完整 + COS 对象存在 + `preprocessed` 标志更新；跳过条件为缺少 COS 凭证（`@pytest.mark.skipif`）

### US1 实现

- [ ] T016 [P] [US1] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/services/preprocessing/video_probe.py` 实现 `probe_and_validate(local_path: Path) -> VideoMeta`：用 `ffprobe -print_format json -show_streams -show_format` 采集 `fps/width/height/duration_ms/codec/size_bytes/has_audio`；失败前缀 `VIDEO_PROBE_FAILED:` 或 `VIDEO_CODEC_UNSUPPORTED:`；集成 `src.services.video_validator.validate_video` 做质量门禁（FR-002a），不达标抛 `VIDEO_QUALITY_REJECTED:`
- [ ] T017 [P] [US1] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/services/preprocessing/video_transcoder.py` 实现 `transcode(input_path, output_path, target_fps, target_short_side)`：subprocess 调 `ffmpeg -vf "scale='if(gt(iw,ih),-2,720)':'if(gt(iw,ih),720,-2)'" -r 30 -c:v libx264 -preset veryfast -an`（音频分离）；错误前缀 `VIDEO_TRANSCODE_FAILED:`
- [ ] T018 [P] [US1] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/services/preprocessing/video_splitter.py` 实现 `split(input_path, output_dir, segment_duration_s) -> Iterator[SegmentInfo]`（生成器流式产出，一段切完立即 yield）：用 `ffmpeg -i input -c copy -f segment -segment_time 180 -reset_timestamps 1 seg_%04d.mp4`，监听目录新文件事件或轮询 `ls` 识别完成；SegmentInfo dataclass 含 `index/start_ms/end_ms/local_path`；失败前缀 `VIDEO_SPLIT_FAILED:`
- [ ] T019 [P] [US1] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/services/preprocessing/audio_exporter.py` 实现 `export_wav(input_path, output_path) -> int`：用 `ffmpeg -i input -vn -ac 1 -ar 16000 -c:a pcm_s16le`；无音轨时返回 `(None, False)` 不抛错（FR-008 无音频不失败）；其他错误前缀 `AUDIO_EXTRACT_FAILED:`
- [ ] T020 [P] [US1] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/services/preprocessing/cos_uploader.py` 实现 `ConcurrentUploader`：`ThreadPoolExecutor(max_workers=settings.preprocessing_upload_concurrency)`、每 worker 持有独立 `cos_client`、`submit_segment(local_path, cos_key)` 返回 Future、3×30s 重试（tenacity stop_after_attempt(3) + wait_fixed(30)）；总错误前缀 `VIDEO_UPLOAD_FAILED:`；新增 `delete_prefix(cos_prefix)` 用于 force=true 清旧产物
- [ ] T021 [US1] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/services/preprocessing_service.py` 实现 DB 层：`create_job(cos_object_key, force, idempotency_key)`、`get_job(job_id)`、`list_segments(job_id)`、`mark_superseded(old_job_ids)`、`mark_preprocessed(cos_object_key)`；force=true 时先查并 superseded 旧 job、删除旧 COS 对象（调用 T020 的 delete_prefix）、再创建新 running job；全部 async，使用 `async_session_factory`
- [ ] T022 [US1] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/services/preprocessing/orchestrator.py` 实现 `run_preprocessing(job_id)` 主协调函数：按 probe → transcode → split → (并发 upload + export_wav + upload audio) 顺序；流式接线 split generator → ConcurrentUploader.submit；每完成一段写 DB segments 记录；全部成功后更新 job status=success + segment_count + audio meta；任何阶段失败置 status=failed + error_message 带前缀；本地 artifact 保留在 `${EXTRACTION_ARTIFACT_ROOT}/preprocessing/{job_id}/`（FR-005c）
- [ ] T023 [P] [US1] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/workers/preprocessing_task.py` 实现 Celery 任务 `preprocess_video(job_id: str)`：`@app.task(queue='preprocessing', name='src.workers.preprocessing_task.preprocess_video')`，内部 `asyncio.run(run_preprocessing(job_id))`；对接 Feature-013 的 `task_channel` 生命周期（`acquire_slot` / `release_slot`）和孤儿回收
- [ ] T024 [US1] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/api/routers/tasks.py` 新增端点 `POST /tasks/preprocessing` 和 `POST /tasks/preprocessing/batch`：调用 `preprocessing_service.create_job`、通过 Feature-013 的 `TaskChannelService.acquire_slot('preprocessing')` 占槽，成功后 `preprocess_video.delay(job_id)`；批量端点复用单条逻辑 + `BATCH_TOO_LARGE` 校验（对齐 contracts/submit_preprocessing.md 和 submit_preprocessing_batch.md）
- [ ] T025 [P] [US1] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/api/routers/video_preprocessing.py` 实现 `GET /video-preprocessing/{job_id}`：查询 job + segments，组装 `PreprocessingJobResponse`；404 时返回 `{"detail": "video_preprocessing job not found"}`；确保 segments 按 segment_index 升序（对齐 contracts/get_preprocessing_job.md）
- [ ] T026 [US1] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/api/main.py` 注册两个新路由模块：`from src.api.routers import video_preprocessing; app.include_router(video_preprocessing.router, prefix="/api/v1", tags=["video-preprocessing"])`；tasks.py 已 include，无需重复
- [ ] T027 [US1] 在 T022 的 orchestrator 末尾调用 `preprocessing_service.mark_preprocessed(cos_object_key)`（FR-006）：仅当 job status→success 时触发，把 `coach_video_classifications.preprocessed` 置 true

**检查点**: US1 MVP 完成；运维可以提交预处理任务并通过 API 查询完整元数据；quickstart.md §1-4 可以完整跑通

---

## 阶段 4: 用户故事 2 - 知识库提取消费预处理产物 (优先级: P1)

**目标**: 改造 Feature-014 的 `download_video` / `pose_analysis` / `audio_transcription` executor，从"直接处理原视频"切换到"消费 video_preprocessing_segments"：按分段顺序迭代、本地温缓存优先、Whisper 强制 CPU 从 COS 预置 WAV 读取。

**独立测试** (US2 验收场景): 对已完成预处理的视频提交 KB 提取 → `pipeline_steps.output_summary.segments_processed` 等于分段数 → `audio_transcription.output_summary.audio_source='cos_preprocessed'` + `whisper_device='cpu'` → rerun 不新建 preprocessing job → 某分段 COS 误删后 KB 提取 failed with `SEGMENT_MISSING:` 前缀。

### US2 测试

- [x] T028 [P] [US2] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/tests/unit/test_pose_analysis_segmented.py` 写单元测试：mock 3 个分段的 `estimate_pose` 返回，验证 pose_analysis executor 按分段顺序迭代累积帧到 `pose.json`；验证 `output_summary.segments_processed=3`、`segments_failed=0`；验证每段处理完释放本地文件引用（通过 open fd 计数或 mock verify）
- [x] T029 [P] [US2] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/tests/unit/test_audio_transcription_from_cos.py` 写单元测试：验证 audio_transcription executor 从 DB 查 `video_preprocessing_jobs.audio_cos_object_key` → 先 head_object 校验 → 本地优先读 → 走 COS fallback；验证 `output_summary.audio_source='cos_preprocessed'` + `whisper_device='cpu'`；mock Whisper 避免真实推理
- [x] T030 [P] [US2] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/tests/unit/test_download_video_segmented.py` 写单元测试：验证改造后 `download_video` executor 从 `video_preprocessing_segments` 表读取所有分段 + audio.wav → COS head_object 校验门禁（缺失抛 `SEGMENT_MISSING:` / `AUDIO_MISSING:`）→ 本地优先 size 一致则跳过下载；`output_summary.video_preprocessing_job_id / segments_downloaded / local_cache_hits / cos_downloads` 字段齐全
- [x] T031 [US2] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/tests/integration/test_kb_extraction_with_preprocessed.py` 写集成测试：端到端验证已预处理视频 → 提交 KB 提取 → 检查 `pose_analysis.output_summary.segments_processed` 与预处理 `segment_count` 一致；rerun 同 job 不触发新 preprocessing；跳过条件为缺少 COS 凭证

### US2 实现

- [x] T032 [US2] 改造 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/services/kb_extraction_pipeline/step_executors/download_video.py`：改写 `execute()` 从 `video_preprocessing_segments` + `video_preprocessing_jobs` 读映射 → 先 `cos_client.head_object` 逐一校验所有分段 + audio.wav 存在（缺失抛 `SEGMENT_MISSING:` / `AUDIO_MISSING:`）→ 对每个分段：本地 `${EXTRACTION_ARTIFACT_ROOT}/preprocessing/{preprocessing_job_id}/seg_NNNN.mp4` 存在且 size 一致 → 跳过下载；否则从 COS 下到 KB job 工作目录的 `segments/` 子目录；audio.wav 同理；`output_artifact_path` 指向 KB job 工作目录（下游 executor 的入口）；`output_summary` 含 `segments_downloaded / segments_total / audio_downloaded / local_cache_hits / cos_downloads / video_preprocessing_job_id`
- [x] T033 [US2] 改造 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/services/kb_extraction_pipeline/step_executors/pose_analysis.py`：从 `download_video` 输出目录的 `segments/seg_NNNN.mp4` 按 segment_index 升序迭代；对每段调 `pose_estimator.estimate_pose` → 把 frames 追加到累积列表（注意 frame 的 `timestamp_ms` 需加上分段的 `start_ms` 偏移还原为原视频时间轴）→ 全部处理完写入 `pose.json`（沿用 artifact_io.write_pose_artifact）；保留 `video_validator.validate_video` 兜底（对第 1 段）；`output_summary` 新增 `segments_processed / segments_failed / backend`
- [x] T034 [P] [US2] 改造 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/services/kb_extraction_pipeline/step_executors/audio_transcription.py`：改写音频来源为 `download_video` 输出的本地 audio.wav（由 T032 已下载好）；保持原有 Whisper 调用链，仅强制 `whisper_device='cpu'`（从 `settings.whisper_device` 读取；warn if 非 cpu，然后强行降级到 cpu 并记日志）；`output_summary.audio_source='cos_preprocessed' / whisper_device='cpu'`

**检查点**: US2 完成；对已预处理视频的 KB 提取无 OOM、rerun 高速复用；quickstart.md §5-6 可跑通

---

## 阶段 5: 用户故事 3 - 视频标准化元数据可观察 (优先级: P2)

**目标**: 完善 `GET /video-preprocessing/{job_id}` 的响应字段覆盖所有可审计元数据。

**独立测试** (US3 验收场景): 预处理成功 job → API 查询返回含原视频 fps/width/height/duration_ms/codec/size_bytes + target_standard + 分段列表 + 音频元信息；失败 job → 明确失败阶段前缀。

### US3 测试

- [ ] T035 [P] [US3] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/tests/integration/test_preprocessing_observability.py` 写集成测试：造三种状态 job 的 DB 记录（success/failed/running/superseded）→ 调 API → 验证响应字段对照 contracts/get_preprocessing_job.md；验证失败 job 的 error_message 前缀 grep 可分类

### US3 实现

- [ ] T036 [US3] 完善 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/api/routers/video_preprocessing.py` 的响应组装：确保 `original_meta` / `target_standard` / `audio` / `segments` 四大块在 DB 字段齐全时完整填充；失败/运行中 job 允许部分字段为 null；验证 `has_audio=false` 时 `audio=null`；`superseded` 状态可查询（审计）
- [ ] T037 [US3] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/services/preprocessing/orchestrator.py` 补充：在 probe 阶段立即把 `original_meta_json` 和 `target_standard_json` 写入 DB（不等整个 pipeline 完成）；任何阶段 failed 时至少保证 `original_meta_json` 已持久化供调试

**检查点**: US3 完成；运维可对任何 job 快速排障

---

## 阶段 6: 用户故事 4 - 批量并发预处理 (优先级: P3)

**目标**: 批量提交接口 + 通道并发上限控制，批量内单条失败不影响其他。

**独立测试** (US4 验收场景): 批量提交 5 个视频 → 任意时刻最多 3 个（通道并发）running → 其他进 pending；单条失败不影响其他条目。

### US4 测试

- [ ] T038 [P] [US4] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/tests/integration/test_preprocessing_batch.py` 写集成测试：mock Celery delay 只观察入队顺序；提交 5 项含 1 项无效 cos_key → 响应 `submitted=4, failed=1`；无效条目 `job_id=null, error_code='COS_KEY_NOT_CLASSIFIED'`

### US4 实现

- [ ] T039 [US4] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/api/routers/tasks.py` 的 `POST /tasks/preprocessing/batch` 完善错误聚合逻辑（T024 已建骨架）：单条 `COS_KEY_NOT_CLASSIFIED` / `CHANNEL_QUEUE_FULL` 不中断批次，聚合到 `results[]` 中 `error_code` + `error_message` 字段；响应 `submitted/reused/failed` 三个计数器准确
- [ ] T040 [US4] 验证 `task_channel_configs` 的 `preprocessing` 通道配置通过 Feature-013 的 `PATCH /admin/channels/preprocessing` 可热更新（并发从 3 → 5 验证批量速度变化）；不需要新代码，只补充 quickstart §4-batch 的验证步骤（在 quickstart.md 追加 Section 3.5）

**检查点**: US4 完成；可批量处理教练系列

---

## 阶段 7: 收尾与横切关注点

**目的**: 清理、周期任务扩展、文档同步。

- [ ] T041 扩展 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/workers/housekeeping_task.py` 的 `cleanup_intermediate_artifacts` 任务：新增扫描 `${EXTRACTION_ARTIFACT_ROOT}/preprocessing/{job_id}/` 目录，`max(mtime, atime) > now - 1h` 则延期（R8），否则满足 `now - mtime > PREPROCESSING_LOCAL_RETENTION_HOURS` 即删除整个目录；确保与 Feature-015 的 `{job_id}/pose.json` 清理路径隔离（各自独立，互不误删）
- [ ] T042 [P] 扩展 `/data/charhuang/char_ai_coding/charhuang_pp_cn/src/workers/orphan_recovery.py`：新增预处理 job 的孤儿扫描（`video_preprocessing_jobs.status='running' AND now - started_at > ORPHAN_TASK_TIMEOUT_SECONDS` → 置 failed with `error_message='orphan_recovered'`）
- [ ] T043 [P] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/docs/architecture.md` 追加 Feature-016 章节：架构图（视频预处理层 + KB 提取消费层）、数据流、关键决策（R1/R3/R7/R9）、新通道 `preprocessing` 的位置
- [ ] T044 [P] 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/docs/features.md` 追加 Feature-016 章节：用户故事、API 列表、配置项、依赖关系（继承 F-013/014/015）
- [ ] T045 [P] 更新 `/data/charhuang/char_ai_coding/charhuang_pp_cn/CODEBUDDY.md` 的 "活跃 Features" 表：新增 `016 | 视频预处理流水线 | POST /tasks/preprocessing, GET /video-preprocessing/{id}`；更新 `.codebuddy/rules/workflow.md` 的"服务启动"章节追加第 5 个 preprocessing worker 启动命令；更新"核心配置"章节的配置表追加 5 项
- [ ] T046 [P] 更新 `/data/charhuang/char_ai_coding/charhuang_pp_cn/.codebuddy/rules/file-organization.md`：把下一个 Feature 编号从 `016` 改为 `017`
- [ ] T047 运行完整测试套件 `cd /data/charhuang/char_ai_coding/charhuang_pp_cn && /opt/conda/envs/coaching/bin/python3.11 -m pytest tests/ -v 2>&1 | tail -30`，确保所有测试通过；失败测试需回溯修复
- [ ] T048 在 `/data/charhuang/char_ai_coding/charhuang_pp_cn/specs/016-video-preprocessing-pipeline/verification.md` 记录部署烟测结果：按 quickstart.md §1-10 手动验证一遍真实短视频端到端（含 COS 凭证）；记录 JOB_ID、耗时、内存峰值、SC-001~007 达成情况；对标 Feature-015 verification.md 的格式

---

## 依赖关系图

```
Phase 1 (T001-T003)
   ↓
Phase 2 基础 (T004-T009)        ← 阻塞所有故事
   ↓
Phase 3 US1 (T010-T027)  🎯 MVP
   ↓
   ├─→ Phase 4 US2 (T028-T034)   ← 依赖 US1 完成（消费预处理产物）
   │      ↓
   │   Phase 5 US3 (T035-T037)   ← 可与 US2 并行，只读路径
   │      ↓
   │   Phase 6 US4 (T038-T040)   ← 可与 US3 并行，独立路径
   │
   └─→ Phase 7 收尾 (T041-T048)  ← 所有故事完成后
```

**跨故事依赖**:
- US2 依赖 US1 的 DB 表 + `preprocessing_service` + 实际预处理产物（集成测试需要）
- US3 依赖 US1 的 API 路由骨架（`/video-preprocessing/{id}` 在 T025 已建）
- US4 依赖 US1 的批量端点骨架（T024 已建）
- Phase 7 依赖所有 US 完成后整合

## 并行执行机会

**Phase 1 内部**: T002 与 T003 可并行（不同文件）
**Phase 2 内部**:
- T004, T005, T008 可完全并行（三个独立 Python 文件）
- T006 与 T005 的模型定义可并行
- T007 迁移脚本依赖 T004/T005/T006 的模型定义，需顺序后置
- T009 (apply migration) 依赖 T007

**Phase 3 US1 测试**: T010-T014 全部 [P] 并行（5 个独立测试文件）
**Phase 3 US1 实现**:
- T016-T020 全部 [P] 并行（5 个独立 service 模块）
- T023 (Celery 任务) 与 T025 (路由) 可并行（不同文件）
- T021 (service) → T022 (orchestrator) → T024 (路由) 串行
- T027 追加在 T022 内部，顺序

**Phase 4 US2 测试**: T028-T030 全部 [P] 并行
**Phase 4 US2 实现**:
- T034 (audio_transcription) 可与 T032/T033 并行（不同文件）
- T032 (download_video) → T033 (pose_analysis) 串行（T033 消费 T032 输出）

**Phase 7**: T042, T043, T044, T045, T046 全部 [P] 并行

## 实现策略

**MVP 优先 (仅 US1 - P1 MVP)**:
1. 完成 Phase 1-3 → 得到最小可用的视频预处理流水线
2. 停下来演示：用真实视频跑 quickstart.md §1-4 → 验证 SC-001/SC-005/SC-006/SC-007
3. 获得反馈再决定是否推进 US2

**完整 P1 (US1 + US2)**:
1. Phase 1-3 完成
2. 启动 Phase 4 US2 → 消除 OOM + 加速 rerun
3. 跑 quickstart.md §1-6 → 验证 SC-002/SC-003
4. 第一次部署烟测（T048）

**增量 P2/P3 (US3 + US4)**:
1. US3 可与 US2 同时进行（只读路径，低风险）
2. US4 批量优化放到最后，依赖 P1 稳定

## 任务统计

| 阶段 | 任务数 | 并行度 |
|------|--------|--------|
| Phase 1 设置 | 3 | T002/T003 并行 |
| Phase 2 基础 | 6 | T004/T005/T008 并行；T007→T009 串行 |
| Phase 3 US1 | 18（测试 6 + 实现 12）| 测试全并行；实现部分并行 |
| Phase 4 US2 | 7（测试 4 + 实现 3）| 测试全并行；T034 可并行 |
| Phase 5 US3 | 3 | T036/T037 串行 |
| Phase 6 US4 | 3 | T040 收尾 |
| Phase 7 收尾 | 8 | 5 项并行 |
| **总计** | **48** | — |

## 独立测试标准（每个故事的验收）

- **US1 MVP**: 提交 → 轮询 success → DB+COS 一致性 → `preprocessed=true`；10 分钟视频 ≤ 50 分钟耗时（SC-006）；分段时长误差 <1s（SC-005）
- **US2**: KB 提取消费产出 `segments_processed=N`、`whisper_device='cpu'`、`audio_source='cos_preprocessed'`；rerun 不触发新预处理；`SEGMENT_MISSING:` 前缀验证可恢复
- **US3**: API 响应字段对照 contract；失败前缀可 grep 分类
- **US4**: 批量 5 项含 1 项失败 → `submitted=4, failed=1`；通道并发 3 生效

## 建议 MVP 范围

**仅 Phase 1-3（完成 T001-T027，28 个任务）**。此范围：
- 可独立演示视频预处理主流水线
- 覆盖章程 III 的 "P1 MVP" 要求
- 真实验证 SC-001 / SC-005 / SC-006 / SC-007
- **尚不具备**消除 KB 提取 OOM 的能力（需 US2）和批量能力（需 US4）

建议在完成 MVP 后先拿短视频做一次烟测，确认 pipeline 稳定再进 US2。
