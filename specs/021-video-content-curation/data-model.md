# 阶段 1 · 数据模型设计 — Feature-021

**分支**: `021-video-content-curation`
**日期**: 2026-05-18
**输入**: [research.md](./research.md) + [spec.md](./spec.md)

---

## 1. 总览

新增 2 张表 + 扩展 2 张已有表 + 1 个 ENUM 值 + 1 份运行时配置文件。所有变更经迁移 `0020_video_content_curation`：

| 项 | 类型 | 说明 |
|---|------|------|
| `video_curation_jobs` | 新表 | 作业级摘要 |
| `video_curation_segment_results` | 新表 | 逐分段判定 + 覆盖留痕 |
| `coach_video_classifications` | 扩展 | 增 `last_curation_job_id` (FK, NULL) + `low_quality` (BOOL, NULL) + `kb_stale_after_override` (BOOL, NOT NULL DEFAULT FALSE) |
| `analysis_tasks.task_type` ENUM | 扩展 | 新增枚举值 `video_curation` |
| `extraction_jobs.output_summary` | 复用 | 新增可选 JSON 字段 `curation_warning` ∈ {`null`, `"low_quality"`}（不改表结构，JSONB 软扩展）|
| `src/config/curation_rubric/v1.yaml` | 配置 | 初版规范文件 |
| `src/config/curation_rubric/schema.json` | 配置 | 规范文件 jsonschema |

---

## 2. 表结构详细设计

### 2.1 `video_curation_jobs`

记录"对一条视频的一次清洗作业"。一条视频可能有多次（force=true 重跑），通过 `cos_object_key + created_at` 时序对齐。

```sql
CREATE TABLE video_curation_jobs (
    id                          BIGSERIAL PRIMARY KEY,
    cos_object_key              VARCHAR(512) NOT NULL,
    coach_video_classification_id BIGINT NOT NULL
        REFERENCES coach_video_classifications(id) ON DELETE CASCADE,
    preprocessing_job_id        BIGINT NOT NULL
        REFERENCES video_preprocessing_jobs(id) ON DELETE RESTRICT,
    curation_rubric_version     VARCHAR(16) NOT NULL,         -- e.g. "v1"
    status                      VARCHAR(16) NOT NULL,         -- pending | running | success | failed
    error_code                  VARCHAR(64) NULL,
    error_message               TEXT NULL,

    -- 视频级摘要（success 时落，覆盖时事务内更新）
    total_segment_count         INTEGER NULL,
    accepted_segment_count      INTEGER NULL,
    rejected_segment_count      INTEGER NULL,
    uncertain_segment_count     INTEGER NULL,
    total_duration_seconds      DOUBLE PRECISION NULL,
    accepted_duration_seconds   DOUBLE PRECISION NULL,
    accepted_duration_ratio     DOUBLE PRECISION NULL,        -- accepted_duration / total_duration
    low_quality                 BOOLEAN NULL,                 -- accepted_duration_ratio < threshold (默认 0.3)
    audio_unavailable           BOOLEAN NULL,
    short_video                 BOOLEAN NULL,                 -- total_duration < threshold (默认 30s)

    -- 调度 / 审计
    submitted_at                TIMESTAMP NOT NULL DEFAULT NOW(),
    started_at                  TIMESTAMP NULL,
    completed_at                TIMESTAMP NULL,
    created_at                  TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMP NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_curation_status CHECK (status IN ('pending','running','success','failed')),
    CONSTRAINT chk_accepted_ratio CHECK (
        accepted_duration_ratio IS NULL OR (accepted_duration_ratio >= 0 AND accepted_duration_ratio <= 1)
    )
);

CREATE INDEX ix_curation_jobs_cos_object_key   ON video_curation_jobs (cos_object_key);
CREATE INDEX ix_curation_jobs_classification   ON video_curation_jobs (coach_video_classification_id);
CREATE INDEX ix_curation_jobs_status_submitted ON video_curation_jobs (status, submitted_at DESC);
```

**幂等性**：`POST /tasks/curation` 默认幂等 — 若该 `cos_object_key` 存在 `status=success` 的最新作业且 `force=false`，直接返回该作业 ID 不新建（spec FR-018）。`force=true` 时新建独立行；旧行 status 与字段不变（保留供 P3 版本对比）。

### 2.2 `video_curation_segment_results`

逐分段判定。与 `video_preprocessing_segments.segment_index` 一一对应。

```sql
CREATE TABLE video_curation_segment_results (
    id                          BIGSERIAL PRIMARY KEY,
    job_id                      BIGINT NOT NULL
        REFERENCES video_curation_jobs(id) ON DELETE CASCADE,
    segment_index               INTEGER NOT NULL,             -- 与 video_preprocessing_segments.segment_index 对齐
    segment_start_ms            INTEGER NOT NULL,
    segment_end_ms              INTEGER NOT NULL,

    -- 自动决策（不可变）
    auto_decision               VARCHAR(16) NOT NULL,         -- accepted | rejected | uncertain
    validity_score              DOUBLE PRECISION NOT NULL,    -- [0, 1]
    rejection_reason            VARCHAR(64) NULL,             -- 仅在非 accepted 时填
    decision_source             VARCHAR(16) NOT NULL,         -- rule | llm
    dim_breakdown               JSONB NULL,                   -- 5 维各自得分 + 命中关键词；审计用

    -- 人工覆盖（同行扩展，避免再开一张表）
    override_decision           VARCHAR(16) NULL,             -- accepted | rejected
    override_user               VARCHAR(64) NULL,
    override_reason             TEXT NULL,
    overridden_at               TIMESTAMP NULL,

    -- PostgreSQL 计算列（事实表口径，永远以 override 优先）
    effective_decision          VARCHAR(16) GENERATED ALWAYS AS (
        COALESCE(override_decision, auto_decision)
    ) STORED,

    created_at                  TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMP NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_auto_decision     CHECK (auto_decision     IN ('accepted','rejected','uncertain')),
    CONSTRAINT chk_override_decision CHECK (override_decision IS NULL OR override_decision IN ('accepted','rejected')),
    CONSTRAINT chk_decision_source   CHECK (decision_source   IN ('rule','llm')),
    CONSTRAINT chk_validity_score    CHECK (validity_score >= 0 AND validity_score <= 1),
    CONSTRAINT uq_curation_segment   UNIQUE (job_id, segment_index)
);

CREATE INDEX ix_curation_seg_job             ON video_curation_segment_results (job_id);
CREATE INDEX ix_curation_seg_effective       ON video_curation_segment_results (job_id, effective_decision);
CREATE INDEX ix_curation_seg_overridden_at   ON video_curation_segment_results (overridden_at)
    WHERE overridden_at IS NOT NULL;
```

**关键约束**：

- `effective_decision` 是 `STORED` 计算列 — 任何对 `override_decision` 的 UPDATE 自动同步；查询永远走计算列，杜绝应用层漏算。
- `(job_id, segment_index)` UNIQUE 防重；与 `video_preprocessing_segments.segment_index` 不建跨表 FK（spec FR-018 force 重跑场景下 segment_index 可能因预处理重切而不一致；强外键反而报错）。
- `dim_breakdown` 字段示例：

```json
{
  "tech_keyword": {"score": 0.85, "weight": 0.35, "matched": ["收小臂", "重心转移"]},
  "non_teaching": {"score": 1.0,  "weight": 0.25, "matched": []},
  "coach_dominance": {"score": 0.92, "weight": 0.20, "dominance_ratio": 0.78},
  "topic_relevance": {"score": 0.70, "weight": 0.15, "matched_keywords": ["弧圈"]},
  "duration_floor": {"score": 1.0, "weight": 0.05, "duration_seconds": 178}
}
```

### 2.3 `coach_video_classifications`（扩展 3 列）

```sql
ALTER TABLE coach_video_classifications
    ADD COLUMN last_curation_job_id    BIGINT NULL
        REFERENCES video_curation_jobs(id) ON DELETE SET NULL,
    ADD COLUMN low_quality             BOOLEAN NULL,
    ADD COLUMN kb_stale_after_override BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX ix_coach_class_last_curation ON coach_video_classifications (last_curation_job_id);
```

**字段语义**：

- `last_curation_job_id`：清洗 success 时由 service 层 UPDATE；force 重跑时指向新作业 ID
- `low_quality`：从 `video_curation_jobs.low_quality` 同步，加这一列是为了让运营列表查询不必跨表 JOIN
- `kb_stale_after_override`：service 层在每次覆盖动作后维护 — 当 `extraction_jobs` 完成时间早于"该视频任何分段的 `overridden_at`"时设为 TRUE；当 `POST /extraction-jobs/{id}/rerun` 完成后自动清零

**只增不减**：本 feature 不修改 / 不删除 `coach_video_classifications` 任何既有列含义。

### 2.4 `analysis_tasks.task_type` ENUM 扩展

```sql
-- PostgreSQL ENUM 添加值（不可移除，向前兼容）
ALTER TYPE task_type_enum ADD VALUE IF NOT EXISTS 'video_curation';
```

`_phase_step_hook` 派生矩阵同步扩展（`src/models/_phase_step_hook.py`）：

```python
_PHASE_STEP_TASK_TYPE_MATRIX["video_curation"] = ("TRAINING", "curate_segments")
_PHASE_TASK_TYPES["TRAINING"].add("video_curation")
```

`tasks.py::_VALID_BUSINESS_STEPS` 白名单加 `"curate_segments"`。

### 2.5 `extraction_jobs.output_summary` 软扩展

不改表结构。在 KB 抽取作业的 `output_summary` JSONB 落入新键：

```json
{
  "kb_items_count": 12,
  "segments_processed": 8,        // 仅 effective_decision=accepted 的分段数（≤ 总分段数）
  "segments_skipped_by_curation": 4,
  "curation_job_id": 1234,
  "curation_rubric_version": "v1",
  "curation_warning": "low_quality"  // 只在 0 < accepted_duration_ratio < 0.3 时填，否则不存在
}
```

---

## 3. 配置文件 schema

### 3.1 `src/config/curation_rubric/schema.json`（jsonschema draft-07）

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["version", "thresholds", "rules", "llm_fallback"],
  "additionalProperties": false,
  "properties": {
    "version": {"type": "string", "pattern": "^v[0-9]+$"},
    "description": {"type": "string"},
    "thresholds": {
      "type": "object",
      "required": [
        "validity_score_accept",
        "validity_score_reject",
        "low_quality_ratio",
        "short_video_seconds",
        "min_segment_seconds"
      ],
      "additionalProperties": false,
      "properties": {
        "validity_score_accept": {"type": "number", "minimum": 0, "maximum": 1},
        "validity_score_reject": {"type": "number", "minimum": 0, "maximum": 1},
        "low_quality_ratio":     {"type": "number", "minimum": 0, "maximum": 1},
        "short_video_seconds":   {"type": "integer", "minimum": 1},
        "min_segment_seconds":   {"type": "integer", "minimum": 1}
      }
    },
    "rules": {
      "type": "object",
      "required": ["tech_keyword","non_teaching","coach_dominance","topic_relevance","duration_floor"],
      "additionalProperties": false,
      "properties": {
        "tech_keyword":     {"$ref": "#/$defs/rule_with_keywords_ref"},
        "non_teaching":     {"$ref": "#/$defs/rule_with_inline_keywords"},
        "coach_dominance":  {"$ref": "#/$defs/rule_coach_dominance"},
        "topic_relevance":  {"$ref": "#/$defs/rule_with_keywords_ref"},
        "duration_floor":   {"$ref": "#/$defs/rule_simple"}
      }
    },
    "llm_fallback": {
      "type": "object",
      "required": ["enabled","invoke_when_score_in","prompt_template","timeout_seconds","unavailable_decision"],
      "additionalProperties": false,
      "properties": {
        "enabled":              {"type": "boolean"},
        "invoke_when_score_in": {"type": "array", "minItems": 2, "maxItems": 2,
                                 "items": {"type": "number", "minimum": 0, "maximum": 1}},
        "prompt_template":      {"type": "string"},
        "timeout_seconds":      {"type": "integer", "minimum": 1, "maximum": 60},
        "unavailable_decision": {"type": "string", "enum": ["uncertain","rejected"]}
      }
    }
  },
  "$defs": {
    "rule_simple": {
      "type": "object",
      "required": ["enabled","weight"],
      "properties": {
        "enabled": {"type": "boolean"},
        "weight":  {"type": "number", "minimum": 0, "maximum": 1}
      }
    },
    "rule_with_keywords_ref": {
      "allOf": [
        {"$ref": "#/$defs/rule_simple"},
        {"properties": {"keywords_ref": {"type": "string"}}, "required": ["keywords_ref"]}
      ]
    },
    "rule_with_inline_keywords": {
      "allOf": [
        {"$ref": "#/$defs/rule_simple"},
        {"properties": {"keywords": {"type": "object"}}, "required": ["keywords"]}
      ]
    },
    "rule_coach_dominance": {
      "allOf": [
        {"$ref": "#/$defs/rule_simple"},
        {"properties": {"min_dominance_ratio": {"type": "number", "minimum": 0, "maximum": 1}},
         "required": ["min_dominance_ratio"]}
      ]
    }
  }
}
```

加载期校验：`src/services/curation/rubric_loader.py::load(version: str)` 第一步即跑 jsonschema 验证；任何失败 ⇒ raise `AppException(ErrorCode.RUBRIC_INVALID, details={"version": version, "error": ...})`。

---

## 4. 数据流（端到端）

```
[POST /tasks/curation]
  │
  ▼
submission_service: 校验前置（视频存在 + tech_category 已分类 + preprocessing_job 已 success）
  │  写入 analysis_tasks(task_type=video_curation, business_phase=TRAINING)
  ▼
Celery default 队列 → workers/curation_task.py::curate_video(job_id)
  │
  ▼
curation_service.run(job_id)
  │  status=running, started_at=NOW
  │
  ├─ rubric_loader.load(curation_rubric_version)         # YAML + schema 校验
  ├─ segment_text_provider.iter(preprocessing_job_id)    # 读 transcript.json + 视频分段 metadata
  │
  ├─ for-each segment:
  │     decision_engine.decide(segment, rubric, tech_category, coach_name)
  │       ├─ rule layer: 5 维加权得 validity_score
  │       │     score >= rubric.thresholds.validity_score_accept → return (accepted, "rule")
  │       │     score <= rubric.thresholds.validity_score_reject → return (rejected, "rule", reason)
  │       │     else: 进 LLM 路
  │       └─ llm layer: 走 llm_client（Venus → OpenAI fallback）+ timeout
  │             返回 (decision, score, reason, "llm") | 不可用 → (uncertain, score, "llm_unavailable", "llm")
  │     INSERT video_curation_segment_results
  │
  ├─ aggregate summary:
  │     accepted_duration_ratio / low_quality / audio_unavailable / short_video
  │     UPDATE video_curation_jobs SET status='success', completed_at=NOW(), summary fields
  │     UPDATE coach_video_classifications SET last_curation_job_id=..., low_quality=...
  │
  └─ status=success
```

**人工覆盖路径**：

```
[PATCH /curation-jobs/{id}/segments/{segment_index}]
  body: {"override_decision":"accepted","override_reason":"..."}
  │
  ▼
curation_service.override_segment(job_id, segment_index, decision, reason, user)
  │ tx start
  ├─ UPDATE video_curation_segment_results SET override_decision=..., override_user=..., overridden_at=NOW()
  ├─ aggregate summary（重新读子表）
  ├─ UPDATE video_curation_jobs SET <summary fields>, updated_at=NOW()
  ├─ UPDATE coach_video_classifications SET low_quality=...
  ├─ if exists extraction_jobs WHERE cos_object_key = ... AND completed_at < NOW():
  │     UPDATE coach_video_classifications SET kb_stale_after_override=TRUE
  │ tx commit
  └─ 返回最新视频级摘要
```

---

## 5. 与既有表的兼容性

| 表 | 关系 | 是否破坏性 |
|---|------|----------|
| `coach_video_classifications` | 扩展 3 列（含 1 个 NOT NULL DEFAULT FALSE） | 否（默认值兜底，旧行不影响） |
| `video_preprocessing_jobs` / `_segments` | FK 引用（RESTRICT） + segment_index 软关联 | 否（只读引用） |
| `analysis_tasks` | ENUM 扩展（向后兼容） | 否（已发布枚举值不改） |
| `extraction_jobs` | JSONB output_summary 软扩展 + 行为变化（前置门） | 是（行为破坏，需审计）— 见下方迁移说明 |
| `tech_knowledge_bases` / `tech_standards` | 不触 | 否 |
| `athlete_video_classifications` / `athletes` | 不触（Feature-020 严格隔离） | 否 |

**关于 KB 抽取行为破坏性变化**：

- 老视频（无 `video_curation_jobs.status=success` 行）将无法通过 `POST /tasks/kb-extraction` 提交，必须先跑清洗。
- **回填策略（plan 阶段决定，tasks 阶段实施）**：迁移完成后跑一次性回填脚本 `scripts/backfill/curation_for_existing_videos.sh` — 对所有 `coach_video_classifications.kb_extracted=true` 的旧视频补跑清洗，确保 force rerun KB 时不被前置门拦住。
- 应急 bypass：`task_channel_configs.kb_extraction.config_payload.bypass_curation_gate=true`（30s TTL 热配置 + 审计日志）。

---

## 6. 索引策略

| 索引 | 用途 |
|-----|-----|
| `video_curation_jobs (cos_object_key)` | spec SC-005 反查锚点 |
| `video_curation_jobs (coach_video_classification_id)` | 视频列表 join |
| `video_curation_jobs (status, submitted_at DESC)` | 任务监控分页 |
| `video_curation_segment_results (job_id)` | 主查询路径 |
| `video_curation_segment_results (job_id, effective_decision)` | KB 抽取读 accepted 分段集合 |
| `video_curation_segment_results (overridden_at) WHERE overridden_at IS NOT NULL` | "存在覆盖"快速筛选 |
| `coach_video_classifications (last_curation_job_id)` | 列表反查 |

---

## 7. 数据生命周期

- `video_curation_jobs.status='success'` 行**不归档**（用于审计 / 版本对比）；磁盘占用极小（每行 < 200 字节）
- `video_curation_segment_results` 同 `video_curation_jobs` 联级 — `ON DELETE CASCADE` 兜底
- 失败作业（`status='failed'`）由既有 `cleanup_expired_tasks` Beat 任务在 `analysis_tasks` 过期阈值内一并清理（不需要新增 Beat）
- 规范文件 vN.yaml 永不删（git 历史保留）

阶段 1 数据模型设计完成。
