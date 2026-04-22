# Data Model: 教练视频技术分类数据库 (Feature 008)

## 实体：CoachVideoClassification

**表名**: `coach_video_classifications`
**迁移版本**: 0009

### 字段定义

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `id` | UUID | PK, DEFAULT gen_random_uuid() | 主键 |
| `coach_name` | VARCHAR(100) | NOT NULL | 教练姓名（从映射配置提取） |
| `course_series` | VARCHAR(255) | NOT NULL | 课程系列名称（COS 目录名） |
| `cos_object_key` | VARCHAR(1024) | NOT NULL, UNIQUE | COS 完整路径，唯一标识 |
| `filename` | VARCHAR(255) | NOT NULL | 视频文件名（不含路径） |
| `tech_category` | VARCHAR(64) | NOT NULL | 主技术类别 ID（见枚举表） |
| `tech_tags` | TEXT[] | NOT NULL, DEFAULT '{}' | 副技术标签数组（可为空数组） |
| `raw_tech_desc` | VARCHAR(255) | NULLABLE | 从文件名提取的原始技术描述 |
| `classification_source` | VARCHAR(10) | NOT NULL, DEFAULT 'rule' | 分类来源：`rule` \| `llm` \| `manual` |
| `confidence` | FLOAT | NOT NULL, DEFAULT 1.0 | 分类置信度 0.0-1.0；rule=1.0，llm=LLM返回值，manual=1.0 |
| `duration_s` | INTEGER | NULLABLE | 视频时长（秒），COS 元数据不含时则为 NULL |
| `name_source` | VARCHAR(10) | NOT NULL, DEFAULT 'map' | 教练名来源：`map`（映射配置命中）\| `fallback`（使用目录名） |
| `kb_extracted` | BOOLEAN | NOT NULL, DEFAULT FALSE | 是否已完成知识库提取 |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | 创建时间 |
| `updated_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | 最后更新时间（人工修正时更新） |

### 索引

```sql
-- 按教练查询（用户故事 2）
CREATE INDEX idx_cvclf_coach ON coach_video_classifications(coach_name);

-- 按技术类别过滤（用户故事 3）
CREATE INDEX idx_cvclf_tech ON coach_video_classifications(tech_category);

-- 按是否已提取过滤（用户故事 3, processed=false 场景）
CREATE INDEX idx_cvclf_kb ON coach_video_classifications(kb_extracted);

-- 复合索引：教练 + 技术类别（汇总统计，用户故事 2/FR-008）
CREATE INDEX idx_cvclf_coach_tech ON coach_video_classifications(coach_name, tech_category);
```

---

## 技术类别枚举（TechCategory）

不单独建表，作为应用层枚举管理（`src/services/tech_classifier.py` 中定义）。

| tech_category ID | 中文名称 | 关键词（用于规则匹配） |
|-----------------|----------|----------------------|
| `forehand_attack` | 正手攻球 | 正手攻球、正手攻 |
| `forehand_topspin` | 正手拉球/上旋 | 正手拉球、正手上旋拉球、正手弧圈 |
| `forehand_topspin_backspin` | 正手拉下旋 | 正手拉下旋、正手下旋拉球 |
| `forehand_loop_fast` | 正手前冲弧圈 | 前冲弧圈 |
| `forehand_loop_high` | 正手高调弧圈 | 高调弧圈 |
| `forehand_push_long` | 正手劈长 | 劈长 |
| `forehand_flick` | 正手拧拉/台内挑打 | 正手拧拉、台内挑打、正手挑 |
| `backhand_attack` | 反手攻球 | 反手攻球、反手攻 |
| `backhand_topspin` | 反手拉球/上旋 | 反手拉球、反手上旋拉球 |
| `backhand_topspin_backspin` | 反手拉下旋 | 反手拉下旋、反手下旋拉球 |
| `backhand_flick` | 反手弹击/快撕 | 反手弹击、快撕、近台弹击 |
| `backhand_push` | 反手推挡/搓球 | 推挡、搓球、反手推 |
| `serve` | 发球 | 发球 |
| `receive` | 接发球 | 接发球 |
| `footwork` | 步法 | 步法、步伐、移动 |
| `forehand_backhand_transition` | 正反手转换 | 正反手转换、转换 |
| `defense` | 防守 | 防守、防弧圈、防快攻 |
| `penhold_reverse` | 直拍横打 | 直拍横打 |
| `stance_posture` | 站位/姿态 | 站位、姿态、握拍、姿势 |
| `general` | 综合/通用 | 综合、前言、总结、实战 |
| `unclassified` | 待分类 | （规则和 LLM 均无法命中时） |

---

## 状态转换

### classification_source 转换

```
[扫描时] → rule（关键词命中）
[扫描时] → llm（关键词未命中，LLM 推断）
[扫描时] → unclassified（LLM 也不确定，confidence < 0.5）
[人工修正] rule/llm/unclassified → manual（PATCH API 调用后）
```

### kb_extracted 转换

```
FALSE（初始）→ TRUE（后续知识库提取任务完成后更新）
```

---

## 数据库迁移

**文件**: `src/db/migrations/versions/0009_coach_video_classifications.py`

```python
"""Add coach_video_classifications table

Revision ID: 0009
Revises: 0008
"""
revision = "0009"
down_revision = "0008"
```

**upgrade**: 创建表 + 索引
**downgrade**: 删除表

---

## 配置文件（非数据库）

### `config/coach_directory_map.json`

目录名 → 教练姓名的静态映射，运行时由 `CosClassificationScanner` 加载。

### `config/tech_classification_rules.json`

技术类别 ID → 关键词列表的映射，运行时由 `TechClassifier` 加载。
