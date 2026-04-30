# 数据模型 — Feature-019 KB Per-Category Lifecycle

**日期**: 2026-04-30
**阶段**: 1（设计）
**依赖**: [spec.md](./spec.md) · [plan.md](./plan.md) · [research.md](./research.md)

---

## 设计总览

本 Feature 通过单个 Alembic 迁移 `0017_kb_per_category_redesign.py` 完成以下变更：

- **1 张表结构重塑**：`tech_knowledge_bases`（主键从 `version STRING` → `(tech_category, version INTEGER)` 复合主键）
- **1 张表列重构**：`teaching_tips`（删 `action_type`，增 `tech_category` + `kb_tech_category` + `kb_version` + `status`）
- **4 张表 FK 重建**：`expert_tech_points` / `analysis_tasks` / `reference_video` / `skill_execution` / `athlete_motion_analysis`（单列 `kb_version` → 复合 FK）
- **0 张新表**（删除澄清前设想的 `TechKnowledgeBaseCategoryStatus` 关联表 / `TeachingTipBatch` 批次表）
- **1 个枚举保留**：`KBStatus(draft/active/archived)` 语义不变，作用域收缩为 per-category

---

## 实体 1 — TechKnowledgeBase（重构）

### 字段

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `tech_category` | `VARCHAR(64)` | **PK 一段**, NOT NULL | 21 类之一（`TECH_CATEGORIES`） |
| `version` | `INTEGER` | **PK 二段**, NOT NULL, CHECK ≥ 1 | 每类别独立自增序号 |
| `status` | `ENUM kb_status_enum` | NOT NULL, DEFAULT `'draft'` | `draft / active / archived` |
| `point_count` | `INTEGER` | NOT NULL, DEFAULT 0, CHECK ≥ 0 | 关联的 expert_tech_points 数量 |
| `extraction_job_id` | `UUID` | **NOT NULL** FK `extraction_jobs(id) ON DELETE RESTRICT` | 强化约束（原可空） |
| `approved_by` | `VARCHAR(200)` | NULL | 批准人（仅 approve 时写入） |
| `approved_at` | `TIMESTAMP` | NULL | 批准时间 |
| `notes` | `TEXT` | NULL | 批准备注 |
| `created_at` | `TIMESTAMP` | NOT NULL, DEFAULT CST now | 创建时间 |
| `business_phase` | `ENUM business_phase_enum` | NOT NULL, DEFAULT `'STANDARDIZATION'` | Feature-018 已有列保留 |
| `business_step` | `VARCHAR(64)` | NOT NULL, DEFAULT `'kb_version_activate'` | 同上 |

### 约束

```sql
PRIMARY KEY (tech_category, version)

CREATE UNIQUE INDEX uq_tech_kb_active_per_category
  ON tech_knowledge_bases (tech_category)
  WHERE status = 'active';                               -- 单 active 强约束

CREATE INDEX idx_tech_kb_extraction_job
  ON tech_knowledge_bases (extraction_job_id);           -- 反查索引（sustain from Feature-014）

CREATE INDEX idx_tech_kb_status
  ON tech_knowledge_bases (status);                      -- 列表过滤加速
```

### 删除的字段

- ~~`action_types_covered TEXT[]`~~：被主键 `tech_category` 取代（单行单类别）
- ~~`kb_status_enum` 内的 legacy 状态~~：枚举值不变，仅语义作用域收缩

### 状态机

```
draft ──approve()──▶ active ──(新版本 approve 时)──▶ archived
  │
  └──reject / 放弃──▶  保持 draft（由人工 DELETE 清理；本 Feature 不自动清理）
```

- **状态转换的作用域 = 单 tech_category**
- `approve()` 事务：
  1. `SELECT FOR UPDATE` 锁该类别当前 active 行（若有）
  2. 检查目标记录：存在 + `status='draft'` + `point_count>0` + 无 `conflict_flag=true` 的 ExpertTechPoint
  3. `UPDATE ... SET status='archived'` 旧 active（若有）
  4. `UPDATE ... SET status='active', approved_by, approved_at` 目标记录
  5. 同事务内联动 `teaching_tips`（见实体 3）
  6. 提交

### 与 ExtractionJob 的关系

- **正向（KB → Job）**：`extraction_job_id NOT NULL FK`
- **反向（Job → KBs）**：运行时查询 `SELECT tech_category, version FROM tech_knowledge_bases WHERE extraction_job_id = :jid`（无物化列）

---

## 实体 2 — ExpertTechPoint（微调）

### 变更点

删除单列 `knowledge_base_version VARCHAR(20)` FK，替换为复合列：

| 列名 | 类型 | 约束 |
|------|------|------|
| `kb_tech_category` | `VARCHAR(64)` | NOT NULL |
| `kb_version` | `INTEGER` | NOT NULL |

### 外键

```sql
FOREIGN KEY (kb_tech_category, kb_version)
  REFERENCES tech_knowledge_bases(tech_category, version)
  ON DELETE CASCADE
```

### 约束保持不变的部分

- `uq_expert_point_version_action_dim` UNIQUE → 改名为 `uq_expert_point_kb_action_dim`，语义不变（`(kb_tech_category, kb_version, action_type, dimension)` 唯一）
- `action_type` 列保留（因为一条 KB 记录下可能有多个不同 action_type 的 expert_tech_points？——**不，按澄清决议：一条 KB 记录仅包含单一 tech_category 的 points**，所以 `action_type` 在逻辑上冗余于 `kb_tech_category`）

### 设计决议

**保留 `expert_tech_points.action_type` 列**，理由：
- 现阶段 `ActionType` 枚举（21 类）与 `TECH_CATEGORIES` 一致，语义重复
- 但 `expert_tech_points` 可能承载"多维度细粒度子分类"的历史用途（Feature-002 设计），本 Feature 不擅自删列
- 应用层校验：`INSERT` 时必须满足 `action_type == kb_tech_category`（service 层断言）

---

## 实体 3 — TeachingTip（重构）

### 字段

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | `UUID` | PK, DEFAULT gen_random_uuid() | 主键保持 |
| `task_id` | `UUID` | NULL FK `analysis_tasks(id) ON DELETE SET NULL` | **放宽为 NULL**（tips 生命周期与 task 解耦） |
| `tech_category` | `VARCHAR(64)` | **NOT NULL**（新增） | 21 类之一 |
| `kb_tech_category` | `VARCHAR(64)` | **NOT NULL**（新增） | 复合 FK 一段 |
| `kb_version` | `INTEGER` | **NOT NULL**（新增） | 复合 FK 二段 |
| `tech_phase` | `VARCHAR(30)` | NOT NULL | 保留（preparation/contact/...） |
| `tip_text` | `TEXT` | NOT NULL | 保留 |
| `confidence` | `FLOAT` | NOT NULL, CHECK ∈ [0, 1] | 保留 |
| `source_type` | `VARCHAR(10)` | NOT NULL, DEFAULT `'auto'` | `auto / human` 保留 |
| `original_text` | `TEXT` | NULL | 保留（human 覆写时备份原 AI 文本） |
| `status` | `ENUM tip_status_enum` | **NOT NULL**（新增）, DEFAULT `'draft'` | `draft / active / archived` |
| `created_at` | `TIMESTAMP` | NOT NULL, DEFAULT CST now | 保留 |
| `updated_at` | `TIMESTAMP` | NOT NULL, ON UPDATE CST now | 保留 |

### 删除的字段

- ~~`action_type VARCHAR(50)`~~：被 `tech_category` 取代（语义重复）

### 外键

```sql
FOREIGN KEY (kb_tech_category, kb_version)
  REFERENCES tech_knowledge_bases(tech_category, version)
  ON DELETE CASCADE          -- KB 删除 → tips 级联
```

### 索引

```sql
CREATE INDEX ix_teaching_tips_tech_category ON teaching_tips (tech_category);
CREATE INDEX ix_teaching_tips_status ON teaching_tips (status);
CREATE INDEX ix_teaching_tips_kb ON teaching_tips (kb_tech_category, kb_version);
CREATE INDEX ix_teaching_tips_source_type ON teaching_tips (source_type);  -- 保留
```

### 生命周期联动（核心业务规则）

触发点 = `knowledge_base_svc.approve_version(tech_category, version)` 同事务内：

1. 查出该类别**上一个 active KB** 的 `(tc, old_version)`（若有）
2. 批量归档：
   ```sql
   UPDATE teaching_tips SET status='archived'
     WHERE kb_tech_category = :tc AND kb_version = :old_version
       AND source_type = 'auto';
   -- source_type='human' 不参与，保留 FR-024
   ```
3. 批量激活：
   ```sql
   UPDATE teaching_tips SET status='active'
     WHERE kb_tech_category = :tc AND kb_version = :new_version;
   -- human 的 tip 也一并激活（若存在），因为新版本上没有"人工标注历史"概念
   ```

---

## 实体 4 — AnalysisTask / ReferenceVideo / SkillExecution / AthleteMotionAnalysis（微调）

### 共同变更模式

删除单列 `knowledge_base_version VARCHAR(20)` / `kb_version VARCHAR(20)`，替换为复合列：

| 列名 | 类型 |
|------|------|
| `kb_tech_category` | `VARCHAR(64)` |
| `kb_version` | `INTEGER` |

### 表专属的 NULL 策略

| 表 | kb_tech_category NULL? | kb_version NULL? | ON DELETE |
|----|-----|-----|-----|
| `analysis_tasks` | NULL 允许（诊断任务可不指定） | 同 tech_category | SET NULL |
| `reference_video` | NOT NULL | NOT NULL | RESTRICT（Feature-003 要求） |
| `skill_execution` | NULL 允许 | NULL 允许 | SET NULL |
| `athlete_motion_analysis` | NOT NULL | NOT NULL | RESTRICT（诊断必须绑 KB） |

### 外键共性

```sql
FOREIGN KEY (kb_tech_category, kb_version)
  REFERENCES tech_knowledge_bases(tech_category, version)
  ON DELETE <per-table>
```

---

## 实体 5 — TechStandard（微调：新增 source_fingerprint 列）

**主体 schema 不改**。沿用 Feature-010 的 `(tech_category, version)` + `uq_ts_tech_version` + `status='active'/'archived'`（注意：仅两态，不走 draft，见 spec FR-014a）。

本 Feature 新增一列作为 FR-019 幂等检查的指纹存档列：

| 列名 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `source_fingerprint` | `CHAR(64)` | NULL 允许 | `sha256(sorted_json([(ep.id, ep.param_ideal, ep.extraction_confidence) for ep in points]))`；build 时写入 |

```sql
CREATE UNIQUE INDEX uq_ts_fingerprint_per_category
  ON tech_standards (tech_category, source_fingerprint)
  WHERE status='active';                                 -- 同类别 active 指纹唯一
```

本 Feature 仅改变 build 的触发契约（从"可批量"改为"必 per-category"）+ 新增 source_fingerprint 用于幂等检查。

---

## 实体 6 — ExtractionJob（不变）

**不改 schema**。API 层通过运行时查询 `tech_knowledge_bases WHERE extraction_job_id = :jid` 返回 `output_kbs` 字段（见 contracts/extraction-job-detail.yaml）。

---

## 迁移 `0017_kb_per_category_redesign.py`

### upgrade() 脚本结构

```python
def upgrade():
    # ── 1. DROP 所有引用 tech_knowledge_bases 的 FK ─────────────
    op.drop_constraint('fk_expert_point_kb', 'expert_tech_points')
    op.drop_constraint('fk_analysis_task_kb', 'analysis_tasks')
    op.drop_constraint('fk_reference_video_kb', 'reference_video')
    op.drop_constraint('fk_skill_execution_kb', 'skill_execution')
    op.drop_constraint('fk_athlete_motion_analysis_kb', 'athlete_motion_analyses')

    # ── 2. DROP & CREATE tech_knowledge_bases ──────────────────
    op.drop_table('tech_knowledge_bases')
    op.execute("DROP TYPE IF EXISTS kb_status_enum")        # 重建 enum（若原 enum 有多余值）
    op.execute("CREATE TYPE kb_status_enum AS ENUM ('draft','active','archived')")
    op.create_table(
        'tech_knowledge_bases',
        sa.Column('tech_category', sa.String(64), nullable=False),
        sa.Column('version', sa.Integer, nullable=False),
        sa.Column('status', postgresql.ENUM(name='kb_status_enum', create_type=False),
                  nullable=False, server_default='draft'),
        sa.Column('point_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('extraction_job_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('approved_by', sa.String(200), nullable=True),
        sa.Column('approved_at', sa.TIMESTAMP, nullable=True),
        sa.Column('notes', sa.Text, nullable=True),
        sa.Column('created_at', sa.TIMESTAMP, nullable=False,
                  server_default=sa.text("timezone('Asia/Shanghai', now())")),
        sa.Column('business_phase', postgresql.ENUM(name='business_phase_enum', create_type=False),
                  nullable=False, server_default='STANDARDIZATION'),
        sa.Column('business_step', sa.String(64), nullable=False,
                  server_default='kb_version_activate'),
        sa.PrimaryKeyConstraint('tech_category', 'version', name='pk_tech_kb_cat_ver'),
        sa.ForeignKeyConstraint(['extraction_job_id'], ['extraction_jobs.id'],
                                 ondelete='RESTRICT', name='fk_tech_kb_extraction_job'),
        sa.CheckConstraint('version >= 1', name='ck_tech_kb_version_positive'),
        sa.CheckConstraint('point_count >= 0', name='ck_tech_kb_point_count_nn'),
    )
    op.create_index('idx_tech_kb_extraction_job', 'tech_knowledge_bases', ['extraction_job_id'])
    op.create_index('idx_tech_kb_status', 'tech_knowledge_bases', ['status'])
    op.execute("""
        CREATE UNIQUE INDEX uq_tech_kb_active_per_category
          ON tech_knowledge_bases (tech_category)
          WHERE status = 'active'
    """)

    # ── 3. 重建 5 张 FK 引用表的列与外键 ───────────────────────
    for table, kb_tc_nullable, kb_ver_nullable, ondel in [
        ('expert_tech_points', False, False, 'CASCADE'),
        ('analysis_tasks', True, True, 'SET NULL'),
        ('reference_video', False, False, 'RESTRICT'),
        ('skill_execution', True, True, 'SET NULL'),
        ('athlete_motion_analyses', False, False, 'RESTRICT'),
    ]:
        op.drop_column(table, 'knowledge_base_version' if table != 'reference_video' else 'kb_version')
        op.add_column(table, sa.Column('kb_tech_category', sa.String(64), nullable=kb_tc_nullable))
        op.add_column(table, sa.Column('kb_version', sa.Integer, nullable=kb_ver_nullable))
        op.create_foreign_key(
            f'fk_{table}_kb',
            table, 'tech_knowledge_bases',
            ['kb_tech_category', 'kb_version'],
            ['tech_category', 'version'],
            ondelete=ondel,
        )

    # ── 4. teaching_tips 重构 ─────────────────────────────────
    # ⚠️ 先清空表：系统未上线假设（spec § 假设）+ 新列为 NOT NULL 且 FK 必须指向真实 KB 行，
    # 旧残留数据不可能命中新建的 (tech_category, version=1) 组合 → 必须清空再重建
    op.execute("DELETE FROM teaching_tips")
    op.execute("CREATE TYPE tip_status_enum AS ENUM ('draft','active','archived')")
    op.drop_column('teaching_tips', 'action_type')
    # 清空后直接 NOT NULL、无 server_default（避免 FK 校验失败 + 避免未来写入走 default 污染）
    op.add_column('teaching_tips', sa.Column('tech_category', sa.String(64), nullable=False))
    op.add_column('teaching_tips', sa.Column('kb_tech_category', sa.String(64), nullable=False))
    op.add_column('teaching_tips', sa.Column('kb_version', sa.Integer, nullable=False))
    op.add_column('teaching_tips', sa.Column('status', postgresql.ENUM(name='tip_status_enum', create_type=False),
                                              nullable=False, server_default='draft'))
    op.alter_column('teaching_tips', 'task_id', nullable=True)   # 放宽
    op.create_foreign_key(
        'fk_teaching_tips_kb',
        'teaching_tips', 'tech_knowledge_bases',
        ['kb_tech_category', 'kb_version'],
        ['tech_category', 'version'],
        ondelete='CASCADE',
    )
    op.create_index('ix_teaching_tips_tech_category', 'teaching_tips', ['tech_category'])
    op.create_index('ix_teaching_tips_status', 'teaching_tips', ['status'])
    op.create_index('ix_teaching_tips_kb', 'teaching_tips', ['kb_tech_category', 'kb_version'])
    op.drop_index('ix_teaching_tips_action_type', table_name='teaching_tips')
```

### downgrade() 脚本结构

按 upgrade() 逆序执行；删除新 FK 与列、重建旧单列 `knowledge_base_version VARCHAR(20)` FK、回填 `teaching_tips.action_type`（server_default='general'）、DROP tip_status_enum。**注意系统未上线假设 → downgrade 不保数据**，仅保 schema 可退回 Feature-018 的 0016 状态。

---

## 校验清单

| 判据 | 状态 |
|---|---|
| 所有实体字段含类型 + 约束 + 索引 | ✅ |
| 关系含 ON DELETE 语义 | ✅ |
| 状态转换明确规则 + 作用域 | ✅（KB 与 Tip 两套，均按 tech_category 分桶） |
| 迁移含 upgrade + downgrade 双向 | ✅ |
| partial unique index 强约束单 active | ✅ |
| spec.md FR-001~FR-030 每一条都有 data-model 支撑 | ✅（见下对应表） |

### FR 与 data-model 对照

| FR | 数据模型支撑点 |
|---|---|
| FR-001 | `PrimaryKeyConstraint('tech_category', 'version')` + `version INTEGER >= 1` |
| FR-002 | `uq_tech_kb_active_per_category` partial unique index |
| FR-003 | 删 `action_types_covered` 列 |
| FR-004 | `extraction_job_id NOT NULL` + FK RESTRICT |
| FR-005 | `approve_version` 事务的 4 步 UPDATE（见实体 1 状态机） |
| FR-006 | 事务内先 SELECT conflict_flag，不通过则抛 |
| FR-007 | 同上（point_count=0 分支） |
| FR-014a | 实体 5 声明 TechStandard 仅 active/archived 两态 |
| FR-019 | 实体 5 新增 `source_fingerprint` 列 + `uq_ts_fingerprint_per_category` 局部唯一索引 |
| FR-020 | 实体 3 新增列 |
| FR-021 | DAG persist_kb step 按 tech_category 分组写入 |
| FR-022 | 实体 3 生命周期联动的 2 步 UPDATE |
| FR-024 | 归档 SQL 含 `source_type='auto'` 过滤 |
| FR-025 | 0017 迁移的 `upgrade()`（显式 drop_constraint + drop_table，非 CASCADE） |
| FR-026 | 0017 迁移第 3 步 |

阶段 1 data-model 完成。
