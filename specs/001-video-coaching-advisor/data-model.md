# 数据模型: 视频教学分析与专业指导建议

**功能分支**: `001-video-coaching-advisor`
**日期**: 2026-04-17

---

## 实体关系概览

```
AnalysisTask ──────────────────────────────────────────────┐
    │ 1                                                      │
    │ 触发                                                   │
    ▼ N                                                      │
AthleteMotionAnalysis ──── 引用 ──── TechKnowledgeBase      │
    │ 1                                 │ 1                  │
    │ 产生                              │ 包含               │
    ▼ 1                                 ▼ N                  │
DeviationReport ─── 比对基础 ──── ExpertTechPoint           │
    │ 1                                                      │
    │ 驱动                                                   │
    ▼ 1                                                      │
CoachingAdvice ◄──────────────────────────────────────────┘
```

---

## 实体定义

### ExpertTechPoint（专家技术要点）

从专业教练视频中提取的单一动作维度技术描述。**不可变记录**（写入后只读，更新知识库时新增版本）。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | 唯一标识 |
| knowledge_base_version | VARCHAR(20) | NOT NULL, FK → TechKnowledgeBase.version | 所属知识库版本 |
| action_type | ENUM | NOT NULL | `forehand_topspin`（正手拉球）或 `backhand_push`（反手拨球） |
| dimension | VARCHAR(100) | NOT NULL | 技术维度名称，如 `elbow_angle`、`swing_trajectory`、`contact_timing`、`weight_transfer` |
| param_min | FLOAT | NOT NULL | 标准参数最小值（含义由 dimension 决定，如角度单位°） |
| param_max | FLOAT | NOT NULL | 标准参数最大值 |
| param_ideal | FLOAT | NOT NULL | 理想参数值（专家最优） |
| unit | VARCHAR(20) | NOT NULL | 参数单位，如 `°`、`ms`、`ratio` |
| extraction_confidence | FLOAT | NOT NULL, [0,1] | 从视频提取时的置信度 |
| source_video_id | UUID | NOT NULL, FK → AnalysisTask.id | 来源教练视频分析任务 |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW() | 创建时间 |

**唯一约束**: `(knowledge_base_version, action_type, dimension)` — 同版本、同动作、同维度只能有一个标准要点

**验证规则**:
- `param_min ≤ param_ideal ≤ param_max`
- `extraction_confidence ≥ 0.7`（低于阈值不录入知识库）

---

### TechKnowledgeBase（技术知识库）

专家技术要点的版本化集合。每次知识库更新创建新版本，历史版本保留不删除。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| version | VARCHAR(20) | PK | 语义版本号，如 `1.0.0`、`1.1.0` |
| action_types_covered | TEXT[] | NOT NULL | 本版本覆盖的动作类型列表 |
| point_count | INT | NOT NULL | 本版本包含的技术要点总数 |
| status | ENUM | NOT NULL, DEFAULT `draft` | `draft`（草稿，待专家审核）/ `active`（当前生效版本）/ `archived`（历史归档） |
| approved_by | VARCHAR(200) | NULLABLE | 审核通过的专家姓名（人工审核后填写） |
| approved_at | TIMESTAMP | NULLABLE | 审核通过时间 |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW() | 版本创建时间 |
| notes | TEXT | NULLABLE | 版本变更说明 |

**约束**: 同时只能有一个 `status = active` 的版本

**状态转换**:
```
draft ──(专家审核通过)──► active ──(新版本发布)──► archived
```

---

### AnalysisTask（分析任务）

记录一次视频分析请求的完整元数据，支持审计追溯。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | 唯一任务标识，提交后返回给调用方 |
| task_type | ENUM | NOT NULL | `expert_video`（专家视频知识提取）/ `athlete_video`（运动员偏差分析）|
| video_filename | VARCHAR(500) | NOT NULL | 原始文件名 |
| video_size_bytes | BIGINT | NOT NULL | 文件大小（字节）|
| video_duration_seconds | FLOAT | NULLABLE | 视频时长（处理后填写）|
| video_fps | FLOAT | NULLABLE | 视频帧率（处理后填写）|
| video_resolution | VARCHAR(20) | NULLABLE | 如 `1920x1080`（处理后填写）|
| video_storage_uri | VARCHAR(1000) | NOT NULL | 视频文件存储路径/URI（处理完成后可清理）|
| status | ENUM | NOT NULL, DEFAULT `pending` | `pending` / `processing` / `success` / `failed` / `rejected` |
| rejection_reason | TEXT | NULLABLE | 质量不足时的拒绝原因（status=rejected 时填写）|
| knowledge_base_version | VARCHAR(20) | NULLABLE, FK → TechKnowledgeBase.version | 分析时使用的知识库版本（athlete_video 任务填写）|
| error_message | TEXT | NULLABLE | 失败时的错误信息 |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW() | 任务创建时间 |
| started_at | TIMESTAMP | NULLABLE | 开始处理时间 |
| completed_at | TIMESTAMP | NULLABLE | 完成时间 |
| deleted_at | TIMESTAMP | NULLABLE | 用户主动删除时间（软删除，12 个月后物理清理）|

**数据保留规则**:
- 默认保留 12 个月（从 `completed_at` 起算）
- 用户主动删除时设置 `deleted_at`，视为立即不可访问
- 定时任务每日检查并物理删除 `deleted_at IS NOT NULL` 或 `completed_at < NOW() - 12 months` 的记录

---

### AthleteMotionAnalysis（运动员动作分析）

对业余运动员视频单次分析的结构化结果，每个识别到的动作片段一条记录。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | 唯一标识 |
| task_id | UUID | NOT NULL, FK → AnalysisTask.id | 所属分析任务 |
| action_type | ENUM | NOT NULL | `forehand_topspin` / `backhand_push` / `unknown`（知识库不覆盖的动作）|
| segment_start_ms | INT | NOT NULL | 动作片段在视频中的起始时间（毫秒）|
| segment_end_ms | INT | NOT NULL | 动作片段结束时间（毫秒）|
| measured_params | JSONB | NOT NULL | 各维度实测参数值，结构：`{"dimension": {"value": float, "unit": str, "confidence": float}}` |
| overall_confidence | FLOAT | NOT NULL, [0,1] | 整体分析置信度（各关键点 visibility 加权平均）|
| is_low_confidence | BOOLEAN | NOT NULL, DEFAULT FALSE | `overall_confidence < 0.7` 时为 true |
| knowledge_base_version | VARCHAR(20) | NOT NULL, FK → TechKnowledgeBase.version | 比对使用的知识库版本 |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW() | 创建时间 |

---

### DeviationReport（偏差报告）

运动员动作分析与专家标准的比对结果，每个偏差维度一条记录。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | 唯一标识 |
| analysis_id | UUID | NOT NULL, FK → AthleteMotionAnalysis.id | 所属动作分析 |
| expert_point_id | UUID | NOT NULL, FK → ExpertTechPoint.id | 比对的专家技术要点 |
| dimension | VARCHAR(100) | NOT NULL | 偏差维度（与 ExpertTechPoint.dimension 一致）|
| measured_value | FLOAT | NOT NULL | 运动员实测值 |
| ideal_value | FLOAT | NOT NULL | 专家理想值（冗余存储便于查询）|
| deviation_value | FLOAT | NOT NULL | 偏差值 = measured_value - ideal_value |
| deviation_direction | ENUM | NOT NULL | `above`（偏高）/ `below`（偏低）/ `none`（在标准范围内）|
| confidence | FLOAT | NOT NULL, [0,1] | 本条偏差的置信度（继承自动作分析）|
| is_low_confidence | BOOLEAN | NOT NULL | `confidence < 0.7` 时为 true，不得用于高可信建议 |
| is_stable_deviation | BOOLEAN | NULLABLE | 稳定性标注（需多段视频聚合后计算，NULL 表示样本不足）|
| impact_score | FLOAT | NULLABLE, [0,1] | 影响程度评分 = abs(deviation_value) 归一化到标准范围百分位 |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW() | 创建时间 |

**稳定性判断规则**（聚合查询计算，非实时字段）:
- 需同类动作（同 action_type + dimension）≥ 3 次分析记录
- 出现该偏差（deviation_direction ≠ none）的比例 ≥ 70% → `is_stable_deviation = true`
- 否则 `is_stable_deviation = false`（样本不足时为 NULL）

---

### CoachingAdvice（指导建议）

基于偏差报告生成的改进建议，每条偏差对应一条建议记录。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK | 唯一标识 |
| deviation_id | UUID | NOT NULL, FK → DeviationReport.id | 关联的偏差记录 |
| task_id | UUID | NOT NULL, FK → AnalysisTask.id | 所属分析任务（便于批量查询）|
| deviation_description | TEXT | NOT NULL | 偏差描述（如"正手拉球肘部角度偏大 15°"）|
| improvement_target | TEXT | NOT NULL | 改进目标（对应专家标准，如"肘部角度控制在 90°~110° 范围内"）|
| improvement_method | TEXT | NOT NULL | 具体改进方法描述（可操作的训练建议）|
| impact_score | FLOAT | NOT NULL, [0,1] | 影响程度评分（同 DeviationReport.impact_score，用于排序）|
| reliability_level | ENUM | NOT NULL | `high`（confidence≥0.7）/ `low`（confidence<0.7，附说明）|
| reliability_note | TEXT | NULLABLE | 低可靠度时的说明（如"该建议基于置信度不足的分析结果，仅供参考"）|
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW() | 创建时间 |

**排序规则**: 按 `impact_score DESC` 排序输出（影响程度最高的建议优先）

---

## 索引策略

```sql
-- 知识库检索
CREATE INDEX idx_expert_point_action_type ON expert_tech_points(action_type, knowledge_base_version);
CREATE INDEX idx_expert_point_dimension ON expert_tech_points(dimension);

-- 任务状态查询
CREATE INDEX idx_task_status ON analysis_tasks(status, created_at);
CREATE INDEX idx_task_deleted ON analysis_tasks(deleted_at) WHERE deleted_at IS NOT NULL;

-- 偏差稳定性聚合（多次分析）
CREATE INDEX idx_deviation_action_dim ON deviation_reports(dimension)
  INCLUDE (analysis_id, deviation_direction);

-- 建议按任务批量查询
CREATE INDEX idx_advice_task ON coaching_advice(task_id, impact_score DESC);
```

---

## 数据安全约束

- 视频文件存储路径（`video_storage_uri`）使用加密存储（数据库列加密或应用层加密）
- 用户关联数据（task_id 级别）的物理删除由定时任务负责，确保 `deleted_at IS NOT NULL` 的记录在用户删除请求后 24 小时内物理清除
- 知识库数据不属于个人隐私数据，不受 12 个月保留限制
