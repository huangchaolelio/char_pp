# 数据模型: 知识库提取流水线化 (Feature-014)

**阶段**: 1 — 设计与契约
**日期**: 2026-04-24

## 实体概览

| 实体 | 存储 | 新建 / 改造 |
|------|------|----------|
| `ExtractionJob` 提取作业 | 新表 `extraction_jobs` | 新建 |
| `PipelineStep` 子任务 | 新表 `pipeline_steps` | 新建 |
| `KbConflict` 冲突标注 | 新表 `kb_conflicts` | 新建 |
| `AnalysisTask` | 已有表 `analysis_tasks`（Feature-013）| 新增 `extraction_job_id` 外键（可空，指向 `extraction_jobs`）|
| `CoachVideoClassification` | 已有表（Feature-008）| 无结构变更；`kb_extracted` 字段由 `merge_kb` 子任务翻转 |
| `TechKnowledgeBase` | 已有表 | 无结构变更；视觉/音频条目带 `source_type` 字段落库 |

---

## ExtractionJob（提取作业）

顶级容器，代表一次 KB 提取的整体生命周期。一个 `AnalysisTask`（Feature-013 kb_extraction 类型）对应一个 `ExtractionJob`。

### 字段

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK | 作业 ID |
| `analysis_task_id` | UUID | FK → `analysis_tasks.id`, UNIQUE | 与 Feature-013 任务一对一 |
| `cos_object_key` | VARCHAR(512) | NOT NULL | 教练视频 COS 路径 |
| `tech_category` | VARCHAR(50) | NOT NULL | 从 `coach_video_classifications` 快照过来 |
| `status` | ENUM | NOT NULL, DEFAULT `pending` | `pending` \| `running` \| `success` \| `failed` |
| `worker_hostname` | VARCHAR(100) | NULL | orchestrator 启动时写入；重跑路由依据 |
| `enable_audio_analysis` | BOOLEAN | NOT NULL, DEFAULT TRUE | 提交参数，用于 audio 路 skip 判定 |
| `audio_language` | VARCHAR(10) | NOT NULL, DEFAULT `zh` | Whisper 参数 |
| `force` | BOOLEAN | NOT NULL, DEFAULT FALSE | 是否为 `force=true` 提交 |
| `superseded_by_job_id` | UUID | NULL, FK → `extraction_jobs.id` | 被 `force` 覆盖时填 |
| `error_message` | TEXT | NULL | 作业级失败原因摘要 |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | |
| `started_at` | TIMESTAMPTZ | NULL | 第一个子任务开始时间 |
| `completed_at` | TIMESTAMPTZ | NULL | 最后子任务结束或失败时间 |
| `intermediate_cleanup_at` | TIMESTAMPTZ | NULL | 中间结果预计清理时间（success +24h / failed +7d） |

### 索引

- `idx_extraction_jobs_task_id` UNIQUE on `analysis_task_id`
- `idx_extraction_jobs_status` on `status, created_at DESC`（列表查询用）
- `idx_extraction_jobs_cos_key_active` on `cos_object_key` WHERE `status IN ('pending', 'running')`（幂等查询）

### 状态转换

```
pending ──(orchestrator 启动)──▶ running
running ──(所有关键子任务 success)──▶ success
running ──(关键子任务 failed / 45min 超时)──▶ failed
failed ──(POST /rerun)──▶ running  (旧 failed/skipped 步骤重置)
success ──(force=true 新作业)──▶ superseded（通过新作业 superseded_by_job_id 指向）
```

### 业务规则

- BR-1: 一个 `analysis_task_id` 只能对应一个活跃 `ExtractionJob`（UNIQUE 约束）
- BR-2: `force=true` 时允许新建，但必须同时把同 `cos_object_key` 的旧 success 作业标 `superseded`（由应用层事务处理）
- BR-3: `status=success` 时 `merge_kb` 子任务必须已成功（否则 status 不得到 success）
- BR-4: `intermediate_cleanup_at` 在 `completed_at` 写入时由 trigger 或应用层计算（success: completed_at + 24h，failed: completed_at + 7d）

---

## PipelineStep（子任务）

作业 DAG 中的单个节点。静态 6 个实例/作业。

### 字段

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK | |
| `job_id` | UUID | NOT NULL, FK → `extraction_jobs.id` ON DELETE CASCADE | |
| `step_type` | ENUM | NOT NULL | 见下方枚举 |
| `status` | ENUM | NOT NULL, DEFAULT `pending` | `pending` \| `running` \| `success` \| `failed` \| `skipped` |
| `retry_count` | SMALLINT | NOT NULL, DEFAULT 0 | 已重试次数（仅 I/O 类用） |
| `error_message` | TEXT | NULL | 失败原因 |
| `output_summary` | JSONB | NULL | 步骤输出摘要（见下方每类步骤） |
| `output_artifact_path` | VARCHAR(1000) | NULL | Worker 本地文件路径（姿态/转写 JSON） |
| `started_at` | TIMESTAMPTZ | NULL | |
| `completed_at` | TIMESTAMPTZ | NULL | |
| `duration_ms` | INTEGER | NULL | `completed_at - started_at` 毫秒 |

### step_type 枚举

```python
class StepType(str, Enum):
    download_video = "download_video"
    pose_analysis = "pose_analysis"
    audio_transcription = "audio_transcription"
    visual_kb_extract = "visual_kb_extract"
    audio_kb_extract = "audio_kb_extract"
    merge_kb = "merge_kb"
```

### DAG 依赖（硬编码）

```python
DEPENDENCIES = {
    "download_video":      [],
    "pose_analysis":       ["download_video"],
    "audio_transcription": ["download_video"],
    "visual_kb_extract":   ["pose_analysis"],
    "audio_kb_extract":    ["audio_transcription"],
    "merge_kb":            ["visual_kb_extract", "audio_kb_extract"],  # audio 失败时降级
}
```

### output_summary 结构（按 step_type）

| step_type | output_summary 示例 |
|-----------|---------------------|
| download_video | `{"video_size_bytes": 52428800, "duration_sec": 600, "fps": 30, "resolution": "1920x1080"}` |
| pose_analysis | `{"keypoints_frame_count": 18000, "detected_segments": 12, "backend": "yolov8"}` |
| audio_transcription | `{"whisper_model": "small", "language_detected": "zh", "transcript_chars": 2400, "skipped": false, "skip_reason": null}` |
| visual_kb_extract | `{"kb_items_count": 8, "source_type": "visual", "tech_category": "forehand_loop_fast"}` |
| audio_kb_extract | `{"kb_items_count": 5, "source_type": "audio", "llm_model": "gpt-4o"}` |
| merge_kb | `{"merged_items": 10, "conflict_items": 3, "kb_version": "1.0.5", "kb_extracted_flag_set": true}` |

### 索引

- `idx_pipeline_steps_job_id` on `job_id, step_type`（UNIQUE；一个作业每种 step 一行）
- `idx_pipeline_steps_running_orphan` on `started_at` WHERE `status = 'running'`（孤儿扫描）

### 业务规则

- BR-5: 每个 `job_id` 必须有且只有 6 个 `PipelineStep`（每 step_type 一个）— 创建作业时批量插入
- BR-6: `skipped` 状态只能由上游失败传播写入；不接受手动 skip
- BR-7: `retry_count` 仅对 I/O 类 step（download_video / audio_transcription / audio_kb_extract）有意义；CPU 类 step 保持 0
- BR-8: 孤儿判定：`status=running` 且 `now() - started_at > 600s`（单子任务超时）→ 标 failed（由 housekeeping 或作业级超时触发）

---

## KbConflict（待审核冲突表）

合并子任务检测到视觉/音频两路参数冲突时写入。

### 字段

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK | |
| `job_id` | UUID | NOT NULL, FK → `extraction_jobs.id` | |
| `cos_object_key` | VARCHAR(512) | NOT NULL | 冗余方便查询 |
| `tech_category` | VARCHAR(50) | NOT NULL | |
| `dimension_name` | VARCHAR(200) | NOT NULL | 维度名，如 "肘部角度"、"重心偏移" |
| `visual_value` | JSONB | NULL | 视觉路的值，如 `{"min": 90, "ideal": 105, "max": 120, "unit": "degrees"}` |
| `audio_value` | JSONB | NULL | 音频路的值，如 `{"text": "肘部保持 130 度左右", "extracted_range": {"min": 125, "max": 135}}` |
| `visual_confidence` | FLOAT | NULL | |
| `audio_confidence` | FLOAT | NULL | |
| `superseded_by_job_id` | UUID | NULL, FK → `extraction_jobs.id` | `force` 覆盖时填 |
| `resolved_at` | TIMESTAMPTZ | NULL | 审核时间（本 Feature 不提供审核 API，预留字段） |
| `resolved_by` | VARCHAR(100) | NULL | 审核人 |
| `resolution` | VARCHAR(20) | NULL | `use_visual` \| `use_audio` \| `use_custom` \| `reject_both` |
| `resolution_value` | JSONB | NULL | 审核后的最终值 |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | |

### 索引

- `idx_kb_conflicts_pending` on `cos_object_key, created_at DESC` WHERE `resolved_at IS NULL AND superseded_by_job_id IS NULL`
- `idx_kb_conflicts_job` on `job_id`

### 业务规则

- BR-9: 写入由 `merge_kb` 子任务负责；维度粒度（一个作业里同一维度只写一行）
- BR-10: `force=true` 新作业时，orchestrator 把旧作业的冲突项 `UPDATE SET superseded_by_job_id = new_job_id`
- BR-11: 本 Feature 不提供审核 API；`resolved_*` 字段仅供未来使用

---

## AnalysisTask 修改

### 新增字段

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `extraction_job_id` | UUID | NULL, FK → `extraction_jobs.id` | 仅当 `task_type = kb_extraction` 时非空 |

### 迁移策略

- Alembic 0013 添加字段（可空，无默认值）
- Feature-013 既有 kb_extraction 任务：不回填（它们是旧版 stub 数据，保持 NULL）
- 新提交的 kb_extraction 任务：在 `TaskSubmissionService.submit_batch` 成功后，同一事务内创建 `ExtractionJob` + 6 个 `PipelineStep` 并回写 `analysis_tasks.extraction_job_id`

---

## 与现有表的关系

```
analysis_tasks (F-013)
    │ 1:1 (task_type='kb_extraction')
    ▼
extraction_jobs (F-014)
    │ 1:N
    ▼
pipeline_steps (F-014, 6 行/作业)

extraction_jobs
    │ 1:N
    ▼
kb_conflicts (F-014, 0-N 行/作业)

coach_video_classifications (F-008)
    │ cos_object_key 匹配（非 FK，业务约束）
    ▼
extraction_jobs

tech_knowledge_bases (F-002)
    ▲ merge_kb 写入最终条目
    │
pipeline_steps (step_type='merge_kb', status='success')
```

---

## Alembic 迁移草案（0013_kb_extraction_pipeline.py）

```python
# 关键操作
def upgrade() -> None:
    # 1. extraction_jobs_status enum
    op.execute("CREATE TYPE extraction_job_status AS ENUM ('pending', 'running', 'success', 'failed')")

    # 2. pipeline_step_status enum
    op.execute("CREATE TYPE pipeline_step_status AS ENUM ('pending', 'running', 'success', 'failed', 'skipped')")

    # 3. pipeline_step_type enum
    op.execute("CREATE TYPE pipeline_step_type AS ENUM ('download_video', 'pose_analysis', 'audio_transcription', 'visual_kb_extract', 'audio_kb_extract', 'merge_kb')")

    # 4. extraction_jobs 表
    op.create_table("extraction_jobs", ...)

    # 5. pipeline_steps 表
    op.create_table("pipeline_steps", ...)

    # 6. kb_conflicts 表
    op.create_table("kb_conflicts", ...)

    # 7. analysis_tasks 新增列
    op.add_column("analysis_tasks", sa.Column("extraction_job_id", UUID, sa.ForeignKey("extraction_jobs.id"), nullable=True))

    # 8. 索引
    op.create_index(...)

def downgrade() -> None:
    op.drop_column("analysis_tasks", "extraction_job_id")
    op.drop_table("kb_conflicts")
    op.drop_table("pipeline_steps")
    op.drop_table("extraction_jobs")
    op.execute("DROP TYPE pipeline_step_type")
    op.execute("DROP TYPE pipeline_step_status")
    op.execute("DROP TYPE extraction_job_status")
```

---

## 数据量估算

| 表 | 典型规模 |
|----|---------|
| `extraction_jobs` | 1-5 条/天（分类完的视频 × 提取次数） |
| `pipeline_steps` | 6-30 条/天 |
| `kb_conflicts` | 0-15 条/天 |

总体规模极小（年级别 <10K 行），无需分区；清理由 `housekeeping_task` 按 `intermediate_cleanup_at` 定期处理。
