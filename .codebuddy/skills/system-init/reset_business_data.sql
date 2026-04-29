-- ============================================================================
-- system-init: 业务数据清理 + 配置表 seed 重置
-- ----------------------------------------------------------------------------
-- 运行方式（单事务，ON_ERROR_STOP）：
--   PGPASSWORD=password psql -h localhost -U postgres -d coaching_db \
--     -v ON_ERROR_STOP=1 -f reset_business_data.sql
--
-- 幂等性：重复执行结果一致（所有业务表 0 行 + task_channel_configs 4 行 seed）
-- 回滚：任何一步失败 → 事务整体回滚，库状态不变
-- 保留：schema、索引、外键、枚举类型、alembic_version 行
-- ============================================================================

\echo '=== system-init: begin ==='
\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------------------
-- Step 1: 清空所有业务表（CASCADE 会把 FK 依赖的子表一并清掉，顺序无关）
-- 覆盖 26 张业务/配置表；alembic_version 刻意排除
-- 若新增业务表未列入此处，SKILL.md 里的 "表清单一致性校验" 会阻断执行
-- ---------------------------------------------------------------------------
TRUNCATE TABLE
    -- 任务 / 管道 / 提取
    analysis_tasks,
    extraction_jobs,
    pipeline_steps,
    kb_conflicts,
    -- 知识库 / 技术点 / 标准
    tech_knowledge_bases,
    expert_tech_points,
    tech_standards,
    tech_standard_points,
    tech_semantic_segments,
    teaching_tips,
    -- 音频 / 诊断 / 建议
    audio_transcripts,
    athlete_motion_analyses,
    deviation_reports,
    coaching_advice,
    diagnosis_reports,
    diagnosis_dimension_results,
    -- 视频预处理
    video_preprocessing_jobs,
    video_preprocessing_segments,
    -- 参考视频 / 技能执行
    reference_videos,
    reference_video_segments,
    skills,
    skill_executions,
    -- 视频分类（两张并存表，禁止合并）
    video_classifications,
    coach_video_classifications,
    -- 教练（由 COS 扫描器自动填充）
    coaches,
    -- 通道配置（下一段会重新 seed）
    task_channel_configs
RESTART IDENTITY CASCADE;

-- ---------------------------------------------------------------------------
-- Step 2: 重建 task_channel_configs seed
--   数值来源：0012_task_pipeline_redesign.py + 0014_video_preprocessing_pipeline.py
--   若以后迁移调整默认值，此处必须同步更新
-- ---------------------------------------------------------------------------
INSERT INTO task_channel_configs
    (task_type, queue_capacity, concurrency, enabled, updated_at)
VALUES
    ('video_classification', 5,  1, TRUE, NOW()),
    ('kb_extraction',        50, 2, TRUE, NOW()),
    ('athlete_diagnosis',    20, 2, TRUE, NOW()),
    ('video_preprocessing',  20, 3, TRUE, NOW());

-- ---------------------------------------------------------------------------
-- Step 3: 校验 — 所有业务表应为 0 行；task_channel_configs 应为 4 行
--   用 DO 块 + RAISE EXCEPTION 在事务内 fail-fast，避免部分成功
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    cfg_cnt  INTEGER;
    task_cnt INTEGER;
    cvc_cnt  INTEGER;
BEGIN
    SELECT COUNT(*) INTO cfg_cnt  FROM task_channel_configs;
    SELECT COUNT(*) INTO task_cnt FROM analysis_tasks;
    SELECT COUNT(*) INTO cvc_cnt  FROM coach_video_classifications;

    IF cfg_cnt <> 4 THEN
        RAISE EXCEPTION 'task_channel_configs 应为 4 行, 实际 %', cfg_cnt;
    END IF;
    IF task_cnt <> 0 THEN
        RAISE EXCEPTION 'analysis_tasks 应为 0 行, 实际 %', task_cnt;
    END IF;
    IF cvc_cnt <> 0 THEN
        RAISE EXCEPTION 'coach_video_classifications 应为 0 行, 实际 %', cvc_cnt;
    END IF;
END $$;

COMMIT;

\echo '=== system-init: done ==='

-- ---------------------------------------------------------------------------
-- Step 4: 执行后摘要（只读，事务外）
-- ---------------------------------------------------------------------------
\echo ''
\echo '--- task_channel_configs (期望 4 行, enabled=t) ---'
SELECT task_type, queue_capacity, concurrency, enabled
FROM task_channel_configs
ORDER BY task_type;

\echo ''
\echo '--- alembic_version (保留) ---'
SELECT version_num FROM alembic_version;
