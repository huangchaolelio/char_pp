# 数据模型: 优化视频提取知识库的处理耗时

**功能**: 007-processing-speed-optimization | **日期**: 2026-04-21

## 变更概述

本功能对数据模型的变更极小：仅在现有 `analysis_tasks` 表新增一个可空 JSONB 字段用于记录耗时统计。不引入新表，不修改现有字段，完全向后兼容。

---

## 变更 1：analysis_tasks.timing_stats

### 字段定义

| 属性 | 值 |
|------|-----|
| 表名 | `analysis_tasks` |
| 字段名 | `timing_stats` |
| 类型 | `JSONB`（PostgreSQL） |
| 可空 | 是（`NULL` 表示优化前历史任务或尚未完成的任务） |
| 默认值 | `NULL` |
| 迁移文件 | `0008_add_timing_stats.py` |

### JSON 结构

```json
{
  "pre_split_s": 12.3,
  "pose_estimation_s": 180.5,
  "kb_extraction_s": 23.1,
  "total_s": 215.9
}
```

| 键 | 类型 | 含义 |
|----|------|------|
| `pre_split_s` | float | 视频预分割阶段耗时（秒），含并行等待时间 |
| `pose_estimation_s` | float | 所有片段姿态估计总耗时（秒） |
| `kb_extraction_s` | float | 知识库提炼（tech points + teaching tips）总耗时（秒） |
| `total_s` | float | 任务端到端总耗时（从 started_at 到 completed_at），秒 |

### ORM 变更

文件：`src/models/analysis_task.py`

新增字段（在 `audio_fallback_reason` 之后）：

```python
from sqlalchemy.dialects.postgresql import UUID, JSONB

# Feature 007: timing stats
timing_stats: Mapped[Optional[dict]] = mapped_column(
    JSONB, nullable=True
)
```

### Alembic 迁移

文件：`src/db/migrations/versions/0008_add_timing_stats.py`

```python
def upgrade() -> None:
    op.add_column(
        "analysis_tasks",
        sa.Column("timing_stats", postgresql.JSONB(), nullable=True),
    )

def downgrade() -> None:
    op.drop_column("analysis_tasks", "timing_stats")
```

---

## 变更 2：TaskStatusResponse schema

文件：`src/api/schemas/` 中的任务响应 schema

新增可选字段：

```python
timing_stats: Optional[dict] = None
```

API 响应示例（`GET /api/v1/tasks/{task_id}`）新增字段：

```json
{
  "id": "...",
  "status": "success",
  "timing_stats": {
    "pre_split_s": 12.3,
    "pose_estimation_s": 180.5,
    "kb_extraction_s": 23.1,
    "total_s": 215.9
  }
}
```

---

## 无变更项

- `analysis_tasks` 其他字段：不变
- `expert_tech_points`、`teaching_tips` 等关联表：不变
- 任务状态机（`TaskStatus` 枚举）：不变
- 任何 API 路由签名：不变（仅响应体新增可选字段）
