# 研究报告: 技术分类体系重构 (Phase 0)

**日期**: 2026-05-29  
**关联**: [plan.md](./plan.md) / [spec.md](./spec.md)

> 本文件解决 plan.md 阶段 0 列出的 3 项 NEEDS CLARIFICATION 与 5 项最佳实践调研，所有 NEEDS CLARIFICATION 在合并到 data-model.md / contracts/ 之前必须在此处闭环。

---

## 1. CSV 数据清洗策略（U+200B、"·"、空白）

### Decision

加载 `pp_book/pp_tech_classification.csv` 时执行三步清洗：

1. **去零宽空格 U+200B**：肉眼检查发现第 4 列「技术大类」中的 `进攻​` / `防御​` 实际是 `进攻` + `\u200b`（零宽空格），导致直接 `==` 比较会失败
2. **拼接 `category_l3`**：CSV 第 3 列 `手部技术`（正手/反手）+ 第 4 列 `技术大类`（发球/进攻/防御）合并为 `category_l3`，分隔符固定 `·`（U+00B7 中点），最终形态如 `正手·进攻`
3. **trim**：所有列 `.strip()` 去前后空白

### Rationale

- ZWSP 是 CSV 来源（可能从 Web/Word 复制）的常见污染，未清洗会导致 LLM enum 校验、字典查询、JOIN 全部失败
- 把 `hand` 与 `tech_class` 合并到 `category_l3` 是 spec Clarifications Q1 决议（严格四级而非五级）的**直接落地方式**：保留信息但不引入第 5 列字段
- `·` 选择 U+00B7 而非 ASCII `.`，避免与 OpenAPI 路径分隔符冲突，且符合中文出版物习惯

### Alternatives Considered

| 方案 | 拒绝理由 |
|---|---|
| 保留五列字段（`hand` 单独成列） | 与 spec FR-001 / Clarifications Q1「严格四级」直接矛盾 |
| 用 ASCII `_` 拼接 | 与 spec 验收场景示例 `category_l3=正手·进攻` 不一致 |
| 在迁移层不清洗、在应用层比较时再 strip | 字典表是事实来源，污染数据落库后无法保证一致性；应在 seed 入口清洗 |

### 落地约束

- 迁移 `0022_tech_taxonomy_rebuild.py` 内嵌 seed 函数：`with open(csv_path) as f: ... cell.replace('\u200b','').strip()`
- 单元测试 `tests/unit/services/test_action_dictionary_service.py::test_seed_strips_zwsp` 显式断言

---

## 2. 字典约束的 LLM Prompt 设计

### Decision

TechClassifierV2 的 LLM 兜底 Prompt 采用**字典内嵌 + JSON Schema 二次校验**双层约束：

```text
你是一位乒乓球技术分类专家。根据视频文件名和所属课程系列，
判断该视频教学的具体动作（action）。

候选动作字典（必须从中选一个 action，禁止编造）：
- action="平击发球" (l1=横拍, l2=反胶, l3=正手·发球)
- action="奔球"     (l1=横拍, l2=反胶, l3=正手·发球)
... （共 44 行）
- action="搓球"     (l1=横拍, l2=反胶, l3=反手·防御)

视频文件名：{filename}
课程系列：{course_series}

仅以 JSON 格式回答（必须 enum 内取值）：
{
  "action": "<上述 44 个 action 之一>",
  "confidence": 0.0,
  "reason": "一句话说明"
}

如果无法确信匹配任何 action，则 action 必须返回 "unclassified"。
只输出 JSON，不要其他内容。
```

**调用后双重校验**：
1. JSON 解析失败 → `unclassified`，confidence=0.0
2. `action` 不在 `tech_actions` 字典 44 行内 → 视为字典违规，落 WARN 日志 + `unclassified`，confidence=0.0
3. `confidence < 0.5` → `unclassified`

`category_l1/l2/l3` 不由 LLM 输出，而是分类器命中 action 后**反查 `tech_actions` 字典**自动填充（一致性保证 + token 节省）。

### Rationale

- 把 44 行字典直接嵌入 prompt 比 RAG 向量检索更可靠（44 行 ≈ 600 token，单次调用成本可接受）
- 让 LLM 只输出 `action` 而不是四元组，能减少模型出错面（模型不需要"理解"四级层级）
- 二次校验是**强字典约束**的硬保障；spec FR-004 明确"LLM 输出必须落在字典内，否则视为失败"
- `unclassified` 作为 enum 中的合法逃生选项，避免模型被迫强行匹配

### Alternatives Considered

| 方案 | 拒绝理由 |
|---|---|
| 让 LLM 输出完整四元组 | 增加输出空间复杂度，模型容易输出 l1/l2/l3 不一致；二次校验逻辑复杂 |
| 用 RAG 检索 top-K 字典再让 LLM 选 | 引入向量库依赖（违反 YAGNI），44 行规模无必要 |
| 不在 prompt 内嵌 enum，依赖 function calling | 现有 LlmClient 抽象（Venus Proxy + OpenAI fallback）未统一支持 function calling，引入会扩大改动面 |
| 命中率不足时降级层级（保留 l1/l2/l3） | 与 spec Clarifications Q1「不做层级降级」直接矛盾 |

### 落地约束

- `src/services/action_dictionary_service.py` 提供 `get_prompt_enum_block() -> str` 单元化字典文本
- `src/services/tech_classifier.py::TechClassifierV2._classify_with_llm()` 调用后二次校验
- 单元测试 `test_tech_classifier_v2.py::test_llm_returns_invalid_action_falls_back_to_unclassified` 必须 RED→GREEN

---

## 3. `tech_knowledge_bases` 复合主键改名的 FK 级联策略

### Decision

`tech_knowledge_bases` 主键 `pk_tech_kb_cat_ver(tech_category, version)` → `pk_tech_kb_action_ver(action, version)`。涉及 4 张子表外键列重命名，统一在迁移 `0022` 内**同事务执行**：

| 子表 | 原外键列 | 新外键列 | 级联策略 |
|---|---|---|---|
| `expert_tech_points` | `(kb_tech_category, kb_version)` | `(kb_action, kb_version)` | NOT NULL, ON DELETE CASCADE |
| `analysis_tasks` | `(kb_tech_category, kb_version)` | `(kb_action, kb_version)` | NULL, ON DELETE SET NULL |
| `reference_videos` | `(kb_tech_category, kb_version)` | `(kb_action, kb_version)` | NOT NULL, ON DELETE RESTRICT |
| `skill_executions` | `(kb_tech_category, kb_version)` | `(kb_action, kb_version)` | NULL, ON DELETE SET NULL |
| `athlete_motion_analyses` | `(knowledge_base_version)` 旧式或 `(kb_tech_category, kb_version)` | `(kb_action, kb_version)` | NOT NULL, ON DELETE RESTRICT |

迁移操作顺序（同事务）：
```
1. system-init 已 TRUNCATE 业务数据（迁移本身不动数据）  ← 由运维流程保证
2. ALTER TABLE 子表 DROP CONSTRAINT fk_xxx
3. ALTER TABLE tech_knowledge_bases DROP CONSTRAINT pk_tech_kb_cat_ver
4. ALTER TABLE tech_knowledge_bases RENAME COLUMN tech_category TO action
5. ALTER TABLE tech_knowledge_bases ADD CONSTRAINT pk_tech_kb_action_ver PRIMARY KEY (action, version)
6. ALTER TABLE tech_knowledge_bases ADD CONSTRAINT fk_tkb_action FOREIGN KEY (action) REFERENCES tech_actions(action) ON DELETE RESTRICT
7. ALTER TABLE 子表 RENAME COLUMN kb_tech_category TO kb_action
8. ALTER TABLE 子表 ADD CONSTRAINT fk_xxx_action FOREIGN KEY (kb_action, kb_version) REFERENCES tech_knowledge_bases(action, version) ON DELETE <原策略>
```

### Rationale

- **必须在 system-init 清场后执行**：业务表为空，FK 重建无数据冲突
- **同事务**：避免中间状态被其他连接看到（即便 worker 已停，psql 直连仍可能干扰）
- 引入新外键 `tech_knowledge_bases.action → tech_actions.action`：保证 KB 只能针对字典内 action 创建，与分类器字典约束一致
- 子表外键改名采用 RENAME COLUMN + 重建 FK，不用 DROP+ADD，保留索引

### Alternatives Considered

| 方案 | 拒绝理由 |
|---|---|
| 保留旧列名 `tech_category` 仅改语义（值变成 action 名） | 与 spec「物理删除 tech_category」直接矛盾；维护语义陷阱（同名不同义） |
| 多步迁移（先加 action 列、双写、再 drop tech_category） | 与 Clarifications Q4「单一原子迁移」直接矛盾 |
| 仅改 `tech_knowledge_bases` 主键，子表外键保留旧名 | PG 不允许 FK 列与主键列名不一致时自动跟踪 RENAME；子表会失效 |

### 落地约束

- `tests/integration/test_migration_0022_taxonomy.py` 端到端跑 upgrade + downgrade 各一次，断言所有外键存在且 PK 命名正确

---

## 4. Alembic 单一迁移内 PRIMARY KEY 重命名最佳实践

### Decision

使用 `op.execute()` 直写 SQL 完成 PK 改造，因为 Alembic op API 的 `drop_constraint` + `create_primary_key` 在跨表 FK 级联时不够灵活：

```python
def upgrade() -> None:
    # ... DROP 子表 FK ...
    op.execute("ALTER TABLE tech_knowledge_bases DROP CONSTRAINT pk_tech_kb_cat_ver")
    op.alter_column("tech_knowledge_bases", "tech_category", new_column_name="action")
    op.create_primary_key("pk_tech_kb_action_ver", "tech_knowledge_bases", ["action", "version"])
    op.create_foreign_key("fk_tkb_action", "tech_knowledge_bases", "tech_actions", ["action"], ["action"], ondelete="RESTRICT")
    # ... RENAME 子表外键列 + 重建 FK ...
```

### Rationale

- `op.alter_column(new_column_name=...)` 安全处理列改名（保留默认值、约束、索引）
- PK 重建后必须**先**为 `tech_knowledge_bases.action` 加外键到 `tech_actions`，再重建子表外键，否则字典约束链路断
- 整段在 `op.execute("BEGIN")` 内自动事务化（Alembic 默认行为）

### Alternatives Considered

- 用 `op.batch_alter_table()`：适合 SQLite，PostgreSQL 上无必要且影响性能
- 完全用 raw SQL：可读性差，不利于 downgrade 对称编写

---

## 5. PostgreSQL 字典表 + system-init TRUNCATE 协同

### Decision

`tech_actions` 字典表**从 system-init 的 TRUNCATE 清单中排除**，处理方式与现有 `task_channel_configs` 一致（字典 = 配置，由迁移 seed，运行期只读）。

`reset_business_data.sql` 需更新：

```sql
-- 文件结构
-- ──────────────────
-- ① TRUNCATE 业务表（排除 tech_actions, task_channel_configs, alembic_version）
TRUNCATE TABLE
  coach_video_classifications,
  video_classifications,
  expert_tech_points,
  tech_knowledge_bases,
  analysis_tasks,
  extraction_jobs,
  pipeline_steps,
  kb_conflicts,
  coaches,
  coach_directory_map,
  video_preprocessing_jobs,
  video_preprocessing_segments,
  -- ... 其余业务表
  RESTART IDENTITY CASCADE;

-- ② 重新 seed task_channel_configs（保持原内容）
-- ③ 不动 tech_actions（字典表，由迁移已 seed 44 行）

-- 健康校验扩展
SELECT count(*) FROM tech_actions;  -- 期望 44，否则 RAISE 'tech_actions seed 缺失'
```

`SKILL.md` 的「执行前校验」步骤新增第 4 项：「`tech_actions` 行数 = 44，否则中止并提示用户先跑 alembic upgrade」。

### Rationale

- 字典表语义稳定：被业务表外键引用，TRUNCATE 会因 FK 约束失败（除非 CASCADE，但那会清掉字典本身，违背意图）
- `RESTART IDENTITY CASCADE` 仅作用于业务表自增序列与表内级联；字典表不在清单内则不受影响
- `task_channel_configs` 是已有先例，沿用一致策略最安全

### Alternatives Considered

| 方案 | 拒绝理由 |
|---|---|
| 把 `tech_actions` 也加入 TRUNCATE + 在 SQL 末尾重新 INSERT seed | 重复工作（迁移已 seed），且 SQL 文件需硬编码 44 行 → 与迁移冲突时易漂移 |
| 把字典 seed 从迁移移到 system-init | 违反 Alembic「迁移 seed 字典」最佳实践；首次部署（无 system-init 调用）会缺失字典 |

---

## 6. 全量扫描幂等性（`cos_object_key` upsert）

### Decision

沿用现有 `CosClassificationScanner._upsert_classification()` 路径，扫描器入库逻辑改造为：

```python
async def _upsert_classification(db, *, cos_object_key, ...):
    stmt = (
        pg_insert(CoachVideoClassification)
        .values(cos_object_key=cos_object_key, action=action,
                category_l1=l1, category_l2=l2, category_l3=l3, ...)
        .on_conflict_do_update(
            index_elements=[CoachVideoClassification.cos_object_key],
            set_={"action": action, "category_l1": l1, "category_l2": l2,
                  "category_l3": l3, "updated_at": now_cst()}
        )
    )
    await db.execute(stmt)
```

需要 `coach_video_classifications.cos_object_key` 上**唯一约束**（不仅是索引）。检查现状 → 0009 迁移已建唯一索引，0022 迁移内升级为唯一约束（如尚未）。

### Rationale

- spec 边界情况第 4 条：「同一视频被多次分类时，按 `cos_object_key` upsert，最新结果覆盖旧值」
- 全量扫描中途失败重启时，已写入记录通过 `cos_object_key` 命中跳过/更新而非重复创建

### Alternatives Considered

- 使用 SELECT-then-INSERT/UPDATE：竞态条件风险（多 worker 时）
- 使用 MERGE（PG 15+）：项目当前 PG 版本未确认 ≥15，沿用 `ON CONFLICT` 更通用

---

## 7. LLM Prompt 字典 enum 的 token 成本

### Decision

字典 enum 块约 600 token（44 行 × ~14 token/行），单次 `classify_video` 调用总成本：

- 输入：prompt 模板 ~200 token + 字典 enum 600 token + 文件名/课程名 ~50 token = **~850 token**
- 输出：JSON 响应 ~30 token
- 模型：Venus Proxy 默认 GPT-4o（输入 $2.5/M token，输出 $10/M token）
- 单次成本：(850 × 2.5 + 30 × 10) / 1_000_000 = **$0.00234 ≈ ¥0.017**

全量 1015 个视频假设 30% 走 LLM 兜底（rule 失败率）≈ 305 次 → **总成本 ≈ ¥5.2**，可接受。

### Rationale

- 600 token 数量级远低于 GPT-4o 上下文窗口（128K），无必要做向量检索
- 字典更新频率低（除非 CSV 扩展），可在分类器初始化时缓存 prompt enum 块

### Alternatives Considered

- RAG 检索 top-10 候选 action 再 LLM 选：减少 80% token 但引入向量库依赖（违反 YAGNI）
- 用更便宜的 GPT-3.5：精度风险，spec SC-002 要求 85% 准确率

---

## 8. TerminologyNormalizer 静态映射初版规模

### Decision

初版 `config/terminology_mapping.json` 收录 30–50 条高频映射，由开发团队基于现有 12 位教练的 KB 提取语料整理。初版条目示例：

```json
{
  "version": "v1",
  "mappings": [
    {"colloquial": "包住球",  "standard": "摩擦加厚",  "body_part": "拍面"},
    {"colloquial": "亮板",    "standard": "拍面打开",  "body_part": "拍面"},
    {"colloquial": "收小臂",  "standard": "前臂内收",  "body_part": "手臂"},
    {"colloquial": "压住球",  "standard": "摩擦减薄",  "body_part": "拍面"},
    {"colloquial": "甩鞭子",  "standard": "前臂加速摆动", "body_part": "手臂"}
    // ... 共 30-50 条
  ]
}
```

LLM 兜底：未命中静态映射时，TerminologyNormalizer 调用 LLM 生成候选标准术语，置信度 < 0.7 则保留原口语 + 标记 `pending_review=true`。

### Rationale

- 30-50 条覆盖最高频口语（依据 spec SC-004：80% 覆盖率），剩余通过 LLM 渐进扩充
- 静态优先：响应快、成本低、可审计；LLM 仅兜底罕见情况
- 标记 `pending_review` 使运营可定期审核未覆盖映射，决定是否加入静态表

### Alternatives Considered

- 全量依赖 LLM：每次 KB 提取都触发额外 LLM 调用，成本 + 延迟均不可接受
- 词向量相似度匹配：引入向量模型依赖，30-50 条规模无必要

### 落地约束

- `src/services/terminology_normalizer.py::TerminologyNormalizer.normalize()` 双层降级
- 集成测试 `tests/integration/test_terminology_normalization.py` 验证「包住球→摩擦加厚」「未知口语→pending_review」两条路径

---

## 研究汇总

| # | 主题 | 决议 | 落地位置 |
|---|---|---|---|
| 1 | CSV 数据清洗 | strip ZWSP + `·` 拼接 l3 + trim | `0022_tech_taxonomy_rebuild.py` seed 函数 |
| 2 | LLM 字典约束 Prompt | 嵌入 44 行 enum + 二次校验 | `tech_classifier.py` + `action_dictionary_service.py` |
| 3 | 复合 PK + FK 级联 | 同事务 RENAME；先父后子重建 FK | `0022` 迁移 |
| 4 | Alembic PK 改名 | `op.execute` + `alter_column(new_column_name)` 组合 | `0022` 迁移 |
| 5 | 字典与 system-init | tech_actions 排除 TRUNCATE，沿用 task_channel_configs 模式 | `reset_business_data.sql` + `SKILL.md` |
| 6 | 扫描幂等性 | `cos_object_key` UNIQUE + ON CONFLICT UPDATE | `cos_classification_scanner.py` |
| 7 | LLM token 成本 | 单次 ¥0.017，全量 ≈ ¥5.2，可接受 | 不需特别治理 |
| 8 | TerminologyNormalizer 静态规模 | 30-50 条 + LLM 兜底 + pending_review | `config/terminology_mapping.json` + service |

→ **所有 NEEDS CLARIFICATION 已解决**，进入阶段 1。
