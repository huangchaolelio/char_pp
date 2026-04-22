-- =============================================================================
-- F 类: 知识库版本管理 & 综合统计
-- 文件: F-kb-versions-overview.sql
-- 用途: 知识库版本管理、全局概览、跨类别对比
-- =============================================================================


-- -----------------------------------------------------------------------------
-- F1: 所有知识库版本状态
-- -----------------------------------------------------------------------------
\echo '=== F1: 知识库版本列表 ==='

SELECT
    version                     AS "版本号",
    status                      AS "状态",
    point_count                 AS "要点数",
    array_length(action_types_covered, 1) AS "覆盖技术类别数",
    action_types_covered        AS "覆盖类别列表",
    approved_by                 AS "审核人",
    approved_at                 AS "审核时间",
    created_at                  AS "创建时间",
    notes                       AS "备注"
FROM tech_knowledge_bases
ORDER BY created_at DESC;


-- -----------------------------------------------------------------------------
-- F2: 全局 KB 提取进度汇总（所有技术类别）
-- 返回: 一张表展示所有类别的完整提取状态
-- -----------------------------------------------------------------------------
\echo ''
\echo '=== F2: 全局 KB 提取进度汇总 ==='

SELECT
    c.tech_category                                         AS "技术类别",
    COUNT(*)                                                AS "总视频",
    SUM(CASE WHEN c.kb_extracted THEN 1 ELSE 0 END)        AS "已提取",
    SUM(CASE WHEN NOT c.kb_extracted THEN 1 ELSE 0 END)    AS "待提取",
    ROUND(
        SUM(CASE WHEN c.kb_extracted THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1
    )                                                       AS "完成率%",
    COUNT(DISTINCT tt.id)                                   AS "教学建议总数"
FROM coach_video_classifications c
LEFT JOIN LATERAL (
    SELECT id FROM analysis_tasks
    WHERE video_storage_uri = c.cos_object_key AND deleted_at IS NULL
    ORDER BY created_at DESC LIMIT 1
) a ON true
LEFT JOIN teaching_tips tt ON tt.task_id = a.id AND tt.confidence >= 0.9
GROUP BY c.tech_category
ORDER BY "总视频" DESC;


-- -----------------------------------------------------------------------------
-- F3: teaching_tips 全局统计（各技术类别建议数量）
-- -----------------------------------------------------------------------------
\echo ''
\echo '=== F3: teaching_tips 各技术类别建议数统计 ==='

SELECT
    c.tech_category             AS "技术类别",
    COUNT(*)                    AS "建议总数",
    COUNT(DISTINCT c.coach_name) AS "覆盖教练数",
    ROUND(AVG(tt.confidence), 3) AS "平均置信度",
    SUM(CASE WHEN tt.confidence >= 0.9 THEN 1 ELSE 0 END)  AS "高置信度(>=0.9)",
    SUM(CASE WHEN tt.confidence < 0.9  THEN 1 ELSE 0 END)  AS "低置信度(<0.9)"
FROM teaching_tips tt
JOIN analysis_tasks a ON tt.task_id = a.id
JOIN coach_video_classifications c ON c.cos_object_key = a.video_storage_uri
GROUP BY c.tech_category
ORDER BY "建议总数" DESC;


-- -----------------------------------------------------------------------------
-- F4: 数据完整性检查（无法关联分类的孤立任务）
-- 用途: 排查 video_storage_uri 与 cos_object_key 不匹配的异常记录
-- -----------------------------------------------------------------------------
\echo ''
\echo '=== F4: 孤立任务检查（无对应分类记录）==='

SELECT
    a.id                        AS "任务ID",
    a.video_filename            AS "视频文件名",
    a.video_storage_uri         AS "存储路径",
    a.status                    AS "状态",
    a.created_at                AS "创建时间"
FROM analysis_tasks a
LEFT JOIN coach_video_classifications c ON c.cos_object_key = a.video_storage_uri
WHERE c.id IS NULL
  AND a.deleted_at IS NULL
  AND a.task_type = 'expert_video'
ORDER BY a.created_at DESC
LIMIT 20;


-- -----------------------------------------------------------------------------
-- F5: 各教练全链路完整性摘要
-- 用途: 快速了解所有教练的数据完整度
-- -----------------------------------------------------------------------------
\echo ''
\echo '=== F5: 各教练全链路完整性摘要 ==='

SELECT
    c.coach_name                        AS "教练",
    COUNT(DISTINCT c.id)                AS "视频总数",
    COUNT(DISTINCT a.id)                AS "已建任务数",
    SUM(CASE WHEN a.status = 'success' OR a.status = 'partial_success' THEN 1 ELSE 0 END) AS "成功任务数",
    COUNT(DISTINCT at2.id)              AS "有转录数",
    COUNT(DISTINCT tt.id)               AS "教学建议总数",
    SUM(CASE WHEN c.kb_extracted THEN 1 ELSE 0 END) AS "KB已提取数"
FROM coach_video_classifications c
LEFT JOIN LATERAL (
    SELECT id, status FROM analysis_tasks
    WHERE video_storage_uri = c.cos_object_key AND deleted_at IS NULL
    ORDER BY created_at DESC LIMIT 1
) a ON true
LEFT JOIN audio_transcripts at2 ON at2.task_id = a.id
LEFT JOIN teaching_tips tt ON tt.task_id = a.id AND tt.confidence >= 0.9
GROUP BY c.coach_name
ORDER BY "视频总数" DESC;
