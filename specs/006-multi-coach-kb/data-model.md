# 数据模型: 多教练知识库提炼与技术校准

**功能**: 006-multi-coach-kb | **日期**: 2026-04-21

## 新增实体

### coaches 表

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | UUID | PK, DEFAULT gen_random_uuid() | 唯一标识 |
| name | VARCHAR(255) | NOT NULL, UNIQUE | 教练姓名，全局唯一 |
| bio | TEXT | NULL | 可选简介 |
| is_active | BOOLEAN | NOT NULL, DEFAULT true | 软删除标记 |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT now() | 创建时间 |

**索引**: `ix_coaches_name`（UNIQUE），`ix_coaches_is_active`

---

## 修改实体

### analysis_tasks 表（新增字段）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| coach_id | UUID | NULL, FK → coaches(id) ON DELETE SET NULL | 关联教练，NULL 表示历史/未指定 |

**索引**: `ix_analysis_tasks_coach_id`

**迁移文件**: `src/db/migrations/versions/0007_multi_coach_kb.py`

---

## 不修改实体

### expert_tech_points 表

无新增字段。查询时通过以下 JOIN 路径获得教练信息：

```sql
expert_tech_points ep
JOIN analysis_tasks t ON ep.source_video_id = t.id
LEFT JOIN coaches c ON t.coach_id = c.id
```

### teaching_tips 表

无新增字段。查询时通过以下 JOIN 路径获得教练信息：

```sql
teaching_tips tt
JOIN analysis_tasks t ON tt.task_id = t.id
LEFT JOIN coaches c ON t.coach_id = c.id
```

---

## 关系图

```
coaches (1)
  └──< analysis_tasks (N)  [coach_id FK, nullable]
         ├──< expert_tech_points (N)  [source_video_id FK]
         └──< teaching_tips (N)       [task_id FK]
```

---

## 校准视图（非持久化）

校准接口返回的聚合数据结构（Pydantic schema）：

### TechPointCalibrationView

```python
class CoachTechPointEntry(BaseModel):
    coach_id: UUID
    coach_name: str
    param_min: float
    param_ideal: float
    param_max: float
    unit: str
    extraction_confidence: float
    source_count: int  # 该教练在此维度的记录数

class TechPointCalibrationView(BaseModel):
    action_type: str
    dimension: str
    coaches: list[CoachTechPointEntry]
```

### TeachingTipCalibrationView

```python
class CoachTipGroup(BaseModel):
    coach_id: UUID
    coach_name: str
    tips: list[str]  # tip_text 列表

class TeachingTipCalibrationView(BaseModel):
    action_type: str
    tech_phase: str
    coaches: list[CoachTipGroup]
```

---

## 状态转换

### Coach 生命周期

```
创建 (is_active=true)
  → 修改名称/简介 (is_active=true)
  → 软删除 (is_active=false)
    ↑ 不可恢复（当前规范不要求恢复功能）
```

软删除后：
- `GET /coaches` 默认不返回（除非传 `include_inactive=true`）
- 关联的历史任务和知识库数据不受影响
- 已关联该教练的任务 `coach_id` 保留（不 SET NULL，因为是软删除）
