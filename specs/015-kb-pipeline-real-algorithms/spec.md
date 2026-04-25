# 功能规范: Feature-014 知识库提取流水线 — 真实算法接入

**功能分支**: `015-kb-pipeline-real-algorithms`
**创建时间**: 2026-04-25
**状态**: 草稿
**输入**: 用户描述: "真实算法接入"

## Clarifications

### Session 2026-04-25

- Q: Feature-002 基线对比是否可本地复现？ → A: 使用参考视频集（3–5 个）做绝对值范围回归检查，不做与 F-002 的直接条目数比对（Feature-002 的 `KbExtractionService` 已被 F-013 改为 stub，本地无法复跑）
- Q: SC-005/SC-006 成功率统计口径？ → A: 批次口径 — 一次提交 N 个视频（N=10）后计算通过率；SC-005 = 视觉路成功的视频数 / N；SC-006 = 有讲解音频且 LLM 抽取出至少 1 条音频条目的视频数 / 有讲解音频的视频数。验证用一次性脚本实现，无需 metrics 基础设施
- Q: 视频质量不达标的作业该如何处理？ → A: 硬失败 + 明确错误码 `VIDEO_QUALITY_REJECTED`。pose_analysis failed，作业终态 failed，error_message 带可机器识别的前缀便于运维脚本过滤；**不自动重试**（视频质量问题非瞬态，rerun 也不能救；需手动介入重新编码或从分类表删除）
- Q: artifact JSON schema 是否需要固定版本？ → A: 无版本、容错解析 — artifact JSON 只是内部实现细节，下游 executor 读取时对缺字段/额外字段容错（用默认值、ignore extras），与 Feature-014 scaffold 已有行为一致；schema 不稳定不做正式版本化（YAGNI）
- Q: LLM prompt 复用还是新写？ → A: 完全复用 `transcript_tech_parser` 既有 prompt + 小修补。若需要 `raw_text_span` 等新字段只追加说明不改原输出结构；LLM 未返回新字段时用默认值（不当失败处理），保证与 Feature-002 历史行为一致

## 背景

Feature-014 交付了**端到端编排骨架**：DAG 调度、并行执行、冲突分离、重试策略、通道兼容性、局部重跑、中间结果清理 — 全部通过测试验证。但 6 个 step executor（pose_analysis / audio_transcription / visual_kb_extract / audio_kb_extract 及相关衔接）当前是 **scaffold**：它们读取或写入空 artifact，`output_summary.note = "scaffold_output_pending_feature014_us2_implementation"`。真实运行一个教练视频时，`coach_video_classifications.kb_extracted` 会被翻转为 `TRUE`，但 `expert_tech_points` 表将没有任何实质条目——这和 Feature-013 `kb_extraction` stub 的产出几乎等价。

Feature-002 已经交付了完整的算法层：`src/services/pose_estimator.py`（YOLOv8 + MediaPipe 双后端）、`src/services/speech_recognizer.py`（Whisper）、`src/services/tech_extractor.py`（姿态 → 4 个技术维度）、`src/services/transcript_tech_parser.py`（LLM 抽取教学口述）、`src/services/llm_client.py`（Venus Proxy → OpenAI 降级）、`src/services/kb_merger.py`（旧版合并逻辑，可作参考）。

本 Feature 把 scaffold executor **替换为真实算法调用**，让一次完整的 KB 提取作业能从真实教练视频产出真实 `ExpertTechPoint` 行，达到 Feature-014 `SC-003` 和 `SC-007` 原本 defer 掉的验证目标。

## 用户场景与测试 *(必填)*

### 用户故事 1 - 视觉路真实姿态分析产出技术条目（优先级: P1）🎯 MVP

管理员对一段已分类的教练正手拉球视频提交一次 KB 提取请求。作业完成后，管理员从查询接口看到 `visual_kb_extract` 子任务的 `output_summary.kb_items_count >= 1`，且 `tech_knowledge_bases` 表下的 `expert_tech_points` 中出现与该视频关联、来源为 `visual` 的条目（至少包含肘部角度 / 挥拍轨迹 / 击球时机 / 重心转移中的若干个），条目的 `param_min / param_max / param_ideal` 满足约束 `min ≤ ideal ≤ max`。

**优先级原因**: 这是 Feature-014 的核心业务价值——没有视觉路的真实条目产出，Feature-014 就退化成 Feature-013 的占位符，整个 DAG 编排能力无法演示任何"从视频到知识"的效果。视觉路优先于音频路是因为音频可能缺失但视觉一定有，它是作业级成功的**关键路径**。

**独立测试**: 提供一段本地测试视频（≥10 秒、≥30fps、包含清晰人物挥拍动作）→ 提交 KB 提取作业 → 等待 success → 查询 `expert_tech_points` 表中对应 `analysis_task_id` 的行，断言 `source_type='visual'` 的条目数 ≥ 1，且所有条目满足 `extraction_confidence >= 0.7`（Feature-002 既有下限）。

**验收场景**:

1. **给定** 一段 10 秒正手拉球视频（已分类为 `forehand_topspin`），管理员提交 KB 提取，**当** 作业终态为 `success`，**那么** `expert_tech_points` 中该作业关联的 `source_type='visual'` 条目至少覆盖 `elbow_angle` 和 `swing_trajectory` 两个维度
2. **给定** 同一视频，**当** 查询 `GET /extraction-jobs/{id}`，**那么** `steps[step_type='pose_analysis'].output_summary` 含 `keypoints_frame_count >= 30`（视频帧数）和 `backend` 字段指示实际用的后端（yolov8 或 mediapipe）
3. **给定** 同一视频，**当** 查询 `GET /extraction-jobs/{id}`，**那么** `steps[step_type='visual_kb_extract'].output_summary` 含 `kb_items_count >= 1` 且不再含 `"scaffold"` 字样的 note
4. **给定** 视频帧率低于 15fps 或分辨率低于 854×480，**当** 作业执行，**那么** `pose_analysis` 步骤返回 `failed` 且 `error_message` 明确提示"视频质量不满足 Feature-002 阈值"；下游 `visual_kb_extract` 自动 skipped

---

### 用户故事 2 - 音频路真实 Whisper 转录 + LLM 抽取教学要点（优先级: P1）

同一次 KB 提取作业在音频路上执行真实的 Whisper 语音识别（遵循 `.env` 中的 `WHISPER_MODEL` 设置），转写结果经过 `TranscriptTechParser` + LLM 抽取产出 `source_type='audio'` 的知识条目（如"教练说：肘部保持 90 度"映射为 `elbow_angle` 维度的参数区间）。与 Feature-002 一致：视觉+音频同 dimension 一致时合并为 `visual+audio`；差异 >10% 进 `kb_conflicts` 待审核。

**优先级原因**: 音频路是 Feature-014 `SC-007` 的关键 — 即"条目数差异 ≤20% vs Feature-002"验证依赖两路都真实产出。无此能力 Feature-014 只有骨架价值，无法证明**业务能力补齐**。但优先级排 US1 之后，因为音频路允许失败（FR-012 降级语义），而视觉路是硬依赖。

**独立测试**: 用一段带清晰中文讲解（如"拉球的时候肘部角度要保持 90 到 120 度"）的正手视频提交作业 → 等待 success → 断言 `expert_tech_points` 中至少有 1 条 `source_type='audio'` 或 `source_type='visual+audio'` 的条目，且 `kb_conflicts` 表对这个作业的记录语义正确（若无冲突为空、若有冲突则冲突项不出现在主 KB）。

**验收场景**:

1. **给定** 视频音频轨道含清晰中文讲解"肘部角度 90 到 120 度"，**当** 作业成功，**那么** `audio_kb_extract.output_summary.kb_items` 至少含 `elbow_angle` 维度条目，`param_min≈90`、`param_max≈120`（±5 容差）
2. **给定** 视觉 + 音频对同维度参数接近（差异 ≤10%），**当** `merge_kb` 完成，**那么** 该维度在 `expert_tech_points` 里仅一行、`source_type='visual+audio'`、`param_ideal ≈ (visual_ideal + audio_ideal)/2`
3. **给定** 视觉 + 音频对同维度差异 >10%，**当** `merge_kb` 完成，**那么** 该维度**不出现**在主 KB，`kb_conflicts` 里有一条对应记录（`resolved_at IS NULL`，`superseded_by_job_id IS NULL`），可由 `GET /extraction-jobs/{id}.conflict_count` 观察到
4. **给定** 视频无音频或音频完全静音，**当** 作业执行，**那么** `audio_transcription` 返回 `skipped`（`skip_reason='no_audio_track'`），`audio_kb_extract` 也 `skipped`，`merge_kb` 走降级模式只合入视觉条目；作业整体仍 `success`
5. **给定** `.env` 未设置 `VENUS_TOKEN` 也未设置 `OPENAI_API_KEY`，**当** 作业执行，**那么** `audio_kb_extract` 返回 `failed` 且 `error_message` 明确提示"LLM 未配置"；视觉路仍可完成、`merge_kb` 降级走视觉路成功

---

### 用户故事 3 - 条目数绝对范围回归（优先级: P2）

运维对一组固定的参考视频集（3–5 个典型教练视频，覆盖正手/反手/发球等技术类别）跑 Feature-014 新流水线，对每个视频的产出条目数做**绝对值范围检查**（例如单视频产出 5–30 条 ExpertTechPoint），保证回归不会因 LLM 输出波动或参数调整而失真。不做与 Feature-002 旧流程的直接条目数比对——Feature-002 的 `KbExtractionService` 已被 Feature-013 改为 stub，本地无法复跑产出条目。

**优先级原因**: 这是**实证业务能力补齐**的最低可行验证路径。用绝对值范围而非与旧版直接比对，避免对 Feature-002 代码做破坏性恢复操作，同时仍能捕捉退化（条目数断崖下降）和异常（LLM 幻觉导致条目数爆炸）。

**独立测试**: 准备 3–5 个参考视频 fixture → 逐个提交作业 → 断言每个视频的产出条目数在预定义范围内 → 结果写入 `specs/015-kb-pipeline-real-algorithms/verification.md`（含视频元数据、预期范围、实测值）。

**验收场景**:

1. **给定** 参考视频集（3–5 个），**当** 所有作业均 `success`，**那么** 每个视频的 `expert_tech_points` 行数 ∈ 该视频的预定义范围（通常 5–30）
2. **给定** 回归测试通过，**当** 结果写入 `verification.md`，**那么** 文件含每个视频的：文件名、时长、tech_category、预期条目范围、实测条目数、视觉/音频来源分布
3. **给定** 某个视频产出 0 条或 >50 条，**当** 测试运行，**那么** 测试 fail 并打印该视频的 artifact 路径供运维回溯

---

### 用户故事 4 - 10 分钟典型视频总耗时不超过旧版 90%（优先级: P2）

运维用一段 10 分钟教练视频跑完整 KB 提取流水线，**挂钟总耗时不超过** Feature-002 单体任务完成同一视频所耗时间的 90%（满足 Feature-014 `SC-003`）。并行能力在 Feature-014 US3 已验证（模拟数据），本故事用真实算法场景再次确认。

**优先级原因**: 这是并行化带来的**性能承诺**的最终实证。P2 因为 `SC-002` 已用模拟数据证明并行正确性，本条是真实场景性能确认。

**独立测试**: 准备 10 分钟测试视频 + 记录 Feature-002 旧流程耗时（已有基线或手工测量）→ 跑 Feature-014 新流水线 → 记录总耗时 → 断言比值 ≤ 90%。

**验收场景**:

1. **给定** 10 分钟视频，Feature-002 旧流程耗时 T_old 秒（基线），**当** Feature-014 新流水线跑完，**那么** 总耗时 T_new ≤ 0.9 × T_old
2. **给定** 同一视频，**当** 查询 `GET /extraction-jobs/{id}`，**那么** `progress.total_steps = 6` 且所有 step 最终 `status='success'`（或 audio 路 skipped）

---

### 边界情况

- **视频格式不支持**：非 `.mp4` 或编码异常 → `download_video` 成功但 `pose_analysis` 因 `VideoCapture` 初始化失败而 failed；error_message 明确指出
- **LLM 服务间歇性失败**：`audio_kb_extract` 调用 Venus 或 OpenAI 遇到 5xx → tenacity 按 Feature-014 FR-021 重试 2 次 × 30s；全部失败后步骤 failed 但视觉路继续
- **LLM 返回非结构化文本**（如 JSON 解析失败）：`audio_kb_extract` 捕获 JSON 解析错误后 step failed；不重试（JSON 格式错误是 LLM 输出问题非瞬态）
- **Whisper 模型未本地下载**（首次运行）：`speech_recognizer.recognize()` 会触发自动下载；若下载失败（网络）→ step failed 并记录下载错误
- **GPU 不可用但 `POSE_BACKEND=yolov8`（强制）**：启动时即 fail fast；若是 `auto` 则自动降级 MediaPipe
- **同一 `cos_object_key` 存在多次 force 重跑产出**：最新作业的 `expert_tech_points` 覆盖旧版本吗？→ 否，旧版本 `TechKnowledgeBase` 版本号保留，供回溯；只有最新作业的 `kb_conflicts` 变为"未 supersede"
- **视频极长（接近 90 分钟上限）**：单个 `pose_analysis` 可能超 10 分钟 step 超时 → step failed；运维需自行分段（Feature-014 FR-020 已定义，本 Feature 不重复实现）

## 需求 *(必填)*

### 功能需求

#### 视觉路（US1 核心）

- **FR-001**: `pose_analysis` 子任务必须调用 `src/services/pose_estimator.py::estimate_pose` 真实 API，对 `download_video` 产出的本地视频文件运行姿态检测；失败时抛出清晰的错误（区分"视频质量不达标" vs "后端模型未加载"）
- **FR-002**: `pose_analysis.output_artifact_path` 必须指向一个序列化的姿态关键点 JSON 文件（按帧存储 MediaPipe 兼容的 33 关键点索引 + visibility），供 `visual_kb_extract` 下游读取，不依赖内存传递；**下游读取时必须容错**：缺字段用默认值、无法识别的额外字段忽略，不使用正式 schema 版本号（YAGNI）
- **FR-003**: `pose_analysis.output_summary` 必须包含 `{keypoints_frame_count, detected_segments, backend, video_duration_sec, fps, resolution}`，`backend` 字段值为实际使用的后端（`yolov8` 或 `mediapipe`，非 `scaffold`）
- **FR-004**: `visual_kb_extract` 子任务必须复用 `src/services/action_segmenter.py` + `src/services/action_classifier.py` + `src/services/tech_extractor.py` 的既有算法，从姿态序列提取 4 个维度（`elbow_angle` / `swing_trajectory` / `contact_timing` / `weight_transfer`）
- **FR-005**: `visual_kb_extract.output_summary.kb_items` 中每条目必须满足字段齐全（`dimension / param_min / param_max / param_ideal / unit / extraction_confidence / action_type / source_type='visual'`），置信度 ≥ 0.7 的条目才入合并（保持 Feature-002 `tech_extractor._CONFIDENCE_THRESHOLD` 阈值）
- **FR-006**: 视频质量不满足 Feature-002 阈值（fps<15、宽<854、高<480）时，`pose_analysis` 立即 failed 且 `error_message` 必须以可机器识别的前缀 `VIDEO_QUALITY_REJECTED:` 开头（格式：`VIDEO_QUALITY_REJECTED: <具体字段>=<实测值> vs <阈值>`，例如 `VIDEO_QUALITY_REJECTED: fps=12 vs 15`）；下游 `visual_kb_extract` skipped，作业整体 failed；**不自动重试**（视频质量问题非瞬态，rerun 也救不回）；运维需手动重新编码视频或从 `coach_video_classifications` 删除该行

#### 音频路（US2 核心）

- **FR-007**: `audio_transcription` 子任务必须复用 `src/services/speech_recognizer.py::SpeechRecognizer` 调用 Whisper，支持 `.env` 中 `WHISPER_MODEL` / `WHISPER_DEVICE` 配置；将 `TranscriptResult` 序列化到 `output_artifact_path` 供下游读取；**下游读取时必须容错**：缺字段用默认值、无法识别的额外字段忽略，不使用正式 schema 版本号
- **FR-008**: 无音频轨道或纯静音视频 → `audio_transcription` 返回 `skipped` 且 `skip_reason` 明确（`no_audio_track` / `silence_below_snr_threshold`）
- **FR-009**: `audio_kb_extract` 子任务必须**完全复用** `src/services/transcript_tech_parser.py` 的现有 LLM prompt 和抽取逻辑，将转写结果映射为 KB item dict 列表；若需要 `raw_text_span` 等新字段（供审核回溯），**只追加提示说明**不改原输出结构；LLM 未返回新字段时用默认值兜底（如 `raw_text_span=None`），不当失败处理——保证与 Feature-002 历史输出格式一致
- **FR-010**: `audio_kb_extract` 内部 LLM 调用必须通过 `src/services/llm_client.py::LLMClient`，遵循 Venus Proxy → OpenAI 降级顺序；LLM 返回非 JSON 时捕获并将 step 标 failed（不重试，因为是输出格式问题）
- **FR-011**: LLM 配置缺失（`VENUS_TOKEN` 和 `OPENAI_API_KEY` 均未设置）时，`audio_kb_extract` 直接 failed 并返回明确错误；视觉路不受影响，`merge_kb` 走降级

#### 合并（US2 + US3）

- **FR-012**: `merge_kb` 必须保留 Feature-014 US2 已实现的冲突分离语义 — 不回退为 Feature-002 的"冲突标注仍入 KB"模式
- **FR-013**: 合并后的 `ExpertTechPoint` 行必须继承 Feature-002 的字段约束（`param_min ≤ param_ideal ≤ param_max`、`extraction_confidence ∈ [0,1]`）；不满足约束的条目由 `merge_kb` 执行**软 clamp**（param_ideal 超出区间时向最近边界收敛；min/max 倒置时取 min/max 正确边界）；clamp 场景记入日志（DEBUG 级）便于运维回溯——此行为由 Feature-014 `merge_kb::_persist_merged_points` 已实现，Feature-015 不新增代码

#### 可观测性与验证（US3 + US4）

- **FR-014**: `GET /extraction-jobs/{id}` 的 `output_summary` 必须暴露真实算法后端信息（backend / whisper_model / llm_model），便于运维辨识是否走到预期路径
- **FR-015**: 必须提供一个可运行的参考视频集回归测试（位置：`specs/015-kb-pipeline-real-algorithms/scripts/run_reference_regression.py` 或等效集成测试），对 3–5 个参考视频逐个触发 Feature-014 新流水线，收集每个视频的产出条目数 + 来源分布 + 耗时；结果写入 `specs/015-kb-pipeline-real-algorithms/verification.md`；每个视频必须带**预定义的条目数范围**（由规范维护者根据视频内容定义，典型值 5–30）
- **FR-016**: 所有真实算法调用失败必须返回结构化错误，包含失败的算法组件名（`yolov8_infer` / `whisper_transcribe` / `llm_venus_call` / `llm_openai_call` / `tech_extractor_rule`）

### 关键实体

- **姿态关键点 artifact**: 序列化为 JSON 的按帧 33 关键点坐标 + visibility；生命周期随 Feature-014 中间结果保留窗口（success 24h / failed 7d）
- **音频转写 artifact**: 序列化的 `TranscriptResult`（语言/模型版本/句子列表/SNR）；同保留窗口
- **知识条目**（无结构变化）: 继承 Feature-002 `ExpertTechPoint`，本 Feature 只是让 `source_video_id` 对应作业下真实产出非空的行

## 成功标准 *(必填)*

### 可衡量的结果

- **SC-001**: 对一段 ≥10 秒的正手拉球测试视频，提交 KB 提取作业到 success，`expert_tech_points` 中来源为 `visual` 的条目数 ≥ 2（覆盖至少 2 个技术维度）；来源为 `audio` 或 `visual+audio` 的条目数 ≥ 1（视频有讲解时）
- **SC-002**: 对 10 分钟典型教练视频，Feature-014 新流水线总耗时 ≤ Feature-002 旧流程耗时的 90%（Feature-014 `SC-003` 实证）
- **SC-003**: 参考视频集（3–5 个）中每个视频的产出条目数 ∈ 对应预定义范围（典型 5–30 条），0 个视频出现"条目数为 0"或"条目数 >50"的异常（替代 Feature-014 `SC-007` 的直接对比验证 — 因为 F-002 `KbExtractionService` 已被 F-013 改为 stub 无法复跑）
- **SC-004**: 100% 的真实算法失败路径都返回结构化错误（失败组件名 + 可读消息），不出现裸 `RuntimeError` 或 `None` 错误消息
- **SC-005**: 视觉路真实姿态分析成功率 ≥ 95%，**批次口径**：一次提交 10 个随机抽取的教练视频后，至少 9 个视频的 `pose_analysis` step 产出非空 keypoints（排除预先过滤掉的视频质量不达标视频）
- **SC-006**: 音频路 LLM 抽取成功率 ≥ 85%，**批次口径**：在同一批 10 个视频中，有清晰讲解的视频子集（典型 7–9 个）里，至少 85% 的视频产出 `audio_kb_extract.output_summary.kb_items_count ≥ 1`（LLM 输出 JSON 格式偶发不稳定是已知局限，留 15% 宽容度）

## 假设

- **复用 Feature-002 算法**：`pose_estimator` / `speech_recognizer` / `tech_extractor` / `transcript_tech_parser` / `llm_client` 五个模块已在 Feature-002 经过验收，本 Feature 不重写算法，只做 executor → 算法的接线
- **测试视频供应**：执行 SC-001/SC-003/SC-005 所需的真实样本视频由运维或部署环境提供（fixtures / COS 样本目录）；不在代码仓库提交大文件
- **本地模型可用性**：Whisper `small` 模型（~500MB）部署时预先下载；YOLOv8-pose 权重（~6MB）随 `ultralytics` 包提供或预先下载
- **LLM 访问**：运行环境具备 `VENUS_TOKEN` 或 `OPENAI_API_KEY` 至少一个；CI 环境允许音频路降级
- **算法精度维度不变**：继续使用 Feature-002 定义的 4 个技术维度（`elbow_angle` / `swing_trajectory` / `contact_timing` / `weight_transfer`）；不扩展新维度（那是独立 Feature）
- **scaffold 保留路径**：当上游 `pose_analysis` 或 `audio_transcription` 的 artifact 缺失或格式非法时，下游 executor 仍然可以返回空 `kb_items` 列表而不崩溃（降级到 Feature-014 scaffold 行为），保证作业能推进到终态
- **冲突阈值不变**：沿用 Feature-014 US2 的 10% 差异阈值 + 音频置信度 <0.5 丢弃规则；不引入新阈值参数
- **不改数据库 schema**：`expert_tech_points` / `tech_knowledge_bases` / `kb_conflicts` 的字段结构已在 Feature-014 敲定，本 Feature 零迁移

## 依赖

- **依赖 Feature-014 已交付**：DAG 编排、重试策略、冲突分离、通道兼容、rerun 全部已存在；本 Feature 只动 4 个 executor 模块和相关测试
- **依赖 Feature-002 算法层**：`src/services/pose_estimator.py` / `speech_recognizer.py` / `tech_extractor.py` / `transcript_tech_parser.py` / `llm_client.py` / `action_classifier.py` / `action_segmenter.py`
- **部署环境依赖**：GPU（可选，YOLOv8 加速）/ CPU（MediaPipe fallback）/ Whisper 模型文件 / LLM 服务访问（Venus 或 OpenAI）

## 范围外

- **算法本身的改进**：4 个维度的阈值/公式调整、新增技术维度、切换 YOLOv8 版本 — 都不在本 Feature
- **LLM prompt 优化**：沿用 `transcript_tech_parser` 现有 prompt；重新设计 prompt 是独立 Feature
- **Feature-002 与 Feature-014 算法对等的数据迁移**：不回填旧版 `ExpertTechPoint` 到新作业形态；两份历史数据并存
- **冲突审核 UI/API**：沿用 Feature-014 仅提供存储层的约定
- **算法性能优化**（如批量推理、多 GPU 等）：本 Feature 只做最小可工作接入
