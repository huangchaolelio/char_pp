# 功能规范: 视频预处理流水线

**功能分支**: `016-video-preprocessing-pipeline`
**创建时间**: 2026-04-25
**状态**: 草稿
**输入**: 用户描述: "对原始增加视频预处理任务,构建视频标准,满足后续的知识库提取能力.当时视频较大时,先对视频进行分割,分割后的视频和原始视频保持映射关系,并上传到cos上.知识库提取,实际上是针对预处理后的视频进行的.原始的知识库提取任务重构,减少重复计算,并优化执行效率."

## Clarifications

### Session 2026-04-25

- Q: 音频路 Whisper OOM（Feature-015 烟测已证实）在本 Feature 如何解决？ → A: 预处理任务一并产出整段 16 kHz 单声道 WAV 并上传 COS；KB 提取直接拉预置音频喂 Whisper；`WHISPER_DEVICE=cpu` 稳定化（CPU small 实测 1-2 GB RSS）
- Q: `force=true` 重新预处理同一 `cos_object_key` 时，旧 job + 旧分段 COS 对象如何处置？ → A: 旧 job 标 `status=superseded` 保留记录供审计；旧分段 COS 对象同步删除释放空间；新 job 的产物放按 job_id 隔离的路径 `preprocessed/{original_cos_key}/jobs/{job_id}/seg_NNNN.mp4`（音频同理 `.../jobs/{job_id}/audio.wav`）避免并发读写竞争
- Q: 多分段 COS 上传的执行模型？ → A: 流式切分（一段切完立即可读）+ `ThreadPoolExecutor(max_workers=2)` 并发上传；切分主线程不被上传阻塞；沿用 Feature-007 commit 8713543 成功实证的 ThreadPool + subprocess 模式
- Q: COS 产物丢失的检测与恢复机制？ → A: 懒检测，KB 提取消费时若下载 404 返回 `SEGMENT_MISSING:` / `AUDIO_MISSING:` 前缀错误；运维看到 failed 后手动 force=true 重建；不引入主动 verify / sweep 基础设施（低频事件不值得）
- Q: 视频质量预检（fps / 分辨率）是否前移到预处理阶段？ → A: 前移到预处理 probe 阶段——不合格视频立即 `VIDEO_QUALITY_REJECTED:` 失败，不进入转码/分段/上传，节省带宽和 COS 存储；`pose_analysis` 的 validate 调用保留为兜底防止标准漂移
- Q: 预处理完成后本地分段文件如何处置？KB 提取是否能复用本地缓存？ → A: 上传 COS 成功后**不立即删本地**——预处理产物在 `EXTRACTION_ARTIFACT_ROOT/preprocessing/{job_id}/` 保留；KB 提取消费时**先检 COS 存在（必须已上传）再查本地**：本地存在 → 直接用本地文件；本地缺失 → 从 COS 下载；这避免"预处理 + 立即 KB 提取"场景下的重复下载
- Q: 并发 KB 提取读同一本地缓存 + 周期清理任务之间的竞争如何协调？ → A: 无显式锁，依赖 POSIX 文件句柄语义；清理任务删前检查 mtime + atime，最近被访问则延期一轮；极端情况下被误删的已打开文件句柄仍有效，最坏结果是下次读时走 COS fallback（FR-005d 已覆盖）
- Q: 本地保留 TTL 精确值 + failed job 的本地残留策略？ → A: success / failed 统一保留 24 小时，简化运维无需区分状态；新增单一配置项 `PREPROCESSING_LOCAL_RETENTION_HOURS`（默认 24）

## 背景

Feature-015 部署烟测（2026-04-25）揭示了知识库提取流水线在真实大视频上的两个核心问题：

1. **内存峰值不可控**：当前 `pose_analysis` 直接对整段视频（可能 10+ 分钟）一次性调用 `estimate_pose`，单次推理内存占用超过 pod memcg 上限被 OOM-killed；历史 Feature-007 已用 "180 秒分段 + 顺序处理" 模式成功绕开，但 Feature-015 未继承该机制
2. **重复计算**：同一教练视频如果需要多次做 KB 提取（rerun / force / 多 tech_category 并行抽取），每次都要重新从 COS 下载原视频 + 重切帧 + 重跑 ffmpeg，浪费带宽和 CPU 时间

此外，不同视频的来源格式、分辨率、帧率、编码参数不一致，直接喂给姿态估计器会产生难以复现的质量差异。

本 Feature 在流水线前端新增**视频预处理**阶段，把"下载 + 标准化 + 分段 + 回传 COS"做成一次性工作，产出可被知识库提取重复消费的**标准化视频片段集**。

## 用户场景与测试 *(必填)*

### 用户故事 1 - 大视频自动分段预处理（优先级: P1）🎯 MVP

**角色**：运维 / 系统

**旅程**：运维通过 API（或系统自动调度）对一个新入库的教练视频触发预处理。系统从 COS 下载视频、检测时长，若超过预设阈值则按固定秒数切分，标准化分辨率和帧率，把每个分段作为独立对象上传回 COS，并在数据库建立原视频 → 分段片段的映射表。完成后视频具备"可提取 KB"的状态。

**优先级原因**：这是整个改造的基础。没有分段产物，长视频的 KB 提取永远跑不动；没有 COS 回传，就无法做重复消费。P1 MVP 必须覆盖此路径。

**独立测试**：提交一个 > 分段阈值 的真实教练视频到预处理任务 → 能在数据库查到 N 个片段记录（N = ceil(时长 / 段长)）→ 每个片段 `cos_object_key` 可在 COS 上下载 → 片段时长误差 < 1 秒 → 所有片段共同拼接覆盖原视频完整时间段（无缺口、无重叠）。

**验收场景**:

1. **给定** 一个时长 600 秒、在 `coach_video_classifications` 中已分类的教练视频，**当** 运维对它触发 `preprocess_video` 任务，**那么** 系统应在 `<5 倍原视频时长` 内完成预处理，产出 ceil(600/180)=4 个分段片段，片段在 COS 上可访问，数据库映射表包含 4 条记录
2. **给定** 一个时长 60 秒的短视频（< 分段阈值），**当** 触发预处理，**那么** 系统仅做标准化而不分段，产出 1 条映射记录，该片段的 cos_object_key 可能指向原视频或标准化后的副本（二者在片段表中表现一致）
3. **给定** 同一原视频第二次触发预处理（force=false），**当** 任务提交，**那么** 系统必须检测到已有有效预处理产物并直接返回已有映射不重复切分、不重复上传

---

### 用户故事 2 - 知识库提取消费预处理产物（优先级: P1）

**角色**：系统（KB 提取流水线）

**旅程**：KB 提取任务收到一个教练视频的提取请求时，不再直接拉原视频，而是读取该视频的预处理映射表，依次对每个分段下载并做 pose / audio 提取，再在任务层合并产出条目。预处理产物可被同一原视频的多次 KB 提取任务复用。

**优先级原因**：这是本改造的"回报"——Feature-015 发现的单次大视频 OOM、rerun 重复下载都因此消除。没有这条链路重构，新建预处理产物也没价值。

**独立测试**：对已完成预处理的视频发起 KB 提取 → 验证流水线消费分段而非原视频（pose.json 按分段累计，不一次性装载全视频）→ rerun 同一任务时，预处理 step 状态为 reused 或 skipped → 两次任务的产出条目数一致、分段下载次数从 N 降为 0（rerun 情况）。

**验收场景**:

1. **给定** 一个视频的预处理产出包含 4 个分段，**当** KB 提取任务对其执行 pose_analysis，**那么** 执行器应按分段顺序处理，每段独立产出 pose_frames 并增量合并到 pose.json；单段内存峰值显著小于全视频一次性处理
2. **给定** 同一任务第一次 KB 提取已完成，**当** 运维发起 rerun，**那么** 预处理 step 应检测到已有产物并跳过，pose_analysis / audio_transcription 可选择从 COS 再下各分段或直接重用本地缓存，**不重新切分 / 不重新上传**
3. **给定** 某个分段在 COS 上被误删，**当** KB 提取读取该分段失败，**那么** 系统必须给出结构化错误（如 `SEGMENT_MISSING:`），不影响其他分段的处理，且能通过重新触发预处理恢复

---

### 用户故事 3 - 视频标准化元数据可观察（优先级: P2）

**角色**：运维 / 审计

**旅程**：运维想了解某个视频的预处理结果——原视频的分辨率、帧率、编码、预处理后的标准化参数、分段策略、每段时长等，通过 API 或数据库查询一次得到完整视图，无需手动跑 ffprobe。

**优先级原因**：可观测性降低排障成本，但非主链路关键路径；P2 优先级合适。

**独立测试**：通过 `GET /api/v1/video-preprocessing/{video_id}` 查询某个预处理任务 → 返回 JSON 含原视频元数据（分辨率、帧率、编码、时长、文件大小）+ 标准化参数（目标分辨率、目标帧率）+ 分段列表（每段起止时间、cos_object_key、文件大小）→ 与 COS 上实际文件尺寸一致。

**验收场景**:

1. **给定** 一个完成预处理的视频，**当** 通过 API 查询元数据，**那么** 响应 JSON 应至少包含：原视频 `fps / width / height / duration_sec / codec / size_bytes`，标准化参数 `target_fps / target_resolution`，分段列表 `segments: [{index, start_ms, end_ms, cos_object_key, size_bytes}, ...]`
2. **给定** 一个视频预处理失败（如 ffmpeg 崩溃），**当** 查询元数据，**那么** 响应应明确展示 failure reason 前缀和失败发生的具体阶段（probe / transcode / split / upload）

---

### 用户故事 4 - 批量并发预处理（优先级: P3）

**角色**：运维

**旅程**：运维对一个教练系列（20+ 视频）批量触发预处理任务，系统按队列容量限制并发数，依次完成。

**优先级原因**：场景价值明确但非 MVP，现有 Feature-013 批量提交接口已能复用。P3 可选。

**独立测试**：批量提交 5 个视频 → 观察 `task_channel` 状态，同一时刻 running 任务不超过配置的并发上限 → 全部完成后数据库映射表包含 5 个原视频 × N 个分段的记录。

**验收场景**:

1. **给定** 预处理通道并发配置为 2，**当** 一次提交 5 个视频，**那么** 任何时刻最多 2 个视频同时处于 preprocessing 状态，其他进入 pending 队列
2. **给定** 批量提交中某个视频预处理失败，**当** 查询批次状态，**那么** 其他视频的处理不受影响，该失败视频状态为 failed 并带有 error_message 前缀

---

### 边界情况

- **视频无音频**：预处理不需要失败；只需要在标准化阶段跳过音频转码，分段时保留"视频无音频"标识
- **视频编码不支持**：如异常编码 / 无法解码 → 结构化错误 `VIDEO_CODEC_UNSUPPORTED:` + 明确失败阶段为 probe
- **COS 上传失败**：单个分段上传失败应重试；多次失败后整个预处理任务失败，已成功上传的分段保留或清理（见下方"清理策略"假设）
- **分段恰好跨越动作关键帧**：接受切分边界可能把一个挥拍动作切成两半；下游 KB 提取在分段间做最小窗口（例如 0.5 秒）前后滑动（由 Feature-014 action_segmenter 负责，不是本 Feature 范畴）
- **COS 产物丢失**：分段或音频在 COS 上被误删时，KB 提取消费时懒检测——下载 404 → `SEGMENT_MISSING:` / `AUDIO_MISSING:` 前缀错误 → 任务 failed；运维手动 `force=true` 重新预处理恢复。不引入主动 verify / sweep 基础设施
- **已预处理视频的原视频被替换**：假设由上游 Feature-008 COS 扫描检测（本 Feature 只管按 `cos_object_key` 查映射表；替换场景在依赖边界外）
- **预处理产物超过本地磁盘容量**：ffmpeg 切分 + 上传阶段以流式方式处理（一段切完立即进上传队列），切分主线程最多保留 3 段本地文件排队上传；上传成功后本地文件不立即删（FR-005c），由周期清理（FR-005e，24h TTL）接管；24 小时内的峰值磁盘占用约等于 1 个教练视频系列（10-30 段 × 10-30 MB/段 ≈ 300-900 MB），可接受
- **本地残留但 COS 上传失败**：FR-005d 的 "COS head 校验" 前置门禁防止此场景——即便 KB 提取本地发现缓存，必须先确认 COS 有同名对象才允许使用，避免"幽灵数据"污染 KB 提取

## 需求 *(必填)*

### 功能需求

- **FR-001**: 系统必须提供 `POST /api/v1/tasks/preprocessing`（单条）和 `POST /api/v1/tasks/preprocessing/batch`（批量）端点，接受 `cos_object_key` + 可选 `force` 参数，创建预处理任务
- **FR-002**: 预处理任务必须从 COS 下载原视频，并用 ffprobe 采集原视频元数据（fps / 宽 / 高 / 时长 / 编码 / 音轨存在性 / 文件大小），将元数据写入 `video_preprocessing_jobs` 表
- **FR-002a**: 预处理在 probe 阶段必须调用 `video_validator.validate_video` 做质量门禁——若 fps / 分辨率不达标立即抛 `VIDEO_QUALITY_REJECTED:` 前缀错误，任务 failed，**不进入转码 / 分段 / 上传阶段**（节省带宽和 COS 存储）；`pose_analysis` 执行器保留的 validate 调用作为兜底，防止标准参数漂移
- **FR-003**: 系统必须按项目级"视频标准"对原视频做转码（目标分辨率 / 目标帧率由配置决定），产出标准化视频
- **FR-004**: 当原视频时长超过可配置的分段阈值（默认 180 秒）时，系统必须按分段秒数切分标准化视频为多个片段；时长不超过阈值时产出 1 个片段（等同原视频的标准化副本）
- **FR-005**: 每个分段片段必须作为独立对象上传到 COS（路径由映射规则决定，如 `preprocessed/{original_cos_key}/seg_{index:04d}.mp4`），并在 `video_preprocessing_segments` 表写入映射记录（原视频 COS key + 段索引 + 起止毫秒 + 分段 COS key + 分段文件大小）
- **FR-005c**: 分段上传必须采用**流式切分 + 并发上传**模型：ffmpeg 切分主线程顺序产出分段到本地磁盘，`ThreadPoolExecutor(max_workers=2)` 并发从本地把完成的分段上传到 COS；主线程不因上传阻塞，继续切下一段。**上传成功后本地分段文件不立即删除**，保留在 `EXTRACTION_ARTIFACT_ROOT/preprocessing/{job_id}/` 目录下作为"温缓存"供后续 KB 提取复用；本地保留 TTL 由 FR-005e 控制。沿用 Feature-007 commit `8713543` 实证的 ThreadPool + subprocess 模式
- **FR-005d**: KB 提取消费预处理产物时必须采用**"COS 存在性门禁 + 本地优先"**两段式读取：(1) 先调 COS head 请求**校验每个分段和音频对象在 COS 上实际存在**（防止本地有残留但 COS 上传已失败的幽灵数据）；(2) COS 存在性通过后，**若本地 `EXTRACTION_ARTIFACT_ROOT/preprocessing/{job_id}/` 目录下有对应文件则直接读本地**，否则从 COS 下载。本地与 COS 必须是位字节级一致（通过 size 或 ETag 比对，至少 size 一致）
- **FR-005e**: 预处理产物的本地保留策略由 `cleanup_intermediate_artifacts` 周期任务（Feature-014 已有 beat）扩展接管：无论 job status（success / failed），本地产物**统一保留 24 小时**（可配置项 `PREPROCESSING_LOCAL_RETENTION_HOURS`，默认 24），超期目录自动删除（不影响 COS 侧）。**清理任务删除前必须检查目录内文件的 `mtime` 和 `atime`**——若最近 1 小时内有被访问（atime > now−1h 或 mtime > now−1h），则延期一轮清理（下次 beat 再判断），防止清理与并发 KB 提取读取竞争；**不引入显式文件锁**，依赖 POSIX 文件句柄语义（已打开的文件被删除后句柄仍有效）。Feature-015 的中间 artifact 清理（`job_id`→`pose.json`/`transcript.json`）与本 Feature 的 `preprocessing/{job_id}/` 预处理产物清理**各自独立**（路径隔离），避免清理一方误删另一方
- **FR-005a**: 预处理任务必须一并产出原视频的整段音频（16 kHz 单声道 WAV，通过 ffmpeg 从原视频提取），上传到 COS（路径 `preprocessed/{original_cos_key}/audio.wav`），并在 `video_preprocessing_jobs` 表记录该音频的 `cos_object_key` + `size_bytes` + `has_audio`。无音频轨道的视频 `has_audio=false` 且不产出 WAV
- **FR-005b**: KB 提取的 `audio_transcription` 执行器必须从 COS 拉取预处理产物 `audio.wav` 直接喂 Whisper，不再自己用 ffmpeg 从视频提取音频；Whisper 推理必须用 CPU 后端（`WHISPER_DEVICE=cpu`）以避免 GPU CUDA 初始化的 58 GB 虚拟地址占用撞 pod memcg 的问题
- **FR-006**: 预处理任务完成后，必须把 `coach_video_classifications.preprocessed` 字段（新增）置为 TRUE；该字段由本 Feature 新增
- **FR-007**: `force=false` 提交的预处理任务必须检测已有的有效预处理产物，若存在 → 返回已有映射、不重复执行切分和上传
- **FR-007a**: `force=true` 提交的预处理任务必须：(1) 把该 `cos_object_key` 下所有旧 job 的 `status` 置为 `superseded`（保留 DB 记录供审计，不硬删）；(2) 删除旧 job 对应的 COS 分段对象 + 音频对象以释放空间；(3) 新 job 的产物上传到按 job_id 隔离的路径 `preprocessed/{original_cos_key}/jobs/{job_id}/seg_NNNN.mp4` 和 `preprocessed/{original_cos_key}/jobs/{job_id}/audio.wav`，避免与并发中的 KB 提取任务读旧对象冲突
- **FR-008**: KB 提取流水线（`kb_extraction_task.extract_kb`）必须读取映射表，按分段顺序下载并处理；不再直接处理原视频
- **FR-009**: 知识库提取内部的 `pose_analysis` 执行器必须改造为按分段迭代：每段下载到本地 → `estimate_pose(segment_path)` → 累积 frames 到内存 → 本段处理完释放本地文件 → 进入下一段
- **FR-010**: 同一原视频的 KB 提取 rerun 必须能复用预处理产物（无需再切分 / 再上传）；rerun 时仅重新下载 + 重跑算法
- **FR-011**: 预处理失败时必须写结构化错误前缀（如 `VIDEO_DOWNLOAD_FAILED:` / `VIDEO_PROBE_FAILED:` / `VIDEO_CODEC_UNSUPPORTED:` / `VIDEO_TRANSCODE_FAILED:` / `VIDEO_SPLIT_FAILED:` / `VIDEO_UPLOAD_FAILED:`），按失败阶段区分
- **FR-011a**: KB 提取消费阶段若从 COS 下载预处理产物遇到 404 / NoSuchKey 错误，必须用结构化错误前缀 `SEGMENT_MISSING:` 或 `AUDIO_MISSING:` 让任务 failed；运维通过手动触发 `force=true` 预处理重建产物恢复；本 Feature 不引入主动 verify 或周期 sweep 基础设施（依运维手动工具链为准）
- **FR-012**: 系统必须提供 `GET /api/v1/video-preprocessing/{job_id}` 端点，返回完整元数据（原视频 + 标准化参数 + 分段列表）供审计
- **FR-013**: 预处理通道必须独立于 KB 提取、诊断、分类三个已有通道，有独立的并发上限、队列容量（默认 3 并发 / 容量 20，可热更新）
- **FR-014**: 预处理任务必须遵守现有的幂等提交机制（idempotency_key）和孤儿任务回收机制（Feature-013 同款）
- **FR-015**: 分段片段在 COS 上的生命周期必须与原视频解耦；本 Feature 内保留策略：分段永久存储直到运维主动清理（预处理产物体积小，可长期复用）
- **FR-016**: 本 Feature 不改 Feature-015 的四个 step executor 的核心算法逻辑（action_segmenter / action_classifier / tech_extractor / LLM 抽取均保持不变），只改"如何喂数据"的接线层

### 关键实体 *(如果功能涉及数据则包含)*

- **VideoPreprocessingJob**（新表）: 每次预处理任务一条记录。关键属性：`id / cos_object_key / status (running|success|failed|superseded) / started_at / completed_at / error_message / original_meta_json / target_standard_json / force / duration_ms / segment_count / audio_cos_object_key / audio_size_bytes / has_audio`。幂等：每个 `cos_object_key` 至多一条 `status='success'` 的记录（partial unique index），旧成功 job 被 `force=true` 覆盖时先转 `superseded`
- **COS 对象分层**（约定）: 原视频仍在 `COS_VIDEO_ALL_COCAH/` 路径下；**预处理产物按 job_id 隔离**：`preprocessed/{original_cos_key}/jobs/{job_id}/seg_NNNN.mp4` + `preprocessed/{original_cos_key}/jobs/{job_id}/audio.wav`，与原视频路径隔离方便批量清理，按 job_id 子目录避免 force 覆盖时的读写竞争
- **VideoPreprocessingSegment**（新表）: 每个分段一条记录。关键属性：`id / job_id (FK) / segment_index / start_ms / end_ms / cos_object_key / size_bytes / has_audio / created_at`。(job_id, segment_index) 唯一
- **CoachVideoClassification**（扩展）: 新增 `preprocessed: bool`（默认 false）字段，表示"至少有一次预处理成功"。与现有 `kb_extracted` 字段并列
- **ExtractionJob / PipelineStep**（保持 Feature-014 schema）: 不新增表，但 `pipeline_steps.output_summary` 在 `pose_analysis` / `audio_transcription` 中新增 `segments_processed` / `segments_skipped` 字段（由 executor 重构时添加）
- **COS 对象分层**（约定）: 原视频仍在 `COS_VIDEO_ALL_COCAH/` 路径下；预处理产物在 `preprocessed/{original_cos_key}/seg_NNNN.mp4`，与原视频路径隔离方便清理

## 成功标准 *(必填)*

### 可衡量的结果

- **SC-001**: 对一个 10 分钟大视频，预处理任务端到端可成功完成（不出现 OOM / SIGKILL），产出可被 KB 提取消费的有效分段集
- **SC-002**: 在 KB 提取任务消费预处理产物后，`pose_analysis` 单步单段处理内存峰值 < 原视频整体处理内存峰值的 50%（以 Worker 进程 RSS 最大值测量）
- **SC-003**: 同一原视频的第 N 次 KB 提取任务（N ≥ 2，带或不带 rerun）对比第 1 次，总耗时减少 ≥ 30%（主要通过跳过重复的下载 + 转码 + 切分）
- **SC-004**: 预处理失败率 ≤ 5%（基于一批次 20 个随机教练视频的实测；排除"视频编码本身不支持"这类源数据问题）
- **SC-005**: 分段时长误差 < 1 秒（实际分段时长 vs 预期 180 秒的偏差绝对值均值）；所有分段拼接覆盖原视频完整时间段（累计时长误差 < 原时长 1%）
- **SC-006**: 单次预处理任务耗时 ≤ 原视频时长的 5 倍（即 10 分钟视频 ≤ 50 分钟预处理）；分段 + 上传可并行优化
- **SC-007**: 100% 的预处理失败都有结构化错误前缀（按失败阶段可 grep 统计分布）

## 假设

- **视频标准参数**：目标帧率采用 30 fps、目标分辨率按原视频纵横比缩放到短边 720（保留 1080p 视频的可选项）。具体数值在 `.env` 或配置文件中定义；本 Feature 不引入需要用户介入选择的复杂策略
- **分段阈值 180 秒**：沿用 Feature-007 历史成功验证的参数；可通过配置（如 `VIDEO_PREPROCESSING_SEGMENT_DURATION_S`）调整
- **COS 目录结构**：预处理产物统一放 `preprocessed/` 前缀下；与原视频树隔离方便批量清理
- **清理策略**：预处理产物默认永久保存（运维可手动删）；不引入自动 TTL，避免和 KB 重跑需求打架。`force=true` 触发旧 job 变 `superseded` 时，对应 COS 对象**同步删除**（FR-007a），DB 记录保留供审计
- **并发限制**：预处理通道默认 3 并发（Worker `--concurrency=3` 或线程池）、队列 20，延续 Feature-013 通道模型的热更新能力
- **视频"较大"判定**：以时长（而非文件大小）判定——时长 > 分段阈值就切分；原因是 pose 估计的计算量与时长线性相关，与文件大小非线性相关
- **分段重叠**：不引入分段重叠。接受在段边界处可能漏掉跨段的动作识别；这是与 Feature-014 `action_segmenter` 独立窗口设计的妥协；若将来需要，可在本 Feature 外扩展
- **音频处理**：预处理任务一并产出整段音频（16 kHz 单声道 WAV，ffmpeg 提取）并上传到 COS `preprocessed/{original_cos_key}/audio.wav`；KB 提取的 `audio_transcription` 直接拉这个音频喂 Whisper，不再从视频实时提取。Whisper 强制使用 CPU 后端（`WHISPER_DEVICE=cpu`）避免 GPU CUDA 初始化的 58 GB 虚拟地址占用。**音频本身不分段**——Whisper 对完整语音效果更好，且 CPU small 模型实测 1–2 GB RSS 稳定不会 OOM
- **依赖 Feature-015**：四个 executor 的算法逻辑已稳定；本 Feature 重构其"数据入口"但不动算法本体
- **依赖 Feature-014**：DAG orchestrator、retry_policy、通道机制、extraction_jobs / pipeline_steps 表复用，不做架构改造
- **依赖 Feature-013**：任务通道、幂等提交、孤儿回收全部复用
- **依赖 Feature-008**：`coach_video_classifications` 是视频数据源；`preprocessed` 字段加在这个表上
