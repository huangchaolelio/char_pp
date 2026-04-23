# 数据模型: 音频增强型教练视频技术知识库提取

**分支**: `002-audio-enhanced-kb-extraction` | **日期**: 2026-04-19

## 新增实体

### 1. AudioTranscript（音频转录结果）

每次专家视频分析任务产生一条音频转录记录（或零条，若音频不可用）。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK, NOT NULL | 主键 |
| `task_id` | UUID | FK → analysis_tasks.id, NOT NULL | 关联分析任务 |
| `language` | VARCHAR(10) | NOT NULL, default "zh" | 识别语言（zh/en/unknown） |
| `model_version` | VARCHAR(50) | NOT NULL | Whisper 模型版本（如 "whisper-small-20231117"） |
| `total_duration_s` | FLOAT | NOT NULL | 音频总时长（秒） |
| `snr_db` | FLOAT | NULLABLE | 信噪比估算值（dB），低于阈值时标注质量不足 |
| `quality_flag` | VARCHAR(20) | NOT NULL, default "ok" | ok / low_snr / unsupported_language / silent |
| `fallback_reason` | TEXT | NULLABLE | 回退到纯视觉模式的原因描述 |
| `sentences` | JSONB | NOT NULL | 句子列表，每条含 start_ms, end_ms, text, confidence |
| `created_at` | TIMESTAMP | NOT NULL, default now() | 创建时间 |

**状态转换**: 无（只读记录）

**索引**: `task_id`（外键查询）、`quality_flag`（过滤可用转录）

---

### 2. TechSemanticSegment（技术语义片段）

从转录文本中识别出的含技术指导意义的片段，包含关键词命中位置和解析出的技术要点。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK, NOT NULL | 主键 |
| `transcript_id` | UUID | FK → audio_transcripts.id, NOT NULL | 关联转录记录 |
| `task_id` | UUID | FK → analysis_tasks.id, NOT NULL | 冗余字段，便于直接查询 |
| `start_ms` | INTEGER | NOT NULL | 片段开始时间（毫秒） |
| `end_ms` | INTEGER | NOT NULL | 片段结束时间（毫秒） |
| `priority_window_start_ms` | INTEGER | NOT NULL | 高优先级分析窗口开始（关键词时间 - 3s） |
| `priority_window_end_ms` | INTEGER | NOT NULL | 高优先级分析窗口结束（关键词时间 + 3s） |
| `trigger_keyword` | VARCHAR(50) | NOT NULL | 触发该片段的关键词（如"示范"） |
| `source_sentence` | TEXT | NOT NULL | 原始句子文本 |
| `dimension` | VARCHAR(50) | NULLABLE | 解析出的技术维度（如"elbow_angle"，未解析时为 NULL） |
| `param_min` | FLOAT | NULLABLE | 解析出的参数最小值（若文本含数值区间） |
| `param_max` | FLOAT | NULLABLE | 解析出的参数最大值 |
| `param_ideal` | FLOAT | NULLABLE | 解析出的参数理想值（区间中值） |
| `unit` | VARCHAR(20) | NULLABLE | 参数单位（如"°"、"cm"） |
| `parse_confidence` | FLOAT | NOT NULL, default 0.5 | 解析置信度（0-1），纯关键词命中无数值时为 0.3 |
| `created_at` | TIMESTAMP | NOT NULL, default now() | 创建时间 |

**索引**: `task_id`、`transcript_id`、`(start_ms, end_ms)`（时间区间查询）

---

## 修改的现有实体

### 3. ExpertTechPoint（新增字段）

在现有 `expert_tech_points` 表追加以下字段（Alembic migration，向后兼容）：

| 新增字段 | 类型 | 约束 | 说明 |
|----------|------|------|------|
| `source_type` | VARCHAR(20) | NOT NULL, default "visual" | visual / audio / visual+audio |
| `transcript_segment_id` | UUID | FK → tech_semantic_segments.id, NULLABLE | 关联语音来源片段（视觉来源为 NULL） |
| `conflict_flag` | BOOLEAN | NOT NULL, default false | true = 与另一来源存在参数冲突，待管理员审核 |
| `conflict_detail` | JSONB | NULLABLE | 冲突详情：{source: visual/audio, value: X, diff_pct: Y} |

---

### 4. AnalysisTask（新增字段）

在 `analysis_tasks` 表追加进度追踪字段：

| 新增字段 | 类型 | 约束 | 说明 |
|----------|------|------|------|
| `total_segments` | INTEGER | NULLABLE | 长视频分段总数（短视频为 NULL） |
| `processed_segments` | INTEGER | NULLABLE, default 0 | 已完成处理的分段数 |
| `progress_pct` | FLOAT | NULLABLE | 处理进度百分比（0-100） |
| `audio_fallback_reason` | TEXT | NULLABLE | 音频回退原因（NULL = 音频正常使用） |

---

## 实体关系

```
analysis_tasks (1) ─── (0..1) audio_transcripts
audio_transcripts (1) ─── (0..N) tech_semantic_segments
analysis_tasks (1) ─── (0..N) tech_semantic_segments
expert_tech_points (0..N) ─── (0..1) tech_semantic_segments  [transcript_segment_id]
```

## 验证规则

- `audio_transcripts.quality_flag` 为 `ok` 时，`sentences` 数组不得为空
- `tech_semantic_segments.end_ms` MUST > `start_ms`
- `tech_semantic_segments.param_max` MUST >= `param_min`（若两者均不为 NULL）
- `expert_tech_points.source_type` = `"audio"` 时，`transcript_segment_id` MUST NOT NULL
- `expert_tech_points.conflict_flag` = true 时，`conflict_detail` MUST NOT NULL

## 状态转换（AnalysisTask 进度）

```
progress_pct: 0% → (每段完成后) N/total × 100% → 100%
更新触发点: 每个 5 分钟分段处理完成后（满足 SC-004 ≤ 30s 更新延迟）
```
