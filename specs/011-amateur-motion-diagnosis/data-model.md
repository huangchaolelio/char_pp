# Feature 011 数据模型

业余选手动作诊断功能的数据库表结构与实体关系说明。

---

## ER 关系图

```
tech_standards (Feature 010)
    │  id (PK)
    │  tech_category
    │  status
    │  version
    │
    ├──< tech_standard_points (Feature 010)
    │       standard_id (FK → tech_standards.id)
    │       dimension
    │       ideal / min / max / unit
    │
    └──< diagnosis_reports  [RESTRICT — 禁止删除被引用的标准]
            id (PK, UUID)
            standard_id (FK → tech_standards.id)
            tech_category
            standard_version
            video_path
            overall_score
            strengths_summary
            created_at
            │
            └──< diagnosis_dimension_results  [CASCADE DELETE]
                    id (PK, BIGSERIAL)
                    report_id (FK → diagnosis_reports.id)
                    dimension
                    measured_value / ideal_value
                    standard_min / standard_max / unit
                    score
                    deviation_level
                    deviation_direction
                    improvement_advice
```

---

## 实体详情

### DiagnosisReport（`diagnosis_reports`）

诊断请求与整体结果的主记录，每次诊断对应一行。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK, `gen_random_uuid()` | 诊断报告唯一标识，同时作为请求 ID 返回给调用方 |
| `tech_category` | VARCHAR(64) | NOT NULL | ActionType 枚举值，如 `forehand_topspin` |
| `standard_id` | BIGINT | FK→tech_standards.id RESTRICT, NOT NULL | 诊断所用技术标准 ID；RESTRICT 防止标准被误删 |
| `standard_version` | INTEGER | NOT NULL | 诊断时标准的版本号快照，保证历史可追溯 |
| `video_path` | TEXT | NOT NULL | 输入视频路径，COS URL（`cos://bucket/key`）或绝对本地路径 |
| `overall_score` | FLOAT | NOT NULL | 所有维度得分的简单均值，范围 [0, 100] |
| `strengths_summary` | TEXT | nullable | JSON 数组，记录得分为 ok 的维度名称列表 |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | 报告创建时间 |

**索引**

| 索引名 | 字段 | 用途 |
|--------|------|------|
| `idx_dr_tech_category` | `tech_category` | 按技术类型查询历史报告 |
| `idx_dr_created_at` | `created_at DESC` | 按时间倒序分页查询 |

---

### DiagnosisDimensionResult（`diagnosis_dimension_results`）

每条记录对应一个诊断维度（关节角度、轨迹比等）的测量与评分结果。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | BIGSERIAL | PK | 自增主键 |
| `report_id` | UUID | FK→diagnosis_reports.id CASCADE, NOT NULL | 所属诊断报告；报告删除时级联删除 |
| `dimension` | VARCHAR(128) | NOT NULL | 维度标识，与 `tech_standard_points.dimension` 一一对应，如 `elbow_angle` |
| `measured_value` | FLOAT | NOT NULL | 从视频中提取的实测值 |
| `ideal_value` | FLOAT | NOT NULL | 标准中的理想值（来自 `tech_standard_points.ideal`） |
| `standard_min` | FLOAT | NOT NULL | 标准下限（来自 `tech_standard_points.min`） |
| `standard_max` | FLOAT | NOT NULL | 标准上限（来自 `tech_standard_points.max`） |
| `unit` | VARCHAR(32) | nullable | 物理单位，如 `°`、`ratio`；无量纲时为 NULL |
| `score` | FLOAT | NOT NULL | 该维度得分，范围 [0, 100] |
| `deviation_level` | VARCHAR(20) | NOT NULL, CHECK IN ('ok','slight','significant') | 偏差等级 |
| `deviation_direction` | VARCHAR(10) | nullable, CHECK IN ('above','below','none') | 偏差方向；level=ok 时值为 'none' |
| `improvement_advice` | TEXT | nullable | LLM 生成的改进建议；deviation_level='ok' 时为 NULL |

**约束**

| 约束名 | 类型 | 字段 | 说明 |
|--------|------|------|------|
| `uq_ddr_report_dimension` | UNIQUE | `(report_id, dimension)` | 同一报告中每个维度只能出现一次 |

**索引**

| 索引名 | 字段 | 用途 |
|--------|------|------|
| `idx_ddr_report` | `report_id` | 按报告 ID 查询所有维度结果 |

---

## Python 枚举

```python
class DeviationLevel(str, Enum):
    ok          = "ok"           # 在标准范围内
    slight      = "slight"       # 轻微偏差
    significant = "significant"  # 显著偏差

class DeviationDirection(str, Enum):
    above = "above"   # 实测值高于标准范围
    below = "below"   # 实测值低于标准范围
    none  = "none"    # 无偏差（ok）
```

---

## 评分算法

每个维度独立计算得分，再对所有维度取均值得到 overall_score。

```
half_width = (standard_max - standard_min) / 2
center     = (standard_min + standard_max) / 2
distance   = |measured_value - center|

if distance <= half_width:
    # 在标准范围内
    deviation_level     = ok
    deviation_direction = none
    score               = 100

elif distance <= 1.5 * half_width:
    # 轻微偏差：线性插值 [100, 60]
    deviation_level = slight
    t     = (distance - half_width) / (0.5 * half_width)   # t ∈ [0,1]
    score = 100 - t * 40

else:
    # 显著偏差：线性插值 [60, 0]，超出 2.5*half_width 时截断为 0
    deviation_level = significant
    t     = (distance - 1.5 * half_width) / half_width      # t ∈ [0,1], clamped
    score = max(0.0, 60 - t * 60)

overall_score = mean(score_i for all dimensions)
```

---

## 与 Feature 010 实体的关系

| Feature 010 实体 | Feature 011 使用方式 |
|-----------------|---------------------|
| `tech_standards` | 按 `tech_category` + `status='active'` 查找当前有效标准；记录 `standard_id` 与 `standard_version` |
| `tech_standard_points` | 读取每个维度的 `ideal / min / max / unit` 用于评分；不写入、不修改 |

---

## 数据验证规则

1. `tech_category` 必须是有效的 ActionType 枚举值，共 12 种。
2. `standard_id` 引用的标准在诊断时必须处于 `status='active'` 状态；若不存在活跃标准，返回 404。
3. `overall_score`、`score` 均应在 [0.0, 100.0] 范围内；由算法保证，不另加 DB CHECK。
4. `deviation_level` 与 `deviation_direction` 须语义一致：`ok` ↔ `none`，`slight`/`significant` ↔ `above`/`below`。
5. `improvement_advice` 仅在 `deviation_level != 'ok'` 时填写；`ok` 维度必须为 NULL。
6. 同一报告内 `(report_id, dimension)` 唯一，由数据库 UNIQUE 约束保证。
