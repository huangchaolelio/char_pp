# 研究: Feature-015 真实算法接入

**阶段**: 0 — 大纲与研究
**日期**: 2026-04-25

## 研究目标

spec.md 已通过 clarify（5 Clarifications 全部解决），plan.md 无 NEEDS CLARIFICATION。本研究聚焦 **Feature-002 既有模块接入点确认**，为阶段 1 设计提供具体函数签名和契约。

---

## R1. `pose_analysis` 接入点

### Decision

调用链：`validate_video(local_path) → estimate_pose(local_path) → 序列化为 pose.json`

**函数签名**（已存在，零改动）:
```python
# src/services/video_validator.py
def validate_video(video_path: Path) -> VideoMeta:
    # 阈值来自 settings.min_video_fps / min_video_width / min_video_height
    # 违规抛 VideoQualityRejected(reason, details={"field":"fps","actual":12,"threshold":15})

# src/services/pose_estimator.py
def estimate_pose(video_path: Path) -> list[FramePoseResult]:
    # FramePoseResult = {timestamp_ms, keypoints: {int: Keypoint}, ...}
    # 后端由 settings.pose_backend 决定 (auto / yolov8 / mediapipe)
```

### Rationale

- `validate_video` 已含所有视频质量规则（fps/width/height 阈值从 settings 读），直接复用即可满足 FR-006；`VideoQualityRejected.reason` 可用来组装 `VIDEO_QUALITY_REJECTED:` 前缀
- `estimate_pose` 已处理后端选择 + fallback；executor 只需调用 + 序列化
- 33 关键点格式是 MediaPipe 标准；YOLOv8 的 `_estimate_pose_yolov8` 已在函数内部做了格式对齐

### Alternatives Considered

| 方案 | 拒绝原因 |
|------|---------|
| executor 自己实现质量预检 | 重复 `video_validator` 工作；阈值不一致风险 |
| 直接把 `list[FramePoseResult]` 通过 session 对象传给下游 | 违反 Feature-014 FR-002（要求 artifact 文件） |
| 使用 protobuf / msgpack 序列化 | JSON 足够；工具兼容性更好 |

### Artifact 序列化格式（pose.json）

Spec Q4 决策：无 schema 版本、容错解析。格式为列表 of frame dict：
```json
{
  "video_path": "/tmp/.../video.mp4",
  "video_meta": {"fps": 30, "width": 1920, "height": 1080, "duration_sec": 10.5, "frame_count": 315},
  "backend": "yolov8",
  "frames": [
    {
      "timestamp_ms": 0,
      "keypoints": {"0": {"x": 0.5, "y": 0.3, "visibility": 0.95}, "11": {...}, ...},
      "frame_confidence": 0.92
    },
    ...
  ]
}
```

下游容错：缺 `video_meta` → 用默认；缺 `backend` → 当 "unknown"；缺某一帧的 keypoint → `tech_extractor` 的 `_angle_at_elbow` 本身已容错（返回 None）。

---

## R2. `audio_transcription` 接入点

### Decision

调用链：`SpeechRecognizer(model_name=settings.whisper_model, device=settings.whisper_device).recognize(audio_path, language=job.audio_language) → 序列化为 transcript.json`

但 `SpeechRecognizer.recognize(wav_path)` 要求 WAV 格式，而下载的是 mp4；需要先用 ffmpeg 提取音频。**Feature-002 已有 `audio_extractor.py`**：

```python
# src/services/audio_extractor.py
def extract_audio_from_video(video_path: Path, output_wav: Path) -> AudioMeta:
    # ffmpeg-python 一次调用
```

### Rationale

- ffmpeg 提取是必须的（Whisper 不直接吃 mp4）
- 无音频 → `audio_extractor` 抛明确异常或返回静音 meta → executor 翻译为 `skipped + skip_reason='no_audio_track'`
- `SpeechRecognizer` 已处理模型懒加载、SNR 计算、quality_flag

### Artifact 序列化格式（transcript.json）

复用 Feature-002 `TranscriptResult` 的字段 + `.model_dump()` 风格：
```json
{
  "video_path": "/tmp/.../video.mp4",
  "audio_path": "/tmp/.../audio.wav",
  "language": "zh",
  "model_version": "whisper-small-20231117",
  "total_duration_s": 600.5,
  "snr_db": 12.3,
  "quality_flag": "good",
  "fallback_reason": null,
  "sentences": [
    {"start": 0.0, "end": 3.2, "text": "拉球的时候肘部保持 90 到 120 度", "confidence": 0.89},
    ...
  ]
}
```

### Alternatives Considered

| 方案 | 拒绝原因 |
|------|---------|
| 把 mp4 直传 Whisper（最新版本支持） | 本仓库锁定 `openai-whisper==20231117`，该版本要求 WAV |
| 不做 ffmpeg 提取，直接 skip | 音频路无法工作 |
| 把音频提取作为 Feature-014 独立 step | 超出本 Feature 范围，增加 DAG 复杂度 |

---

## R3. `visual_kb_extract` 接入点

### Decision

调用链：`读 pose.json → 反序列化为 FramePoseResult → segment_actions → classify_segment(每段) → extract_tech_points(每段) → 转换为 KB item dict 列表`

**函数签名**（零改动）:
```python
# src/services/action_segmenter.py
def segment_actions(frames: list[FramePoseResult]) -> list[ActionSegment]:
    # 基于手腕速度峰值分段

# src/services/action_classifier.py
def classify_segment(segment: ActionSegment, frames: list[FramePoseResult], 
                     action_type_hint: Optional[str] = None) -> ClassifiedSegment:
    # 返回 action_type (forehand_topspin / backhand_push / ...) + confidence

# src/services/tech_extractor.py
def extract_tech_points(classified: ClassifiedSegment,
                        all_frames: list[FramePoseResult],
                        confidence_threshold: float = 0.7) -> ExtractionResult:
    # 4 维度: elbow_angle / swing_trajectory / contact_timing / weight_transfer
    # 置信度 < 0.7 的维度自动丢弃
```

### Rationale

- 算法链路已由 Feature-002 验证过；executor 只是 **orchestration**
- `action_type_hint` 可用 `job.tech_category`（已分类），避免分类歧义
- 各段的 `ExtractionResult.dimensions` 合并成 `kb_items` 列表

### 转换映射

```python
for classified in classified_segments:
    result = extract_tech_points(classified, all_frames)
    for dim in result.dimensions:
        kb_items.append({
            "dimension": dim.dimension,
            "param_min": dim.param_min,
            "param_max": dim.param_max,
            "param_ideal": dim.param_ideal,
            "unit": dim.unit,
            "extraction_confidence": dim.extraction_confidence,
            "action_type": result.action_type,  # 或 job.tech_category 作 fallback
            "source_type": "visual",
        })
```

### 同 dimension 多次出现

一个视频可能有多段挥拍（多个 ActionSegment），同 `elbow_angle` 维度可能在多段都产出。`F14KbMerger` 当前 `merge()` 的输入是"扁平 list of dict"，用 `{dim: item}` 索引 → 后来者覆盖先行者。

**决策**: 保持覆盖行为。若未来需要聚合（取均值、取最大置信度），在 merger 层引入，本 Feature 不改。

### Alternatives Considered

| 方案 | 拒绝原因 |
|------|---------|
| 先聚合同 dimension 的多段再入 merger | 改 merger 范围超出本 Feature |
| executor 内部做 dimension 去重（取最高置信度） | 现有覆盖行为可用；YAGNI |

---

## R4. `audio_kb_extract` 接入点

### Decision

调用链：`读 transcript.json → TranscriptTechParser.parse(sentences) → TechSemanticSegment 列表 → 转换为 KB item dict 列表`

**函数签名**:
```python
# src/services/transcript_tech_parser.py
class TranscriptTechParser:
    def __init__(self, llm_client: LlmClient): ...
    def parse(self, sentences: list[dict]) -> list[TechSemanticSegment]:
        # sentences: [{start, end, text, confidence}]
        # 内部调 LLM_client.chat 做 JSON 结构化抽取
```

**LlmClient 初始化**（Q5 决策 — 完全复用现有配置）：
```python
from src.config import get_settings
s = get_settings()
if not (s.venus_token and s.venus_base_url) and not s.openai_api_key:
    raise RuntimeError("LLM_UNCONFIGURED: no VENUS_TOKEN and no OPENAI_API_KEY")
llm = LlmClient(
    venus_token=s.venus_token, venus_base_url=s.venus_base_url, venus_model=s.venus_model,
    openai_api_key=s.openai_api_key, openai_base_url=s.openai_base_url,
    openai_model=s.openai_model, timeout_s=s.openai_timeout_s,
)
```

### TechSemanticSegment → KB item dict 转换

`TechSemanticSegment` 字段（Feature-002 已有）包含 `dimension`、`param_min/max/ideal`、`unit`、`parse_confidence`、`is_reference_note`、`text_span`。

```python
for seg in parsed_segments:
    if seg.is_reference_note or seg.dimension is None:
        continue  # reference notes 不入 KB
    if seg.parse_confidence < 0.5:
        continue  # 低置信度音频条目丢弃（与 F14KbMerger 的 audio_min_confidence 一致）
    kb_items.append({
        "dimension": seg.dimension,
        "param_min": seg.param_min,
        "param_max": seg.param_max,
        "param_ideal": seg.param_ideal,
        "unit": seg.unit or "",
        "extraction_confidence": seg.parse_confidence,
        "action_type": job.tech_category,
        "source_type": "audio",
        "raw_text_span": seg.text_span,  # Q5 小修补：新增字段
    })
```

### LLM 配置缺失处理（FR-011）

初始化 `LlmClient` 时若配置缺失，executor 立即抛 `RuntimeError("LLM_UNCONFIGURED: ...")`。`F14KbMerger` 的 CPU step 分类未改（`audio_kb_extract` 仍是 I/O 类，有 tenacity 重试）——但 `LLM_UNCONFIGURED` 应该**不重试**（配置问题非瞬态）。

**决策**: 继续让 `audio_kb_extract` 走 I/O 重试路径（保持 Feature-014 retry_policy 一致性），但 `LlmClient.__init__` 抛的 `ValueError` 不在 `RETRIABLE_EXCEPTIONS` 中，tenacity 会立即放弃——即自然 fail fast。

### Alternatives Considered

| 方案 | 拒绝原因 |
|------|---------|
| executor 直接调 `LlmClient.chat`，重写 prompt | 违反 Q5 决策 |
| 保留 TechSemanticSegment 到 DB，merger 读 DB | 违反 "artifact + kb_items dict" 契约 |
| 把 LLM 调用做到 `TranscriptTechParser` 外部 | 放弃 Feature-002 现有实现优势 |

---

## R5. `merge_kb` 的 action_type 处理

### Decision

现状：`merge_kb.py::_coerce_action_type(raw, fallback)` 把字符串 action_type 映射到 `ActionType` 枚举，映射不到则丢弃并记日志。

本 Feature 不改 merge_kb —— Feature-014 的冲突分离 + 通用 action_type 转换已经足够覆盖真实算法产出的 11 个 ActionType 枚举值。

### Rationale

- 视觉路的 `action_type` 来自 `classify_segment` 输出（与 ActionType 枚举保持一致）
- 音频路的 `action_type` fallback 到 `job.tech_category`（已由 classifications 确认）
- 两路的 action_type 不一致时（罕见）：merger 已按 dimension 粒度合并，action_type 继承 visual 一侧（`merge._merge_both` 现有行为）

### Alternatives Considered

无 —— Feature-014 merger 逻辑本身没有被证明有缺陷，不触碰。

---

## R6. 参考视频集管理（US3）

### Decision

参考视频集以 **manifest JSON** 方式描述；视频文件本身不入 git（过大），fixture 路径由运维配置。

Manifest 格式（`specs/015-kb-pipeline-real-algorithms/reference_videos.json`）:
```json
{
  "videos": [
    {
      "name": "forehand_topspin_sample_1",
      "cos_object_key": "fixtures/f015/forehand_topspin_1.mp4",
      "tech_category": "forehand_topspin",
      "expected_items_min": 3,
      "expected_items_max": 25,
      "has_speech": true,
      "notes": "清晰讲解，30fps 1080p，15s"
    },
    ...
  ]
}
```

回归脚本（`scripts/run_reference_regression.py`）读 manifest → 对每个视频提交作业 → 等待 success → 检查条目数 ∈ [min, max] → 汇总到 verification.md。

### Rationale

- manifest 与代码分离，运维可维护样本集
- 绝对值范围替代 F-002 对比（Q1 决策）
- 批次口径（N=10）的回归测试直接驱动 SC-005/SC-006（Q2 决策）

### Alternatives Considered

| 方案 | 拒绝原因 |
|------|---------|
| 把视频文件放 `tests/fixtures/` | mp4 太大污染仓库 |
| 用 git LFS | 依赖运行环境 LFS 配置；增加 CI 复杂度 |
| 只提供脚本，视频路径每次命令行输入 | 不可重复；运维心智负担高 |

---

## R7. 端到端测试视频 fixture

### Decision

**US1 / US2 的 `test_visual_kb_real.py` / `test_audio_kb_real.py` 不依赖真实 mp4**：用 **合成的 pose.json 和 transcript.json** 作为上游 step 的 artifact 输出，skip pose_analysis/audio_transcription 的真实执行，只验证 `visual_kb_extract` / `audio_kb_extract` 的接线正确性。

US3 的参考视频集回归才走真实完整链路（需要部署环境 + fixture 视频）。

### Rationale

- 单元 + 集成测试必须可在 CI 跑（无 GPU、无 Whisper 模型、无 LLM）
- pose.json / transcript.json 可人工构造（小体积、结构清晰）
- US3 的真实端到端验证属于**部署验证阶段**，不要求 CI 通过

### Alternatives Considered

| 方案 | 拒绝原因 |
|------|---------|
| CI 里跑真实 Whisper | 模型下载 500MB + CPU 推理慢 |
| CI 里跑真实 LLM 调用 | 网络依赖 + API 成本 |
| 用 pytest-skip 动态跳过真实测试 | 复杂；不如 fixture artifact 干净 |

---

## R8. 错误码规范（FR-016）

### Decision

executor 内部失败时用**结构化 error_message 前缀**：

| 前缀 | 触发点 | 重试策略 |
|------|--------|---------|
| `VIDEO_QUALITY_REJECTED:` | pose_analysis 预检失败 | 不重试（视频质量非瞬态，Q3 决策） |
| `WHISPER_LOAD_FAILED:` | Whisper 模型加载异常 | I/O 步骤 tenacity 重试 3 次 × 30s |
| `WHISPER_NO_AUDIO:` | 音频轨道为空 | executor 返回 `skipped`（非 failed） |
| `POSE_NO_KEYPOINTS:` | estimate_pose 返回空列表 | 不重试（CPU 步骤） |
| `ACTION_CLASSIFY_FAILED:` | 无 ClassifiedSegment 产出 | 不重试 |
| `LLM_UNCONFIGURED:` | 无 Venus 也无 OpenAI 配置 | 不重试（配置问题非瞬态） |
| `LLM_JSON_PARSE:` | LLM 返回非法 JSON | 不重试（输出格式问题） |
| `LLM_CALL_FAILED:` | 网络 / 5xx | I/O 步骤 tenacity 重试 |

### Rationale

- 前缀便于运维脚本 grep 分类故障
- 错误码 → 重试策略的映射清晰
- 复用 Feature-014 retry_policy 的 `RETRIABLE_EXCEPTIONS`（ConnectionError / TimeoutError / OSError），其它异常类型自然不重试

### Alternatives Considered

| 方案 | 拒绝原因 |
|------|---------|
| 新增 error_code 列到 pipeline_steps | 违反 YAGNI；前缀约定足够 |
| 枚举型 Python 异常类 | 复杂；grep 前缀就够用 |

---

## 汇总

| 决策 | 文档节点 |
|------|---------|
| 调用 F-002 `validate_video` + `estimate_pose` 串联 pose_analysis | R1 |
| ffmpeg 提取音频 → SpeechRecognizer → 序列化 | R2 |
| action_segmenter + classify_segment + extract_tech_points 串联 visual | R3 |
| TranscriptTechParser（复用 prompt，追加 raw_text_span） | R4 |
| merge_kb 不改，只消费更多真实 kb_items | R5 |
| 参考视频集 manifest JSON 管理 | R6 |
| 单元/集成测试用合成 artifact；US3 部署阶段走真实链路 | R7 |
| 结构化错误码前缀表（8 个） | R8 |

**所有决策均无 NEEDS CLARIFICATION。阶段 1 设计可以开始。**
