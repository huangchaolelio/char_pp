-- =============================================================================
-- A 类: 任务进度与状态查询
-- 文件: A-task-progress.sql
-- 用途: 查看视频分析任务的整体进度、卡死任务、失败原因、处理速率
-- 数据库: coaching_db (PostgreSQL)
-- 使用方式: psql -h 127.0.0.1 -p 5432 -U postgres -d coaching_db -f A-task-progress.sql
-- =============================================================================


-- -----------------------------------------------------------------------------
-- A1: 全局任务状态汇总
-- 返回: 各状态的任务数量和百分比
-- -----------------------------------------------------------------------------
\echo '=== A1: 全局任务状态汇总 ==='

SELECT
    status                                          AS "状态",
    COUNT(*)                                        AS "任务数",
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 1) AS "占比%"
FROM analysis_tasks
WHERE deleted_at IS NULL
GROUP BY status
ORDER BY "任务数" DESC;


-- -----------------------------------------------------------------------------
-- A2: 僵尸任务检查（processing 状态但 30 分钟无进展）
-- 返回: 卡死任务列表，按卡死时长倒序
-- REPLACE: INTERVAL '30 minutes' 可调整判断阈值
-- -----------------------------------------------------------------------------
\echo ''
\echo '=== A2: 僵尸任务检查（processing 超过 30 分钟无进展）==='

SELECT
    a.id                                                                   AS "任务ID",
    a.video_filename                                                        AS "视频文件名",
    a.started_at                                                            AS "开始时间",
    EXTRACT(EPOCH FROM (NOW() - a.started_at)) / 60                        AS "已卡死(分钟)",
    a.progress_pct                                                          AS "进度%",
    a.processed_segments || '/' || a.total_segments                        AS "段进度"
FROM analysis_tasks a
WHERE a.status = 'processing'
  AND a.deleted_at IS NULL
  AND a.started_at < NOW() - INTERVAL '30 minutes'
ORDER BY a.started_at ASC;


-- -----------------------------------------------------------------------------
-- A3: 失败/rejected 任务列表（含错误信息）
-- 返回: 失败任务及原因，关联视频分类信息
-- REPLACE: AND c.tech_category = 'forehand_topspin' 可按技术类别筛选（去掉注释启用）
-- -----------------------------------------------------------------------------
\echo ''
\echo '=== A3: 失败/rejected 任务列表 ==='

SELECT
    a.id                        AS "任务ID",
    a.status                    AS "状态",
    c.coach_name                AS "教练",
    c.tech_category             AS "技术类别",
    a.video_filename            AS "视频文件名",
    a.error_message             AS "错误信息",
    a.rejection_reason          AS "拒绝原因",
    a.created_at                AS "创建时间",
    a.completed_at              AS "完成时间"
FROM analysis_tasks a
LEFT JOIN coach_video_classifications c ON c.cos_object_key = a.video_storage_uri
WHERE a.status IN ('failed', 'rejected')
  AND a.deleted_at IS NULL
-- AND c.tech_category = 'forehand_topspin'   -- 按技术类别筛选
ORDER BY a.created_at DESC
LIMIT 50;


-- -----------------------------------------------------------------------------
-- A4: 最近 N 小时任务处理速率（每小时完成数）
-- REPLACE: INTERVAL '24 hours' 可调整时间窗口
-- -----------------------------------------------------------------------------
\echo ''
\echo '=== A4: 最近 24 小时任务处理速率（每小时）==='

SELECT
    DATE_TRUNC('hour', completed_at)    AS "小时",
    COUNT(*)                            AS "完成任务数",
    SUM(CASE WHEN status = 'success'         THEN 1 ELSE 0 END) AS "成功",
    SUM(CASE WHEN status = 'partial_success' THEN 1 ELSE 0 END) AS "部分成功",
    SUM(CASE WHEN status = 'failed'          THEN 1 ELSE 0 END) AS "失败",
    SUM(CASE WHEN status = 'rejected'        THEN 1 ELSE 0 END) AS "拒绝"
FROM analysis_tasks
WHERE completed_at > NOW() - INTERVAL '24 hours'
  AND deleted_at IS NULL
GROUP BY DATE_TRUNC('hour', completed_at)
ORDER BY "小时" DESC;


-- -----------------------------------------------------------------------------
-- A5: 指定技术类别的 KB 提取进度汇总
-- REPLACE: 'forehand_topspin' 替换为目标技术类别
-- -----------------------------------------------------------------------------
\echo ''
\echo '=== A5: 指定技术类别 KB 提取进度（示例：forehand_topspin）==='

SELECT
    tech_category                                           AS "技术类别",
    COUNT(*)                                                AS "总视频数",
    SUM(CASE WHEN kb_extracted THEN 1 ELSE 0 END)          AS "已提取",
    SUM(CASE WHEN NOT kb_extracted THEN 1 ELSE 0 END)      AS "待提取",
    ROUND(
        SUM(CASE WHEN kb_extracted THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1
    )                                                       AS "完成率%"
FROM coach_video_classifications
WHERE tech_category = 'forehand_topspin'   -- REPLACE: 目标技术类别
GROUP BY tech_category;
