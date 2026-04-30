# 研究报告 — Feature-019 KB Per-Category Lifecycle

**日期**: 2026-04-30
**阶段**: 0（设计前研究）
**规范**: [spec.md](./spec.md) · [plan.md](./plan.md)

---

## 研究动机

`spec.md` 中的澄清决议（Q1-Q5）与 `plan.md` 技术背景已解决所有 `NEEDS CLARIFICATION` 项，但仍有 **6 项实现决策**需要在阶段 0 固化依据，才能进入阶段 1 的 data-model / contracts 设计。每项研究采用 **Decision / Rationale / Alternatives considered** 三段式。

---

## R1 — 复合主键 `(tech_category, version)` 的 PostgreSQL 落地方式

**Decision**:
采用 SQLAlchemy 声明式复合主键：`__table_args__ = (PrimaryKeyConstraint('tech_category', 'version'), ...)`；Alembic 迁移用 `sa.PrimaryKeyConstraint('tech_category', 'version', name='pk_tech_kb_cat_ver')`。**单 active 强约束**通过 partial unique index 实现：

```sql
CREATE UNIQUE INDEX uq_tech_kb_active_per_category
  ON tech_knowledge_bases (tech_category)
  WHERE status = 'active';
```

**Rationale**:
- PostgreSQL partial index 是项目内已验证的"单 active"约束模式（`tech_standards.uq_ts_tech_version` + Feature-010 使用了相同思路），与 Feature-019 语义同构。
- 复合主键让所有外键列天然是"(tech_category, version)"二元组，语义清晰。
- SQLAlchemy 2.0 async 对复合主键 FK 完全支持（`ForeignKeyConstraint(['kb_tech_category', 'kb_version'], ['tech_knowledge_bases.tech_category', 'tech_knowledge_bases.version'])`）。

**Alternatives considered**:
- **代理单列主键 `id BIGSERIAL`**：易用但主键语义模糊，查询时仍要按 `(tech_category, version)` 定位，无收益。
- **保留单列 `version STRING` 主键 + 加 `tech_category` 列**：退化回老设计，不满足澄清决议 Q1。
- **应用层保证单 active（代码 if/else）**：章程原则 IX 强调"DB 层强约束"，应用层易被并发绕过，拒绝。

---

## R2 — Integer version 的递增策略

**Decision**:
按 `tech_category` 取 `MAX(version) + 1` 在同事务内产生新值，辅以 `INSERT ... ON CONFLICT DO NOTHING` + 重试一次的"乐观并发"兜底（并发率低，预计不需要 DB sequence）。

伪代码：
```python
async def create_draft_version(session, tech_category: str) -> TechKnowledgeBase:
    next_v = (await session.execute(
        select(func.coalesce(func.max(TechKnowledgeBase.version), 0) + 1)
        .where(TechKnowledgeBase.tech_category == tech_category)
    )).scalar_one()
    kb = TechKnowledgeBase(tech_category=tech_category, version=next_v, status='draft', ...)
    session.add(kb); await session.flush()
    return kb
```

**Rationale**:
- 每类别独立递增 ⇒ 不共享全局 sequence，不影响其它类别并发。
- KB 草稿产出场景预期每分钟 ≤ 1 次（extraction_job 平均 3min+），MAX+1 无并发风险。
- 若真出现并发（同一类别 2 个草稿同时 insert version=3），第二条会在复合主键 UNIQUE 上失败，捕获 IntegrityError 后重试一次。

**Alternatives considered**:
- **per-category PostgreSQL sequence**（每类别一个 `sequence_forehand_attack`）：21 个 sequence 管理复杂，违反简洁原则（章程原则 IV）。
- **数据库触发器 BEFORE INSERT 自动赋值**：把业务逻辑沉到数据库层，不利于测试与可观测，拒绝。
- **semver 字符串（旧行为）**：澄清决议 Q2 明确放弃，版本号意义由"语义版本"退化为"序号"。

---

## R3 — KB approve 事务的锁策略

**Decision**:
在 `approve_version(tech_category, version)` service 开头执行：
```sql
SELECT 1 FROM tech_knowledge_bases
  WHERE tech_category = :tc AND status = 'active'
  FOR UPDATE;
```
该行级锁作用于**目标类别的当前 active 行**。若无 active 行（本类别首次批准），锁一张"虚拟 stub 行"——改用 `pg_advisory_xact_lock(hashtext(tech_category))` 在事务级锁该类别命名空间。事务提交后锁自动释放。

**Rationale**:
- 行级 FOR UPDATE 保证"归档旧 active + 激活新 draft"两步原子、不被并发 approve 夹击。
- 首次批准场景下无 active 行可锁，用 `pg_advisory_xact_lock(hashtext(tech_category))` 兜底（项目 Feature-013 `TaskSubmissionService._advisory_lock_key` 已有同模式参考）。
- Partial unique index 是第二层保险：即使锁失效，DB 层也会拒绝第二条 active 的插入。

**Alternatives considered**:
- **表级 `LOCK TABLE tech_knowledge_bases IN SHARE ROW EXCLUSIVE`**：粒度过大，不同类别的 approve 互等，违反 Q1 的"独立"承诺。
- **完全依赖 partial unique index 兜底（不显式加锁）**：失败时返回 IntegrityError 需要业务层重试，用户体验差（第二个并发 approve 直接 500）。
- **应用层互斥锁（Redis SETNX）**：引入 Redis 依赖做事务同步，与 PostgreSQL 事务边界不一致，拒绝。

---

## R4 — KB ↔ extraction_job 双向关联落地

**Decision**:
- 正向（KB → Job）：`tech_knowledge_bases.extraction_job_id UUID NOT NULL`（已由 Feature-014 迁移 0015 奠定；本 Feature 将 NOT NULL 约束强化，不再允许 legacy null）。
- 反向（Job → KBs）：不新增物化列；`GET /api/v1/extraction-jobs/{id}` 响应里 `output_kbs` 字段通过**运行时查询** `SELECT tech_category, version, created_at FROM tech_knowledge_bases WHERE extraction_job_id = :jid` 生成。

**Rationale**:
- 双向物化会引入一致性负担（一次 insert 要改两张表）。
- 反向查询命中 `idx_tech_kb_extraction_job` 索引（已存在，Feature-014 遗产），单作业输出 KB 数 ≤ 21 条 ⇒ 查询 P99 < 5 ms。
- 简洁原则（章程原则 IV）：能从正向衍生的信息不物化。

**Alternatives considered**:
- **`extraction_jobs.output_kb_versions JSONB` 列**：反向一致性靠触发器或业务代码维护，复杂且易漂移。
- **新增关联表 `extraction_job_kb_outputs(job_id, tech_category, version)`**：多一张表与现有正向 FK 语义重复，拒绝。

---

## R5 — teaching_tips 与 KB 的绑定关系

**Decision**:
`teaching_tips` 新增两组列：
- `tech_category VARCHAR(64) NOT NULL`（主键无关，纯业务分桶）
- `kb_tech_category VARCHAR(64) NOT NULL` + `kb_version INTEGER NOT NULL` ⇒ 复合 FK → `tech_knowledge_bases(tech_category, version)`

状态联动通过 **service 层显式编排**：`knowledge_base_svc.approve_version` 在同事务内：
1. `UPDATE teaching_tips SET status='archived' WHERE (kb_tech_category, kb_version) = (tc, 当前 active.version) AND source_type='auto'`
2. `UPDATE teaching_tips SET status='active' WHERE (kb_tech_category, kb_version) = (tc, :new_version) AND source_type='auto'`

**Rationale**:
- FK 复合键 ⇒ 数据一致性由 DB 保证（KB 被删则 tips `ON DELETE CASCADE`）。
- service 层显式联动 > 数据库触发器：可观测（日志）、可测试（单测可替换）、符合章程原则 V。
- `source_type='human'` 不参与 WHERE 过滤 ⇒ 自动满足 FR-024（人工标注不被覆盖）。

**Alternatives considered**:
- **PostgreSQL 触发器 ON UPDATE tech_knowledge_bases.status**：跨业务表的触发器不利于调试与测试，拒绝。
- **引入独立 `teaching_tip_batches(batch_id, kb_ref, status)`**：澄清决议 Q3 已裁决不引入。
- **保留 `action_type` 列，不加 `tech_category`**：二者语义重复，按澄清决议明确要求删 `action_type`（FR-020）。

---

## R6 — 迁移 0017 的向后可回滚策略

**Decision**:
实现 **full upgrade + full downgrade** 两套 SQL（禁用 `DROP ... CASCADE`，遵循 FR-025；保留 DDL 可审计性）：
- `upgrade()`：先 `drop_constraint` 逐个摘除 5 张 FK 引用表指向 `tech_knowledge_bases` 的外键，再 `drop_table('tech_knowledge_bases')` → 重建新 schema（复合主键 `(tech_category, version)`）→ 为 5 张 FK 引用表 `alter_column/drop_column/add_column/create_foreign_key` 重建复合外键。同时 `alter_column teaching_tips drop_column action_type / add_column tech_category / kb_tech_category / kb_version / status`。
- `downgrade()`：反向执行——显式 `drop_constraint` 所有复合 FK → `drop_table('tech_knowledge_bases')` → 按 Feature-014/016 老 schema 重建（单列 `version STRING` 主键），FK 表的复合列 DROP 后 ADD 回 `knowledge_base_version VARCHAR`。

**Rationale**:
- 章程要求"每个迁移必须可 downgrade"（附加约束：迁移生成约定）。
- 系统未上线 → downgrade 不保数据，仅保 schema 还原（`system-init` skill 配合清库）。
- 整个迁移可通过 `alembic upgrade head && alembic downgrade -1` 来回执行 3 次验证幂等（SC-006）。

**Alternatives considered**:
- **多 step 增量迁移 `0017a / 0017b / 0017c`**：过度工程，系统未上线单迁移更清晰。
- **不写 downgrade（pass）**：违反章程，拒绝。

---

## R7 — API 契约与已有 v1.4.0 信封的对齐

**Decision**:
所有新/改 API 响应通过 `SuccessEnvelope[T]` + `ok(data)` / `page(items, ...)` 构造器封装。错误统一抛 `AppException(ErrorCode.XXX, details=...)` 由 `src/api/errors.py::register_exception_handlers` 转信封。

新增错误码在 `src/api/errors.py` 三张映射表登记：
```python
class ErrorCode(str, Enum):
    ...
    KB_CONFLICT_UNRESOLVED = "KB_CONFLICT_UNRESOLVED"            # 409
    KB_EMPTY_POINTS = "KB_EMPTY_POINTS"                          # 409
    NO_ACTIVE_KB_FOR_CATEGORY = "NO_ACTIVE_KB_FOR_CATEGORY"      # 409
    STANDARD_ALREADY_UP_TO_DATE = "STANDARD_ALREADY_UP_TO_DATE"  # 409

ERROR_STATUS_MAP[ErrorCode.KB_CONFLICT_UNRESOLVED] = HTTPStatus.CONFLICT
ERROR_STATUS_MAP[ErrorCode.KB_EMPTY_POINTS] = HTTPStatus.CONFLICT
ERROR_STATUS_MAP[ErrorCode.NO_ACTIVE_KB_FOR_CATEGORY] = HTTPStatus.CONFLICT
ERROR_STATUS_MAP[ErrorCode.STANDARD_ALREADY_UP_TO_DATE] = HTTPStatus.CONFLICT

ERROR_DEFAULT_MESSAGE[ErrorCode.KB_CONFLICT_UNRESOLVED] = "知识库存在未解决的冲突点"
ERROR_DEFAULT_MESSAGE[ErrorCode.KB_EMPTY_POINTS] = "知识库为空，无法批准"
ERROR_DEFAULT_MESSAGE[ErrorCode.NO_ACTIVE_KB_FOR_CATEGORY] = "该技术类别无已激活的知识库"
ERROR_DEFAULT_MESSAGE[ErrorCode.STANDARD_ALREADY_UP_TO_DATE] = "标准已是最新，无需重建"
```

**Rationale**:
- 章程原则 IX 强制；复用现有信封机制，无需引入新组件。
- 四个新 code 全是 409（业务状态冲突类），HTTP 语义一致。

**Alternatives considered**:
- **400 vs 409 的取舍**：`KB_EMPTY_POINTS` / `KB_CONFLICT_UNRESOLVED` 是"资源存在但状态不符"→ 用 409；`STANDARD_ALREADY_UP_TO_DATE` 是"已是最终态" → 409；`NO_ACTIVE_KB_FOR_CATEGORY` 是"依赖资源缺失但不是 404" → 409。全部统一 409 便于客户端分流。
- **使用已有 `KB_VERSION_NOT_DRAFT` 覆盖"冲突/空集"**：语义不同（旧 code 只管"状态不是 draft"），不应复用。

---

## 汇总：阶段 0 完成判据

| 判据 | 状态 |
|---|---|
| 所有 NEEDS CLARIFICATION 已解决 | ✅（澄清阶段 Q1-Q5 已裁决） |
| 每个依赖技术选型有 Decision / Rationale / Alternatives 记录 | ✅（R1-R7 七份） |
| 涉及 DB 约束强度的决策落到 partial unique index / 行锁两层保险 | ✅（R1 + R3） |
| 涉及并发安全性的决策识别出重试路径 | ✅（R2 乐观并发） |
| 涉及回滚的决策给出双向 SQL | ✅（R6） |
| API 契约与章程 v1.4.0 信封对齐 | ✅（R7） |

**阶段 0 结论**: 研究完成，可进入阶段 1（data-model + contracts + quickstart 设计）。
