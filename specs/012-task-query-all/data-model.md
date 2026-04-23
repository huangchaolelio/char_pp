# 数据模型: 全量任务查询接口

**功能**: 012-task-query-all
**日期**: 2026-04-23

## 无新表/字段变更

本功能为纯查询功能，不新增数据库表或字段，所有数据均从现有表中聚合读取。

---

## 查询涉及的现有表

### analysis_tasks（主表）

| 字段 | 类型 | 列表返回 | 详情返回 | 说明 |
|------|------|----------|----------|------|
| id | UUID | ✓ | ✓ | 主键 |
| task_type | Enum | ✓ | ✓ | expert_video / athlete_video |
| status | Enum | ✓ | ✓ | pending/processing/success/partial_success/failed/rejected |
| video_filename | String(500) | ✓ | ✓ | 视频文件名 |
| video_storage_uri | EncryptedString | ✓ | ✓ | 存储路径（解密后返回） |
| video_size_bytes | BigInteger | — | ✓ | 文件大小 |
| video_duration_seconds | Float | ✓ | ✓ | 视频时长 |
| video_fps | Float | — | ✓ | 帧率 |
| video_resolution | String(20) | — | ✓ | 分辨率 |
| progress_pct | Float | ✓ | ✓ | 处理进度百分比 |
| total_segments | Integer | — | ✓ | 总分段数 |
| processed_segments | Integer | — | ✓ | 已处理分段数 |
| error_message | Text | ✓ | ✓ | 错误信息（若有） |
| rejection_reason | Text | — | ✓ | 拒绝原因（若有） |
| audio_fallback_reason | Text | — | ✓ | 音频回退原因（若有） |
| knowledge_base_version | String(FK) | ✓ | ✓ | 关联知识库版本 |
| coach_id | UUID(FK) | ✓ | ✓ | 关联教练 ID（若有） |
| timing_stats | JSONB | — | ✓ | 处理各阶段耗时统计 |
| created_at | TIMESTAMP(tz) | ✓ | ✓ | 创建时间 |
| started_at | TIMESTAMP(tz) | ✓ | ✓ | 处理开始时间 |
| completed_at | TIMESTAMP(tz) | ✓ | ✓ | 完成时间 |
| deleted_at | TIMESTAMP(tz) | 过滤掉 | 不返回 | 软删除标记，IS NULL 才可见 |

**筛选支持**:
- `status` IN（枚举值列表）
- `task_type` =
- `coach_id` =
- `created_at` BETWEEN `created_after` AND `created_before`

**排序支持**:
- `ORDER BY created_at [ASC|DESC]`（默认 DESC）
- `ORDER BY completed_at [ASC|DESC] NULLS LAST`

---

### coaches（JOIN 获取教练姓名）

| 字段 | 用途 |
|------|------|
| id | JOIN 条件 analysis_tasks.coach_id = coaches.id |
| name | 列表和详情均返回教练姓名 |

LEFT JOIN，若教练记录不存在则 coach_name 返回 null。

---

### 关联统计摘要（仅详情端点聚合）

| 来源表 | 统计字段 | 聚合方式 |
|--------|----------|----------|
| expert_tech_points | tech_point_count | COUNT(*) WHERE source_video_id = task_id |
| audio_transcript | has_transcript | EXISTS WHERE task_id = task_id |
| tech_semantic_segments | semantic_segment_count | COUNT(*) WHERE task_id = task_id |
| athlete_motion_analyses | motion_analysis_count | COUNT(*) WHERE task_id = task_id |
| deviation_reports（via athlete_motion_analyses） | deviation_count | COUNT(*) via subquery |
| coaching_advice | advice_count | COUNT(*) WHERE task_id = task_id |

---

## 新增 Pydantic Schema（仅响应层）

### TaskListItemResponse（列表轻量版）

```
task_id: UUID
task_type: TaskType
status: TaskStatus
video_filename: str
video_storage_uri: str          # 解密后的存储路径
video_duration_seconds: float | None
progress_pct: float | None
error_message: str | None
knowledge_base_version: str | None
coach_id: UUID | None
coach_name: str | None
created_at: datetime
started_at: datetime | None
completed_at: datetime | None
```

### TaskListResponse（列表分页包装）

```
items: list[TaskListItemResponse]
total: int                      # 总记录数
page: int                       # 当前页码（从 1 开始）
page_size: int                  # 实际使用的每页大小（截断后）
total_pages: int                # 总页数
```

### TaskSummary（关联统计摘要，嵌入 TaskStatusResponse）

```
tech_point_count: int           # 提取的技术点数量（教练视频任务）
has_transcript: bool            # 是否有音频转录
semantic_segment_count: int     # 语义分段数量
motion_analysis_count: int      # 动作分析分段数量（运动员视频任务）
deviation_count: int            # 偏差报告数量
advice_count: int               # 建议条数
```

### TaskStatusResponse 扩展（新增可选字段）

在现有字段基础上追加：
```
summary: TaskSummary | None     # 仅详情端点填充，列表端点为 None
```

---

## 分页约束

| 参数 | 类型 | 默认值 | 约束 |
|------|------|--------|------|
| page | int | 1 | ≥ 1 |
| page_size | int | 20 | 1 ≤ page_size ≤ 200，超出自动截断为 200 |
| sort_by | str | "created_at" | 枚举: created_at / completed_at |
| order | str | "desc" | 枚举: asc / desc |
| status | TaskStatus | — | 可选，枚举值 |
| task_type | TaskType | — | 可选，枚举值 |
| coach_id | UUID | — | 可选 |
| created_after | datetime | — | 可选，ISO 8601 |
| created_before | datetime | — | 可选，ISO 8601 |
