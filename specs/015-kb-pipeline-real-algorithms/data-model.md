# 数据模型: Feature-015 真实算法接入

**阶段**: 1 — 设计与契约
**日期**: 2026-04-25

## 实体概览

本 Feature **不新增任何 DB 表或列** —— schema 在 Feature-014 已完整定义（`extraction_jobs` / `pipeline_steps` / `kb_conflicts` + `analysis_tasks.extraction_job_id`）。本 Feature 关注的是：

1. **artifact 文件的 JSON 结构**（内部契约，无版本）
2. **`pipeline_steps.output_summary` JSONB 字段的真实算法键**（与 Feature-014 scaffold 版本的差异）
3. **`kb_items` dict 列表格式**（跨 executor 传递到 merger 的契约，已由 Feature-014 merger.py 定义，本文档仅记录"真实算法产出的实际字段范围"）

---

## Artifact 文件格式

### 1. `pose.json`（pose_analysis executor 产出）

由 `pose_analysis.py` 写到 `<extraction_artifact_root>/<job_id>/pose.json`，供 `visual_kb_extract` 消费。

```jsonc
{
  "video_path": "/tmp/coaching-advisor/jobs/<job_id>/video.mp4",
  "video_meta": {
    "fps": 30.0,
    "width": 1920,
    "height": 1080,
    "duration_seconds": 10.5,
    "frame_count": 315
  },
  "backend": "yolov8",              // "yolov8" | "mediapipe" | "unknown"
  "frames": [
    {
      "timestamp_ms": 0,
      "frame_confidence": 0.92,
      "keypoints": {
        "0":  {"x": 0.5, "y": 0.3, "visibility": 0.95},   // NOSE
        "11": {"x": 0.48, "y": 0.45, "visibility": 0.91}, // LEFT_SHOULDER
        "12": {"x": 0.52, "y": 0.45, "visibility": 0.93}, // RIGHT_SHOULDER
        ...
      }
    }
    // ... 一帧一个对象
  ]
}
```

**下游容错解析规则**（Q4 决策）:
- 缺 `video_meta` → 用 `{}`；缺 `frames` → 用 `[]`；
- 缺某一帧的 `keypoints` → 跳过该帧（tech_extractor 已容错）；
- 缺 `backend` → 当 "unknown"；
- 未知顶层键 → ignore，不报错。

### 2. `transcript.json`（audio_transcription executor 产出）

由 `audio_transcription.py` 写到 `<extraction_artifact_root>/<job_id>/transcript.json`，供 `audio_kb_extract` 消费。

```jsonc
{
  "video_path": "/tmp/coaching-advisor/jobs/<job_id>/video.mp4",
  "audio_path": "/tmp/coaching-advisor/jobs/<job_id>/audio.wav",
  "language": "zh",
  "model_version": "whisper-small-20231117",
  "total_duration_s": 600.5,
  "snr_db": 12.3,
  "quality_flag": "good",          // "good" | "low_snr" | "unusable"
  "fallback_reason": null,         // null | "no_audio_track" | "silence_below_snr"
  "sentences": [
    {
      "start": 0.0,
      "end": 3.2,
      "text": "拉球的时候肘部保持 90 到 120 度",
      "confidence": 0.89
    }
  ]
}
```

**下游容错解析规则**:
- 缺 `sentences` → 用 `[]`（downstream 生成空 kb_items）；
- `quality_flag="unusable"` → downstream 仍尝试但预期空结果；
- 未知顶层键 → ignore。

### 3. `audio.wav`（audio_transcription 中间产物）

ffmpeg 从 mp4 提取的 16kHz 单声道 WAV 文件。路径记录在 `transcript.json::audio_path`。保留期随 Feature-014 中间结果保留窗口（success 24h / failed 7d）。

---

## `pipeline_steps.output_summary` 真实算法键

每个 step 的 `output_summary` JSONB 必须暴露**真实算法后端信息**（FR-014）：

### `pose_analysis.output_summary`
```json
{
  "keypoints_frame_count": 315,
  "detected_segments": 3,           // classified segments; 0 如果没有动作
  "backend": "yolov8",
  "video_duration_sec": 10.5,
  "fps": 30.0,
  "resolution": "1920x1080"
}
```
与 Feature-014 scaffold 的差异：`backend` 不再是 `"scaffold"`，`keypoints_frame_count > 0`。

### `audio_transcription.output_summary`
```json
{
  "whisper_model": "small",
  "language_detected": "zh",
  "transcript_chars": 2400,
  "sentences_count": 24,
  "snr_db": 12.3,
  "quality_flag": "good",
  "skipped": false,
  "skip_reason": null
}
```
或 skipped 场景：
```json
{
  "skipped": true,
  "skip_reason": "disabled_by_request",   // 或 "no_audio_track"
  "whisper_model": null
}
```

### `visual_kb_extract.output_summary`
```json
{
  "kb_items": [ /* 见下方 kb item dict 格式 */ ],
  "kb_items_count": 8,
  "source_type": "visual",
  "tech_category": "forehand_topspin",
  "backend": "action_segmenter+tech_extractor",
  "segments_processed": 3,
  "segments_skipped_low_confidence": 1
}
```

### `audio_kb_extract.output_summary`
```json
{
  "kb_items": [ /* 见下方 */ ],
  "kb_items_count": 5,
  "source_type": "audio",
  "llm_model": "gpt-4o-mini",          // 或 "venus-xxx"
  "llm_backend": "openai",             // "venus" | "openai"
  "parsed_segments_total": 7,
  "dropped_low_confidence": 2,
  "dropped_reference_notes": 1
}
```

### `merge_kb.output_summary`（不变，Feature-014 已定义）
```json
{
  "merged_items": 10,
  "inserted_tech_points": 10,
  "conflict_items": 3,
  "degraded_mode": false,
  "kb_version": "0.12345.67890",
  "kb_extracted_flag_set": true
}
```

---

## `kb_items` dict 格式（executor → merger 契约）

**已由 Feature-014 merger.py 定义**，本 Feature 只记录**真实算法产出时各字段的实际取值范围**。

```jsonc
{
  "dimension": "elbow_angle",          // "elbow_angle" | "swing_trajectory" | "contact_timing" | "weight_transfer"
  "param_min": 90.0,
  "param_max": 120.0,
  "param_ideal": 105.0,
  "unit": "°",                          // "°" | "ratio" | "ms"
  "extraction_confidence": 0.89,       // [0.0, 1.0]，tech_extractor 过滤 <0.7；音频路过滤 <0.5
  "action_type": "forehand_topspin",   // ActionType enum 的 value
  "source_type": "visual",             // executor 设置：visual executor = "visual"; audio executor = "audio"
  
  // 仅 audio 路 （Q5 小修补新增字段，F14KbMerger 当前会 ignore 额外字段，符合 FR-002/FR-007 容错规则）
  "raw_text_span": "拉球的时候肘部保持 90 到 120 度"
}
```

**约束**（继承 Feature-002 ExpertTechPoint 表约束）:
- `param_min ≤ param_ideal ≤ param_max`
- `0.0 ≤ extraction_confidence ≤ 1.0`
- `dimension` ∈ `{elbow_angle, swing_trajectory, contact_timing, weight_transfer}`（tech_extractor 目前产出这 4 个）
- `unit` 与 dimension 一一对应（elbow_angle → °、swing_trajectory → ratio、contact_timing → ms、weight_transfer → ratio）
- `action_type` 能映射到 ActionType enum（否则被 `merge_kb._coerce_action_type` 丢弃 + 日志告警）

---

## 错误码约定（FR-016）

`pipeline_steps.error_message` 的前缀约定（非字段，字符串开头约定）：

| 前缀 | step_type | 含义 |
|------|-----------|------|
| `VIDEO_QUALITY_REJECTED:` | pose_analysis | 视频 fps/分辨率不达标；不重试 |
| `POSE_NO_KEYPOINTS:` | pose_analysis | estimate_pose 返回空；不重试 |
| `WHISPER_LOAD_FAILED:` | audio_transcription | 模型加载或下载失败；tenacity I/O 重试 |
| `WHISPER_NO_AUDIO:` | audio_transcription | 返回 `skipped + skip_reason="no_audio_track"`（非 failed） |
| `ACTION_CLASSIFY_FAILED:` | visual_kb_extract | 无分段 / 无分类；不重试 |
| `LLM_UNCONFIGURED:` | audio_kb_extract | Venus + OpenAI 均未配置；不重试 |
| `LLM_JSON_PARSE:` | audio_kb_extract | LLM 返回非法 JSON；不重试 |
| `LLM_CALL_FAILED:` | audio_kb_extract | 网络/5xx；tenacity I/O 重试 |

前缀格式：`<CODE>: <human-readable details>`。例如：
- `VIDEO_QUALITY_REJECTED: fps=12 vs 15`
- `LLM_JSON_PARSE: expected 'dimension' key in LLM output`

---

## 参考视频集 manifest（US3）

`specs/015-kb-pipeline-real-algorithms/reference_videos.json`：

```jsonc
{
  "videos": [
    {
      "name": "forehand_topspin_sample_1",
      "cos_object_key": "fixtures/f015/forehand_topspin_1.mp4",
      "tech_category": "forehand_topspin",
      "expected_items_min": 3,
      "expected_items_max": 25,
      "has_speech": true,
      "notes": "清晰讲解，30fps 1080p，15 秒"
    }
  ]
}
```

字段约束:
- `expected_items_min ≥ 1`
- `expected_items_max ≥ expected_items_min`
- `has_speech: bool` 控制 SC-006 分母统计（有讲解的视频才算在音频路成功率分母里）

---

## 数据关系图

```
coach_video_classifications (Feature-008)
    │ cos_object_key ───────────────────────────┐
    │                                           │
    ▼                                           ▼
analysis_tasks (Feature-013)              extraction_jobs (Feature-014)
    │ extraction_job_id FK ─────────────────────│
    │                                           │
    ▼                                           ▼
pipeline_steps (Feature-014, 6/job)         kb_conflicts (Feature-014)
    │ output_summary JSONB                      ▲
    │ ├── Feature-014: scaffold note            │ 冲突写入（merge_kb）
    │ └── Feature-015: 真实 backend/model 字段  │
    │                                           │
    ▼ output_artifact_path                      │
本地 FS:                                         │
  jobs/<job_id>/                                │
    ├── video.mp4 (download_video)              │
    ├── pose.json (pose_analysis) ◄── Feature-015 结构                              
    ├── audio.wav (audio_transcription)         │
    └── transcript.json (audio_transcription) ◄── Feature-015 结构
                                                │
                                                │
expert_tech_points (Feature-002) ◄──────────────┘
    │ 由 merge_kb 批量插入，source_type 按 kb item 填
    │ source_video_id 指回 analysis_tasks
```

---

## 数据量估算

| 产物 | 典型大小 | 保留期 |
|------|---------|--------|
| pose.json（10 分钟视频） | 5–20 MB（~18000 帧 × ~1KB/帧） | 作业 success +24h / failed +7d |
| transcript.json（10 分钟视频） | 50–200 KB | 同上 |
| audio.wav（10 分钟视频 16kHz 单声道） | ~19 MB | 同上 |
| 新增 ExpertTechPoint / 作业 | 5–30 行 | 永久（KB 主数据） |
| 新增 KbConflict / 作业 | 0–10 行 | 永久（审核数据） |

**峰值磁盘**: 2 个并行作业 × 45MB ≈ 90MB（kb_extraction 通道 concurrency=2），完全可控。
