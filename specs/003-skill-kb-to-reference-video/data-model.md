# 数据模型: Skill KB 到参考视频

**分支**: `003-skill-kb-to-reference-video` | **日期**: 2026-04-20

---

## 新增实体

### 1. Skill（可重复执行的提炼配置）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK, NOT NULL | 主键 |
| `name` | VARCHAR(200) | UNIQUE, NOT NULL | 技能名称（全局唯一） |
| `description` | TEXT | NULLABLE | 描述 |
| `action_types` | TEXT[] | NOT NULL | 要提炼的动作类型列表（如 `["forehand_topspin"]`） |
| `video_source_config` | JSONB | NOT NULL | `{"type": "cos_prefix"\|"task_ids", "value": "..."\|[...]}` |
| `enable_audio` | BOOLEAN | NOT NULL, default true | 是否启用音频增强提取 |
| `audio_language` | VARCHAR(10) | NOT NULL, default "zh" | 音频识别语言 |
| `extra_config` | JSONB | NOT NULL, default `{}` | 可选扩展配置（如 dimension_expectations） |
| `created_by` | VARCHAR(100) | NOT NULL | 创建者标识 |
| `is_active` | BOOLEAN | NOT NULL, default true | 软删除标志（false = 已删除） |
| `created_at` | TIMESTAMP | NOT NULL, default now() | 创建时间 |

**索引**: `name`（唯一约束）、`is_active`（过滤软删除）

---

### 2. SkillExecution（单次执行记录）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK, NOT NULL | 主键 |
| `skill_id` | UUID | FK → skills.id, NOT NULL | 所属 Skill |
| `status` | VARCHAR(20) | NOT NULL, default "pending" | pending/running/success/failed/approved/rejected |
| `skill_config_snapshot` | JSONB | NOT NULL | 执行时 Skill 配置快照（保证历史可追溯） |
| `kb_version` | VARCHAR(20) | FK → tech_knowledge_bases.version, NULLABLE | 执行成功后填充 |
| `error_message` | TEXT | NULLABLE | 失败时的错误信息 |
| `rejection_reason` | TEXT | NULLABLE | 驳回时的原因 |
| `approved_by` | VARCHAR(100) | NULLABLE | 审批人标识 |
| `approved_at` | TIMESTAMP | NULLABLE | 审批时间 |
| `created_at` | TIMESTAMP | NOT NULL, default now() | 创建时间 |
| `updated_at` | TIMESTAMP | NOT NULL, default now() | 最后更新时间 |

**状态转换**:
```
pending → running → success → approved
                 ↘ failed   ↘ rejected
```

**索引**: `(skill_id, status)`（按 skill 查询执行历史）

---

### 3. ReferenceVideo（参考视频记录）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK, NOT NULL | 主键 |
| `execution_id` | UUID | FK → skill_executions.id, UNIQUE, NOT NULL | 关联执行（1:1） |
| `kb_version` | VARCHAR(20) | FK → tech_knowledge_bases.version, NOT NULL | 对应 KB 版本 |
| `generation_status` | VARCHAR(30) | NOT NULL, default "pending" | pending/generating/completed/generation_failed |
| `cos_key` | TEXT | NULLABLE | COS 对象路径（生成完成后填充） |
| `duration_seconds` | FLOAT | NULLABLE | 视频总时长（秒） |
| `total_dimensions` | INTEGER | NOT NULL, default 0 | KB 中技术维度总数 |
| `included_dimensions` | INTEGER | NOT NULL, default 0 | 实际纳入视频的维度数（受时长上限截断） |
| `error_message` | TEXT | NULLABLE | 生成失败时的错误信息 |
| `created_at` | TIMESTAMP | NOT NULL, default now() | 创建时间 |
| `updated_at` | TIMESTAMP | NOT NULL, default now() | 最后更新时间 |

**索引**: `execution_id`（唯一约束）、`kb_version`

---

### 4. ReferenceVideoSegment（参考视频片段）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK, NOT NULL | 主键 |
| `reference_video_id` | UUID | FK → reference_videos.id, ON DELETE CASCADE, NOT NULL | 所属参考视频 |
| `sequence_order` | INTEGER | NOT NULL | 片段在最终视频中的顺序（从 1 开始） |
| `dimension` | VARCHAR(100) | NOT NULL | 技术维度名称（来自 ExpertTechPoint.dimension） |
| `label_text` | TEXT | NOT NULL | 叠加标注文字（如 `"肘部角度: 90°~120°"`） |
| `source_video_cos_key` | TEXT | NOT NULL | 原始视频 COS 路径 |
| `source_start_ms` | INTEGER | NOT NULL | 原始视频片段开始时间（毫秒） |
| `source_end_ms` | INTEGER | NOT NULL | 原始视频片段结束时间（毫秒） |
| `extraction_confidence` | FLOAT | NOT NULL | 对应 ExpertTechPoint 的提取置信度 |
| `conflict_flag` | BOOLEAN | NOT NULL, default false | 是否有冲突（显示黄色警告） |

**索引**: `reference_video_id`（级联删除查询）、`sequence_order`

---

## 实体关系图

```
Skill (1) ─────────── (N) SkillExecution
                              │ 1
                              │
                              ▼ 1
                       ReferenceVideo (1) ─── (N) ReferenceVideoSegment
                              │
                              ▼ FK
                       TechKnowledgeBase (已有)
                              │
                              ▼ FK
                       ExpertTechPoint (已有)
```

---

## 与现有实体的关系

| 现有实体 | 关系 | 说明 |
|----------|------|------|
| `TechKnowledgeBase` | SkillExecution.kb_version → version | Skill 执行产出的 KB 草稿 |
| `ExpertTechPoint` | 通过 kb_version 间接关联 | 参考视频从 ExpertTechPoint 取时间戳和置信度 |
| `AnalysisTask` | ExpertTechPoint.source_video_id → id | 获取原始视频 COS key |
| `TechSemanticSegment` | ExpertTechPoint.transcript_segment_id → id | 获取音频时间戳（Feature-002） |

---

## Alembic 迁移顺序

```
0001_initial_schema          ← AnalysisTask, ExpertTechPoint, TechKnowledgeBase
0002_audio_enhanced_kb       ← AudioTranscript, TechSemanticSegment, ExpertTechPoint 新字段
0003_skill_reference_video   ← Skill, SkillExecution, ReferenceVideo, ReferenceVideoSegment（本次）
```
