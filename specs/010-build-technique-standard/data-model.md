# 数据模型: 构建单项技术标准知识库

**功能**: 010-build-technique-standard
**日期**: 2026-04-22
**迁移文件**: `src/db/migrations/versions/0010_tech_standard.py`

---

## 新增实体

### 1. `tech_standards` 表

技术标准的版本化主记录，每个技术类别可有多个版本（历史），只有一个 active。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | BIGSERIAL | PK | 自增主键 |
| `tech_category` | VARCHAR(64) | NOT NULL | 21类技术类别 ID（与 coach_video_classifications.tech_category 一致） |
| `version` | INTEGER | NOT NULL, DEFAULT 1 | 同技术类别的版本序号，从 1 递增 |
| `status` | VARCHAR(16) | NOT NULL, DEFAULT 'active' | 枚举: active / archived |
| `source_quality` | VARCHAR(16) | NOT NULL | 枚举: multi_source（≥2教练）/ single_source（1教练） |
| `coach_count` | INTEGER | NOT NULL | 参与构建的不同教练数量 |
| `point_count` | INTEGER | NOT NULL | 参与聚合的有效技术点总数（排除 conflict_flag=true） |
| `built_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | 构建时间 |

**唯一约束**: `(tech_category, version)`

**索引**:
- `idx_ts_tech_status` ON `(tech_category, status)` — 查询某技术的 active 版本
- `idx_ts_tech_version` ON `(tech_category, version DESC)` — 按版本倒序查历史

---

### 2. `tech_standard_points` 表

技术标准的最小粒度，每条记录对应某个技术标准版本下某一维度的统计参数。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | BIGSERIAL | PK | 自增主键 |
| `standard_id` | BIGINT | FK → tech_standards.id, NOT NULL | 所属技术标准版本 |
| `dimension` | VARCHAR(128) | NOT NULL | 技术维度名称（与 ExpertTechPoint.dimension 对应） |
| `ideal` | FLOAT | NOT NULL | 建议值（中位数） |
| `min` | FLOAT | NOT NULL | 可接受最小值（P25） |
| `max` | FLOAT | NOT NULL | 可接受最大值（P75） |
| `unit` | VARCHAR(32) | | 参数单位（如 °、ms、cm），可为空 |
| `sample_count` | INTEGER | NOT NULL | 该维度参与计算的技术点数量 |
| `coach_count` | INTEGER | NOT NULL | 该维度参与计算的不同教练数量 |

**唯一约束**: `(standard_id, dimension)`

**索引**:
- `idx_tsp_standard` ON `(standard_id)` — 按标准版本查所有维度
- `idx_tsp_dimension` ON `(dimension)` — 跨标准按维度查询（可选，供分析使用）

---

## 现有实体（只读，作为数据源）

### `expert_tech_points`（已存在）

| 关键字段 | 说明 |
|----------|------|
| `action_type` | 技术类别（与 tech_standards.tech_category 对应） |
| `dimension` | 技术维度 |
| `param_ideal` | 教练给出的建议值 |
| `param_min` | 教练给出的最小值 |
| `param_max` | 教练给出的最大值 |
| `unit` | 单位 |
| `extraction_confidence` | 提取置信度（≥0.7 才参与聚合） |
| `conflict_flag` | 音视频冲突标记（=true 则排除） |
| `coach_name` | 来源教练（用于统计教练数量） |

**聚合过滤条件**:
```sql
WHERE action_type = :tech_category
  AND extraction_confidence >= 0.7
  AND conflict_flag = FALSE
```

---

## 实体关系图

```
expert_tech_points (已存在，只读)
    │
    │ 聚合（中位数+P25/P75）
    ▼
tech_standards (新建)
    │  id (PK)
    │  tech_category, version, status
    │  source_quality, coach_count, point_count
    │
    │ 1 : N
    ▼
tech_standard_points (新建)
    │  standard_id (FK)
    │  dimension, ideal, min, max, unit
    │  sample_count, coach_count
```

---

## 状态转换

```
构建触发
    │
    ▼
[新版本创建] → status = 'active'
    │
    │ (同技术再次触发构建)
    ▼
[旧版本] → status = 'archived'
[新版本] → status = 'active'
```

- 同一 `tech_category` 同一时刻最多 1 条 `status='active'` 记录
- 构建新版本时：先将旧 active 版本 UPDATE 为 archived，再 INSERT 新版本

---

## 聚合逻辑示意

对于每个 `(tech_category, dimension)` 组合：

```python
values = [etp.param_ideal for etp in valid_points]  # 使用 param_ideal 作为代表值
ideal = median(values)           # 中位数
min_val = percentile(values, 25) # P25
max_val = percentile(values, 75) # P75
sample_count = len(values)
coach_count = len(set(etp.coach_name for etp in valid_points))
```

注：`param_ideal` 为教练给出的建议值，作为聚合的代表性数值；若需更精细聚合，可扩展为同时考虑 `param_min` 和 `param_max`（超出本功能范围）。
