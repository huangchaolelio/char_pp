"""Feature-018 — business_phase / business_step 双列下沉到四张业务表。

Revision ID: 0016
Revises: 0015
Create Date: 2026-04-30

五步迁移（data-model.md § 5）：
  1. 创建 ``business_phase_enum`` type
  2. 四张表 ADD COLUMN（NULL 可空，瞬时）
  3. 回填 UPDATE（单事务，当前数据量可一次完成）
  4. ALTER COLUMN SET NOT NULL
  5. 创建两个索引（analysis_tasks.phase_step / extraction_jobs.phase）

downgrade 反向五步。
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 创建 enum type
    op.execute(
        "CREATE TYPE business_phase_enum AS ENUM "
        "('TRAINING', 'STANDARDIZATION', 'INFERENCE')"
    )

    # 2. 四张表 ADD COLUMN（NULL 可空，瞬时）
    for tbl in (
        "analysis_tasks",
        "extraction_jobs",
        "video_preprocessing_jobs",
        "tech_knowledge_bases",
    ):
        op.execute(f"ALTER TABLE {tbl} ADD COLUMN business_phase business_phase_enum")
        op.execute(f"ALTER TABLE {tbl} ADD COLUMN business_step VARCHAR(64)")

    # 3. 回填（单事务）
    op.execute(
        """
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
        """
    )
    op.execute(
        "UPDATE extraction_jobs SET business_phase='TRAINING', business_step='extract_kb'"
    )
    op.execute(
        "UPDATE video_preprocessing_jobs SET business_phase='TRAINING', business_step='preprocess_video'"
    )
    op.execute(
        "UPDATE tech_knowledge_bases SET business_phase='STANDARDIZATION', business_step='kb_version_activate'"
    )

    # 4. 添加 NOT NULL 约束
    for tbl in (
        "analysis_tasks",
        "extraction_jobs",
        "video_preprocessing_jobs",
        "tech_knowledge_bases",
    ):
        op.execute(f"ALTER TABLE {tbl} ALTER COLUMN business_phase SET NOT NULL")
        op.execute(f"ALTER TABLE {tbl} ALTER COLUMN business_step SET NOT NULL")

    # 5. 索引
    op.create_index(
        "idx_analysis_tasks_phase_step",
        "analysis_tasks",
        ["business_phase", "business_step"],
    )
    op.create_index(
        "idx_extraction_jobs_phase",
        "extraction_jobs",
        ["business_phase"],
    )


def downgrade() -> None:
    op.drop_index("idx_extraction_jobs_phase", table_name="extraction_jobs")
    op.drop_index("idx_analysis_tasks_phase_step", table_name="analysis_tasks")
    for tbl in (
        "analysis_tasks",
        "extraction_jobs",
        "video_preprocessing_jobs",
        "tech_knowledge_bases",
    ):
        op.execute(f"ALTER TABLE {tbl} DROP COLUMN IF EXISTS business_step")
        op.execute(f"ALTER TABLE {tbl} DROP COLUMN IF EXISTS business_phase")
    op.execute("DROP TYPE IF EXISTS business_phase_enum")
