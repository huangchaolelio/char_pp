# 阶段 1 数据模型: Feature-022 业务流程四阶段化 + 内容准备阶段引入审核门

**关联**: [plan.md](./plan.md) | [spec.md](./spec.md) | [research.md](./research.md)
**日期**: 2026-05-28
**迁移文件**: `src/db/migrations/versions/0021_content_review_workflow.py`

---

## 1. 实体清单

| # | 实体 | 类型 | 说明 |
|---|------|------|------|
| E1 | `business_phase_enum` | PostgreSQL enum 扩值 | 增加 `'CONTENT_PREP'` 值 |
| E2 | `coach_video_classifications` | 既有表加列 | 4 列 + 4 索引 |
| E3 | `content_review_decisions` | 新建表 | 审核决策留痕 |
| E4 | `task_channel_configs` | 既有表加行 | 新增 `task_type='content_review_gate'` 配置行 |

---

## 2. E1 — `business_phase_enum` 扩值

```sql
ALTER TYPE business_phase_enum ADD VALUE IF NOT EXISTS 'CONTENT_PREP' BEFORE 'TRAINING';
```

**最终枚举**：`CONTENT_PREP` < `TRAINING` < `STANDARDIZATION` < `INFERENCE`

**回填**：
```sql
-- analysis_tasks 表回填
UPDATE analysis_tasks SET business_phase = 'CONTENT_PREP'
WHERE business_step IN ('scan_cos_videos', 'preprocess_video', 'classify_video', 'curate_segments');

-- video_preprocessing_jobs 表回填
UPDATE video_preprocessing_jobs SET business_phase = 'CONTENT_PREP';

-- video_curation_jobs 表（如果已加 phase 列）回填
UPDATE video_curation_jobs SET business_phase = 'CONTENT_PREP';
```

`extract_kb` / `kb_version_activate` 保持 `TRAINING`；`build_standards` 保持 `STANDARDIZATION`；`diagnose_athlete` 保持 `INFERENCE`。

---

## 3. E2 — `coach_video_classifications` 加列

### 3.1 新增列

```python
review_state: Mapped[str] = mapped_column(
    String(32), nullable=False,
    server_default="pending_review",
)
# 枚举语义（应用层校验）：pending_review / approved / rejected / stale

review_version: Mapped[int] = mapped_column(
    Integer, nullable=False, default=0, server_default="0",
)
# 乐观锁 / 审计序号；每次 review_state 变更 +1

last_decision_id: Mapped[Optional[uuid.UUID]] = mapped_column(
    UUID(as_uuid=True),
    ForeignKey("content_review_decisions.id", ondelete="SET NULL"),
    nullable=True,
)
# 指向最近一次决策；首次审核前为 NULL

pending_since: Mapped[Optional[datetime]] = mapped_column(
    TIMESTAMP(timezone=False),
    nullable=True,
)
# 进入 pending_review 状态的时刻；用于积压告警 + 平均时延统计
```

### 3.2 新增 CHECK 约束

```python
CheckConstraint(
    "review_state IN ('pending_review', 'approved', 'rejected', 'stale')",
    name="ck_cvclf_review_state",
),
```

### 3.3 新增索引（满足 FR-018）

```python
Index("idx_cvclf_review_state_decided", "review_state", "last_decision_id"),  # 默认列表
Index("idx_cvclf_coach_review", "coach_name", "review_state"),                  # 教练筛选
Index("idx_cvclf_tech_review", "tech_category", "review_state"),                # 类别筛选
# 部分索引：仅索引 pending 行，加速积压告警与平均等待时延
Index(
    "idx_cvclf_pending_since",
    "pending_since",
    postgresql_where=text("review_state = 'pending_review'"),
),
```

### 3.4 状态机

```
[初始]                         pending_review
                                  │
              (审核通过)            │  (审核拒绝)
                ┌─────────────────┼─────────────────┐
                ▼                 ▼                 ▼
            approved          rejected            stale
                │                 │                 ▲
                │ (重新清洗)        │ (重新清洗)       │
                └─────────────────┼─────────────────┘
                                  ▼
                            pending_review (重审)
```

**关键规则**：
1. `approved → stale` 由 `stale_handler` 在清洗成功回调中触发（澄清 Q3）
2. `rejected → stale` **不**触发（澄清 Q5：拒绝条目永久保留 rejected 状态；如需重审需运营手动 reset）
3. `stale → pending_review` 由 `stale_handler` 在 `pending_since = now()` 后立即写入
4. 任何状态变更必须 `review_version += 1`，且写一条 `content_review_decisions` 行（仅当变更涉及 `decision`）

---

## 4. E3 — `content_review_decisions` 新表

### 4.1 表结构

```python
class ContentReviewDecision(Base):
    __tablename__ = "content_review_decisions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    cvclf_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("coach_video_classifications.id", ondelete="CASCADE"),
        nullable=False,
    )
    cleansing_version: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("video_curation_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    # 枚举：approved / rejected
    reason_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)
    reviewer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False),
        nullable=False,
        server_default=text("timezone('Asia/Shanghai', now())"),
    )
    superseded_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=False),
        nullable=True,
    )
    # 后续决策覆盖本决策时填写（用于审计 + 重洗后批量标记）

    __table_args__ = (
        CheckConstraint(
            "decision IN ('approved', 'rejected')",
            name="ck_crd_decision",
        ),
        CheckConstraint(
            "(decision = 'rejected' AND reason_code IS NOT NULL) "
            "OR decision = 'approved'",
            name="ck_crd_rejected_requires_reason",
        ),
        Index("idx_crd_cvclf_decided", "cvclf_id", "decided_at"),         # 该条目的决策时序
        Index("idx_crd_decided_at", "decided_at"),                         # 时间窗统计
        Index("idx_crd_reviewer_decided", "reviewer_id", "decided_at"),    # 人均吞吐
    )
```

### 4.2 验证规则

- `decision = 'rejected'` 时 `reason_code` 必填（应用层 + DB CHECK 双保险）
- `reason_code` 应用层枚举（不入 DB enum 以便扩展）：
  - `quality_low` — 视频质量低 / 模糊 / 抖动严重
  - `tech_irrelevant` — 与目标技术类别不符
  - `coach_unauthorized` — 教练授权问题
  - `content_duplicated` — 与既有素材重复
  - `other` — 其他（必须配合 `note` 解释）
- `note` 限长 1000 字符（应用层校验）；`reason_code='other'` 时 `note` 必填
- `cleansing_version` 在创建时从 `coach_video_classifications.last_curation_job_id` 拷贝（保证决策与具体清洗版本绑定）
- `superseded_at` 一旦写入不可修改（应用层只允许 NULL → 时间戳）

### 4.3 关系

```python
# CoachVideoClassification 上新增反向关系
review_decisions: Mapped[list["ContentReviewDecision"]] = relationship(
    "ContentReviewDecision",
    primaryjoin="CoachVideoClassification.id == ContentReviewDecision.cvclf_id",
    cascade="all, delete-orphan",
    lazy="noload",
    order_by="ContentReviewDecision.decided_at.desc()",
)

last_decision: Mapped[Optional["ContentReviewDecision"]] = relationship(
    "ContentReviewDecision",
    primaryjoin="CoachVideoClassification.last_decision_id == ContentReviewDecision.id",
    foreign_keys=[CoachVideoClassification.last_decision_id],
    lazy="noload",
    post_update=True,  # 因循环 FK，需要 post_update
)
```

注意循环 FK（`coach_video_classifications.last_decision_id → content_review_decisions.id`，反过来 `content_review_decisions.cvclf_id → coach_video_classifications.id`）需要 `post_update=True`。

---

## 5. E4 — `task_channel_configs` 新增配置行

迁移在 `upgrade()` 末尾插入：

```python
op.execute("""
INSERT INTO task_channel_configs (task_type, queue_capacity, concurrency, enabled, updated_at)
VALUES ('content_review_gate', 0, 0, true, timezone('Asia/Shanghai', now()))
ON CONFLICT (task_type) DO NOTHING
""")
```

> **语义复用**：`enabled = true` 表示"严格审核门"（默认）；`enabled = false` 表示"绕过"模式（FR-014）
> `queue_capacity / concurrency` 字段在此 task_type 下无意义，置 0

切换接口：`PATCH /api/v1/admin/review-gate`（详见 contracts）。

---

## 6. 迁移脚本骨架

```python
# src/db/migrations/versions/0021_content_review_workflow.py
"""Feature-022 — Content review workflow + 4-phase business flow.

Revision ID: 0021_content_review_workflow
Revises: 0020_video_content_curation
Create Date: 2026-05-28
"""

revision = "0021_content_review_workflow"
down_revision = "0020_video_content_curation"

def upgrade():
    # Step 1: 扩 enum（必须在事务外执行）
    op.execute("COMMIT")
    op.execute("ALTER TYPE business_phase_enum ADD VALUE IF NOT EXISTS 'CONTENT_PREP' BEFORE 'TRAINING'")

    # Step 2: 回填既有任务行
    op.execute("""
        UPDATE analysis_tasks SET business_phase = 'CONTENT_PREP'
        WHERE business_step IN ('scan_cos_videos', 'preprocess_video', 'classify_video', 'curate_segments')
    """)
    op.execute("UPDATE video_preprocessing_jobs SET business_phase = 'CONTENT_PREP'")
    # video_curation_jobs 若有 business_phase 列也回填（条件判断）

    # Step 3: 建新表 content_review_decisions
    op.create_table(
        "content_review_decisions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("cvclf_id", sa.UUID(),
            sa.ForeignKey("coach_video_classifications.id", ondelete="CASCADE"),
            nullable=False),
        sa.Column("cleansing_version", sa.UUID(),
            sa.ForeignKey("video_curation_jobs.id", ondelete="SET NULL"),
            nullable=True),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("reviewer_id", sa.String(64), nullable=False),
        sa.Column("decided_at", sa.TIMESTAMP(),
            server_default=sa.text("timezone('Asia/Shanghai', now())"),
            nullable=False),
        sa.Column("superseded_at", sa.TIMESTAMP(), nullable=True),
        sa.CheckConstraint("decision IN ('approved', 'rejected')",
            name="ck_crd_decision"),
        sa.CheckConstraint(
            "(decision = 'rejected' AND reason_code IS NOT NULL) OR decision = 'approved'",
            name="ck_crd_rejected_requires_reason"),
    )
    op.create_index("idx_crd_cvclf_decided", "content_review_decisions",
        ["cvclf_id", "decided_at"])
    op.create_index("idx_crd_decided_at", "content_review_decisions", ["decided_at"])
    op.create_index("idx_crd_reviewer_decided", "content_review_decisions",
        ["reviewer_id", "decided_at"])

    # Step 4: coach_video_classifications 加 4 列
    op.add_column("coach_video_classifications",
        sa.Column("review_state", sa.String(32),
            server_default="pending_review", nullable=False))
    op.add_column("coach_video_classifications",
        sa.Column("review_version", sa.Integer(),
            server_default="0", nullable=False))
    op.add_column("coach_video_classifications",
        sa.Column("last_decision_id", sa.UUID(),
            sa.ForeignKey("content_review_decisions.id", ondelete="SET NULL"),
            nullable=True))
    op.add_column("coach_video_classifications",
        sa.Column("pending_since", sa.TIMESTAMP(), nullable=True))
    op.create_check_constraint(
        "ck_cvclf_review_state", "coach_video_classifications",
        "review_state IN ('pending_review', 'approved', 'rejected', 'stale')")

    # Step 5: 索引
    op.create_index("idx_cvclf_review_state_decided", "coach_video_classifications",
        ["review_state", "last_decision_id"])
    op.create_index("idx_cvclf_coach_review", "coach_video_classifications",
        ["coach_name", "review_state"])
    op.create_index("idx_cvclf_tech_review", "coach_video_classifications",
        ["tech_category", "review_state"])
    op.create_index("idx_cvclf_pending_since", "coach_video_classifications",
        ["pending_since"], postgresql_where=sa.text("review_state = 'pending_review'"))

    # Step 6: task_channel_configs 加配置行
    op.execute("""
        INSERT INTO task_channel_configs (task_type, queue_capacity, concurrency, enabled, updated_at)
        VALUES ('content_review_gate', 0, 0, true, timezone('Asia/Shanghai', now()))
        ON CONFLICT (task_type) DO NOTHING
    """)

def downgrade():
    # 注意：enum 减值需要重建 type，操作复杂；测试阶段我们采用"只前进"策略：
    # downgrade 仅回退表 / 列 / 索引 / 配置行，不回退 enum 扩值
    op.execute("DELETE FROM task_channel_configs WHERE task_type = 'content_review_gate'")
    op.drop_index("idx_cvclf_pending_since", "coach_video_classifications")
    op.drop_index("idx_cvclf_tech_review", "coach_video_classifications")
    op.drop_index("idx_cvclf_coach_review", "coach_video_classifications")
    op.drop_index("idx_cvclf_review_state_decided", "coach_video_classifications")
    op.drop_constraint("ck_cvclf_review_state", "coach_video_classifications", type_="check")
    op.drop_column("coach_video_classifications", "pending_since")
    op.drop_column("coach_video_classifications", "last_decision_id")
    op.drop_column("coach_video_classifications", "review_version")
    op.drop_column("coach_video_classifications", "review_state")
    op.drop_index("idx_crd_reviewer_decided", "content_review_decisions")
    op.drop_index("idx_crd_decided_at", "content_review_decisions")
    op.drop_index("idx_crd_cvclf_decided", "content_review_decisions")
    op.drop_table("content_review_decisions")
    # enum 不回退（测试阶段允许）
```

---

## 7. 容量与性能预估

| 维度 | 预估 | 来源 |
|------|------|------|
| `coach_video_classifications` 累计行数 | ≤ 50 万 | 澄清 Q4 |
| `content_review_decisions` 累计行数 | ≤ 100 万（每条审核条目期望 ≤ 2 次决策） | 推导 |
| 列表查询 P95 | < 500 ms（page_size ≤ 50） | FR-017 |
| 决策提交 P95 | < 200 ms | plan.md 性能目标 |
| 统计接口 30 天窗口 P95 | < 200 ms（受益于 `idx_crd_decided_at`） | research.md R7 |
| 审核门追加延迟 | < 20 ms（一次 PK lookup） | plan.md 性能目标 |

---

## 8. 数据完整性验收清单

- [ ] 迁移 `0021_content_review_workflow` 在 `alembic upgrade head` 后无报错
- [ ] 既有 `coach_video_classifications` 行均成功回填默认 `review_state='pending_review'`
- [ ] `analysis_tasks.business_phase` 在 4 个 step 上正确归属为 `CONTENT_PREP`
- [ ] `content_review_decisions` 表 + 3 个索引创建成功
- [ ] `task_channel_configs` 中存在 `content_review_gate` 行且 `enabled=true`
- [ ] `business_phase_enum` 4 个值齐全且顺序正确
- [ ] 循环 FK（`coach_video_classifications.last_decision_id` ↔ `content_review_decisions.cvclf_id`）级联行为正确（手工 INSERT/DELETE 测试）
