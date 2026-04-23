-- =============================================================================
-- B 类: 视频分类统计查询
-- 文件: B-video-classification.sql
-- 用途: 查看视频分类状态、各技术类别分布、KB 提取进度、待处理列表
-- =============================================================================


-- -----------------------------------------------------------------------------
-- B1: 所有技术类别视频数及 KB 提取进度汇总
-- 返回: 21 个技术类别的视频数、已提取数、完成率
-- -----------------------------------------------------------------------------
\echo '=== B1: 所有技术类别 KB 提取进度汇总 ==='

SELECT
    tech_category                                           AS "技术类别",
    COUNT(*)                                                AS "总视频数",
    SUM(CASE WHEN kb_extracted THEN 1 ELSE 0 END)          AS "已提取",
    SUM(CASE WHEN NOT kb_extracted THEN 1 ELSE 0 END)      AS "待提取",
    ROUND(
        SUM(CASE WHEN kb_extracted THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1
    )                                                       AS "完成率%"
FROM coach_video_classifications
GROUP BY tech_category
ORDER BY "总视频数" DESC;


-- -----------------------------------------------------------------------------
-- B2: 各教练视频数及 KB 提取进度
-- 返回: 每位教练的视频总数和提取进度
-- -----------------------------------------------------------------------------
\echo ''
\echo '=== B2: 各教练视频数及 KB 提取进度 ==='

SELECT
    coach_name                                              AS "教练",
    COUNT(*)                                                AS "总视频数",
    SUM(CASE WHEN kb_extracted THEN 1 ELSE 0 END)          AS "已提取",
    SUM(CASE WHEN NOT kb_extracted THEN 1 ELSE 0 END)      AS "待提取",
    COUNT(DISTINCT tech_category)                           AS "技术类别数",
    ROUND(
        SUM(CASE WHEN kb_extracted THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1
    )                                                       AS "完成率%"
FROM coach_video_classifications
GROUP BY coach_name
ORDER BY "总视频数" DESC;


-- -----------------------------------------------------------------------------
-- B3: 指定技术类别的待提取视频列表（kb_extracted = false）
-- REPLACE: 'backhand_flick' 替换为目标技术类别
-- -----------------------------------------------------------------------------
\echo ''
\echo '=== B3: 待提取视频列表（示例：backhand_flick）==='

SELECT
    id                  AS "分类ID",
    coach_name          AS "教练",
    filename            AS "视频文件名",
    classification_source AS "分类来源",
    confidence          AS "置信度",
    created_at          AS "创建时间"
FROM coach_video_classifications
WHERE tech_category = 'backhand_flick'   -- REPLACE: 目标技术类别
  AND kb_extracted = false
ORDER BY coach_name, filename;


-- -----------------------------------------------------------------------------
-- B4: 指定技术类别下各教练视频分布
-- REPLACE: 'forehand_topspin' 替换为目标技术类别
-- -----------------------------------------------------------------------------
\echo ''
\echo '=== B4: 技术类别下各教练分布（示例：forehand_topspin）==='

SELECT
    coach_name                                              AS "教练",
    COUNT(*)                                                AS "视频数",
    SUM(CASE WHEN kb_extracted THEN 1 ELSE 0 END)          AS "已提取",
    SUM(CASE WHEN NOT kb_extracted THEN 1 ELSE 0 END)      AS "待提取"
FROM coach_video_classifications
WHERE tech_category = 'forehand_topspin'   -- REPLACE: 目标技术类别
GROUP BY coach_name
ORDER BY "视频数" DESC;


-- -----------------------------------------------------------------------------
-- B5: 分类来源分布（rule/llm/manual 各占多少）
-- 用途: 了解分类质量，llm 比例高说明规则覆盖不足
-- -----------------------------------------------------------------------------
\echo ''
\echo '=== B5: 分类来源分布 ==='

SELECT
    classification_source   AS "分类来源",
    COUNT(*)                AS "视频数",
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 1) AS "占比%"
FROM coach_video_classifications
GROUP BY classification_source
ORDER BY "视频数" DESC;
