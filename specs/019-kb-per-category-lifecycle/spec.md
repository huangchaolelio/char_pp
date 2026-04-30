# 功能规范: 按技术类别独立管理知识库 / 标准 / 教学提示生命周期

**功能分支**: `019-kb-per-category-lifecycle`
**创建时间**: 2026-04-30
**澄清日期**: 2026-04-30（终态裁决：系统未上线，可改表、无需向前兼容）
**状态**: 已澄清
**输入**: 用户描述:
> 完善专业知识库业务流程：
> 1. 可以查询「从哪个 KB 提取任务生成了知识库」；
> 2. 可以查询所有生成的知识库列表；
> 3. 单条知识库审批不互相影响；
> 4. 标准知识库从已激活的知识库生成，且按单项技术类型生成，不同技术类型有不同的标准知识库（教学提示同理）；
> 5. 根据知识库业务流程重构系统。

---

## 澄清决议摘要

*2026-04-30 · 基于"系统未上线、追求终态最简"方针*

| # | 抉择点 | 裁决 | 含义 |
|---|--------|------|------|
| Q1 | KB 主键 | **`(tech_category, version)` 复合主键** | tech_category 提升为 KB 一等身份；消除"一版本覆盖多类别"语义 |
| Q2 | 版本号 | **每 tech_category 独立递增整数（从 1 开始）** | 放弃 semver；正手攻球 v1/v2/v3 与反手拉 v1/v2 各自独立叙事 |
| Q3 | 教学提示 | **不新增 batch 表**；tips 行内直接 FK 绑 `(tech_category, kb_version)`，与 KB 走同一生命周期 | KB approve 时 tips 联动激活；专家只审一次 |
| Q4 | standards build | **强制按单 `tech_category` 触发**，删除批量入口 | API 仅接受 `tech_category` 必填请求 |
| Q5 | 数据处置 | **drop & recreate** | 不写回填脚本，用新迁移 `0017` 重建 |

---

## 用户场景与测试 *(必填)*

### 用户故事 1 — 按单一技术类别独立审批知识库（优先级: P1）

专家审阅"正手攻球 v3 (draft)"时点击「批准」，只希望正手攻球的当前 active 版本被替换；反手拉、发球等其它技术类别的 active 版本**完全不动**。今天批正手、明天批反手，两次操作彼此独立。

**优先级原因**: 这是用户诉求的核心——当前"全表单 active"导致每次 approve 误伤其它类别，是 MVP 必须先解决的问题。P1。

**独立测试**:
- 先让 `(forehand_attack, v1) = active`；再创建 `(backhand_topspin, v1) = draft` → 批准 `(backhand_topspin, v1)`。
- 预期：`(forehand_attack, v1)` 仍为 active；`(backhand_topspin, v1)` 成为 active。两条独立并存。

**验收场景**:
1. **给定** `(forehand_attack, v1) = active`，**当** 专家批准 `(backhand_topspin, v1)` 草稿，**那么** `(forehand_attack, v1)` 保持 active，`(backhand_topspin, v1)` 变 active，两者并存。
2. **给定** `(forehand_attack, v1) = active`，**当** 专家批准 `(forehand_attack, v2)` 草稿，**那么** `(forehand_attack, v1)` → archived，`(forehand_attack, v2)` → active；其它类别不变。
3. **给定** `(forehand_attack, v2)` 草稿存在未解决的 `expert_tech_points.conflict_flag=true` 记录，**当** 专家批准，**那么** 返回 409 + `KB_CONFLICT_UNRESOLVED`，状态仍为 draft，不产生副作用。

---

### 用户故事 2 — 全量查询知识库列表并回溯提取来源（优先级: P1）

管理员打开"知识库管理"视图，一屏看到**所有** KB 记录（每条一个 `(tech_category, version)`，不论状态），每条记录都能回溯到**由哪一次 KB 提取作业（extraction_job）产出**。

**优先级原因**: 与故事 1 同为"管理台"的两个面。追溯能力是专家审批的前置条件。P1。

**独立测试**:
- 列表接口返回 N 条记录；每条含 `tech_category / version / status / extraction_job_id / point_count` 等字段；用任一记录的 `extraction_job_id` 调 extraction-jobs 详情接口应 200 返回。

**验收场景**:
1. **给定** 系统内存在 6 条 KB 记录（2 draft、3 archived、1 active，覆盖 3 个技术类别），**当** 管理员请求 KB 列表，**那么** 返回全部 6 条，每条都有非 null 的 `extraction_job_id`。
2. **给定** 某条 KB 的 `extraction_job_id` 有值，**当** 管理员用该 id 调 `GET /api/v1/extraction-jobs/{id}`，**那么** 必 200 且响应里 `output_kbs` 字段包含该 `(tech_category, version)`。
3. **给定** 列表条目数 > 100，**当** 管理员以 `page=2&page_size=50` 翻页，**那么** 响应 `meta.total` 准确反映总数；`page_size>100` 返回 400 + `INVALID_PAGE_SIZE`。
4. **给定** 请求带 `?tech_category=forehand_attack` 过滤，**那么** 只返回该类别下的记录；请求带 `?status=active` 过滤，那么返回各类别当前 active 记录。

---

### 用户故事 3 — 按单一技术类别构建技术标准（优先级: P1）

触发 `POST /api/v1/standards/build` **必须**携带 `tech_category`，仅基于"该类别当前 active KB 所含 expert_tech_points"构建；新产出 standard 激活时只归档同类别的旧 active，其它类别 0 变更。

**优先级原因**: 对应用户诉求 4。核心业务约束——不同技术类型有不同的标准。P1。

**独立测试**:
- `tech_standards` 中正手攻球现 `version=3, active`，反手拉现 `version=2, active`；为正手攻球触发 build → 产出 `version=4, active`，v3 变 archived；反手拉不变。

**验收场景**:
1. **给定** `tech_standards` 内正手攻球 v3 active / 反手拉 v2 active，**当** 为 forehand_attack 触发 build，**那么** 正手攻球出现 v4 active、v3 archived；反手拉 v2 保持 active。
2. **给定** forehand_attack 在 `tech_knowledge_bases` 无任何 active 行，**当** 为 forehand_attack 触发 build，**那么** 返回 409 + `NO_ACTIVE_KB_FOR_CATEGORY`。
3. **给定** 请求体不含 `tech_category`，**当** 调用 `POST /standards/build`，**那么** 返回 422 + `VALIDATION_FAILED`。
4. **给定** 某类别 active KB 的 expert_tech_points 指纹与上次 build 完全一致，**当** 再次触发 build，**那么** 返回 409 + `STANDARD_ALREADY_UP_TO_DATE`；`tech_standards` 不新增行。

---

### 用户故事 4 — 按单一技术类别管理教学提示（优先级: P2）

`teaching_tips` 直接绑 `(tech_category, kb_version)`：KB 提取完成时以 `status=draft` 落库；KB approve 时该类别对应的 tips **整组**随 KB 激活一并转为 active；诊断阶段只读 active tips。

**优先级原因**: 对应用户诉求"教学提示同理"。与 KB 绑同一生命周期，无需独立审批入口——专家审一次 KB 即完成 tips 的发布。P2。

**独立测试**:
- extraction_job 产出 `(forehand_attack, v2)` draft KB + 12 条 draft tips；approve 该 KB → KB 和 tips 都变 active；诊断侧查 forehand_attack 的 tips 得到这 12 条；反手拉 tips 状态不变。

**验收场景**:
1. **给定** extraction_job 成功，**当** 持久化阶段完成，**那么** 该作业产出的 tips 入表 `status=draft`、`tech_category` / `kb_version` 与 KB 记录一致。
2. **给定** 专家批准 `(forehand_attack, v2)` KB，**当** approve 事务成功，**那么** 同一类别的上个 active KB 对应的 tips 批量转 archived；新批 tips 转 active；反手拉 tips 不变。
3. **给定** 诊断侧请求 `GET /api/v1/teaching-tips?tech_category=forehand_attack`，**当** 该类别有 active tips，**那么** 默认仅返回 active 行；传 `?include_status=draft,archived` 才返回其它状态。
4. **给定** `source_type='human'` 的 tip 记录，**当** KB approve 触发 tips 批量归档，**那么** human 行不被改动（保留 Feature-005 的"人工标注不可被自动流覆盖"语义）。

---

### 用户故事 5 — KB 提取作业反向可查其产出的 KB 记录（优先级: P2）

在 `GET /api/v1/extraction-jobs/{id}` 里直接看到"本次作业产出了哪些 `(tech_category, version)` KB 记录"，便于排查"任务成功但 KB 列表找不到对应记录"的数据一致性问题。

**优先级原因**: 与故事 2 形成"KB ↔ Job"双向链路。P2（非 MVP 必选，但便于运维）。

**独立测试**:
- 挑一条 succeeded 的 extraction_job → 读其详情 → `output_kbs` 数组非空且可反向核对。

**验收场景**:
1. **给定** 某 extraction_job 为 `succeeded`，**当** 查询其详情，**那么** 响应含 `output_kbs: [{tech_category, version, created_at}, ...]`。
2. **给定** extraction_job 为 running / failed，**当** 查询详情，**那么** `output_kbs = []`。

---

### 边界情况

- **extraction_job 产出跨多类别**：一次作业可以产出 N 条 KB 记录（每类别一条），用相同 `extraction_job_id` 关联；审批时每条独立审批。
- **KB 草稿 `point_count=0`**：approve 时拒绝，返回 409 + `KB_EMPTY_POINTS`。
- **并发 approve**：通过 `(tech_category)` 维度的行级锁（`SELECT ... FOR UPDATE`）+ partial unique index 双保险防"双 active"。
- **诊断读无 active KB**：返回 409 + `NO_ACTIVE_KB_FOR_CATEGORY`（明确告知该类别尚未发布）。
- **删表重建**：未上线系统 → `0017` 迁移采用**显式 `drop_constraint` + `drop_table` 路径**（禁用 `DROP CASCADE`，见 FR-025）；不保留历史数据。现有测试数据在启动 `system-init` skill 后清空。

---

## 需求 *(必填)*

### 功能需求

#### KB 维度（tech_knowledge_bases）— 核心重构

- **FR-001**: `tech_knowledge_bases` 主键 MUST 变更为 `(tech_category, version)` 复合主键；`version` 列 MUST 为 per-category 自增整数（每类别独立从 1 递增）。
- **FR-002**: `status` 列保留 `draft / active / archived` 三态；MUST 通过 PostgreSQL partial unique index `WHERE status='active'` 在 `tech_category` 维度上强约束"单 active"。
- **FR-003**: `action_types_covered` 字段 MUST 删除（被主键的 `tech_category` 列取代，避免冗余）。
- **FR-004**: `extraction_job_id` 字段 MUST 保持 NOT NULL（新系统无 legacy 数据）；每条 KB 记录 MUST 可回溯到其产出作业。
- **FR-005**: `approve` 操作 MUST 仅影响单条 `(tech_category, version)` 记录 + 同类别的上一条 active；不得跨类别产生副作用。事务内完成"旧 active → archived、当前 draft → active"两步。
- **FR-006**: `approve` 前 MUST 做冲突检查：若该 `(tech_category, version)` 下存在 `expert_tech_points.conflict_flag=true`，返回 409 + `KB_CONFLICT_UNRESOLVED`，状态保持 draft。
- **FR-007**: `approve` 前 MUST 做空集检查：若该记录的 `point_count=0`，返回 409 + `KB_EMPTY_POINTS`。

#### KB 查询与追溯

- **FR-008**: 提供 `GET /api/v1/knowledge-base/versions` 列表接口（v1.4.0 统一信封 + `PaginationMeta`）。每条记录字段：`tech_category` / `version` / `status` / `point_count` / `extraction_job_id` / `approved_by` / `approved_at` / `created_at` / `notes`。
- **FR-009**: 列表接口 MUST 支持过滤：`tech_category`（精确匹配）、`status`（精确匹配 draft/active/archived）、`extraction_job_id`（精确匹配，定位单作业产出的全部记录）。
- **FR-010**: 提供 `GET /api/v1/knowledge-base/versions/{tech_category}/{version}` 详情接口（路径 ID 使用复合主键两段）；返回字段同列表项 + 该记录所含 `expert_tech_points` 摘要（数量、维度数、冲突数）。
- **FR-011**: 提供 `POST /api/v1/knowledge-base/versions/{tech_category}/{version}/approve`（body: `{approved_by, notes?}`）；成功返回新 active 记录 + 被归档的旧 active 的 version 号。
- **FR-012**: `GET /api/v1/extraction-jobs/{id}` 响应 MUST 新增 `output_kbs` 字段：`[{tech_category, version, created_at}]`，仅 `succeeded` 作业非空数组。
- **FR-013**: 分页参数严格遵守章程原则 IX：`page ≥ 1`，`page_size ∈ [1, 100]`；越界返回 400 + `INVALID_PAGE_SIZE`。

#### 标准（tech_standards）重构

- **FR-014**: `tech_standards` 表结构保持不变（已是 per-category 版本化，`uq_ts_tech_version` 约束天然适配本功能）。
- **FR-014a**: `TechStandard` 生命周期 MUST 保持两态模型 `active / archived`（**不引入 draft 态**）；build 成功后新记录直接以 `status='active'` 入库，旧同类别 active 同事务内转 `archived`。与 KB 的三态 `draft→active→archived` 模型刻意区分——standards 由系统聚合构建，无需专家二次审批。
- **FR-015**: `POST /api/v1/standards/build` MUST 变更契约：`tech_category` 改为必填字段；移除"不传就全量 build"的旧路径（原路径返回 422 + `VALIDATION_FAILED`）。
- **FR-016**: build 数据源 MUST 限定为"该 `tech_category` 当前 active KB 所含 expert_tech_points"；不允许混入其它类别数据。
- **FR-017**: build 成功插入新 `tech_standards` 行后 MUST 仅归档同类别的旧 active 行；其它类别不变。
- **FR-018**: 若目标类别在 `tech_knowledge_bases` 无 active 行，返回 409 + `NO_ACTIVE_KB_FOR_CATEGORY`。
- **FR-019**: build 幂等：若目标类别 active KB 下 `expert_tech_points` 的指纹——定义为 `sha256(sorted_json([(ep.id, ep.param_ideal, ep.extraction_confidence) for ep in points]))`（字段名均属 `ExpertTechPoint` 表，与 `TechStandardPoint` 无关）——与该类别现有 active `TechStandard` 记录绑定的指纹一致，返回 409 + `STANDARD_ALREADY_UP_TO_DATE`，不新增行。指纹值作为新列 `tech_standards.source_fingerprint CHAR(64)` 随本 Feature 顺便补齐（仅新增列，不破坏原 schema）。

#### 教学提示（teaching_tips）重构

- **FR-020**: `teaching_tips` 表 MUST 新增列：`tech_category VARCHAR(64) NOT NULL`（原有 `action_type` 列**删除**，与 tech_category 语义重复）、`kb_tech_category` + `kb_version`（复合 FK → `tech_knowledge_bases`）、`status ENUM(draft,active,archived) NOT NULL DEFAULT 'draft'`。
- **FR-021**: tips 写入时机：extraction_job 成功持久化 expert_tech_points 后，同事务内按 tech_category 维度批量写入 tips（`status=draft`、`kb_version` 指向本次产出的 draft KB）。
- **FR-022**: KB approve 事务 MUST 联动更新 tips：旧 active KB 对应的 tips → archived；新激活 KB 对应的 tips → active。由同一事务保证一致性。
- **FR-023**: 诊断侧读 tips 的接口（`GET /api/v1/teaching-tips`）默认只返回 `status='active'`；支持 `?include_status=draft,archived` 显式放宽。
- **FR-024**: `source_type='human'` 的 tip 在 KB approve 联动归档中保持原状态不变（保留 Feature-005 的"人工标注不可被自动流覆盖"语义）。

#### 重构性约束

- **FR-025**: 本功能 MUST 以单一迁移 `0017_kb_per_category_redesign.py` 实现：按 data-model.md § 迁移骨架的 4 步顺序执行——(1) `drop_constraint` 显式解绑 5 张引用表的旧 FK；(2) `drop_table` + 重建 `tech_knowledge_bases`；(3) 逐表 `drop_column` + `add_column` + `create_foreign_key` 重建复合 FK；(4) `teaching_tips` 列重构。**不使用 `DROP CASCADE`**（章程原则 VI：DDL 显式声明，避免级联副作用不可见）。
- **FR-026**: 以下表的 FK 列 MUST 同步变更为"指向 `(tech_category, kb_version)` 复合主键"（或改为不可空的复合外键）：`analysis_tasks.knowledge_base_version` → 拆为 `(kb_tech_category, kb_version)`；`expert_tech_points.knowledge_base_version` → 同上；`reference_video.kb_version` → 同上；`skill_execution.kb_version` → 同上；`athlete_motion_analysis.knowledge_base_version` → 同上。
- **FR-027**: `approve_version` service 签名 MUST 从 `(version: str)` 改为 `(tech_category: str, version: int)`；所有调用点（路由 + 测试）同步更新。
- **FR-028**: 所有新错误码 MUST 登记到 `src/api/errors.py::ErrorCode` + `ERROR_STATUS_MAP` + `ERROR_DEFAULT_MESSAGE`：`KB_CONFLICT_UNRESOLVED`（409）、`KB_EMPTY_POINTS`（409）、`NO_ACTIVE_KB_FOR_CATEGORY`（409）、`STANDARD_ALREADY_UP_TO_DATE`（409）。错误码前缀 MUST 同步登记 `docs/business-workflow.md` § 7.4。
- **FR-029**: 本功能 MUST NOT 引入新 Celery 队列、新 worker、新后台任务；standards build 与 KB approve 均为同步 API（HTTP 请求内完成）。
- **FR-030**: 本功能 MUST NOT 引入新 `business_step`；保留 `kb_version_activate` 与 `build_standards` 两个现有步骤（Feature-018 已定义）。

### 关键实体

| 实体 | 状态 | 结构变化 |
|-----|------|---------|
| **TechKnowledgeBase** | 重构 | 主键变为 `(tech_category, version)`；`version` 改 Integer；删 `action_types_covered`；`extraction_job_id` 转为 NOT NULL |
| **TechStandard** | 不动 | 已是 per-category，保留现有 schema |
| **TeachingTip** | 重构 | 增列 `tech_category` / `kb_tech_category` + `kb_version` / `status`；删列 `action_type` |
| **ExpertTechPoint** | 微调 | `knowledge_base_version` 字段拆成 `kb_tech_category` + `kb_version` 复合 FK |
| **AnalysisTask / ReferenceVideo / SkillExecution / AthleteMotionAnalysis** | 微调 | FK 指向 `(tech_category, kb_version)` 复合键 |
| **ExtractionJob** | 不动 | 不改 schema；API 层新增 `output_kbs` 反查字段 |

### 业务阶段映射 *(必填 - 原则 X / 章程 v1.5.0)*

- **所属阶段**: `STANDARDIZATION`（本功能是对该阶段内"KB 版本激活 + 技术标准构建"两步的语义精化；不新增阶段、不跨阶段）。
- **所属步骤**: `kb_version_activate` + `build_standards`（均为 `docs/business-workflow.md` § 4 / § 5 已定义步骤）。**不新增步骤**（故不触发"先扩 workflow 文档"的前置动作）。
- **DoD 引用**: `docs/business-workflow.md` § 2 "STANDARDIZATION 完成判据"现有行不需扩展；精化解释："每个 `TECH_CATEGORIES` 维度在 `tech_knowledge_bases` / `tech_standards` 两张表各自存在 ≥1 条 active 记录 → 诊断可用"。
- **可观测锚点**:
  - § 7.1 任务级 — `extraction_jobs ↔ tech_knowledge_bases` 反向关系通过 `output_kbs` 响应字段在 API 层可见。
  - § 7.2 步骤级 — KB `approve_per_category` / `standards_build_per_category` 的成功/失败计数纳入现有步骤指标，仅新增 tag `tech_category`。
  - § 7.3 诊断级 — `NO_ACTIVE_KB_FOR_CATEGORY` 触发时 WARN 级日志含 `tech_category`。
- **章程级约束影响**:
  - § 4.2 "单 active 约束"措辞 MUST 由"全局单 active"改为"per-category 单 active"（语义明确，作用范围缩小）。
  - § 7.4 错误码表 MUST 新增 4 个错误码（见 FR-028）。
  - § 4.3 状态机图不变（`draft → active → archived` 三态不变），仅注释补充"每转换作用域 = 单 tech_category"。
- **回滚剧本**:
  - 风险等级 **low-risk**（系统未上线、无真实生产数据）。
  - 回滚策略：`alembic downgrade -1`（回到 0016）；配合 `system-init` skill 清空业务数据，再重启 API / Worker 即可回到上个 Feature 的语义。
  - 无需新增 § 10 剧本项（系统未上线无生产数据保护需求）。

---

## 成功标准 *(必填)*

### 可衡量的结果

- **SC-001**: 专家批准一条单类别 KB 草稿后，其它 20 个技术类别在 `tech_knowledge_bases` 上的 active 行 **100% 保持不变**（集成测试覆盖全部 21 类）。
- **SC-002**: KB 列表接口单次返回 ≥200 条记录并携带完整字段（`tech_category / version / status / extraction_job_id / point_count`），**100 次连续请求的 P95 延迟 ≤ 300 ms**（warm-cache；无 LLM / 无 COS 调用，纯 SQL）。
- **SC-003**: 在任意 `succeeded` 的 extraction_job 上，"Job ↔ KB 记录双向可查"成功率 = **100%**；集成测试样本包含 3 种 job 状态（succeeded / running / failed）各 ≥1 例；归档后任一条 KB 记录的 `extraction_job_id` MUST 非 null。
- **SC-004**: 为任一技术类别触发一次 standards build 的端到端耗时 ≤ **10 秒**（无 LLM，仅 SQL 聚合）；其它类别 `tech_standards` 行 0 变更。
- **SC-005**: 诊断侧请求某类别且该类别无 active KB 时，接口 **P95 ≤ 200 ms** 返回 409 + `NO_ACTIVE_KB_FOR_CATEGORY`，无挂起无空响应。
- **SC-006**: 迁移 `0017` 在干净空库 + 新 seed 场景下可完整 `upgrade head` 与 `downgrade base`，来回各执行 3 次不报错。
- **SC-007**: 合约测试覆盖所有新/变更 API 接口（列表 / 详情 / approve / standards-build / extraction-job detail）共 ≥5 个合约测试文件；单元测试覆盖 `approve_version` 新签名的 6 条分支（单类别新批 / 同类别覆盖 / 跨类别并存 / 冲突拒绝 / 空集拒绝 / 并发双批）。

---

## 假设

- **系统未上线**：无真实生产数据，可自由改表、重新 seed；不保留任何历史 KB 版本。迁移仍走显式 `drop_constraint` + `drop_table` 路径（FR-025 禁用 `DROP CASCADE`，保留 DDL 可审计性）。
- **21 类 `TECH_CATEGORIES`** 在本功能周期稳定（定义位置 `src/services/tech_classifier.py`）。
- **`source_type='human'` 的 tip 数量极少**（每类别 ≤ 20 条）；一期接受"human 行单独挂不参与 auto 流批量归档"的最简实现。
- **Celery 队列拓扑不变**：见 FR-029；不新增队列、不调并发数、不改 `task_channel_configs` seed（Feature-013 / 018 配置沿用）。
- **KB 提取 DAG 产出口径**：`kb_extraction_pipeline` 在 persist 步骤已按 `expert_tech_points.action_type` 区分类别；本功能只需在 persist 步完成后**按 tech_category 分组**产出 N 条 KB 记录（每类别一条）。该改动在 plan 阶段落到 `src/services/knowledge_base_svc.py::create_draft_version` + DAG persist executor。
- **诊断读路径**：`diagnosis_service._get_active_standard` 现已按 tech_category 分桶查 `tech_standards`；本功能不改诊断读路径，仅承诺"每类别确有 active standard"前提。

---
