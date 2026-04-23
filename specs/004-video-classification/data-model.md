# Data Model: COS 教学视频分类体系

**功能**: 004-video-classification
**日期**: 2026-04-20

## 实体

### VideoClassification

**表名**: `video_classifications`
**主键**: `cos_object_key`（COS 完整路径，每个视频唯一）

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|------|------|------|--------|------|
| cos_object_key | String(500) | 否 | — | 主键，COS 完整路径 |
| coach_name | String(100) | 否 | — | 教练姓名，从 COS 路径解析 |
| tech_category | String(50) | 否 | — | 技术大类，如"正手技术" |
| tech_sub_category | String(50) | 是 | null | 技术中类，如"正手攻球" |
| tech_detail | String(50) | 是 | null | 技术细分，与中类相同或更精细 |
| video_type | String(20) | 否 | — | tutorial \| training |
| action_type | String(50) | 是 | null | ActionType 枚举值或 null（发球/步法等无对应枚举） |
| classification_confidence | Float | 否 | — | 0.5=无匹配 \| 0.7=大类匹配 \| 1.0=细分精确匹配 |
| manually_overridden | Boolean | 否 | false | 是否经过人工修正 |
| override_reason | Text | 是 | null | 人工修正原因 |
| classified_at | TIMESTAMP(tz) | 否 | now() | 首次分类时间 |
| updated_at | TIMESTAMP(tz) | 否 | now() | 最后更新时间 |

**索引**:
- `ix_video_classifications_tech_category` — 按大类过滤
- `ix_video_classifications_action_type` — 按 action_type 批量查询（提交任务时使用）
- `ix_video_classifications_coach_name` — 按教练过滤

**约束**:
- `video_type IN ('tutorial', 'training')`
- `classification_confidence IN (0.5, 0.7, 1.0)`
- 当 `manually_overridden = true` 时，`override_reason` 不应为 null（应用层约束）

---

## 分类树配置（YAML 结构）

**文件**: `src/config/video_classification.yaml`
**版本控制**: 由 git 管理，变更后需重新触发全量分类

```yaml
version: "1.0"

coaches:
  - name: "孙浩泓"
    cos_prefix_keywords: ["孙浩泓"]

categories:
  - id: string              # 唯一标识符
    name: string            # 显示名称
    tech_category: string   # 技术大类
    tech_sub_category: string | null
    tech_detail: string | null
    action_type: string | null  # ActionType 枚举值或 null
    require_keywords: [string]  # 文件名必须包含的关键词（AND 逻辑）
    match_keywords: [string]    # 文件名至少包含一个（OR 逻辑）
    exclude_keywords: [string]  # 文件名不能包含（优先级最高）
    confidence: float           # 命中时的置信度（默认 1.0）
```

**匹配优先级**: YAML 数组顺序即优先级，首个命中的分类规则生效。

---

## 状态转换

```
初始（无记录）
    ↓ 首次 refresh / 任务提交时懒触发
auto_classified (manually_overridden=false)
    ↓ PATCH API 人工修正
manually_overridden (manually_overridden=true)
    ↓ refresh（跳过，不覆盖）
manually_overridden（保持不变）
```

---

## 与现有模型的关系

`VideoClassification` 是独立表，通过 `cos_object_key` 与 `AnalysisTask.video_storage_uri` 关联（应用层关联，无外键约束，因为 AnalysisTask 可能引用未分类的视频）。

```
VideoClassification (cos_object_key) ←→ AnalysisTask (video_storage_uri)
                                                 [应用层关联，无 FK]
```
