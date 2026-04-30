# 数据模型: 处理流程规范化（Workflow Standardization） — Feature-018

**阶段**: 1（Design & Contracts）
**输入**: [spec.md](./spec.md) FR-001 ~ FR-016 + [research.md](./research.md) R1–R7
**范围**: 本 Feature 仅**增列与索引**，不新增数据表；外加 1 个 YAML 台账与 1 个响应 DTO。

---

## 1. BusinessPhase Postgres enum

```sql
CREATE TYPE business_phase_enum AS ENUM ('TRAINING', 'STANDARDIZATION', 'INFERENCE');
```

- **约束**: 三值枚举，不可新增；若需扩展，先走章程 `/speckit.constitution` MINOR
- **落位**: Alembic 迁移 `0016_business_phase_step.py`（`revision="0016"` / `down_revision="0015"`，当前 head 为 `0015_kb_audit_and_expand_action_types`）
- **降级**: `alembic downgrade -1` 时，先 `DROP COLUMN business_phase` 再 `DROP TYPE business_phase_enum`

---

## 2. `business_step` 字符串集合（8 值，Clarification Q1 决议）

Pydantic `Literal` 守卫；**数据库层为 `VARCHAR(64)` 无 enum**（方便未来扩展）。

```python
# src/models/_phase_step_hook.py
BusinessStep = Literal[
    "scan_cos_videos",       # TRAINING
    "preprocess_video",      # TRAINING
    "classify_video",        # TRAINING
    "extract_kb",            # TRAINING
    "review_conflicts",      # STANDARDIZATION
    "kb_version_activate",   # STANDARDIZATION
    "build_standards",       # STANDARDIZATION
    "diagnose_athlete",      # INFERENCE
]
```

> `generate_report` **不**在集合内（Clarification Q1），作为 `diagnose_athlete` 步骤内部子产物。

---

## 3. 四张业务表的字段扩展

### 3.1 `analysis_tasks`

| 列名 | 类型 | NULL | 默认 | 说明 |
|------|------|------|------|------|
| `business_phase` | `business_phase_enum` | **NO**（双层：钩子派生 + 列级 NOT NULL） | — | 由 `before_insert` 钩子按 `task_type` 派生 |
| `business_step` | `VARCHAR(64)` | **NO** | — | 同上；值属于 `BusinessStep` 集合 |

**索引**:
```sql
CREATE INDEX idx_analysis_tasks_phase_step ON analysis_tasks (business_phase, business_step);
```

**派生规则**（FR-002 静态映射）:

| `task_type` | `parent_scan_task_id` | → `business_phase` | → `business_step` |
|------------|----------------------|--------------------|-------------------|
| `video_classification` | IS NULL | `TRAINING` | `scan_cos_videos` |
| `video_classification` | NOT NULL | `TRAINING` | `classify_video` |
| `video_preprocessing` | — | `TRAINING` | `preprocess_video` |
| `kb_extraction` | — | `TRAINING` | `extract_kb` |
| `athlete_diagnosis` | — | `INFERENCE` | `diagnose_athlete` |

### 3.2 `extraction_jobs`

| 列名 | 类型 | NULL | 派生 |
|------|------|------|------|
| `business_phase` | `business_phase_enum` | NO | 固定 `TRAINING` |
| `business_step` | `VARCHAR(64)` | NO | 固定 `extract_kb` |

**索引**: `idx_extraction_jobs_phase(business_phase)`。

### 3.3 `video_preprocessing_jobs`

| 列名 | 类型 | NULL | 派生 |
|------|------|------|------|
| `business_phase` | `business_phase_enum` | NO | 固定 `TRAINING` |
| `business_step` | `VARCHAR(64)` | NO | 固定 `preprocess_video` |

### 3.4 `tech_knowledge_bases`

| 列名 | 类型 | NULL | 派生 |
|------|------|------|------|
| `business_phase` | `business_phase_enum` | NO | 固定 `STANDARDIZATION` |
| `business_step` | `VARCHAR(64)` | NO | 固定 `kb_version_activate` |

---

## 4. `_phase_step_hook.py` 派生表与钩子语义

```python
# src/models/_phase_step_hook.py
from sqlalchemy import event, inspect as sa_inspect
from src.models.analysis_task import AnalysisTask, TaskType
from src.models.extraction_job import ExtractionJob
from src.models.video_preprocessing_job import VideoPreprocessingJob
from src.models.tech_knowledge_base import TechKnowledgeBase

BusinessPhase = Literal["TRAINING", "STANDARDIZATION", "INFERENCE"]

# 表名 → (默认 phase, 默认 step) 或派生函数
_TABLE_DEFAULTS: dict[type, tuple[str, str] | Callable] = {
    ExtractionJob: ("TRAINING", "extract_kb"),
    VideoPreprocessingJob: ("TRAINING", "preprocess_video"),
    TechKnowledgeBase: ("STANDARDIZATION", "kb_version_activate"),
    AnalysisTask: _derive_for_analysis_task,  # 函数：读 task_type + parent_scan_task_id
}

def _derive_for_analysis_task(row: AnalysisTask) -> tuple[str, str]:
    tt = row.task_type
    if tt == TaskType.video_classification:
        step = "scan_cos_videos" if row.parent_scan_task_id is None else "classify_video"
        return ("TRAINING", step)
    if tt == TaskType.video_preprocessing:
        return ("TRAINING", "preprocess_video")
    if tt == TaskType.kb_extraction:
        return ("TRAINING", "extract_kb")
    if tt == TaskType.athlete_diagnosis:
        return ("INFERENCE", "diagnose_athlete")
    raise ValueError(f"PHASE_STEP_UNMAPPED: unknown task_type={tt!r}")

def _assign_phase_step(mapper, connection, target):
    state = sa_inspect(target)
    # 判定调用方是否显式传入
    phase_changed = state.attrs.business_phase.history.has_changes()
    step_changed = state.attrs.business_step.history.has_changes()
    if phase_changed and step_changed:
        return  # 显式传入，尊重调用方
    if phase_changed ^ step_changed:
        raise ValueError("PHASE_STEP_UNMAPPED: must set both business_phase and business_step, or neither")

    rule = _TABLE_DEFAULTS.get(type(target))
    if rule is None:
        raise ValueError(f"PHASE_STEP_UNMAPPED: no default for table={type(target).__name__}")
    phase, step = rule(target) if callable(rule) else rule
    target.business_phase = phase
    target.business_step = step

def register_phase_step_hooks() -> None:
    for model_cls in _TABLE_DEFAULTS:
        event.listen(model_cls, "before_insert", _assign_phase_step)
```

**调用点**: `src/db/session.py` 模块顶部 `import` 时触发一次 `register_phase_step_hooks()`；或在 `src/api/main.py::create_app()` 启动前调用。

**Fail-Fast**:
- 未知 `task_type` → `PHASE_STEP_UNMAPPED`
- 只传 phase 未传 step（或反之） → `PHASE_STEP_UNMAPPED`
- 钩子异常 → 列级 `NOT NULL` 作为最后兜底 → Postgres 返回 `NOT NULL violation`（映射到 `AppException(INTERNAL_ERROR)` + `logging.exception`）

---

## 5. 迁移回填 SQL（FR-002）

**文件**: `src/db/migrations/versions/0016_business_phase_step.py`（`revision="0016"` / `down_revision="0015"`）

```python
def upgrade() -> None:
    # 1. 创建 enum type
    op.execute("CREATE TYPE business_phase_enum AS ENUM ('TRAINING', 'STANDARDIZATION', 'INFERENCE')")

    # 2. 四张表 ADD COLUMN（NULL 可空，瞬时）
    for tbl in ("analysis_tasks", "extraction_jobs",
                "video_preprocessing_jobs", "tech_knowledge_bases"):
        op.execute(f"ALTER TABLE {tbl} ADD COLUMN business_phase business_phase_enum")
        op.execute(f"ALTER TABLE {tbl} ADD COLUMN business_step VARCHAR(64)")

    # 3. 回填（单事务，当前数据量可一次完成）
    op.execute("""
        UPDATE analysis_tasks SET
          business_phase = CASE task_type
            WHEN 'athlete_diagnosis' THEN 'INFERENCE'::business_phase_enum
            ELSE 'TRAINING'::business_phase_enum
          END,
          business_step = CASE
            WHEN task_type = 'video_classification' AND parent_scan_task_id IS NULL THEN 'scan_cos_videos'
            WHEN task_type = 'video_classification' THEN 'classify_video'
            WHEN task_type = 'video_preprocessing' THEN 'preprocess_video'
            WHEN task_type = 'kb_extraction' THEN 'extract_kb'
            WHEN task_type = 'athlete_diagnosis' THEN 'diagnose_athlete'
          END
    """)
    op.execute("UPDATE extraction_jobs SET business_phase='TRAINING', business_step='extract_kb'")
    op.execute("UPDATE video_preprocessing_jobs SET business_phase='TRAINING', business_step='preprocess_video'")
    op.execute("UPDATE tech_knowledge_bases SET business_phase='STANDARDIZATION', business_step='kb_version_activate'")

    # 4. 添加 NOT NULL 约束
    for tbl in ("analysis_tasks", "extraction_jobs",
                "video_preprocessing_jobs", "tech_knowledge_bases"):
        op.execute(f"ALTER TABLE {tbl} ALTER COLUMN business_phase SET NOT NULL")
        op.execute(f"ALTER TABLE {tbl} ALTER COLUMN business_step SET NOT NULL")

    # 5. 索引
    op.create_index("idx_analysis_tasks_phase_step", "analysis_tasks",
                    ["business_phase", "business_step"])
    op.create_index("idx_extraction_jobs_phase", "extraction_jobs", ["business_phase"])

def downgrade() -> None:
    op.drop_index("idx_extraction_jobs_phase", table_name="extraction_jobs")
    op.drop_index("idx_analysis_tasks_phase_step", table_name="analysis_tasks")
    for tbl in ("analysis_tasks", "extraction_jobs",
                "video_preprocessing_jobs", "tech_knowledge_bases"):
        op.execute(f"ALTER TABLE {tbl} DROP COLUMN IF EXISTS business_step")
        op.execute(f"ALTER TABLE {tbl} DROP COLUMN IF EXISTS business_phase")
    op.execute("DROP TYPE IF EXISTS business_phase_enum")
```

---

## 6. `config/optimization_levers.yml` Schema

```yaml
# config/optimization_levers.yml — Feature-018 优化杠杆台账
# 与 docs/business-workflow.md § 9 表格双向同步（纳入 workflow_drift.py 扫描）
version: 1

levers:
  # ── runtime_params（热配置，30s 内生效） ────────────────────────
  - key: task_channel_configs.kb_extraction.concurrency
    type: runtime_params
    source: db_table
    source_ref: task_channel_configs
    effective_in_seconds: 30
    restart_scope: none
    business_phase: [TRAINING]
    sensitive: false

  - key: task_channel_configs.athlete_diagnosis.concurrency
    type: runtime_params
    source: db_table
    source_ref: task_channel_configs
    effective_in_seconds: 30
    restart_scope: none
    business_phase: [INFERENCE]
    sensitive: false

  # ── algorithm_models（需重启 worker） ────────────────────────────
  - key: POSE_BACKEND
    type: algorithm_models
    source: env
    effective_in_seconds: null
    restart_scope: worker
    business_phase: [TRAINING, INFERENCE]
    sensitive: false

  - key: WHISPER_MODEL_SIZE
    type: algorithm_models
    source: env
    effective_in_seconds: null
    restart_scope: worker
    business_phase: [TRAINING]
    sensitive: false

  # ── rules_prompts（需重启 API） ──────────────────────────────────
  - key: config/tech_classification_rules.json
    type: rules_prompts
    source: config_file
    effective_in_seconds: null
    restart_scope: api
    business_phase: [TRAINING]
    sensitive: false

  # ── 敏感键（只返回 is_configured） ───────────────────────────────
  - key: VENUS_TOKEN
    type: algorithm_models
    source: env
    effective_in_seconds: null
    restart_scope: worker
    business_phase: [TRAINING, INFERENCE]
    sensitive: true

  - key: OPENAI_API_KEY
    type: algorithm_models
    source: env
    effective_in_seconds: null
    restart_scope: worker
    business_phase: [TRAINING, INFERENCE]
    sensitive: true

  - key: COS_SECRET_KEY
    type: runtime_params
    source: env
    effective_in_seconds: null
    restart_scope: worker
    business_phase: [TRAINING, STANDARDIZATION]
    sensitive: true

  - key: POSTGRES_PASSWORD
    type: runtime_params
    source: env
    effective_in_seconds: null
    restart_scope: api
    business_phase: [TRAINING, STANDARDIZATION, INFERENCE]
    sensitive: true
```

**加载时校验规则**（`OptimizationLeversService.__init__` fail-fast）:
- `type ∈ {runtime_params, algorithm_models, rules_prompts}`
- `source ∈ {db_table, env, config_file}`
- `restart_scope ∈ {none, worker, api}`
- `business_phase` 为非空列表，元素属于 3 值枚举
- `key` 全局唯一
- 解析失败 → `raise ValueError("OPTIMIZATION_LEVERS_YAML_INVALID: ...")` → API 启动失败

---

## 7. `WorkflowOverviewSnapshot` 响应 DTO

```python
# src/api/schemas/business_workflow.py
from pydantic import BaseModel, ConfigDict

class StepSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: Literal[*BusinessStep]
    pending: int
    processing: int
    success: int
    failed: int
    recent_24h_completed: int
    # 仅完整档返回；降级档省略
    p50_seconds: float | None = None
    p95_seconds: float | None = None

class PhaseSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phase: Literal["TRAINING", "STANDARDIZATION", "INFERENCE"]
    steps: dict[str, StepSnapshot]  # key ∈ BusinessStep 子集

class WorkflowOverviewSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    TRAINING: PhaseSnapshot
    STANDARDIZATION: PhaseSnapshot
    INFERENCE: PhaseSnapshot
```

**信封契约**:
- 成功（完整档）: `{success:true, data:WorkflowOverviewSnapshot, meta:{generated_at,window_hours,degraded:false}}`
- 成功（降级档）: `{success:true, data:<省略 p50_seconds/p95_seconds>, meta:{generated_at,window_hours,degraded:true,degraded_reason:"row_count_exceeds_latency_budget"}}`
- 注：`degraded` / `degraded_reason` 放入 `meta` 而非 `PaginationMeta`，本 Feature **扩展 `PaginationMeta` 无意义**，改为 `meta` 为自由 dict（Pydantic `SuccessEnvelope` 的 `meta` 已接受 `PaginationMeta | None`）
  - **实现策略**: 新增 `WorkflowOverviewMeta(BaseModel)` + `class Config(SuccessEnvelope[WorkflowOverviewSnapshot])` 定制——或临时用 `SuccessEnvelope[dict]` 绕过（待 Feature-017 `SuccessEnvelope` 扩展 `meta` 泛型后再重构）
  - 本 Feature 选**方案 A**: 声明 `response_model=SuccessEnvelope[WorkflowOverviewSnapshot]` + 路由层直接构造 `SuccessEnvelope(success=True, data=..., meta=WorkflowOverviewMeta(...).model_dump())`

---

## 8. `OptimizationLever` API 响应 DTO

```python
# src/api/schemas/admin_levers.py
class LeverEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    type: Literal["runtime_params", "algorithm_models", "rules_prompts"]
    source: Literal["db_table", "env", "config_file"]
    effective_in_seconds: int | None
    restart_scope: Literal["none", "worker", "api"]
    business_phase: list[Literal["TRAINING", "STANDARDIZATION", "INFERENCE"]]
    # 非敏感：返回 current_value + last_changed_at + last_changed_by
    current_value: str | int | bool | None = None
    last_changed_at: datetime | None = None
    last_changed_by: str | None = None
    # 敏感：仅返回 is_configured
    is_configured: bool | None = None

class LeverGroups(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runtime_params: list[LeverEntry]
    algorithm_models: list[LeverEntry]
    rules_prompts: list[LeverEntry]
```

**`last_changed_at` / `last_changed_by` 取值语义**（与 spec FR-013 对齐；遵循原则 IV，不引入新表存历史）：

| `source` | `last_changed_at` 来源 | `last_changed_by` 来源 |
|----------|-----------------------|----------------------|
| `db_table` | `task_channel_configs.updated_at`（既有列） | `task_channel_configs.updated_by`（既有列，无值 ⇒ `null`） |
| `config_file` | `subprocess.run(["git", "log", "-1", "--format=%aI", "--", path])` stdout；非 git 检出或文件不存在 ⇒ `null` | 同上命令 `--format=%an`；失败 ⇒ `null` |
| `env` | 恒 `null`（`.env` 无内置 author 元数据） | 恒 `null` |

Service 层实现 MUST 对 `git log` 子进程调用做超时守护（`timeout=2s`）与异常捕获，失败时 `null` 兜底，**禁止**抛 500 影响整个台账响应。

---

## 9. `DriftReport` JSON 结构（`scripts/audit/workflow_drift.py` 产物）

```json
{
  "scanned_at": "2026-04-30T12:51:20+08:00",
  "mode": "full | changed-only",
  "exit_code": 0,
  "drifts": [
    {
    "kind": "error_code_prefix | task_status_enum | extraction_job_status | kb_status | scorer_threshold | channel_seed | optimization_lever | spec_template_fields",
      "identifier": "WHISPER_GPU_OOM",
      "code_side": "WHISPER_GPU_OOM",
      "doc_side": null,
      "severity": "error"
    }
  ],
  "summary": {
    "scanned_sections": 12,
    "drift_count": 0
  }
}
```

文件位置: `/tmp/workflow_drift_report.json`（CI 收集为 artifact）。

---

## 10. 索引总览（本 Feature 新增 2 个）

| 索引 | 表 | 列 | 目的 |
|------|-----|-----|------|
| `idx_analysis_tasks_phase_step` | `analysis_tasks` | `(business_phase, business_step)` | `GET /tasks?business_phase=...&business_step=...` 筛选 + `overview` 聚合 |
| `idx_extraction_jobs_phase` | `extraction_jobs` | `(business_phase)` | `GET /extraction-jobs?business_phase=...` 筛选 |

> `video_preprocessing_jobs` / `tech_knowledge_bases` 的 `business_phase` 值恒定单一，无需建索引。

---

## 11. 不变性与回滚

- **不改变**: `analysis_tasks.status` 枚举值、`extraction_jobs.status` 枚举值、`task_type_enum`、错误码前缀总集、`diagnosis_scorer` 阈值——本 Feature 是"观测与治理"层加固，不触碰既有业务语义
- **可回滚**: `alembic downgrade -1` 在一分钟内完成列与 enum 的删除；`/business-workflow/overview` 与 `/admin/levers` 为只读接口，关闭路由即下线，无数据残留
