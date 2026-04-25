# 阶段 0 研究笔记: 视频预处理流水线

**日期**: 2026-04-25
**分支**: `016-video-preprocessing-pipeline`

本文件把 spec.md 的 8 条 Clarifications 翻译为 `Decision / Rationale / Alternatives` 三元组，并补充 plan.md 技术背景里依赖 / 集成的最佳实践研究。所有"NEEDS CLARIFICATION"在 spec.md Clarify 阶段已清零，本阶段只沉淀研究论证。

---

## R1 — Whisper 内存稳定化（CPU + 预置 WAV）

**Decision**: 预处理阶段一并产出 16 kHz mono WAV 并上传 COS `preprocessed/{cos_key}/jobs/{job_id}/audio.wav`。KB 提取的 `audio_transcription` executor 直接从 COS 拉取 WAV 喂 Whisper，**强制** `WHISPER_DEVICE=cpu`。

**Rationale**:
- Feature-015 烟测（2026-04-25）实证 GPU 后端加载 torch CUDA → 单进程虚拟地址 58 GB，触发 pod memcg 64 GB 硬上限 SIGKILL
- Whisper `small` 在 CPU float32 模式 RSS 稳定在 1–2 GB（OpenAI 官方 issue 讨论 + Feature-007 历史烟测验证）
- 音频完整性对 Whisper 精度关键（切分会截断语音），所以**不对音频做分段**
- WAV 一次性产出 + 复用：同一原视频后续 KB 提取无需重复 ffmpeg-extract-audio

**Alternatives considered**:
- ❌ GPU Whisper + 降 FP16：仍占 58 GB 虚地址，memcg 不区分 RSS / 虚拟
- ❌ Whisper `tiny` 替代 `small`：精度下降超过可接受范围（中文字错率 +5-8pp）
- ❌ 保留现有"从视频实时提取音频"：每次 rerun 重复 ffmpeg，浪费 CPU 与本地磁盘 I/O
- ❌ 音频也分段：Whisper 对 180s 片段质量明显下降（边界词被截断）

---

## R2 — 分段阈值 180 秒 + 仅基于时长判定

**Decision**: 分段阈值硬编码默认 180 秒（配置项 `video_preprocessing_segment_duration_s`）。**以时长判定**是否需要分段，**不以文件大小**。

**Rationale**:
- Feature-007 commit `8713543` 在该阈值下稳定处理过 ≥ 45 分钟长视频，历史实证数据充分
- pose estimation 的计算量和 RSS 峰值 ~ O(duration)，与文件大小（受编码参数强影响）非线性相关
- 180s × 30fps = 5400 帧，YOLOv8 batch=16 处理 ≈ 340 batches / 单段，pose buffer 可控
- 短视频（< 180s）跳过分段 → 只做标准化转码 + 单段上传，省 ffmpeg 切分开销

**Alternatives considered**:
- ❌ 阈值 60s：分段过多（典型教练视频 10 分钟 → 10 段），COS list / DB 查询成本上升，benefit 边际
- ❌ 阈值 600s：单段内存峰值接近 OOM 边界，失去安全余量
- ❌ 以 file size 为阈值：同一时长不同编码参数的视频会被分到不同分支，破坏一致性

---

## R3 — 流式切分 + ThreadPoolExecutor(2) 并发上传

**Decision**: ffmpeg 切分主线程顺序产出分段到 `${EXTRACTION_ARTIFACT_ROOT}/preprocessing/{job_id}/`，`concurrent.futures.ThreadPoolExecutor(max_workers=2)` 消费队列并发上传到 COS。主线程**不因上传阻塞**。

**Rationale**:
- 沿用 Feature-007 commit `8713543` 已验证的并发模式，实证稳定
- I/O-bound（上传）用 ThreadPool 即可饱和带宽；不需要 ProcessPool
- `max_workers=2` 在内网带宽 + COS QPS 配额下是甜点值，>2 会撞 COS 429
- 切分主线程最多保留 3 段本地文件排队上传（背压上限），峰值本地磁盘 ≈ 60–90 MB（标准化后单段 20–30 MB × 3）

**Alternatives considered**:
- ❌ 同步串行上传：10 段视频上传时间和切分时间串行累加，~2 倍总耗时
- ❌ `asyncio + aiohttp` 改造：COS SDK 不原生支持 async，会引入新依赖和调试复杂度
- ❌ ProcessPool：主要针对 CPU-bound 任务，此处 I/O-bound 用 Process 反而增加 IPC 开销

**实施约束**: 每个上传线程持有独立的 cos_client 实例（SDK 不是线程安全的）

---

## R4 — force=true → superseded + 删旧 COS 对象 + job_id 隔离路径

**Decision**: `force=true` 重新预处理时三步联动：
1. 旧 job row `status='running'|'success'` → 置 `status='superseded'`（partial unique index `(cos_object_key) WHERE status='success'` 保证幂等）
2. 旧 job 产物 COS 对象**同步删除**（先 list 前缀 → 批量 delete_object）
3. 新 job 产物写入 `preprocessed/{cos_key}/jobs/{new_job_id}/seg_NNNN.mp4` 和 `audio.wav`

**Rationale**:
- `superseded` 保留审计痕迹，DB 不硬删
- 删旧 COS 对象释放存储（分段体积累积可观）
- job_id 隔离路径避免"正在 KB 提取读旧对象 + force 覆盖写新对象"的读写竞争
- 路径层级不冲突：旧 job 路径 `.../jobs/{old_id}/` 删除后，新 job `.../jobs/{new_id}/` 独立写入

**Alternatives considered**:
- ❌ 覆盖写同路径：并发读写同名对象会触发 COS 一致性窗口问题，KB 提取可能读到半成品
- ❌ 硬删旧 DB 行：失去审计能力，排障困难
- ❌ 保留旧 COS 对象不删：存储成本无限增长

---

## R5 — 懒检测 COS 缺失

**Decision**: COS 产物丢失只在 KB 提取消费时**懒检测**：`cos_client.head_object` 返回 404 → RuntimeError with `SEGMENT_MISSING:` / `AUDIO_MISSING:` 前缀 → 任务 failed。不引入主动 verify 或周期 sweep 任务。

**Rationale**:
- COS 丢失是低频事件（误删 / 跨区域复制失败），绝大多数 job 不命中
- 引入 sweep 任务增加调度复杂度 + 常驻资源占用
- 懒检测在真正需要时触发恢复，符合 YAGNI
- 运维发现 `*_MISSING:` 错误后用现有 `force=true` 接口即可重建

**Alternatives considered**:
- ❌ 每 N 小时扫描所有预处理产物：对万级对象无意义消耗 COS QPS
- ❌ 预处理完成时双写校验：已在上传阶段做 ETag 校验（R3 补充），入库后再查多余

---

## R6 — probe 阶段前置视频质量门禁

**Decision**: 预处理 probe 阶段调用 `video_validator.validate_video`，不合格立即抛 `VIDEO_QUALITY_REJECTED:` 错误，**不进入转码/分段/上传**。`pose_analysis` 的 validate 调用**保留**作为兜底防止标准漂移。

**Rationale**:
- 早失败节省：不合格视频每次通过大约 10–50 MB 下载 + 转码 CPU + COS 存储
- 兜底保留原因：将来改 `target_short_side=1080` 时，预处理规格和 pose 规格可能错位，validate 兜底可兜住
- validate_video 是纯 CPU + OpenCV probe，毫秒级开销

**Alternatives considered**:
- ❌ 仅在 pose_analysis 做 validate：浪费完整预处理链路 + 撒满 COS 无效产物
- ❌ 预处理做 validate 后移除 pose_analysis 的 validate：失去兜底，标准漂移难检测

---

## R7 — 本地温缓存 + COS 存在性门禁两段式读取

**Decision**: 预处理完成后本地 `${EXTRACTION_ARTIFACT_ROOT}/preprocessing/{job_id}/` **不立即删**，保留 24h（成功/失败统一）。KB 提取消费时：
1. **先 COS `head_object` 校验**每个分段 / audio.wav 存在（防本地有缓存但 COS 上传失败的幽灵数据）
2. COS 存在后，**若本地文件存在且 size 一致 → 直接读本地**，否则从 COS 下载到本地再读

**Rationale**:
- 现实场景：预处理完成后几分钟内触发 KB 提取（最常见），本地命中率 ≥ 90%，省一次 COS 下载
- COS 存在性门禁防止"本地有残留但 COS 上传失败"造成的错误产物污染 KB 提取
- Size 比对是最便宜的一致性校验（无 ETag 或 md5 计算开销）
- 24h 窗口对典型批量 KB 提取任务（1–4 小时）足够宽

**Alternatives considered**:
- ❌ 上传成功立即删本地：直接读写 COS，rerun 必然重下载，浪费带宽
- ❌ 本地永久保留：磁盘无限膨胀，pod 有硬限制
- ❌ 只读本地不校验 COS：KB 提取成功但实际 COS 缺失，后续 rerun 会突然 404，难排障

---

## R8 — 无锁 + mtime/atime 检查协调清理与并发读

**Decision**:
- 清理任务 `cleanup_intermediate_artifacts` 删除前检查目录内文件的 `mtime` 和 `atime`：若最近 1 小时内有访问（`max(mtime, atime) > now - 1h`）→ 延期一轮清理（下次 beat 再判断）
- **不引入显式文件锁**，依赖 POSIX 已打开文件句柄语义（被删除文件句柄仍有效）
- 极端情况：并发读取的文件被清理任务删除 → 已持有 fd 读完正常，下次读走 COS fallback（R7 已覆盖）

**Rationale**:
- 显式文件锁（flock / fcntl）引入死锁风险 + 跨进程 edge case
- 24h 保留窗口远大于典型 KB 提取步骤耗时（分钟级），atime 检查足以覆盖绝大多数竞争场景
- POSIX 语义保证最坏情况是多一次 COS 下载，不会导致数据错误

**Alternatives considered**:
- ❌ flock 文件锁：复杂性高，调试困难
- ❌ Redis 分布式锁：新增依赖，24h TTL 锁的释放语义复杂
- ❌ 不做 atime 检查（仅按创建时间删）：可能误删正在读的文件，导致 EIO 错误
- ❌ mtime 由 Linux 默认关闭（noatime mount）: 主机默认挂载行为不可控 — 检查时需兼容"atime 不可用"降级（退化为仅 mtime）

**实施约束**: 挂载点若 `noatime` 会让 atime 失效 → 代码用 `os.stat` 同时取 atime/mtime，取 `max()`；最坏情况退化为 mtime-only 检查，不影响正确性

---

## R9 — 预处理产物保留 TTL 24h（统一 success/failed）

**Decision**: 新增单一配置项 `PREPROCESSING_LOCAL_RETENTION_HOURS`（默认 24）。无论 job status（`success` / `failed` / `superseded`），本地 `preprocessing/{job_id}/` 目录统一保留 24 小时。

**Rationale**:
- 运维简化：不需要按 status 区分清理策略
- 24h 对"预处理后触发 KB 提取"的常见 workflow 足够宽（KB 提取典型 10–30 分钟内触发）
- Feature-015 的成功 24h / 失败 168h 分化策略对**调试**有价值（失败 artifact 留 7 天供排障），但**预处理产物**主要作用是温缓存而非调试，24h 够用
- 失败 job 的本地残留通过 `error_message` 前缀 + DB 记录已足够排障，不依赖本地文件

**Alternatives considered**:
- ❌ success 24h / failed 168h（和 Feature-015 保持一致）：两份配置项 + 两套路径管理，复杂度不值
- ❌ 48h / 72h：磁盘占用更高，获益边际（常见 rerun 在 24h 内触发）
- ❌ 永久保留：pod 存储有限，无法接受

---

## R10 — 新 `preprocessing` 通道独立于现有四通道

**Decision**: 新增第 5 个任务通道 `preprocessing`：独立队列名、独立 Celery Worker（`--concurrency=3`）、独立 `task_channel_configs` 行（默认容量 20，可热更新）。

**Rationale**:
- 与 `classification` / `kb_extraction` / `diagnosis` / `default` 严格物理隔离，沿用 Feature-013 通道模型
- 预处理 CPU 特征（ffmpeg 重编码）与 KB 提取（算法推理）不同，共享 Worker 会互相抢 CPU
- 并发 3 是 3 × ffmpeg + 3 × ThreadPool-upload ≈ 6–9 条并发链路，匹配典型 pod 的 8–16 CPU

**Alternatives considered**:
- ❌ 复用 `default` 队列：扫描任务和预处理混跑，预处理慢会阻塞扫描
- ❌ 复用 `kb_extraction` 队列：两者 CPU 特征不同、资源竞争
- ❌ 并发 1：太保守，批量预处理 20 个视频 × 10 分钟 × 1 并发 = 200 分钟耗时，不可接受

---

## R11 — 不改 Feature-014 DAG 定义，只改 executor 实现

**Decision**: Feature-014 `pipeline_definition.py::DEPENDENCIES` 保持 6 步不变（download → pose ∥ audio_transcribe → visual_kb ∥ audio_kb → merge）。本 Feature 只改 4 个 executor 的"数据入口"：
- `download_video` → 读 segments 表顺序下载所有分段 + audio.wav 到本地
- `pose_analysis` → 按分段迭代 `estimate_pose` + 累积 frames 到 `pose.json`
- `audio_transcription` → 直接从本地（或 COS 下载）读 `audio.wav` 喂 Whisper
- `visual_kb_extract` / `audio_kb_extract` / `merge_kb` → **不变**

**Rationale**:
- DAG 拓扑已稳定，无 topology 变更需求
- 算法逻辑（action_segmenter / action_classifier / tech_extractor / LLM 抽取）均保持不变，零精度回归
- 只改"如何喂数据"层，改动范围可控

**Alternatives considered**:
- ❌ 新增 `preprocessing_check` step 作为 DAG 第 1 步：引入 DAG 定义改动，orchestrator / retry_policy 需同步改，范围膨胀
- ❌ 拆 pose_analysis 为 N 个 per-segment step：DAG 动态生成复杂度高，违反静态 DAG 原则

---

## R12 — 本地磁盘峰值占用估算

**Decision**: 按以下公式约束，验证不撞 pod 磁盘：

```
单 job 峰值磁盘 = 原视频下载（最大 500 MB）
               + 转码中间文件（可能与原视频同量级，最大 500 MB）
               + 分段队列（max 3 段 × 30 MB = 90 MB）
               + 已上传等待清理（N 段 × 30 MB，典型 10 段 ≈ 300 MB）
               + audio.wav（90 min 16kHz mono ≈ 170 MB，典型 10 分钟 ≈ 19 MB）
             ≈ 1.5 GB（峰值）

并发 3 个 job  → 约 4.5 GB
24h TTL 累积    → 约 20 个预处理 job × 350 MB = 7 GB

总预算: < 15 GB 磁盘占用（pod 典型 30+ GB，安全）
```

**Rationale**:
- 原视频转码完立即删（ffmpeg 转码本身不需要保留），节省 500 MB
- 180s 标准化分段 720p 30fps ≈ 20–30 MB，可控
- 24h TTL 保守预估，按实际业务 QPS 调整

---

## R13 — `coach_video_classifications.preprocessed` 与 `kb_extracted` 并列

**Decision**: 在 `coach_video_classifications` 表新增列 `preprocessed: bool NOT NULL DEFAULT false`，与现有 `kb_extracted` 并列。**不合并**两列含义。

**Rationale**:
- `preprocessed=true` 语义："有 ≥ 1 条 video_preprocessing_jobs 记录 status='success'"
- `kb_extracted=true` 语义："ExtractionJob 产出过 ExpertTechPoints 并合入 KB"
- 合并两列会破坏 Feature-008 原有语义，下游查询 SQL 全部要改
- 两字段 + 两索引支持未来查询 "已预处理但未提取 KB 的视频"（常见运维报表）

**Alternatives considered**:
- ❌ 复用 `kb_extracted`：语义漂移，破坏向后兼容
- ❌ 新建状态枚举列：`status IN ('raw','preprocessed','kb_extracted')`，可能退化（KB 提取失败回到 preprocessed？）语义复杂，不如两个独立 bool

---

## R14 — Alembic 迁移 0014 设计

**Decision**: 生成单一迁移文件 `0014_video_preprocessing_pipeline.py`，包含：
1. 创建 `video_preprocessing_jobs` 表（含 partial unique index `(cos_object_key) WHERE status='success'`）
2. 创建 `video_preprocessing_segments` 表（外键到 jobs，(job_id, segment_index) 唯一）
3. 扩展 `coach_video_classifications` 新增 `preprocessed: bool NOT NULL DEFAULT false`（加 index `idx_cvclf_preprocessed`）
4. 扩展 `task_channel_configs` 插入 1 行 `channel_type='preprocessing', concurrency=3, queue_capacity=20`（种子数据）

**Rationale**:
- 单一迁移避免多步部署复杂度
- Partial unique index 实现 FR-007 幂等（`cos_object_key` 至多一条 success）
- 种子数据确保通道热更新接口开箱即用

**Alternatives considered**:
- ❌ 拆成 0014 / 0015 / 0016 多个迁移：部署步骤变多，收益小
- ❌ 不种子 `preprocessing` 通道配置：API 首次调用会缺 row，需要运维手工 INSERT

---

## R15 — 契约测试（tests/contract/）边界

**Decision**: 仅为 3 个新 API 写契约测试（submit / batch submit / get job），**不**为 KB 提取 API 写新契约测试（API 未变化，只是内部 executor 改）。

**Rationale**:
- 契约层测试的职责是验证 API 请求/响应 schema 稳定
- KB 提取 API（`POST /tasks/kb-extraction` / `GET /extraction-jobs/{id}`）在本 Feature 中 schema 零改动
- 内部 executor 行为变化由 integration 测试覆盖

**Alternatives considered**:
- ❌ 为 KB extraction 重写契约测试：重复劳动，零新增价值

---

## 总结矩阵（Clarifications → Research）

| Spec Clarify Q | 本文档研究编号 | 状态 |
|----------------|----------------|------|
| Q1 Whisper OOM | R1 | ✅ 已解决 |
| Q2 force=true 处置 | R4 | ✅ 已解决 |
| Q3 上传并发模型 | R3 | ✅ 已解决 |
| Q4 COS 缺失检测 | R5 | ✅ 已解决 |
| Q5 质量预检前移 | R6 | ✅ 已解决 |
| Q6 本地温缓存 | R7 | ✅ 已解决 |
| Q7 清理与读取竞争 | R8 | ✅ 已解决 |
| Q8 TTL 精确值 | R9 | ✅ 已解决 |

**剩余 NEEDS CLARIFICATION**: **无**。阶段 0 完成，可进入阶段 1 设计。
