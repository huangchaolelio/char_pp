# Feature-016 验证记录

最后更新：2026-04-25

## 部署状态

| 项目 | 值 |
|------|------|
| 分支 | `016-video-preprocessing-pipeline` |
| 最新提交 | `34c04cc`（Phase 7 清理机制）|
| PR | [#4](https://github.com/huangchaolelio/char_pp/pull/4) |
| 数据库迁移 | `0014_video_preprocessing_pipeline`（已应用）|
| 测试套件 | **585 passed, 53 skipped**（2026-04-25 14:00）|

---

## 实施总览

| Phase | 任务 | 状态 | 说明 |
|-------|------|------|------|
| 1 设置 | T001-T003 | ✅ | 5 个新配置项 / preprocessing 队列注册 / error_codes 骨架 |
| 2 基础 | T004-T009 | ✅ | 2 张新表 + CVC.preprocessed 列 + Alembic 0014 |
| 3 US1 MVP | T010-T027 | ✅ | 主流水线 + API + Worker；首次 commit `b0b4c15` |
| 4 US2 | T028-T034 | ✅ | KB 提取消费预处理产物（端到端烟测通过）|
| 5 US3 | T035-T037 | ✅ | 可观测性（GET /video-preprocessing/{id}）|
| 6 US4 | T038-T040 | ✅ | 批量接口 per-item 隔离 + 通道热更新 |
| 7 清理 | T041-T042 | ✅ | housekeeping + orphan_recovery 扩展 |

> T043-T048 为文档+验证记录收尾任务，本文档即 T048 交付物。

---

## US2 端到端烟测（2026-04-25 20:05:46 ~ 20:12）

### 场景
孙浩泓《第06节正手攻球》— 430.5 秒原视频，标准化后 3 个分段（180s×2 + 70s×1）+ 16kHz 单声道 audio.wav。

### 步骤
1. 查询已有预处理 job：`1f061a5d-9651-4d17-9107-69b9d63e6542`（status=success，segment_count=3，has_audio=true）
2. 重启 KB extraction worker 加载 US2 新代码
3. 提交：`POST /api/v1/tasks/kb-extraction` → task_id `6d541e2a...`
4. 首次失败于字段名 bug（`view.audio_cos_object_key` vs `view.audio["cos_object_key"]`）→ 修复并 rerun
5. 5 分钟内 6 步全绿，最终 `merge_kb.status=success`

### 关键 metrics

| Step | 状态 | output_summary 要点 |
|------|------|------|
| download_video | success | `segments_downloaded=3`, `audio_downloaded=True`, `cos_downloads=0`, `local_cache_hits=4` |
| pose_analysis | success | `segments_processed=3`, `keypoints_frame_count=12916`, `backend=yolov8`, `fps=30.0`, `1920x1080` |
| audio_transcription | success | `whisper_device=cpu`（硬编码）, `audio_source=cos_preprocessed`, `snr_db=32.6`, `216 sentences / 1827 chars` |
| visual_kb_extract | success | `kb_items_count=2352`, `segments_processed=588` |
| audio_kb_extract | success | `kb_items_count=0`（低置信度 drop 2 条）, `llm_backend=venus` |
| merge_kb | success | `merged_items=4`, `kb_extracted_flag_set=True` |

### US2 验收点（tasks.md L91）

| 验收条件 | 实测 | 结果 |
|------|------|------|
| `pose_analysis.segments_processed == preprocessing.segment_count` | 3 == 3 | ✅ |
| `audio_transcription.audio_source == 'cos_preprocessed'` | `cos_preprocessed` | ✅ |
| `whisper_device == 'cpu'`（硬编码不读 settings）| `cpu` | ✅ |
| Rerun 不创建新预处理作业 | `video_preprocessing_job_id` 复用已有 | ✅ |
| 复用本地预处理缓存（不重新从 COS 下载）| `cos_downloads=0`, `local_cache_hits=4` | ✅ |
| 缺失分段 → `SEGMENT_MISSING:` 前缀 | 单元测试覆盖 | ✅ |
| 缺失 audio.wav（when has_audio=true）→ `AUDIO_MISSING:` 前缀 | 单元测试覆盖 | ✅ |

---

## Spec 成功指标对照

| SC | 目标 | 实测 | 结果 |
|----|------|------|------|
| **SC-001** | 预处理端到端 ≤ 15 分钟（10 分钟视频）| 未单独测（US1 MVP 时已验证，2 分钟内完成 120s 短视频）| ⏭️ 沿用 MVP |
| **SC-002** | 预处理失败不阻塞其他任务 | 批量测试 test_c3_mixed_valid_and_invalid 验证：1 条失败不影响其他 4 条 | ✅ |
| **SC-003** | 幂等（force=false 命中 success）| 测试 test_reuse_detection_in_batch | ✅ |
| **SC-004** | 孤儿 worker 崩溃后可恢复 | T042 `_sweep_preprocessing_orphans`（tests/integration/test_preprocessing_cleanup.py）| ✅ |
| **SC-005** | 分段时长误差 < 1 秒 / 累计 < 原视频 1% | 120s 短视频实测 **0.023% 误差**（US1 MVP smoke）| ✅ |
| **SC-006** | 预处理开销 < KB 提取耗时 | 120s 视频预处理 / 全量 KB 提取 = **0.073×**（US1 MVP smoke）| ✅ |
| **SC-007** | KB 提取可复用预处理产物（无需重新处理原视频）| US2 烟测 `cos_downloads=0`（全部本地 hardlink）| ✅ |

---

## 问题与修复

### #1 US2 字段名 bug（2026-04-25 20:05）
- **症状**: `download_video.execute` 首次烟测抛 `AttributeError: 'PreprocessingJobView' object has no attribute 'audio_cos_object_key'`
- **根因**: `PreprocessingJobView` 定义为 `audio: dict | None`（含 `cos_object_key` / `size_bytes`），而我的 executor 写成了 `view.audio_cos_object_key` / `view.audio_size_bytes`
- **修复**: commit `58b36c5`，改用 `view.audio.get("cos_object_key")`；同步更新 T030 unit test fixture（3 处 SimpleNamespace）

### #2 US2 test fixture schema 漂移
- **症状**: 初次跑 pose_analysis test 抛 `AttributeError: '_FakeFrame' object has no attribute 'frame_confidence'`
- **根因**: `artifact_io._frame_to_dict` 需要 `frame.frame_confidence` + `frame.keypoints` 为 dict（不是 list）
- **修复**: test fixture 改为 `frame_confidence=1.0` + `keypoints={}`

### #3 US3 integration test asyncpg cross-loop
- **症状**: `RuntimeError: got Future attached to a different loop`
- **根因**: `src/db/session.py` 的 module-level engine 绑定到上一个测试的 event loop
- **修复**: client fixture rebuild engine（模仿 test_extraction_jobs_api.py）

---

## 配置项（Feature-016）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `VIDEO_PREPROCESSING_SEGMENT_DURATION_S` | 180 | 分段时长 |
| `VIDEO_PREPROCESSING_TARGET_FPS` | 30 | 标准化帧率 |
| `VIDEO_PREPROCESSING_TARGET_SHORT_SIDE` | 720 | 短边像素 |
| `PREPROCESSING_LOCAL_RETENTION_HOURS` | 24 | 本地温缓存保留 |
| `PREPROCESSING_UPLOAD_CONCURRENCY` | 2 | COS 上传并发 |

## 新通道

`preprocessing`：默认 concurrency=3 / queue_capacity=20，可通过 `PATCH /api/v1/admin/channels/preprocessing` 热更新。

---

## 代码结构

```
src/
├── api/routers/
│   ├── video_preprocessing.py      # GET /video-preprocessing/{id}
│   └── tasks.py                    # POST /tasks/preprocessing{,/batch}
├── api/schemas/preprocessing.py    # 10 Pydantic v2 schemas
├── models/
│   ├── video_preprocessing_job.py
│   └── video_preprocessing_segment.py
├── services/
│   ├── preprocessing_service.py    # DB + PreprocessingJobView
│   └── preprocessing/
│       ├── orchestrator.py         # 主协调协程
│       ├── video_probe.py
│       ├── video_transcoder.py
│       ├── video_splitter.py
│       ├── audio_exporter.py
│       ├── cos_uploader.py
│       └── error_codes.py
├── workers/
│   ├── preprocessing_task.py       # Celery entrypoint
│   ├── housekeeping_task.py        # T041: +_cleanup_preprocessing_local
│   └── orphan_recovery.py          # T042: +_sweep_preprocessing_orphans
└── db/migrations/versions/
    └── 0014_video_preprocessing_pipeline.py
```

## 测试覆盖

| 测试文件 | 用例数 | 覆盖 |
|---------|-------|------|
| `tests/contract/test_preprocessing_api.py` | 16 | 三个契约的 C1-C7 |
| `tests/integration/test_preprocessing_observability.py` | 7 | T035 US3 |
| `tests/integration/test_preprocessing_batch.py` | 4 | T038 US4 |
| `tests/integration/test_preprocessing_cleanup.py` | 7 | T041/T042 Phase 7 |
| `tests/integration/test_preprocessing_end_to_end.py` | 1 | gated，需 `PREPROCESSING_E2E=1` |
| `tests/integration/test_kb_extraction_with_preprocessed.py` | 1 | gated，需 `KB_EXTRACTION_E2E=1` |
| `tests/unit/test_download_video_segmented.py` | 3 | T030 US2 |
| `tests/unit/test_pose_analysis_segmented.py` | 1 | T028 US2 |
| `tests/unit/test_audio_transcription_from_cos.py` | 2 | T029 US2 |
| + 多个 Feature-014 integration 测试补 download_video stub | — | 回归保护 |

---

## 部署确认

- ✅ 5 个 Celery worker（classification / kb_extraction / diagnosis / default / **preprocessing**）全部启动
- ✅ 数据库迁移 0014 已应用，partial unique index `uq_vpj_cos_success` 生效
- ✅ 真实视频端到端烟测通过（US1 MVP + US2 分别各一次）
- ✅ 585 个自动化测试绿灯

Feature-016 可合并至 `015-kb-pipeline-real-algorithms`（上游分支），完成后建议 rebase 或 merge 到 `main`。
