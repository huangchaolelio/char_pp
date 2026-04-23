-- =============================================================================
-- D 类: 按技术类别查知识库
-- 文件: D-tech-knowledge-base.sql
-- 用途: 按乒乓球技术类别查看知识库内容、教学建议、技术参数要点
-- REPLACE: 所有 'forehand_topspin' 替换为目标技术类别
-- 可用技术类别: forehand_topspin, forehand_topspin_backspin, forehand_loop_fast,
--   forehand_loop_high, forehand_flick, forehand_attack, forehand_push_long,
--   backhand_topspin, backhand_topspin_backspin, backhand_flick, backhand_push,
--   backhand_attack, serve, receive, footwork, forehand_backhand_transition,
--   defense, penhold_reverse, stance_posture, general
-- =============================================================================


-- -----------------------------------------------------------------------------
-- D1: 指定技术类别的 KB 提取进度汇总
-- -----------------------------------------------------------------------------
\echo '=== D1: 指定技术类别 KB 提取进度（示例：forehand_topspin）==='

SELECT
    coach_name                                              AS "教练",
    COUNT(*)                                                AS "总视频数",
    SUM(CASE WHEN kb_extracted THEN 1 ELSE 0 END)          AS "已提取",
    SUM(CASE WHEN NOT kb_extracted THEN 1 ELSE 0 END)      AS "待提取",
    ROUND(
        SUM(CASE WHEN kb_extracted THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1
    )                                                       AS "完成率%"
FROM coach_video_classifications
WHERE tech_category = 'forehand_topspin'   -- REPLACE: 目标技术类别
GROUP BY coach_name
ORDER BY "总视频数" DESC;


-- -----------------------------------------------------------------------------
-- D2: 指定技术类别所有 teaching_tips（按教练和技术阶段分组）
-- 返回: 高置信度（>=0.9）的教学建议，按教练分组
-- -----------------------------------------------------------------------------
\echo ''
\echo '=== D2: 指定技术类别 teaching_tips（示例：forehand_topspin，置信度>=0.9）==='

SELECT
    c.coach_name                AS "教练",
    tt.tech_phase               AS "技术阶段",
    tt.tip_text                 AS "教学建议",
    tt.confidence               AS "置信度",
    c.filename                  AS "来源视频"
FROM teaching_tips tt
JOIN analysis_tasks a ON tt.task_id = a.id
JOIN coach_video_classifications c ON c.cos_object_key = a.video_storage_uri
WHERE c.tech_category = 'forehand_topspin'  -- REPLACE: 目标技术类别
  AND tt.confidence >= 0.9
ORDER BY c.coach_name, tt.tech_phase, tt.confidence DESC;


-- -----------------------------------------------------------------------------
-- D3: 指定技术类别各教练建议数量对比
-- 用途: 快速了解哪个教练贡献了最多知识，哪个阶段最丰富
-- -----------------------------------------------------------------------------
\echo ''
\echo '=== D3: 各教练 teaching_tips 数量对比（示例：forehand_topspin）==='

SELECT
    c.coach_name                AS "教练",
    COUNT(*)                    AS "总建议数",
    SUM(CASE WHEN tt.tech_phase = 'preparation'    THEN 1 ELSE 0 END) AS "准备阶段",
    SUM(CASE WHEN tt.tech_phase = 'contact'        THEN 1 ELSE 0 END) AS "击球阶段",
    SUM(CASE WHEN tt.tech_phase = 'follow_through' THEN 1 ELSE 0 END) AS "收拍阶段",
    SUM(CASE WHEN tt.tech_phase = 'footwork'       THEN 1 ELSE 0 END) AS "步法",
    SUM(CASE WHEN tt.tech_phase = 'general'        THEN 1 ELSE 0 END) AS "综合",
    ROUND(AVG(tt.confidence), 3)                                       AS "平均置信度"
FROM teaching_tips tt
JOIN analysis_tasks a ON tt.task_id = a.id
JOIN coach_video_classifications c ON c.cos_object_key = a.video_storage_uri
WHERE c.tech_category = 'forehand_topspin'  -- REPLACE: 目标技术类别
  AND tt.confidence >= 0.9
GROUP BY c.coach_name
ORDER BY "总建议数" DESC;


-- -----------------------------------------------------------------------------
-- D4: 指定技术类别的 expert_tech_points（技术参数维度）
-- 返回: 各维度的参数范围（min/max/ideal）和置信度
-- -----------------------------------------------------------------------------
\echo ''
\echo '=== D4: 指定技术类别技术参数要点（示例：forehand_topspin）==='

SELECT
    etp.knowledge_base_version  AS "知识库版本",
    etp.dimension               AS "技术维度",
    etp.param_min               AS "最小值",
    etp.param_ideal             AS "理想值",
    etp.param_max               AS "最大值",
    etp.unit                    AS "单位",
    etp.source_type             AS "数据来源",
    etp.extraction_confidence   AS "提取置信度",
    etp.conflict_flag           AS "存在冲突"
FROM expert_tech_points etp
WHERE etp.action_type = 'forehand_topspin'  -- REPLACE: 目标技术类别
ORDER BY etp.knowledge_base_version DESC, etp.dimension;


-- -----------------------------------------------------------------------------
-- D5: 指定技术类别的知识库版本列表（最新活跃版本）
-- -----------------------------------------------------------------------------
\echo ''
\echo '=== D5: 知识库版本状态（示例：forehand_topspin）==='

SELECT
    kb.version                  AS "版本号",
    kb.status                   AS "状态",
    kb.point_count              AS "要点数",
    kb.action_types_covered     AS "覆盖动作类型",
    kb.approved_by              AS "审核人",
    kb.approved_at              AS "审核时间",
    kb.created_at               AS "创建时间",
    kb.notes                    AS "备注"
FROM tech_knowledge_bases kb
WHERE 'forehand_topspin' = ANY(kb.action_types_covered)  -- REPLACE: 目标技术类别
ORDER BY kb.created_at DESC;
