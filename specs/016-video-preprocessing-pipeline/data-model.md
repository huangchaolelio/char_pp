# 数据模型: 视频预处理流水线

**日期**: 2026-04-25
**迁移目标文件**: `src/db/migrations/versions/0014_video_preprocessing_pipeline.py`
**基线**: `0013_kb_extraction_pipeline`（Feature-014）

---

## 实体关系概览

```
coach_video_classifications (扩展)
  └── preprocessed: bool  ← 新增
       ↑
       │ (应用层维护，不是外键)
       │
video_preprocessing_jobs (新表)
  │ PK: id (UUID)
  │ UQ: (cos_object_key) WHERE status='success'
  │
  └─< video_preprocessing_segments (新表)
      │ PK: id (UUID)
      │ FK: job_id → video_preprocessing_jobs.id (CASCADE DELETE)
      │ UQ: (job_id, segment_index)

task_channel_configs (种子数据)
  └── 新增 row: channel_type='preprocessing', concurrency=3, queue_capacity=20
```

---

## 1. VideoPreprocessingJob（新表 `video_preprocessing_jobs`）

每次预处理任务一条记录。幂等通过 partial unique index 保证：同一 `cos_object_key` 至多一条 `status='success'`。

### 字段

| 列名 | 类型 | 可空 | 默认 | 说明 |
|------|------|------|------|------|
| `id` | `UUID` | NO | `uuid4()` | 主键 |
| `cos_object_key` | `VARCHAR(1024)` | NO | — | 原视频 COS key，对应 `coach_video_classifications.cos_object_key` |
| `status` | `VARCHAR(16)` | NO | `'running'` | 状态机（见下）|
| `force` | `BOOLEAN` | NO | `false` | 是否 force=true 提交 |
| `error_message` | `TEXT` | YES | NULL | 失败原因（带结构化前缀）|
| `started_at` | `TIMESTAMP WITH TZ` | NO | `now()` | 任务开始时间 |
| `completed_at` | `TIMESTAMP WITH TZ` | YES | NULL | 任务结束时间（success / failed / superseded 任一）|
| `duration_ms` | `INTEGER` | YES | NULL | 原视频时长（毫秒，probe 阶段采集） |
| `segment_count` | `INTEGER` | YES | NULL | 分段数（0 < duration ≤ 180s 时为 1）|
| `original_meta_json` | `JSONB` | YES | NULL | 原视频元数据：`{fps, width, height, duration_ms, codec, size_bytes, has_audio}` |
| `target_standard_json` | `JSONB` | YES | NULL | 标准化参数：`{target_fps, target_short_side, segment_duration_s}` |
| `audio_cos_object_key` | `VARCHAR(1024)` | YES | NULL | 预处理音频 COS key（仅 has_audio=true 时有值）|
| `audio_size_bytes` | `BIGINT` | YES | NULL | 预处理音频文件字节数 |
| `has_audio` | `BOOLEAN` | NO | `false` | 原视频是否有音轨 |
| `local_artifact_dir` | `VARCHAR(512)` | YES | NULL | 本地温缓存目录（TTL 清理依据）|
| `created_at` | `TIMESTAMP WITH TZ` | NO | `now()` | 记录创建时间 |
| `updated_at` | `TIMESTAMP WITH TZ` | NO | `now()` | 记录更新时间（onupdate）|

### 约束

```sql
-- 状态枚举
CHECK (status IN ('running', 'success', 'failed', 'superseded'))

-- 音频一致性：has_audio=true 必须有 cos_key 和 size
CHECK (
  (has_audio = false AND audio_cos_object_key IS NULL AND audio_size_bytes IS NULL)
  OR (has_audio = true AND status = 'running')
  OR (has_audio = true AND audio_cos_object_key IS NOT NULL AND audio_size_bytes IS NOT NULL)
)

-- 幂等 (部分唯一索引)
UNIQUE INDEX uq_vpj_cos_success ON (cos_object_key) WHERE status = 'success'

-- 常规索引
INDEX idx_vpj_status          ON (status)
INDEX idx_vpj_cos_object_key  ON (cos_object_key)
INDEX idx_vpj_created_at      ON (created_at)
```

### 状态机

```
                 submit (force=false, 已有 success) → 返回已有 job，不新建
                                │
                                ▼
 [new]  ─────submit─────▶ [running] ─────ok─────▶ [success]
                                │
                                │ error
                                ▼
                           [failed]

 [success] ──force=true──▶ [superseded]  (旧 job 被新 job 替代)
```

- `running` → `success`: 全部步骤完成，COS 上传完毕
- `running` → `failed`: 任一步骤抛错，error_message 带前缀
- `success` → `superseded`: 同 cos_object_key 收到 `force=true` 提交时被替代（同事务中把旧的置 superseded、删 COS 对象、插入新 running job）
- `failed` → `superseded`: 不触发（failed job 不影响幂等，force=true 可直接新建 running）

---

## 2. VideoPreprocessingSegment（新表 `video_preprocessing_segments`）

每个分段一条记录。与 job 一对多。

### 字段

| 列名 | 类型 | 可空 | 默认 | 说明 |
|------|------|------|------|------|
| `id` | `UUID` | NO | `uuid4()` | 主键 |
| `job_id` | `UUID` | NO | — | FK → `video_preprocessing_jobs.id`（CASCADE DELETE）|
| `segment_index` | `INTEGER` | NO | — | 分段顺序索引（从 0 开始）|
| `start_ms` | `INTEGER` | NO | — | 段起始毫秒（相对原视频）|
| `end_ms` | `INTEGER` | NO | — | 段结束毫秒（相对原视频）|
| `cos_object_key` | `VARCHAR(1024)` | NO | — | 分段 COS key |
| `size_bytes` | `BIGINT` | NO | — | 分段文件字节数 |
| `created_at` | `TIMESTAMP WITH TZ` | NO | `now()` | 记录创建时间 |

### 约束

```sql
-- 外键
FOREIGN KEY (job_id) REFERENCES video_preprocessing_jobs(id) ON DELETE CASCADE

-- 组合唯一：同 job 内分段索引唯一
UNIQUE (job_id, segment_index)

-- 索引
INDEX idx_vps_job_id ON (job_id)

-- 时长校验
CHECK (end_ms > start_ms)
CHECK (size_bytes > 0)
CHECK (segment_index >= 0)
```

---

## 3. CoachVideoClassification（扩展 `coach_video_classifications`）

在现有表（Feature-008）新增一列，不改其他。

### 新增字段

| 列名 | 类型 | 可空 | 默认 | 说明 |
|------|------|------|------|------|
| `preprocessed` | `BOOLEAN` | NO | `false` | 是否至少有一次成功的 VideoPreprocessingJob |

### 新增索引

```sql
INDEX idx_cvclf_preprocessed ON (preprocessed)
```

### 维护

- **FR-006 触发**: `video_preprocessing_jobs.status` 变为 `'success'` 时，应用层（`preprocessing_service.mark_preprocessed()`）把对应 `coach_video_classifications.preprocessed = true`
- **不做反向降级**: `superseded` 或 `failed` 不把 `preprocessed` 置回 false（因为仍有历史成功记录）—— 除非所有历史 jobs 都变成 superseded，此时按应用层语义，新一轮 force 完成后会再次置 true，无需降级
- **不是外键关系**: `video_preprocessing_jobs.cos_object_key` 匹配 `coach_video_classifications.cos_object_key`，通过应用层（不是数据库约束）保持一致

---

## 4. TaskChannelConfig（扩展种子数据）

在现有 `task_channel_configs` 表（Feature-013）插入 1 行：

```sql
INSERT INTO task_channel_configs (
  channel_type, concurrency, queue_capacity, updated_at
) VALUES (
  'preprocessing', 3, 20, now()
)
ON CONFLICT (channel_type) DO NOTHING;
```

**字段约束**（已存在 CHECK）:
- `channel_type IN ('classification', 'kb_extraction', 'diagnosis', 'default', 'preprocessing')` — 需迁移同步更新 CHECK constraint

**迁移步骤**:
```python
# 0014 迁移内包含
op.drop_constraint('ck_tcc_channel_type', 'task_channel_configs', type_='check')
op.create_check_constraint(
    'ck_tcc_channel_type',
    'task_channel_configs',
    "channel_type IN ('classification', 'kb_extraction', 'diagnosis', 'default', 'preprocessing')",
)
```

---

## 5. ExtractionJob / PipelineStep（Feature-014 schema 保持不变）

**不新增列**。但 `pipeline_steps.output_summary` JSONB 在 executor 改造时会新增字段：

### `download_video.output_summary`（新增字段）

```json
{
  "segments_downloaded": 4,
  "segments_total": 4,
  "audio_downloaded": true,
  "local_cache_hits": 3,
  "cos_downloads": 1,
  "video_preprocessing_job_id": "uuid-..."
}
```

### `pose_analysis.output_summary`（新增字段）

```json
{
  "backend": "yolov8",
  "segments_processed": 4,
  "segments_failed": 0,
  "total_frames": 21600,
  "pose_detected_frames": 21423
}
```

### `audio_transcription.output_summary`（新增字段）

```json
{
  "audio_source": "cos_preprocessed",
  "whisper_model": "small",
  "whisper_device": "cpu",
  "segments_count": 142
}
```

这些字段通过 JSONB 自由扩展，**不改 schema**，不需要迁移。

---

## 6. 验证规则

### 预处理提交阶段

| 规则 | 代码位置 | 错误响应 |
|------|----------|----------|
| `cos_object_key` 非空 | `src/api/schemas/preprocessing.py` | 422 Pydantic |
| `cos_object_key` 在 `coach_video_classifications` 存在 | `preprocessing_service.create_job()` | 400 `COS_KEY_NOT_CLASSIFIED` |
| `force=false` 且已有 success job | `preprocessing_service.create_job()` | 200 返回已有 job（非错误）|
| 批量提交 items 数 ≤ `batch_max_size` | `tasks.py` router | 400 `BATCH_TOO_LARGE`（复用 F-013）|

### 运行时阶段

| 规则 | 代码位置 | 失败行为 |
|------|----------|----------|
| probe fps ≥ 15 且 resolution ≥ 854×480 | `video_probe.probe_and_validate()` | `VIDEO_QUALITY_REJECTED:` → job failed |
| probe 可解码（ffprobe 非零退出）| 同上 | `VIDEO_PROBE_FAILED:` |
| 编码可转码 | `video_transcoder.transcode()` | `VIDEO_TRANSCODE_FAILED:` 或 `VIDEO_CODEC_UNSUPPORTED:` |
| 切分完成且段数 = ceil(duration / 180) | `video_splitter.split()` | `VIDEO_SPLIT_FAILED:` |
| 每段上传成功（重试 3×30s）| `cos_uploader.upload()` | `VIDEO_UPLOAD_FAILED:` 单段重试用尽 |
| 音频提取成功（has_audio=true 时）| `audio_exporter.export_wav()` | `AUDIO_EXTRACT_FAILED:` |

### KB 提取消费阶段

| 规则 | 代码位置 | 失败行为 |
|------|----------|----------|
| 分段 COS `head_object` 存在 | `download_video.py`（改造）| `SEGMENT_MISSING:` |
| 音频 COS `head_object` 存在 | 同上 | `AUDIO_MISSING:` |
| 本地文件 size 与 DB 记录一致 | 同上 | 失败则清理本地 → 从 COS 重下 |

---

## 7. 索引策略 & 查询性能

预期的主要查询模式：

| 查询 | 索引 |
|------|------|
| `WHERE cos_object_key = ? AND status = 'success'` | `uq_vpj_cos_success` |
| `WHERE cos_object_key = ? ORDER BY created_at DESC` | `idx_vpj_cos_object_key` + `idx_vpj_created_at` |
| `WHERE status = 'running' ORDER BY created_at`（孤儿扫描）| `idx_vpj_status` + `idx_vpj_created_at` |
| `WHERE job_id = ? ORDER BY segment_index`（消费分段）| `(job_id, segment_index)` 唯一键自动覆盖 |
| `WHERE preprocessed = false AND tech_category = ?`（运维报表）| `idx_cvclf_preprocessed` + 已有 `idx_cvclf_tech` |

---

## 8. 迁移脚本大纲

```python
# src/db/migrations/versions/0014_video_preprocessing_pipeline.py

revision = "0014"
down_revision = "0013"

def upgrade() -> None:
    # 1. video_preprocessing_jobs
    op.create_table(
        "video_preprocessing_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, ...),
        sa.Column("cos_object_key", sa.String(1024), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("force", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("segment_count", sa.Integer, nullable=True),
        sa.Column("original_meta_json", JSONB, nullable=True),
        sa.Column("target_standard_json", JSONB, nullable=True),
        sa.Column("audio_cos_object_key", sa.String(1024), nullable=True),
        sa.Column("audio_size_bytes", sa.BigInteger, nullable=True),
        sa.Column("has_audio", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("local_artifact_dir", sa.String(512), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed', 'superseded')",
            name="ck_vpj_status",
        ),
    )
    op.create_index("idx_vpj_status", "video_preprocessing_jobs", ["status"])
    op.create_index("idx_vpj_cos_object_key", "video_preprocessing_jobs", ["cos_object_key"])
    op.create_index("idx_vpj_created_at", "video_preprocessing_jobs", ["created_at"])
    op.execute("""
        CREATE UNIQUE INDEX uq_vpj_cos_success
        ON video_preprocessing_jobs (cos_object_key)
        WHERE status = 'success'
    """)

    # 2. video_preprocessing_segments
    op.create_table(
        "video_preprocessing_segments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, ...),
        sa.Column("job_id", UUID(as_uuid=True),
                  sa.ForeignKey("video_preprocessing_jobs.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("segment_index", sa.Integer, nullable=False),
        sa.Column("start_ms", sa.Integer, nullable=False),
        sa.Column("end_ms", sa.Integer, nullable=False),
        sa.Column("cos_object_key", sa.String(1024), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("job_id", "segment_index", name="uq_vps_job_index"),
        sa.CheckConstraint("end_ms > start_ms", name="ck_vps_timeline"),
        sa.CheckConstraint("size_bytes > 0", name="ck_vps_size"),
        sa.CheckConstraint("segment_index >= 0", name="ck_vps_index"),
    )
    op.create_index("idx_vps_job_id", "video_preprocessing_segments", ["job_id"])

    # 3. coach_video_classifications 扩展
    op.add_column(
        "coach_video_classifications",
        sa.Column("preprocessed", sa.Boolean, nullable=False, server_default="false"),
    )
    op.create_index("idx_cvclf_preprocessed", "coach_video_classifications", ["preprocessed"])

    # 4. task_channel_configs CHECK 更新 + 种子
    op.drop_constraint("ck_tcc_channel_type", "task_channel_configs", type_="check")
    op.create_check_constraint(
        "ck_tcc_channel_type",
        "task_channel_configs",
        "channel_type IN ('classification', 'kb_extraction', 'diagnosis', 'default', 'preprocessing')",
    )
    op.execute("""
        INSERT INTO task_channel_configs (channel_type, concurrency, queue_capacity, updated_at)
        VALUES ('preprocessing', 3, 20, now())
        ON CONFLICT (channel_type) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM task_channel_configs WHERE channel_type = 'preprocessing'")
    op.drop_constraint("ck_tcc_channel_type", "task_channel_configs", type_="check")
    op.create_check_constraint(
        "ck_tcc_channel_type",
        "task_channel_configs",
        "channel_type IN ('classification', 'kb_extraction', 'diagnosis', 'default')",
    )
    op.drop_index("idx_cvclf_preprocessed", "coach_video_classifications")
    op.drop_column("coach_video_classifications", "preprocessed")
    op.drop_index("idx_vps_job_id", "video_preprocessing_segments")
    op.drop_table("video_preprocessing_segments")
    op.execute("DROP INDEX IF EXISTS uq_vpj_cos_success")
    op.drop_index("idx_vpj_created_at", "video_preprocessing_jobs")
    op.drop_index("idx_vpj_cos_object_key", "video_preprocessing_jobs")
    op.drop_index("idx_vpj_status", "video_preprocessing_jobs")
    op.drop_table("video_preprocessing_jobs")
```

---

## 9. 数据量预估

假设 `coach_video_classifications` 有 ~1015 条（全量 COS 教练视频）：

| 指标 | 预估 |
|------|------|
| 全量首次预处理后 `video_preprocessing_jobs` 行数 | ≈ 1015 |
| 平均分段数（典型 10 分钟）| ≈ 4 |
| 全量预处理后 `video_preprocessing_segments` 行数 | ≈ 4060 |
| `video_preprocessing_jobs` 表大小 | < 1 MB |
| `video_preprocessing_segments` 表大小 | < 2 MB |
| COS 预处理产物总占用 | ~1015 × 4 段 × 25 MB = **~100 GB** |
| COS audio.wav 总占用 | ~1015 × 20 MB = **~20 GB** |

COS 存储成本可接受；DB 行数远在 PostgreSQL 舒适区。
