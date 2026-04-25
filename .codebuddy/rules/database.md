---
alwaysApply: false
paths: src/models/**/*.py, src/db/**/*.py
---

# 数据库模型规范

- 所有模型继承 `Base`，表名使用蛇形命名
- 关联关系必须有外键约束，索引在模型内通过 `Index(...)` 声明
- 迁移文件命名：`NNNN_描述.py`，由 Alembic 自动生成
- 禁止同步 session，统一使用 `AsyncSession`（来自 `src/db/session.py` 的 `async_session_factory`）

# 两张视频分类表并存（禁止合并）

| 表名 | 来源 | 维护者 | 说明 |
|------|------|--------|------|
| `video_classifications` | Feature-004 | `VideoClassifierService` + refresh API | yaml 规则分类，12 教练 |
| `coach_video_classifications` | Feature-008 | `CosClassificationScanner` | COS 全量，21 类技术 + `kb_extracted` 字段 |

两表职责不同，**禁止合并**。

# 教练表规则

- `coaches` 表由 `CosClassificationScanner._upsert_coach()` 自动同步，扫描时一并维护
- 一个 COS 目录 = 一个独立教练实体
- 同 base_name 的多目录：第 1 个保持原名，后续加 `_2`、`_3` 后缀
- `bio` 字段来源：COS 目录名本身；已有 bio 时不覆盖，仅在 `bio=None` 时回填

# 迁移版本

当前最新迁移：`0013_kb_extraction_pipeline`（Feature-014，新增 `extraction_jobs` / `pipeline_steps` / `kb_conflicts` 三表 + `analysis_tasks.extraction_job_id` 列）。新增模型必须创建对应迁移文件后才能上线。
