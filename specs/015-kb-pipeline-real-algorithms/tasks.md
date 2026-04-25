---
description: "Feature-015 — 真实算法接入的可执行任务清单"
---

# 任务: Feature-014 知识库提取流水线 — 真实算法接入

**输入**: 来自 `/specs/015-kb-pipeline-real-algorithms/` 的设计文档
**前置条件**: plan.md ✅ / spec.md ✅（5 条 Clarifications）/ research.md ✅ / data-model.md ✅ / contracts/ ✅ / quickstart.md ✅

**测试策略**: 章程原则 II（TDD）— 测试任务在对应实现任务之前编写；US1/US2 用合成 artifact 在 CI 验证，US3/US4 留部署阶段走真实视频。

## 格式: `[ID] [P?] [Story] 描述`

- **[P]**: 可以并行运行（不同文件，无依赖关系）
- **[Story]**: US1 / US2 / US3 / US4 — 映射到 spec.md 用户故事
- 所有文件路径为仓库内绝对/相对路径

## 路径约定

- 后端单一项目：`src/`、`tests/`
- Feature-015 只改 4 个 executor 文件 + 加测试 + 加一个脚本
- 无数据库迁移、无路由变更、无新服务模块

---

## 阶段 1: 设置（零新增依赖）

**目的**: 本 Feature 不引入新包、无配置变更；唯一的"设置"是确认 Feature-014 基础设施可用。

- [X] T001 确认 Feature-014 DAG 骨架就绪：执行 `/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/integration/test_pipeline_dag.py tests/integration/test_extraction_jobs_api.py -v` 全绿作为基线
- [X] T002 [P] 确认 Feature-002 算法模块可导入：`from src.services import pose_estimator, speech_recognizer, action_segmenter, action_classifier, tech_extractor, transcript_tech_parser, llm_client, video_validator, audio_extractor`（若任一缺失或导入失败，停止执行并向用户确认）

---

## 阶段 2: 基础（阻塞前置条件）

**目的**: 为 4 个 executor 改造提供共享基础设施（错误码前缀 + artifact 序列化/反序列化辅助）

**⚠️ 关键**: 此阶段完成前任何 US 代码不得合并

### 共享辅助工具

- [X] T003 [P] 在 `src/services/kb_extraction_pipeline/artifact_io.py` 新建 artifact 读写辅助函数：
    - `write_pose_artifact(path, video_meta, backend, frames) -> None`：序列化 `list[FramePoseResult]` 到 JSON（按 data-model.md 格式）
    - `read_pose_artifact(path) -> (video_meta_dict, backend_str, frames_list_of_FramePoseResult)`：容错反序列化，缺字段用默认值（FR-002/Q4）
    - `write_transcript_artifact(path, transcript_result) -> None`：序列化 `TranscriptResult` 到 JSON
    - `read_transcript_artifact(path) -> dict`：容错反序列化，返回原生 dict（不还原 TranscriptResult，因为下游 TranscriptTechParser 接受 sentences dict）
- [X] T004 [P] 在 `src/services/kb_extraction_pipeline/error_codes.py` 新建错误码前缀常量（data-model.md § 错误码约定表）：
    - `VIDEO_QUALITY_REJECTED` / `POSE_NO_KEYPOINTS` / `WHISPER_LOAD_FAILED` / `WHISPER_NO_AUDIO` / `ACTION_CLASSIFY_FAILED` / `LLM_UNCONFIGURED` / `LLM_JSON_PARSE` / `LLM_CALL_FAILED`
    - `format_error(code: str, details: str) -> str`：返回 `"{code}: {details}"` 供 executor 抛异常时使用

### 基础单元测试

- [X] T005 [P] 在 `tests/unit/test_artifact_parsers.py` 编写容错解析测试：
    - 完整 JSON 可读回；缺 `video_meta` / `frames` / `backend` 时用默认值不抛；未知顶层键忽略
    - 完整 transcript JSON 可读回；缺 `sentences` 返回 `[]`；LOC 读回的 sentences 保持 list[dict] 结构
    - 覆盖 FR-002 / FR-007 容错语义
- [X] T006 [P] 在 `tests/unit/test_error_codes.py` 编写错误码工具测试：
    - `format_error("VIDEO_QUALITY_REJECTED", "fps=12 vs 15")` == `"VIDEO_QUALITY_REJECTED: fps=12 vs 15"`
    - 8 个错误码常量全部定义

**检查点**: 基础就绪 — artifact_io 可往返读写，错误码前缀统一

---

## 阶段 3: 用户故事 1 — 视觉路真实姿态分析（优先级: P1）🎯 MVP

**目标**: 视频 → 真实 ExpertTechPoint 条目（visual 来源），覆盖至少 2 个技术维度

**独立测试**: 用合成 pose.json fixture 驱动 `visual_kb_extract`，断言生成非空 kb_items 且 source_type='visual'；再用合成视频（或 skip）验证 `pose_analysis` 调用真实 pose_estimator 时输出 backend 非 scaffold

### 测试（TDD 先写）

- [X] T007 [P] [US1] 在 `tests/unit/test_video_quality_gate.py` 编写单元测试：
    - 调 `video_validator.validate_video()` 用合成 VideoMeta 数据，验证低 fps/低分辨率抛 `VideoQualityRejected`
    - 验证 executor 捕获异常后 error_message 以 `VIDEO_QUALITY_REJECTED:` 前缀开头（通过 mock `video_validator.validate_video`）
    - FR-006 覆盖
- [X] T008 [P] [US1] 在 `tests/integration/test_visual_kb_real.py` 编写集成测试（合成 pose.json 驱动）：
    - 准备完整的 pose.json fixture（含 ≥30 帧、11+12+13+14+15+16 keypoints、模拟正手拉球挥动轨迹）
    - 构造 ExtractionJob 让 `pose_analysis.output_artifact_path` 指向 fixture
    - 直接调 `visual_kb_extract.execute(session, job, step)`
    - 断言返回 `output_summary.kb_items_count >= 2` 且 kb_items 含 `elbow_angle` / `swing_trajectory` 至少一个维度
    - 断言所有条目 `source_type='visual'` 且 `extraction_confidence >= 0.7`
    - FR-004 / FR-005 / SC-001 视觉部分覆盖

### 实现

- [X] T009 [US1] 改造 `src/services/kb_extraction_pipeline/step_executors/pose_analysis.py`：
    - 导入 `validate_video` / `VideoQualityRejected` / `estimate_pose`
    - 读 download_video step 的 `output_artifact_path`（本地 mp4）
    - Step 1: 调 `validate_video(Path(video_path))` → 若抛 `VideoQualityRejected` → 用 `format_error('VIDEO_QUALITY_REJECTED', ...)` 重新抛 `RuntimeError`
    - Step 2: 调 `estimate_pose(Path(video_path))` → 返回 list[FramePoseResult]
    - Step 3: 若 frames 为空 → 抛 `RuntimeError('POSE_NO_KEYPOINTS: estimate_pose returned 0 frames')`
    - Step 4: 调 `write_pose_artifact(<job_dir>/pose.json, video_meta, backend=settings.pose_backend, frames)` 序列化
    - Step 5: 返回 `{status: success, output_summary: {keypoints_frame_count, detected_segments: 0（US3 起由 visual_kb 填）, backend, video_duration_sec, fps, resolution}, output_artifact_path: <pose.json>}`
    - **重要**: `validate_video` 与 `estimate_pose` 是 CPU 阻塞调用，用 `await asyncio.to_thread(...)` 包装避免阻塞事件循环（Feature-014 US3 并行要求）
- [X] T010 [US1] 改造 `src/services/kb_extraction_pipeline/step_executors/visual_kb_extract.py`：
    - 读 `pose_analysis.output_artifact_path` → 调 `read_pose_artifact()` 还原 `(video_meta, backend, frames)`
    - 若 frames 为空 → 返回 `output_summary.kb_items=[], segments_processed=0`（不抛异常，允许作业通过；但 merge_kb 会因空 kb 走降级）
    - 调 `action_segmenter.segment_actions(frames)` → `segments: list[ActionSegment]`
    - 若 segments 为空 → 返回空 kb_items 并记录 `segments_processed=0`
    - 对每个 segment: 调 `action_classifier.classify_segment(segment, frames, action_type_hint=job.tech_category)` → `ClassifiedSegment`
    - 调 `tech_extractor.extract_tech_points(classified, frames, confidence_threshold=0.7)` → `ExtractionResult`
    - 展开 `ExtractionResult.dimensions` 到 kb_items dict list（按 data-model.md § kb_items 格式；action_type 来自 classified.action_type）
    - 统计 `segments_skipped_low_confidence = 空 dimensions 的 segment 数`
    - 返回 `output_summary` 含 `{kb_items, kb_items_count, source_type=visual, tech_category, backend='action_segmenter+tech_extractor', segments_processed, segments_skipped_low_confidence}`
    - 用 `asyncio.to_thread` 包装 CPU 循环

**检查点**: US1 完成 — 视觉路从真实视频产出 kb_items 可在集成测试中演示

---

## 阶段 4: 用户故事 2 — 音频路真实 Whisper + LLM 抽取（优先级: P1）

**目标**: 对有讲解视频产出 `source_type='audio'` 或 `visual+audio'` 条目，冲突项进 kb_conflicts 不入主 KB

**独立测试**: 合成 transcript.json 驱动 `audio_kb_extract`；端到端验证（合成音频 fixture）LLM 抽取产出 kb_items；LLM 未配置时 fail fast

### 测试

- [X] T011 [P] [US2] 在 `tests/unit/test_audio_kb_llm_gate.py` 编写单元测试：
    - Monkeypatch `get_settings()` 返回无 VENUS 也无 OPENAI 配置
    - 调 `audio_kb_extract.execute(...)` 并捕获异常，断言 `error_message.startswith('LLM_UNCONFIGURED:')`
    - FR-011 覆盖
- [X] T012 [P] [US2] 在 `tests/integration/test_audio_kb_real.py` 编写集成测试（合成 transcript.json fixture 驱动）：
    - 准备 transcript.json fixture：含 3 条 sentences 如 "拉球时肘部保持 90 到 120 度"、"重心前移"
    - Monkeypatch `transcript_tech_parser.TranscriptTechParser.parse()` 返回预构造的 `list[TechSemanticSegment]`（含 `elbow_angle` 维度 + 1 条 reference_note）
    - 直接调 `audio_kb_extract.execute(session, job, step)`
    - 断言返回 `kb_items_count >= 1`；所有条目 `source_type='audio'` 带 `raw_text_span` 字段；reference_note 和 confidence <0.5 的条目被丢弃
    - FR-009 / FR-010 覆盖
- [X] T013 [P] [US2] 在 `tests/integration/test_audio_kb_real.py` 追加上游 skipped 传播测试：
    - 构造 ExtractionJob，把 `audio_transcription` step.status 设为 `skipped`
    - 调 `audio_kb_extract.execute(...)` 返回 `status=skipped, skip_reason='audio_transcription_skipped'`
    - 不调 LLM（用 mock 确认 0 次调用）

### 实现

- [X] T014 [US2] 改造 `src/services/kb_extraction_pipeline/step_executors/audio_transcription.py`：
    - 保留 `enable_audio_analysis=False` 的 skipped 分支
    - 读 download_video 的 `output_artifact_path`（mp4）
    - 调 `audio_extractor.extract_audio_from_video(Path(video_path), Path(job_dir)/'audio.wav')` → `AudioMeta` 或抛无音频异常
    - 若抛"无音频"异常 → 返回 `status=skipped, skip_reason='no_audio_track'`
    - 否则调 `SpeechRecognizer(model_name=settings.whisper_model, device=settings.whisper_device).recognize(audio_path, language=job.audio_language)` → `TranscriptResult`
    - 若 `quality_flag == 'unusable'` 或 `sentences` 为空且是静音 → 返回 `status=skipped, skip_reason='silence_below_snr_threshold'`
    - 调 `write_transcript_artifact(<job_dir>/transcript.json, transcript_result)`
    - 返回 `output_summary` 含 `{whisper_model, language_detected, transcript_chars, sentences_count, snr_db, quality_flag, skipped=False}`
    - `SpeechRecognizer.recognize` 是 CPU 密集型，用 `asyncio.to_thread` 包装
    - FR-007 / FR-008 覆盖
- [X] T015 [US2] 改造 `src/services/kb_extraction_pipeline/step_executors/audio_kb_extract.py`：
    - 读 `audio_transcription` step：若 `status=skipped` → 返回 `status=skipped, kb_items=[]`（保留 Feature-014 行为）
    - 读 `transcript.json` artifact → `read_transcript_artifact()` → dict（含 `sentences`）
    - 若 `sentences=[]` → 返回 `kb_items=[]`（空讲解，成功但无条目）
    - 初始化 `LlmClient`：从 `get_settings()` 读 VENUS / OPENAI 配置；**主动预检**：若两者全缺（`not (s.venus_token and s.venus_base_url) and not s.openai_api_key`）→ 抛 `RuntimeError(format_error('LLM_UNCONFIGURED', ...))`，不依赖 `LlmClient.__init__` 原生 ValueError；统一异常类型便于 executor 结构化错误处理
    - 初始化 `TranscriptTechParser(llm_client)`
    - 调 `parser.parse(sentences)` 获取 `list[TechSemanticSegment]`
    - 捕获 JSON 解析异常（ValueError / JSONDecodeError）→ 抛 `RuntimeError(format_error('LLM_JSON_PARSE', ...))`（不重试，因是格式问题）
    - 过滤规则（FR-009/Q5）：
        - 丢 `is_reference_note=True` 的段
        - 丢 `dimension is None` 的段
        - 丢 `parse_confidence < 0.5` 的段
    - 余下段转换为 kb_items dict（action_type=job.tech_category，源=audio；新增 `raw_text_span=seg.text_span` 字段 — LLM 未返回时用 None）
    - 返回 `output_summary` 含 `{kb_items, kb_items_count, source_type=audio, llm_model=llm._default_model, llm_backend=llm._backend, parsed_segments_total, dropped_low_confidence, dropped_reference_notes}`
    - 用 `asyncio.to_thread` 包装 `parser.parse`（LLM 是阻塞 HTTP 调用，见 Feature-002 实现）

**检查点**: US2 完成 — 音频路真实 LLM 抽取集成验证通过；合成 fixture 驱动的单元+集成测试全绿

---

## 阶段 5: 用户故事 3 — 参考视频集绝对范围回归（优先级: P2）

**目标**: 对固定 3–5 个参考视频跑完整流水线，每个视频条目数 ∈ 预定义范围；结果写入 verification.md

**独立测试**: 回归脚本可重复运行；failed 视频 fail-loud 指出 artifact 路径

### 资产准备

- [X] T016 [US3] 创建参考视频集 manifest 模板 `specs/015-kb-pipeline-real-algorithms/reference_videos.json`：
    - 含 3 个占位视频条目（name / cos_object_key / tech_category / expected_items_min / expected_items_max / has_speech / notes）
    - 注释运维填入真实 COS 路径 + 预期范围

### 回归脚本

- [X] T017 [US3] 创建 `specs/015-kb-pipeline-real-algorithms/scripts/run_reference_regression.py`：
    - CLI：`--manifest <path>` + `--output <verification.md path>` + `--random-sample N`（可选，N=10 时抽样而非读 manifest）
    - 为每个视频：
        - 调 `POST /api/v1/tasks/kb-extraction`（复用 existing HTTP 客户端逻辑，可直接用 httpx 或 requests）
        - 轮询 `GET /api/v1/extraction-jobs/{id}` 直到 status ∈ {success, failed}
        - 查询 `expert_tech_points WHERE source_video_id=task_id` 统计行数 + 按 source_type 分组
        - 断言条目数 ∈ [expected_items_min, expected_items_max]（manifest 模式）
        - 或条目数 ∈ [1, 50]（random-sample 模式的默认范围）
    - 汇总报告：每个视频一行（name / status / duration_s / visual_items / audio_items / visual+audio_items / total / 期望范围 / 通过? / error_message）
    - 写入 `verification.md` Markdown 表格
    - 脚本退出码：0=全部通过，1=至少一个失败

### 集成测试验证

- [X] T018 [P] [US3] 在 `tests/integration/test_real_algorithms_regression.py` 编写回归测试：
    - 用 mock 的 HTTP client（httpx MockTransport）模拟 3 个视频提交到 success
    - 对每个 mock 作业 seed `expert_tech_points` 行（10 条 visual + 2 条 audio）
    - 调回归脚本的核心函数（从 `scripts/run_reference_regression.py` 导出）
    - 断言报告生成 + 条目数统计正确 + 退出码 0

**检查点**: US3 完成 — 回归脚本可在部署环境跑，报告生成正确

---

## 阶段 6: 用户故事 4 — 10 分钟视频耗时验证（优先级: P2）

**目标**: 在部署环境用真实视频验证 SC-002

**独立测试**: 本阶段**不提供 CI 测试**（依赖真实视频 + 真实 Whisper/LLM），只提供运维手册

### 运维文档

- [X] T019 [US4] 在 `specs/015-kb-pipeline-real-algorithms/quickstart.md § Step 5` 已有手册，**无代码任务**
- [X] T020 [US4] 在 `specs/015-kb-pipeline-real-algorithms/scripts/run_reference_regression.py` 增加 `--measure-wallclock` 开关：
    - 对每个视频记录 `extraction_jobs.started_at → completed_at` 的 duration_ms
    - 在 verification.md 报告中单独一列展示
    - 若 manifest 里某视频标注 `baseline_f002_seconds`，则额外计算比值并在报告中打印 `pass/fail vs 0.9x baseline`
    - SC-002 部署验证支撑

**检查点**: US4 完成 — 运维可用 `run_reference_regression.py --measure-wallclock` 在真实环境跑 SC-002 验证

---

## 阶段 7: 完善与横切关注点

### verification.md 模板

- [X] T021 [P] 创建 `specs/015-kb-pipeline-real-algorithms/verification.md` 骨架：
    - SC-001 / SC-002 / SC-003 / SC-004 / SC-005 / SC-006 表格（状态 / 实测值 / 备注）
    - CI 测试统计（本 Feature 新增测试全绿）
    - 部署阶段 TODO 清单（真实视频回归、耗时基线）

### 文档更新

- [X] T022 [P] 在 `docs/architecture.md` § "知识库提取流水线（Feature 014）" 追加 "Feature-015 真实算法接入" 小节：
    - 4 个 executor 接线的函数调用链（pose_analysis → pose_estimator / audio_transcription → SpeechRecognizer / visual_kb_extract → action_segmenter+classifier+tech_extractor / audio_kb_extract → TranscriptTechParser+LlmClient）
    - 错误码前缀表（8 个）
    - 参考视频集 manifest 使用说明
- [X] T023 [P] 在 `docs/features.md` 的 Features 清单追加 Feature-015 条目（对齐 013/014 的格式）
- [X] T024 [P] 更新 `CHANGELOG.md` 的 `[Unreleased]` 新增 Feature-015 条目：
    - 新增（4 个 executor 真实接入、artifact_io + error_codes 辅助模块、参考视频回归脚本）
    - 配置（无新增 .env 字段；沿用 Feature-002 的 WHISPER_* / POSE_BACKEND / VENUS_* / OPENAI_*）
    - 测试覆盖（本 Feature 新增单元 + 集成测试数）
    - 无数据迁移
- [X] T025 [P] 更新 `CODEBUDDY.md` 的 Features 表新增 Feature-015 行（已完成）

### 全仓回归

- [X] T026 执行全仓测试：`/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/ --tb=no -q`；确认 Feature-014 基线 498 passed + Feature-015 新增测试全部通过，全仓 0 failed
- [X] T027 运行 Feature-015 单独测试套件：`/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/unit/test_artifact_parsers.py tests/unit/test_error_codes.py tests/unit/test_video_quality_gate.py tests/unit/test_audio_kb_llm_gate.py tests/integration/test_visual_kb_real.py tests/integration/test_audio_kb_real.py tests/integration/test_real_algorithms_regression.py -v`

---

## 依赖关系与执行顺序

### 阶段依赖关系

- 阶段 1（设置）→ 无依赖，可立即开始
- 阶段 2（基础）→ 阶段 1 完成后；阻塞所有 US
- 阶段 3（US1 P1 MVP）→ 阶段 2 完成后
  - US1 内：T007+T008（测试并行）→ T009（pose_analysis 实现）→ T010（visual_kb_extract 实现，依赖 T009 的 pose.json 格式）
- 阶段 4（US2 P1）→ 阶段 2 完成后；**可与 US1 并行开发不同 executor**（US1 动 pose+visual，US2 动 audio_transcription+audio_kb_extract）
  - US2 内：T011+T012+T013（测试并行）→ T014（audio_transcription 实现）→ T015（audio_kb_extract 实现，依赖 T014 的 transcript.json 格式）
- 阶段 5（US3 P2）→ 依赖 US1 + US2 完成（回归需要完整流水线）
- 阶段 6（US4 P2）→ 依赖 US3（回归脚本扩展 `--measure-wallclock`）
- 阶段 7（完善）→ 所有 US 完成后

### 并行机会

- **阶段 1**：T001、T002 并行
- **阶段 2**：T003 / T004 / T005 / T006 全部并行（不同文件）
- **US1 测试**：T007 / T008 并行
- **US2 测试**：T011 / T012 / T013 并行
- **US1 ∥ US2**：整个阶段可并行（影响不同 executor 文件）
- **阶段 7 文档**：T022 / T023 / T024 / T025 并行

### 用户故事依赖

- US1 → 需要阶段 2 的 artifact_io + error_codes
- US2 → 需要阶段 2 的 artifact_io + error_codes
- US3 → 需要 US1 + US2 的 executor 真实可跑
- US4 → 需要 US3 的回归脚本

### 每个故事的独立测试标准

- **US1**：`visual_kb_extract` 接收合成 pose.json fixture → 产出 kb_items_count >= 2 的 visual 条目；`pose_analysis` 对低质量视频返回 `VIDEO_QUALITY_REJECTED:` 前缀 error
- **US2**：`audio_kb_extract` 接收合成 transcript.json fixture → 产出 kb_items 含 `raw_text_span` 字段；LLM 未配置时 fail fast `LLM_UNCONFIGURED:`
- **US3**：参考视频集回归脚本对 mock HTTP 作业产出报告，条目数统计正确
- **US4**：（部署阶段）`run_reference_regression.py --measure-wallclock` 能输出 pass/fail vs 0.9x baseline

---

## 并行示例: 阶段 2（基础）

```text
任务 T003: "src/services/kb_extraction_pipeline/artifact_io.py"
任务 T004: "src/services/kb_extraction_pipeline/error_codes.py"
任务 T005: "tests/unit/test_artifact_parsers.py"
任务 T006: "tests/unit/test_error_codes.py"
```

## 并行示例: US1 + US2 同时推进

```text
Dev A（US1 视觉路）：
  任务 T007 + T008: 测试先行
  任务 T009: pose_analysis.py 实现
  任务 T010: visual_kb_extract.py 实现

Dev B（US2 音频路）：
  任务 T011 + T012 + T013: 测试先行
  任务 T014: audio_transcription.py 实现
  任务 T015: audio_kb_extract.py 实现

→ 两路完全独立，只在 US3 集成点汇合
```

## 并行示例: 阶段 7 文档

```text
任务 T022: "docs/architecture.md 追加"
任务 T023: "docs/features.md 追加"
任务 T024: "CHANGELOG.md Unreleased"
任务 T025: "CODEBUDDY.md Features 表"
```

---

## 实施策略

### 仅 MVP（US1）

1. 阶段 1 + 阶段 2（基础就绪）
2. 阶段 3（US1 视觉路接入）
3. 手工验证：合成 pose.json → visual_kb_extract 产出条目；低质量视频 → pose_analysis fail fast
4. 演示视觉路从真实视频产出真实 ExpertTechPoint

### 增量交付

1. 设置 + 基础（T001–T006）→ 基础就绪
2. US1（T007–T010）→ MVP：视觉路真实产出
3. US2（T011–T015）→ 音频路真实抽取
4. US3（T016–T018）→ 参考视频集回归
5. US4（T019–T020）→ 耗时基线验证
6. 阶段 7（T021–T027）→ 文档 + verification + 全仓回归

### 并行团队策略（可选）

- Dev A：US1 视觉路（pose_analysis + visual_kb_extract）
- Dev B：US2 音频路（audio_transcription + audio_kb_extract）
- Dev C：US3 回归脚本 + manifest（US1+US2 完成后接手）
- Dev D：文档 + verification.md（US1/US2 开发期间并行写初稿）

---

## 任务总数

- 总任务数: **27**
- 阶段 1 设置: 2 个（T001–T002）
- 阶段 2 基础: 4 个（T003–T006）
- US1 视觉路: 4 个（T007–T010）
- US2 音频路: 5 个（T011–T015）
- US3 回归: 3 个（T016–T018）
- US4 耗时验证: 2 个（T019–T020）
- 阶段 7 收尾: 7 个（T021–T027）

## 建议 MVP 范围

**阶段 1 + 阶段 2 + US1** — 10 个任务（T001–T010），覆盖视觉路真实算法接入 + 错误码 + 容错 artifact 解析，可独立演示"视频 → ExpertTechPoint 条目"的端到端效果。

## 格式验证

- ✅ 所有 27 个任务以 `- [ ] TNNN` 复选框开头
- ✅ 设置/基础/收尾任务无 `[USn]` 标签
- ✅ US 阶段任务全部带 `[USn]` 标签
- ✅ 所有任务含具体文件路径（`src/services/...py` / `tests/...py` / `specs/...md`）
- ✅ 并行任务标 `[P]`；冲突文件的任务未标 `[P]`（如 T009 → T010 顺序；T014 → T015 顺序）
