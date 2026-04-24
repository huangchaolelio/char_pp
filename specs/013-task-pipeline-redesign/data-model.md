# 数据模型: 任务处理管道重新设计

**来源**: `spec.md`（关键实体章节）+ `research.md`（R2/R4/R7/R8）
**日期**: 2026-04-24

---

## 实体概览

| 实体 | 存储载体 | 说明 |
|------|---------|------|
| TaskChannel | `task_channel_configs` 表（新） | 每类任务的容量、并发、启用状态 |
| AnalysisTask | `analysis_tasks` 表（改造） | 所有任务统一存储，task_type 字段区分 |
| SubmissionResult | 响应 DTO（无持久化） | 单/批量提交的响应结构 |
| ResetReport | 响应 DTO（无持久化） | 数据重置操作结果 |
| ClassificationTask | `AnalysisTask where task_type='video_classification'` | 单条视频分类任务（概念视图） |
| KbExtractionTask | `AnalysisTask where task_type='kb_extraction'` | 知识库提取任务（概念视图） |
| DiagnosisTask | `AnalysisTask where task_type='athlete_diagnosis'` | 诊断任务（概念视图） |

---

## 1. TaskChannel（新增表 `task_channel_configs`）

### 字段

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `task_type` | `task_type_enum` | PRIMARY KEY | 通道标识，枚举值：`video_classification` / `kb_extraction` / `athlete_diagnosis` |
| `queue_capacity` | INTEGER | NOT NULL, > 0 | 最大积压（pending + processing）上限 |
| `concurrency` | INTEGER | NOT NULL, > 0 | Worker 同时处理任务数（运维参考值，对应 `celery -c`） |
| `enabled` | BOOLEAN | NOT NULL DEFAULT TRUE | 通道是否接受新任务 |
| `updated_at` | TIMESTAMPTZ | NOT NULL DEFAULT now() | 配置上次变更时间 |

### 默认数据（迁移时插入）

```sql
INSERT INTO task_channel_configs (task_type, queue_capacity, concurrency, enabled) VALUES
  ('video_classification', 5,  1, true),
  ('kb_extraction',       50,  2, true),
  ('athlete_diagnosis',   20,  2, true);
```

### 业务规则

- `queue_capacity` 调低到低于当前 `pending+processing` 数时：不强制中止已在进行的任务（FR 边界情况），新提交即时被拒绝
- `enabled=false` 的通道：所有新提交返回 `503 CHANNEL_DISABLED`
- 变更通过 `PATCH /api/v1/admin/channels/{task_type}`（管理型接口），内存缓存 TTL 30 秒

---

## 2. AnalysisTask（既有表 `analysis_tasks` 改造）

### 字段变更

#### 新增列

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `cos_object_key` | VARCHAR(1000) | NULLABLE（诊断任务用户上传无此字段） | COS 明文 key，用于幂等去重判断（与加密的 `video_storage_uri` 并存） |
| `submitted_via` | VARCHAR(20) | NOT NULL DEFAULT 'single' | 提交方式：`single` / `batch` / `scan` |
| `parent_scan_task_id` | UUID | NULLABLE, FK → analysis_tasks.id ON DELETE SET NULL | 扫描任务拆分出的子分类任务指向父任务（仅 video_classification 使用） |

#### 修改列

| 列名 | 变更 |
|------|------|
| `task_type` | 类型 DROP TYPE CASCADE 并重建为 `('video_classification','kb_extraction','athlete_diagnosis')` 3 值枚举 |

#### 保留列（不变）

`id, video_filename, video_size_bytes, video_duration_seconds, video_fps, video_resolution, video_storage_uri, status, rejection_reason, knowledge_base_version, error_message, total_segments, processed_segments, progress_pct, audio_fallback_reason, created_at, started_at, completed_at, deleted_at, timing_stats, coach_id`

### 新增索引

```sql
-- 幂等去重：同一视频同一类型不允许有「未决」或「已成功」的任务
CREATE UNIQUE INDEX idx_analysis_tasks_idempotency
  ON analysis_tasks (cos_object_key, task_type)
  WHERE status IN ('pending', 'processing', 'success')
    AND deleted_at IS NULL
    AND cos_object_key IS NOT NULL;

-- 限流计数查询加速
CREATE INDEX idx_analysis_tasks_channel_count
  ON analysis_tasks (task_type, status)
  WHERE status IN ('pending', 'processing')
    AND deleted_at IS NULL;
```

### 状态机（保持既有，无新增状态）

```
pending ─► processing ─► success
                      └─► partial_success
                      └─► failed
          └─► rejected  (提交时限流/幂等直接拒绝，不入队)
          └─► failed    (orphan recovery 扫描)
```

**变更点**:
- `rejected` 状态现在还用于表示「通道已满」「重复提交」的拒绝记录（写入 DB 以便监控）
- `failed` 新增一种来源：orphan recovery（`error_message='orphan recovered on worker restart'`）

### 实体视图（按 task_type 过滤）

应用层不再用独立表，而是以查询视图或 SQLAlchemy 过滤条件呈现：

- **ClassificationTask 视图**: `task_type='video_classification'`，仅关心 `cos_object_key`、`coach_id`、结果写入 `coach_video_classifications` 而非本表业务字段
- **KbExtractionTask 视图**: `task_type='kb_extraction'`，关心 `cos_object_key`、`coach_id`、`audio_transcript`（关联）、`expert_tech_points`（关联）、`tech_semantic_segments`（关联）
- **DiagnosisTask 视图**: `task_type='athlete_diagnosis'`，关心 `video_storage_uri`、`athlete_motion_analyses`（关联）、`diagnosis_report`（关联）、`knowledge_base_version`

---

## 3. SubmissionResult（响应 DTO）

仅响应体结构，无持久化。

```python
class SubmissionItem(BaseModel):
    index: int                  # 请求数组中的位置（单条时恒为 0）
    task_id: UUID | None = None # 接受时返回
    status: Literal["accepted", "rejected"]
    rejection_code: str | None = None
    # 可选的 code: QUEUE_FULL | DUPLICATE_TASK | CLASSIFICATION_REQUIRED | CHANNEL_DISABLED | INVALID_INPUT
    rejection_message: str | None = None

class SubmissionResult(BaseModel):
    task_type: Literal["video_classification", "kb_extraction", "athlete_diagnosis"]
    accepted_count: int
    rejected_count: int
    items: list[SubmissionItem]
    channel_snapshot: ChannelSnapshot  # 提交后的通道状态
```

---

## 4. ChannelSnapshot（响应 DTO）

```python
class ChannelSnapshot(BaseModel):
    task_type: str
    queue_capacity: int
    current_pending: int
    current_processing: int
    remaining_slots: int            # = queue_capacity - pending - processing
    enabled: bool
    recent_completion_rate_per_min: float | None  # 近 10 分钟完成速率
```

用于 `GET /api/v1/task-channels` 与提交响应。

---

## 5. ResetReport（响应 DTO）

```python
class ResetReport(BaseModel):
    reset_at: datetime
    deleted_counts: dict[str, int]   # 表名 → 删除行数
    preserved_counts: dict[str, int] # 保留的核心资产表行数（便于核对）
    duration_ms: int
```

---

## 实体关系图

```
task_channel_configs (1) ─── (N) analysis_tasks
                                    │
                                    ├── (1) audio_transcript        (kb_extraction)
                                    ├── (N) expert_tech_points       (kb_extraction)
                                    ├── (N) tech_semantic_segments   (kb_extraction)
                                    ├── (N) athlete_motion_analyses  (diagnosis)
                                    ├── (N) coaching_advice          (diagnosis)
                                    ├── (N) teaching_tips            (diagnosis)
                                    ├── (N) diagnosis_report         (diagnosis)
                                    └── ─► coaches (coach_id)
                                    └── ─► tech_knowledge_bases (knowledge_base_version)
                                    └── ─► analysis_tasks (parent_scan_task_id, self-ref)
```

---

## 迁移计划（Alembic 0012）

1. 执行 `task_reset_service` 的清理 SQL（空载状态可跳过，但迁移需幂等）
2. `ALTER TABLE analysis_tasks DROP CONSTRAINT ...`（删除与旧枚举相关的外键/约束，若有）
3. `DROP TYPE task_type_enum CASCADE`
4. `CREATE TYPE task_type_enum AS ENUM ('video_classification', 'kb_extraction', 'athlete_diagnosis')`
5. `ALTER TABLE analysis_tasks ADD COLUMN cos_object_key VARCHAR(1000)`
6. `ALTER TABLE analysis_tasks ADD COLUMN submitted_via VARCHAR(20) NOT NULL DEFAULT 'single'`
7. `ALTER TABLE analysis_tasks ADD COLUMN parent_scan_task_id UUID REFERENCES analysis_tasks(id) ON DELETE SET NULL`
8. `ALTER TABLE analysis_tasks ALTER COLUMN task_type TYPE task_type_enum USING task_type::text::task_type_enum` (因为表为空此处安全)
9. 创建新索引（idempotency partial + channel_count）
10. `CREATE TABLE task_channel_configs (...)`
11. INSERT 默认 3 行配置
