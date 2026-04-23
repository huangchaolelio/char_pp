# Data Model: Feature-005 教学建议知识库

## 新增实体

### teaching_tips 表

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | UUID | PK, default uuid4 | 主键 |
| `task_id` | UUID | FK → analysis_tasks.id, NOT NULL | 来源教练视频任务 |
| `action_type` | VARCHAR(50) | NOT NULL | 动作类型（同 ActionType 枚举值） |
| `tech_phase` | VARCHAR(30) | NOT NULL | 技术阶段：preparation/contact/follow_through/footwork/general |
| `tip_text` | TEXT | NOT NULL | 教学建议文字内容（中文） |
| `confidence` | FLOAT | NOT NULL, CHECK(0≤confidence≤1) | LLM 提炼置信度 |
| `source_type` | VARCHAR(10) | NOT NULL, DEFAULT 'auto' | 'auto'（LLM提炼）或 'human'（人工审核） |
| `original_text` | TEXT | nullable | 原始 AI 生成内容（human 状态时保留） |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | 创建时间 |
| `updated_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() | 最后更新时间 |

**索引**：
- `ix_teaching_tips_task_id` ON (task_id)
- `ix_teaching_tips_action_type` ON (action_type)
- `ix_teaching_tips_source_type` ON (source_type)

**不需要** knowledge_base_version（独立于 KB 版本审批流，Q2 澄清结论）。

---

## 修改的实体

### coaching_advice 表（不修改 schema）

`CoachingAdvice.improvement_method` 字段在生成时追加 TeachingTip 内容，格式：

```
{原有改进方法描述}

💡 教练建议：
• {tip_text_1}（来源：{task视频名}）
• {tip_text_2}（来源：{task视频名}）
```

不新增字段，通过 `AdviceGenerator` 逻辑扩展实现。

---

## 状态转换

```
TeachingTip.source_type:

  auto ──→ human   （通过 PATCH /teaching-tips/{id} 编辑后变更）
  human ──X auto   （人工审核状态不可逆退回自动）

重新触发（POST /tasks/{task_id}/extract-tips）:
  删除 source_type='auto' 的旧条目
  保留 source_type='human' 的条目（不覆盖）
  写入新 auto 条目
```

---

## 实体关系

```
AnalysisTask (1) ──→ (N) TeachingTip
AudioTranscript (1) ──→ (N) TeachingTip  [逻辑关系，不设 FK]
TeachingTip (N) ──→ [action_type match] (N) CoachingAdvice  [无 FK，运行时查询]
```
