-- =============================================================================
-- E 类: 音频转录查询
-- 文件: E-audio-transcripts.sql
-- 用途: 查看音频转录质量、内容统计、缺失排查
-- =============================================================================


-- -----------------------------------------------------------------------------
-- E1: 转录质量分布（全局）
-- 返回: ok/low_snr/silent/unsupported_language 各占多少
-- -----------------------------------------------------------------------------
\echo '=== E1: 全局转录质量分布 ==='

SELECT
    quality_flag                AS "质量标记",
    COUNT(*)                    AS "转录数",
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 1) AS "占比%"
FROM audio_transcripts
GROUP BY quality_flag
ORDER BY "转录数" DESC;


-- -----------------------------------------------------------------------------
-- E2: 指定技术类别的转录概况
-- REPLACE: 'forehand_topspin' 替换为目标技术类别
-- -----------------------------------------------------------------------------
\echo ''
\echo '=== E2: 指定技术类别转录概况（示例：forehand_topspin）==='

SELECT
    c.coach_name                AS "教练",
    c.filename                  AS "视频文件名",
    at2.quality_flag            AS "转录质量",
    at2.total_duration_s        AS "音频时长(秒)",
    jsonb_array_length(at2.sentences) AS "句子数",
    at2.snr_db                  AS "信噪比(dB)",
    at2.model_version           AS "模型版本"
FROM audio_transcripts at2
JOIN analysis_tasks a ON at2.task_id = a.id
JOIN coach_video_classifications c ON c.cos_object_key = a.video_storage_uri
WHERE c.tech_category = 'forehand_topspin'  -- REPLACE: 目标技术类别
ORDER BY c.coach_name, c.filename;


-- -----------------------------------------------------------------------------
-- E3: 任务成功但缺少转录的视频（排查缺失）
-- 返回: status=success 但没有 audio_transcripts 的任务
-- -----------------------------------------------------------------------------
\echo ''
\echo '=== E3: 任务成功但缺少转录记录 ==='

SELECT
    a.id                        AS "任务ID",
    c.coach_name                AS "教练",
    c.tech_category             AS "技术类别",
    a.video_filename            AS "视频文件名",
    a.audio_fallback_reason     AS "音频降级原因",
    a.completed_at              AS "完成时间"
FROM analysis_tasks a
JOIN coach_video_classifications c ON c.cos_object_key = a.video_storage_uri
LEFT JOIN audio_transcripts at2 ON at2.task_id = a.id
WHERE a.status IN ('success', 'partial_success')
  AND a.deleted_at IS NULL
  AND at2.id IS NULL
ORDER BY a.completed_at DESC;


-- -----------------------------------------------------------------------------
-- E4: 指定任务的转录详情（查看句子内容）
-- REPLACE: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx' 替换为目标任务 ID
-- -----------------------------------------------------------------------------
\echo ''
\echo '=== E4: 指定任务转录详情（REPLACE task_id）==='

SELECT
    at2.id                      AS "转录ID",
    at2.quality_flag            AS "质量标记",
    at2.total_duration_s        AS "总时长(秒)",
    at2.snr_db                  AS "信噪比(dB)",
    jsonb_array_length(at2.sentences) AS "句子总数",
    at2.sentences               AS "句子列表(JSONB)"
FROM audio_transcripts at2
WHERE at2.task_id = 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'  -- REPLACE: 目标任务ID
LIMIT 1;
