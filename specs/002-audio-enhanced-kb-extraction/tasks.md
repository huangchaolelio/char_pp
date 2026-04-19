# 任务: 音频增强型教练视频技术知识库提取

**输入**: 来自 `/specs/002-audio-enhanced-kb-extraction/` 的设计文档
**前置条件**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅, quickstart.md ✅

## 格式: `[ID] [P?] [Story] 描述`
- **[P]**: 可以并行运行（不同文件，无依赖关系）
- **[Story]**: 此任务属于哪个用户故事（US1, US2, US3）
- 描述中包含确切的文件路径

## 路径约定
- 单一后端项目: `src/`, `tests/`
- 算法模块: `src/services/`, `src/models/`, `src/workers/`
- 测试: `tests/unit/`, `tests/integration/`, `tests/contract/`

---

## 阶段 1: 设置（共享基础设施）

**目的**: 安装新依赖、初始化关键词词表配置、确认 Whisper 模型可用

- [ ] T001 在 `pyproject.toml` 中添加 `openai-whisper==20231117` 和 `jieba==0.42.1` 依赖，运行 `pip install` 确认安装成功
- [ ] T002 [P] 创建 `config/keywords/tech_hint_keywords.json`，填入乒乓球教学关键词词表初版（示范、注意看、标准动作、这一拍、关键点、击球瞬间、重心、转腰等约 30 条）
- [ ] T003 [P] 在 `docs/models/whisper-small-zh.md` 创建模型登记文件，记录模型名称（whisper-small）、版本（20231117）、语言支持（zh）、推理延迟基准（CPU 4×实时）
- [ ] T004 在 `src/config.py` 中新增 Whisper 相关配置项：`WHISPER_MODEL`（默认 small）、`WHISPER_DEVICE`（默认 cpu）、`AUDIO_KEYWORD_FILE`、`AUDIO_PRIORITY_WINDOW_S`（默认 3.0）、`AUDIO_SNR_THRESHOLD_DB`（默认 10.0）、`AUDIO_CONFLICT_THRESHOLD_PCT`（默认 0.15）、`LONG_VIDEO_SEGMENT_DURATION_S`（默认 300）、`MAX_VIDEO_DURATION_S`（默认 5400，即 90 分钟）

---

## 阶段 2: 基础（阻塞性前置条件）

**目的**: 新建数据库表和模型字段扩展，所有用户故事均依赖此阶段完成

**⚠️ 关键**: 在此阶段完成之前，无法开始任何用户故事工作

- [ ] T005 在 `src/models/audio_transcript.py` 中创建 `AudioTranscript` ORM 模型，字段：`id`(UUID PK)、`task_id`(FK)、`language`、`model_version`、`total_duration_s`、`snr_db`、`quality_flag`（ok/low_snr/unsupported_language/silent）、`fallback_reason`、`sentences`(JSONB)、`created_at`
- [ ] T006 [P] 在 `src/models/tech_semantic_segment.py` 中创建 `TechSemanticSegment` ORM 模型，字段：`id`(UUID PK)、`transcript_id`(FK)、`task_id`(FK)、`start_ms`、`end_ms`、`priority_window_start_ms`、`priority_window_end_ms`、`trigger_keyword`、`source_sentence`、`dimension`、`param_min`、`param_max`、`param_ideal`、`unit`、`parse_confidence`、`created_at`
- [ ] T007 在 `src/models/expert_tech_point.py` 中为现有 `ExpertTechPoint` 模型新增字段：`source_type`(visual/audio/visual+audio，默认 visual)、`transcript_segment_id`(FK，可为 NULL)、`conflict_flag`(BOOLEAN，默认 false)、`conflict_detail`(JSONB，可为 NULL)
- [ ] T008 [P] 在 `src/models/analysis_task.py` 中为现有 `AnalysisTask` 模型新增字段：`total_segments`(INTEGER，可为 NULL)、`processed_segments`(INTEGER，可为 NULL)、`progress_pct`(FLOAT，可为 NULL)、`audio_fallback_reason`(TEXT，可为 NULL)
- [ ] T009 创建 Alembic migration 文件，包含：新建 `audio_transcripts` 表、新建 `tech_semantic_segments` 表、在 `expert_tech_points` 追加 4 个字段、在 `analysis_tasks` 追加 4 个字段；运行 `alembic upgrade head` 验证迁移成功
- [ ] T010 在 `src/models/__init__.py` 中导出两个新模型，确保 SQLAlchemy metadata 注册正确

**检查点**: 数据库迁移完成，所有模型可用 — 可开始用户故事实现

---

## 阶段 3: 用户故事 1 — 音频/字幕内容提炼为知识库 (优先级: P1) 🎯 MVP

**目标**: 系统能从教练视频音频/字幕中提取含数值参数的技术要点，与视觉结果合并写入知识库，并标注来源；冲突条目阻塞 KB approve

**独立测试**: 上传一段含口头技术讲解的教练视频（如"肘部角度 90 到 120 度"），调用 `GET /tasks/{id}/result`，验证 `tech_points` 中存在 `source_type="audio"` 且 `dimension="elbow_angle"`、`param_min=90`、`param_max=120` 的条目

### 用户故事 1 的测试 ⚠️

> **注意: 先编写这些测试，确保在实现前它们失败**

- [ ] T011 [P] [US1] 在 `tests/unit/test_transcript_tech_parser.py` 中编写单元测试：覆盖数值区间提取（"90°-120°"→min=90,max=120）、单值提取（"保持 90 度"→ideal=90）、无数值文本返回空（"重心要前移"→None）、BODY_PART_MAP 维度映射正确
- [ ] T012 [P] [US1] 在 `tests/unit/test_kb_merger.py` 中编写单元测试：覆盖同维度无冲突自动合并（差值≤15%→source_type="visual+audio"）、有冲突标注（差值>15%→conflict_flag=True）、纯视觉条目不修改、纯音频条目写入
- [ ] T013 [P] [US1] 在`tests/integration/test_audio_enhanced_kb_extraction.py` 中编写集成测试：端到端验证含语音视频 → 知识库含音频来源条目；回退测试：静音视频 → 所有条目 source_type="visual"、audio_fallback_reason 非空

### 用户故事 1 的实现

- [ ] T014 [P] [US1] 在 `src/services/audio_extractor.py` 中实现 `AudioExtractor` 类：`extract_wav(video_path, output_path)` 调用 ffmpeg 提取 16kHz 单声道 WAV；`estimate_snr(wav_path) -> float` 估算信噪比；WAV 提取失败时抛出 `AudioExtractionError`
- [ ] T015 [P] [US1] 在 `src/services/speech_recognizer.py` 中实现 `SpeechRecognizer` 类：`__init__` 加载 Whisper 模型（懒加载，首次调用时初始化）；`recognize(wav_path, language="zh") -> AudioTranscript` 返回带时间戳句列表；SNR 低于阈值时设置 `quality_flag="low_snr"`；不支持语言时设置 `quality_flag="unsupported_language"`；静音时设置 `quality_flag="silent"`
- [ ] T016 [US1] 在 `src/services/transcript_tech_parser.py` 中实现 `TranscriptTechParser` 类：`parse(sentences: list[dict]) -> list[TechSemanticSegment]` 对每句话执行 BODY_PART_MAP 关键词匹配和数值区间正则提取；仅含数值参数的句子生成 `TechSemanticSegment`（`parse_confidence` 根据匹配质量赋值）；纯文字描述句子存为 `dimension=None`（参考注释，不写入 KB）（依赖 T006）
- [ ] T017 [US1] 在 `src/services/kb_merger.py` 中实现 `KbMerger` 类：`merge(visual_points: list[ExtractionResult], audio_segments: list[TechSemanticSegment]) -> list[MergedTechPoint]`；同维度参数差≤15% 自动合并（`source_type="visual+audio"`，`param_ideal` 取均值）；差>15% 设置 `conflict_flag=True`、填充 `conflict_detail`；纯视觉条目 `source_type="visual"`；纯音频条目 `source_type="audio"`（依赖 T016）
- [ ] T018 [US1] 修改 `src/workers/expert_video_task.py`：在现有姿态提取步骤后插入音频增强分支（`enable_audio_analysis=True` 时）：调用 `AudioExtractor` → `SpeechRecognizer` → `TranscriptTechParser` → `KbMerger`；音频不可用时调用纯视觉路径并设置 `audio_fallback_reason`；WAV 临时文件处理后立即删除（依赖 T014、T015、T016、T017）
- [ ] T019 [US1] 修改 `src/api/routers/tasks.py`：在 `POST /tasks/expert-video` 请求体 schema 中新增可选字段 `enable_audio_analysis`（默认 true）和 `audio_language`（默认 "zh"）；更新 `GET /tasks/{task_id}/result` 响应 schema：新增 `audio_analysis` 对象和 `conflicts` 数组（依赖 T018）
- [ ] T020 [US1] 修改 `src/api/schemas/` 中对应的 Pydantic schema 文件：在 `ExpertTechPointResponse` 中新增 `source_type`、`conflict_flag`、`conflict_detail` 字段；新增 `AudioAnalysisInfo` 和 `ConflictDetail` schema；更新 `ExpertVideoResultResponse` 包含 `audio_analysis` 和 `conflicts`
- [ ] T021 [US1] 修改 `src/services/knowledge_base_svc.py`：在 KB approve 前检查该版本是否存在 `conflict_flag=True` 的 `ExpertTechPoint`；若存在则拒绝 approve 并返回冲突条目列表（`CONFLICT_UNRESOLVED` 错误）；在 `POST /knowledge-base/{version}/approve` 响应中说明需要先处理的冲突数量（依赖 T017）
- [ ] T022 [US1] 为 `contracts/api-changes.md` 中定义的新增错误码 `AUDIO_EXTRACTION_FAILED`、`UNSUPPORTED_AUDIO_LANGUAGE`、`CONFLICT_UNRESOLVED` 在现有错误处理模块中注册，确保返回格式一致

**检查点**: US1 完整可测试 — 音频提取 → 转录 → 技术解析 → 合并 → 知识库（含冲突阻塞审批）

---

## 阶段 4: 用户故事 2 — 音频/字幕定位精准技术片段 (优先级: P1)

**目标**: 系统利用转录文本中的关键词定位高价值示范片段，在姿态提取时优先分析这些片段；音频不可用时自动回退纯视觉模式

**独立测试**: 上传含"这是标准正手拉球示范"口播的视频，任务完成后验证 `tech_semantic_segments` 中该时间戳区间被标记为高优先级，且对应技术要点提取命中该片段

### 用户故事 2 的测试 ⚠️

> **注意: 先编写这些测试，确保在实现前它们失败**

- [ ] T023 [P] [US2] 在 `tests/unit/test_keyword_locator.py` 中编写单元测试：关键词命中 → 返回正确时间窗口（关键词时间 ±3s）；多个关键词命中 → 区间合并；无关键词命中 → 返回空列表；窗口边界不越界（start_ms ≥ 0，end_ms ≤ video_duration）

### 用户故事 2 的实现

- [ ] T024 [US2] 在 `src/services/keyword_locator.py` 中实现 `KeywordLocator` 类：`__init__(keyword_file_path)` 加载词表 JSON；`locate(sentences: list[dict]) -> list[PriorityWindow]` 遍历句子，命中词表中任意词时生成 `PriorityWindow(start_ms, end_ms, trigger_keyword)`；重叠区间合并；返回排序后的优先窗口列表（依赖 T002）
- [ ] T025 [US2] 修改 `src/workers/expert_video_task.py`：音频处理后将 `KeywordLocator` 结果传入 action segmenter；高优先级窗口内的动作片段优先排在分析队列前；非窗口内的片段仍处理但标记为低优先级；音频回退（`quality_flag != "ok"`）时全部片段按现有视觉逻辑处理（依赖 T024，在 T018 已有音频分支基础上扩展）
- [ ] T026 [US2] 在 `src/workers/expert_video_task.py` 中完善音频不可用时的结构化回退：在 `AnalysisTask.audio_fallback_reason` 中写入具体原因字符串（静音/低信噪比/语言不支持/无音频流）；在任务结果的 `audio_analysis.quality_flag` 中反映回退状态；回退不影响任务最终状态（status 仍为 success）

**检查点**: US2 完整可测试 — 关键词命中 → 优先窗口 → 片段优先分析 → 回退正常工作

---

## 阶段 5: 用户故事 3 — 长视频完整分析支持 (优先级: P2)

**目标**: 支持最长 90 分钟教学视频，分段处理并提供实时进度查询；某分段失败时保留已完成结果并标记"部分完成"；超过 90 分钟时在上传阶段拒绝

**独立测试**: 提交一段 20 分钟视频，轮询 `GET /tasks/{id}` 观察 `progress_pct` 从 0 递增至 100，任务完成后知识库覆盖视频中所有动作类型

### 用户故事 3 的测试 ⚠️

> **注意: 先编写这些测试，确保在实现前它们失败**

- [ ] T027 [P] [US3] 在 `tests/unit/test_long_video_task.py` 中编写单元测试：视频时长 ≤ 90 分钟 → 正常接受；视频时长 > 90 分钟 → 返回 `VIDEO_TOO_LONG`；进度计算公式：`processed_segments / total_segments × 100` 正确；分段失败时任务状态变为 `partial_success` 而非 `failed`
- [ ] T028 [P] [US3] 在 `tests/integration/test_long_video_progress.py` 中编写集成测试：任务处理中调用进度接口返回 `progress_pct` 和 `processed_segments`；`progress_pct` 在每段完成后更新（验证更新间隔 ≤ 30s）

### 用户故事 3 的实现

- [ ] T029 [US3] 修改 `src/api/routers/tasks.py`：在 `POST /tasks/expert-video` 处理逻辑中，提交前用 ffprobe 获取视频时长；若时长 > `MAX_VIDEO_DURATION_S`（5400s=90min）则立即返回 `422 VIDEO_TOO_LONG` 错误，不创建任务；在 `GET /tasks/{task_id}` 响应 schema 中新增 `progress_pct`、`processed_segments`、`total_segments` 字段（依赖 T008）
- [ ] T030 [US3] 修改 `src/workers/expert_video_task.py`：将现有单次处理逻辑重构为分段循环（每段 `LONG_VIDEO_SEGMENT_DURATION_S` 秒）；任务开始时计算 `total_segments` 并写入 DB；每段处理完成后更新 `processed_segments` 和 `progress_pct`；每段独立捕获异常，失败时记录失败分段编号到 `error_message`（JSON 数组格式），继续处理下一段；全部分段完成后：若有失败分段则任务 status 设为 `partial_success`，否则 `success`（依赖 T029）
- [ ] T031 [US3] 在 `src/models/analysis_task.py` 中为 `TaskStatus` 枚举新增 `partial_success` 值，确保 API 响应和数据库存储一致
- [ ] T032 [US3] 更新 `src/api/schemas/` 中 `TaskStatusResponse`，在响应中暴露 `progress_pct`、`processed_segments`、`total_segments`（当 status 为 processing 或 partial_success 时有值）

**检查点**: US3 完整可测试 — 90min 上传限制 + 分段进度更新 + 失败分段保留

---

## 阶段 6: 收尾与横切关注点

**目的**: 完善测试覆盖、结构化日志、文档和验收确认

- [ ] T033 [P] 在所有新增服务（`audio_extractor.py`、`speech_recognizer.py`、`keyword_locator.py`、`transcript_tech_parser.py`、`kb_merger.py`）中补充结构化日志：记录音频提取成功/失败、SNR 值、转录句数、关键词命中数、合并结果统计、冲突数量
- [ ] T034 [P] 在 `tests/contract/test_expert_video_api_v2.py` 中补充 API 契约测试：验证 `POST /tasks/expert-video` 新增字段的 schema；验证 `GET /tasks/{id}` progress 字段结构；验证 `GET /tasks/{id}/result` `audio_analysis` 和 `conflicts` 字段
- [ ] T035 更新 `specs/002-audio-enhanced-kb-extraction/plan.md` 精准度基准表：添加验证结果列（T001-T005 各测试用例的实测数据）
- [ ] T036 [P] 按 `specs/002-audio-enhanced-kb-extraction/quickstart.md` 执行端到端手工验证：安装依赖 → 运行迁移 → 提交含语音视频 → 验证知识库含音频来源条目 → 验证 90min+ 视频被拒绝 → 验证冲突阻塞 KB approve
- [ ] T037 [P] 在 `src/workers/expert_video_task.py` 中为音频临时文件（WAV）添加 `finally` 块确保删除，验证任何失败路径下均不残留音频文件（数据隐私合规）

---

## 依赖关系与执行顺序

### 阶段依赖关系

- **设置（阶段 1）**: 无依赖 — 立即开始
- **基础（阶段 2）**: 依赖阶段 1（T001-T004）完成 — 阻塞所有用户故事
- **US1（阶段 3）**: 依赖阶段 2（T005-T010）完成
- **US2（阶段 4）**: 依赖阶段 2 完成；US2 的 `KeywordLocator` 依赖 US1 的音频提取流程（T018），**建议 US1 完成后再开始 US2**
- **US3（阶段 5）**: 依赖阶段 2 完成；分段逻辑在 `expert_video_task.py` 中与 US1/US2 共用，**建议 US1 完成后再开始 US3**
- **收尾（阶段 6）**: 依赖全部用户故事完成

### 用户故事依赖关系

```
阶段1（设置）
    ↓
阶段2（基础：DB迁移）
    ↓
阶段3（US1：音频→KB）← MVP，最优先
    ↓
阶段4（US2：关键词定位）← 复用 US1 音频管道
阶段5（US3：长视频）   ← 复用 expert_video_task 循环结构
    ↓
阶段6（收尾）
```

### 故事内部顺序

- 测试（T011-T013）→ 底层服务（T014-T016）→ 合并/业务逻辑（T017-T018）→ API 层（T019-T022）

### 并行机会

- T005、T006 可并行（不同模型文件）
- T007、T008 可并行（不同字段扩展）
- T011、T012、T013 可并行（不同测试文件）
- T014、T015 可并行（不同服务文件）
- T023 可在 US1 进行时并行编写
- T027、T028 可在 US1/US2 进行时并行编写
- T033、T034、T036、T037 可并行（收尾阶段）

---

## 并行示例：用户故事 1

```bash
# 并行编写测试（在 US1 实现任务开始前）:
任务: "tests/unit/test_transcript_tech_parser.py 单元测试"   # T011
任务: "tests/unit/test_kb_merger.py 单元测试"               # T012
任务: "tests/integration/test_audio_enhanced_kb_extraction.py 集成测试"  # T013

# 并行实现底层服务（T011-T013 完成后）:
任务: "src/services/audio_extractor.py"    # T014
任务: "src/services/speech_recognizer.py" # T015
```

---

## 实施策略

### 仅 MVP（用户故事 1）

1. 完成阶段 1: 设置（T001-T004）
2. 完成阶段 2: 基础（T005-T010）— **阻塞所有故事**
3. 完成阶段 3: 用户故事 1（T011-T022）
4. **停止并验证**: 含语音视频 → KB 含音频来源条目 + 冲突阻塞审批
5. 演示/部署 MVP

### 增量交付

1. 阶段 1+2 → 基础就绪
2. 阶段 3（US1）→ 独立测试 → 演示（**MVP**）
3. 阶段 4（US2）→ 独立测试 → 演示（音频定位优化）
4. 阶段 5（US3）→ 独立测试 → 演示（长视频支持）
5. 阶段 6（收尾）→ 合规验证 → 合并

---

## 注意事项

- [P] 任务 = 不同文件，无依赖关系，可并行执行
- [Story] 标签将任务映射到对应用户故事，保证可追溯性
- Whisper 模型首次加载约 2-3 秒，测试中需 mock 以避免 CI 超时
- WAV 临时文件务必在 `finally` 块中清理（T037），防止数据泄露
- `partial_success` 状态新增需同步更新所有状态判断逻辑（API 响应、前端轮询等）
- 冲突阻塞审批逻辑（T021）需确保在知识库 approve 接口的事务中检查，防止并发竞争
- 每个任务或逻辑组完成后提交，提交消息引用对应任务 ID（如 `feat(T018): integrate audio pipeline into expert_video_task`）
