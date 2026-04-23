-- =============================================================================
-- C 类: 按教练查链路查询
-- 文件: C-coach-chain.sql
-- 用途: 按教练姓名追踪从视频分类 → 任务提交 → 音频转录 → 知识库的完整链路
-- REPLACE: 所有 '沙指导' 替换为目标教练姓名
-- =============================================================================


-- -----------------------------------------------------------------------------
-- C1: 指定教练全部视频及关联任务状态
-- 展示: 每个视频的分类信息 + 最新一次 analysis_task 的状态
-- -----------------------------------------------------------------------------
\echo '=== C1: 指定教练视频及任务状态（示例：沙指导）==='

SELECT
    c.coach_name                AS "教练",
    c.tech_category             AS "技术类别",
    c.filename                  AS "视频文件名",
    c.kb_extracted              AS "KB已提取",
    a.status                    AS "任务状态",
    a.id                        AS "任务ID",
    a.created_at                AS "任务创建时间",
    a.completed_at              AS "任务完成时间",
    a.error_message             AS "错误信息"
FROM coach_video_classifications c
LEFT JOIN LATERAL (
    -- 取每个视频最新的一条任务
    SELECT id, status, created_at, completed_at, error_message
    FROM analysis_tasks
    WHERE video_storage_uri = c.cos_object_key
      AND deleted_at IS NULL
    ORDER BY created_at DESC
    LIMIT 1
) a ON true
WHERE c.coach_name = '沙指导'          -- REPLACE: 目标教练姓名
ORDER BY c.tech_category, c.filename;


-- -----------------------------------------------------------------------------
-- C2: 指定教练的 teaching_tips（按技术类别分组）
-- 返回: 该教练所有已提取的教学建议
-- REPLACE: '沙指导' 替换为目标教练姓名
-- 可选过滤: confidence >= 0.9（高质量建议）
-- -----------------------------------------------------------------------------
\echo ''
\echo '=== C2: 指定教练 teaching_tips（示例：沙指导）==='

SELECT
    c.tech_category             AS "技术类别",
    tt.action_type              AS "动作类型",
    tt.tech_phase               AS "技术阶段",
    tt.tip_text                 AS "教学建议",
    tt.confidence               AS "置信度",
    c.filename                  AS "来源视频",
    tt.created_at               AS "提取时间"
FROM teaching_tips tt
JOIN analysis_tasks a ON tt.task_id = a.id
JOIN coach_video_classifications c ON c.cos_object_key = a.video_storage_uri
WHERE c.coach_name = '沙指导'          -- REPLACE: 目标教练姓名
  AND tt.confidence >= 0.9            -- 可调整置信度过滤阈值
ORDER BY c.tech_category, tt.tech_phase, tt.confidence DESC;


-- -----------------------------------------------------------------------------
-- C3: 指定教练各技术类别 teaching_tips 数量统计
-- -----------------------------------------------------------------------------
\echo ''
\echo '=== C3: 指定教练 teaching_tips 数量统计（示例：沙指导）==='

SELECT
    c.tech_category             AS "技术类别",
    tt.tech_phase               AS "技术阶段",
    COUNT(*)                    AS "建议数",
    ROUND(AVG(tt.confidence), 3) AS "平均置信度"
FROM teaching_tips tt
JOIN analysis_tasks a ON tt.task_id = a.id
JOIN coach_video_classifications c ON c.cos_object_key = a.video_storage_uri
WHERE c.coach_name = '沙指导'          -- REPLACE: 目标教练姓名
GROUP BY c.tech_category, tt.tech_phase
ORDER BY c.tech_category, "建议数" DESC;


-- -----------------------------------------------------------------------------
-- C4: 指定教练完整链路状态（视频 → 任务 → 转录 → KB）
-- 用途: 一次性查看某教练所有数据的完整质量状态
-- -----------------------------------------------------------------------------
\echo ''
\echo '=== C4: 指定教练完整链路状态（示例：沙指导）==='

SELECT
    c.filename                  AS "视频文件名",
    c.tech_category             AS "技术类别",
    c.kb_extracted              AS "KB已提取",
    a.status                    AS "任务状态",
    at2.quality_flag            AS "转录质量",
    jsonb_array_length(at2.sentences) AS "转录句子数",
    at2.total_duration_s        AS "音频时长(秒)",
    COUNT(DISTINCT tt.id)       AS "教学建议数"
FROM coach_video_classifications c
LEFT JOIN LATERAL (
    SELECT id, status FROM analysis_tasks
    WHERE video_storage_uri = c.cos_object_key AND deleted_at IS NULL
    ORDER BY created_at DESC LIMIT 1
) a ON true
LEFT JOIN audio_transcripts at2 ON at2.task_id = a.id
LEFT JOIN teaching_tips tt ON tt.task_id = a.id AND tt.confidence >= 0.9
WHERE c.coach_name = '沙指导'          -- REPLACE: 目标教练姓名
GROUP BY c.filename, c.tech_category, c.kb_extracted, a.status,
         at2.quality_flag, at2.sentences, at2.total_duration_s
ORDER BY c.tech_category, c.filename;
