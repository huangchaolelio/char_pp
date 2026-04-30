---
description: "Feature-019 任务清单 · KB Per-Category Lifecycle"
---

# 任务: 按技术类别独立管理知识库 / 标准 / 教学提示生命周期

**输入**: 来自 `/specs/019-kb-per-category-lifecycle/` 的设计文档
**前置条件**: [spec.md](./spec.md) ✅ · [plan.md](./plan.md) ✅ · [research.md](./research.md) ✅ · [data-model.md](./data-model.md) ✅ · [contracts/](./contracts/) ✅ · [quickstart.md](./quickstart.md) ✅

**测试**: 章程原则 II 强制——本 Feature 新增/变更 API 必须先有合约测试；算法变更有单元测试；迁移有集成测试。tasks.md 生成的测试为必需任务（非可选）。

**组织结构**: 任务按 5 个用户故事（P1 × 3 / P2 × 2）分组，每个故事可独立实施、独立测试、作为 MVP 增量交付。

## 格式: `[ID] [P?] [Story] 描述`
- **[P]**: 可以并行运行（不同文件，无依赖）
- **[Story]**: 映射到 spec.md 用户故事（US1~US5）
- 每个任务含确切文件路径

## 路径约定

单一 Python 后端项目（章程附加约束）：仓库根目录下的 `src/` + `tests/`；禁止创建 `frontend/` 等前端目录。

---

## 阶段 1: 设置

**目的**: 项目环境就绪

- [X] T001 激活项目虚拟环境并切换到功能分支 `019-kb-per-category-lifecycle`（命令：`source /opt/conda/envs/coaching/bin/activate && git checkout 019-kb-per-category-lifecycle`）；确认 `alembic heads` 当前为 `0016_*`（Feature-018 基线）
- [ ] T002 【可选】使用 `/skills system-init` 清空本地业务数据，避免旧 KB 记录干扰后续迁移验证

---

## 阶段 2: 基础（阻塞前置）

**目的**: 在任何用户故事实施之前，必须完成数据库迁移、ORM 模型、错误码三件套，否则所有 service / 路由都无法对齐新 schema。

**⚠️ 关键**: 本阶段完成前，无法开始任何用户故事。

### 迁移（唯一迁移文件，按 data-model.md § 迁移骨架落地）

- [X] T003 创建 Alembic 迁移 `src/db/migrations/versions/0017_kb_per_category_redesign.py`：实现 `upgrade()`——按 data-model.md 第 1 节的 4 步顺序（drop FK / drop+recreate tech_knowledge_bases / 重建 5 张 FK 引用表的复合列 / teaching_tips 重构）；revision='0017'，down_revision='0016_xxx'（查 `alembic heads`）
- [X] T004 在同文件实现 `downgrade()`：按逆序还原 Feature-018 的 schema（单列 `knowledge_base_version VARCHAR(20)` FK + 回填 `teaching_tips.action_type` + DROP `tip_status_enum`）；不保数据，仅保证 schema 可退

### ORM 模型重构（[P]：不同文件，可并行）

- [X] T005 [P] 重构 `src/models/tech_knowledge_base.py`：改 `PrimaryKeyConstraint('tech_category', 'version')`；`version Integer nullable=False`；删 `action_types_covered` 列；`extraction_job_id` nullable=False；加 partial unique index `uq_tech_kb_active_per_category`；保留 `business_phase` / `business_step` 两列（Feature-018 遗产）
- [X] T006 [P] 重构 `src/models/teaching_tip.py`：加 `tech_category` / `kb_tech_category` / `kb_version` / `status` 四列；删 `action_type` 列；`task_id` 改 nullable=True；加复合 FK `ForeignKeyConstraint(['kb_tech_category','kb_version'], ['tech_knowledge_bases.tech_category','tech_knowledge_bases.version'], ondelete='CASCADE')`；加 3 个索引
- [X] T007 [P] 微调 `src/models/expert_tech_point.py`：删 `knowledge_base_version` 列；加 `kb_tech_category` + `kb_version` 复合 FK；重命名 UNIQUE 约束 `uq_expert_point_version_action_dim` → `uq_expert_point_kb_action_dim`
- [X] T008 [P] 微调 `src/models/analysis_task.py`：`knowledge_base_version` → `kb_tech_category` + `kb_version`（均 nullable，ondelete='SET NULL'）
- [X] T009 [P] 微调 `src/models/reference_video.py`：`kb_version VARCHAR` → `kb_tech_category` + `kb_version INTEGER`（均 NOT NULL，ondelete='RESTRICT'）
- [X] T010 [P] 微调 `src/models/skill_execution.py`：同 T008（均 nullable，ondelete='SET NULL'）
- [X] T011 [P] 微调 `src/models/athlete_motion_analysis.py`：同 T009（均 NOT NULL，ondelete='RESTRICT'）

### 错误码登记（原则 IX 强制）

- [X] T012 在 `src/api/errors.py::ErrorCode` 新增 4 个枚举值：`KB_CONFLICT_UNRESOLVED`、`KB_EMPTY_POINTS`、`NO_ACTIVE_KB_FOR_CATEGORY`、`STANDARD_ALREADY_UP_TO_DATE`；在同文件 `ERROR_STATUS_MAP` 映射全部为 HTTP 409；在 `ERROR_DEFAULT_MESSAGE` 落中文默认消息（参见 [contracts/error-codes.md](./contracts/error-codes.md)）

### 基础测试（迁移幂等 — 先 Red）

- [X] T013 新建 `tests/integration/test_0017_migration_roundtrip.py`：验证 `alembic upgrade head` → `alembic downgrade -1` → `alembic upgrade head` 循环 3 次均无错误；upgrade 后 `tech_knowledge_bases` 主键为 `(tech_category, version)`；partial unique index `uq_tech_kb_active_per_category` 存在且生效（插两条同类别 active 应报 IntegrityError）；**补充断言**：(a) 插入 `extraction_job_id=NULL` 的 KB 应抛 IntegrityError（FR-004 验证）；(b) 同 `(tech_category, 'active')` 连续 INSERT 两条应抛 UniqueViolation（FR-002 验证）

**检查点**: T003-T013 全部完成、迁移可双向来回执行 → 可开始并行实施用户故事（US1-US5）

---

## 阶段 3: US1 — 按单一技术类别独立审批知识库（优先级 P1）🎯 MVP 核心

**目标**: 专家批准 `(tech_category, version)` 草稿时，其它技术类别的 active 版本 100% 保持不变；事务内完成"旧 active → archived、当前 draft → active"两步 + tips 联动。

**独立测试**: 准备 `(forehand_attack, v1)=active` + `(backhand_topspin, v1)=draft`；POST approve backhand_topspin v1；验证 forehand_attack v1 仍 active，backhand_topspin v1 变 active，两者并存。

### Schema 重构（前置：合约测试 T014 依赖本任务的 Pydantic 模型）

- [X] T014pre [US1] 重构 `src/api/schemas/knowledge_base.py`：新增 `KbVersionItem` / `KbVersionDetail` / `ApproveKbRequest` / `ApproveKbResponse` Pydantic v2 模型；字段与 [contracts/kb-version-approve.yaml](./contracts/kb-version-approve.yaml) 对齐；使用 `model_config = ConfigDict(from_attributes=True)`；该模块是 T014/T022/T023 合约测试的导入目标，必须在三个合约测试任务之前完成

### 合约测试（先 Red — 章程原则 II 强制）

- [X] T014 [US1] 新建 `tests/contract/test_kb_version_approve.py`：按 [contracts/kb-version-approve.yaml](./contracts/kb-version-approve.yaml) 验证响应 schema；覆盖 200 成功、404 `KB_VERSION_NOT_FOUND`、409 `KB_VERSION_NOT_DRAFT` / `KB_EMPTY_POINTS` / `KB_CONFLICT_UNRESOLVED`、422 `VALIDATION_FAILED`（缺 approved_by）；断言响应信封 `success/data/meta` 顶层结构与 data 内的 `new_active` / `previous_active_version` / `tips_updated` 字段

### 单元测试（先 Red）

- [ ] T015 [US1] 新建 `tests/unit/test_approve_version_branches.py`：参数化覆盖 6 条分支——(a) 该类别首批（无旧 active，previous_active_version=null）；(b) 同类别覆盖（旧 v1 archived、新 v2 active）；(c) 跨类别并存（批 backhand 不影响 forehand）；(d) 冲突拒绝（mock `expert_tech_points.conflict_flag=true` → 抛 `KB_CONFLICT_UNRESOLVED`）；(e) 空集拒绝（point_count=0 → 抛 `KB_EMPTY_POINTS`）；(f) 并发模拟（asyncio.gather 两个同类别 approve → 一个成功一个因 partial unique index 失败）

### Service 层实现（TDD Green）

- [X] T016 [US1] 重构 `src/services/knowledge_base_svc.py`：函数签名 `async def approve_version(session, tech_category: str, version: int, approved_by: str, notes: str | None = None) -> dict`；按 research.md R3 实现行级锁 + `pg_advisory_xact_lock(hashtext(tech_category))` 兜底；按 data-model.md 实体 1 状态机 5 步事务（SELECT FOR UPDATE → 校验 → UPDATE 旧 active → UPDATE 目标 → 调用 teaching_tip_svc 联动）；全部错误抛 `AppException(ErrorCode.XXX)`
- [X] T017 [US1] 重构 `src/services/knowledge_base_svc.py::create_draft_version`：签名改为 `(session, tech_category: str, extraction_job_id: UUID, point_count: int) -> TechKnowledgeBase`；实现 research.md R2 的 `MAX(version)+1` per-category 递增 + IntegrityError 重试一次；入库 status=draft
- [X] T018 [US1] 新建 `src/services/teaching_tip_svc.py`：函数 `async def relink_on_kb_approve(session, tech_category: str, old_version: int | None, new_version: int) -> dict`；返回 `{"archived_count": N, "activated_count": M}`；按 data-model.md 实体 3 生命周期联动实现 2 步 UPDATE（归档时含 `source_type='auto'` 过滤）

### 路由层实现

- [X] T019 [US1] 重构 `src/api/routers/knowledge_base.py`：新增 `POST /versions/{tech_category}/{version}/approve`（注意 `APIRouter(prefix='/knowledge-base')` + `app.include_router(..., prefix='/api/v1')` 拼接规则）；请求体 Pydantic schema `ApproveKbRequest(approved_by: str, notes: str | None)`；响应通过 `ok(data)` 构造器；`tech_category` 路径参数服务端归一化小写
- [X] T020 [US1] **保留哨兵** `src/api/routers/knowledge_base.py` 中老的 `POST /versions/{version}/approve` 单列路径：修改为立即抛 `AppException(ErrorCode.ENDPOINT_RETIRED, details={"successor": "/api/v1/knowledge-base/versions/{tech_category}/{version}/approve", "migration_note": "Feature-019 将主键提升为 (tech_category, version) 复合键"})`；同时在 `src/api/routers/_retired.py::RETIREMENT_LEDGER` 登记一条；在 `specs/019-kb-per-category-lifecycle/contracts/retirement-ledger.md`（新建）追加台账条目。**线常原则 IX 强制：禁止物理删除**

### Schema 重构

- [ ] T021 [NOOP] [US1] （**已由 T014pre 承接，保留编号避免依赖图错乱**）若 T014pre 中实现的 Pydantic 模型发现还缺字段，在本任务并行追加（依赖 T016-T019 service 方法实际返回结构修补）；**无追加内容则直接标 ✅ 关闭**（默认情况）

**检查点**: US1 任务全部完成 → 可独立冒烟"按类别独立审批"（参照 quickstart § 4 冒烟 1）→ MVP P1 其一达成

---

## 阶段 4: US2 — 全量查询知识库列表并回溯提取来源（优先级 P1）

**目标**: 提供 KB 列表与详情接口，支持按 `tech_category` / `status` / `extraction_job_id` 过滤；每条记录带 `extraction_job_id` 可反查提取作业。

**独立测试**: 建 6 条 KB（2 draft / 3 archived / 1 active，覆盖 3 类别）→ 列表接口无过滤返回 6 条；`?tech_category=forehand_attack&status=active` 过滤返回 1 条；`page_size=500` 返回 400 `INVALID_PAGE_SIZE`。

### 合约测试（先 Red）

- [X] T022 [US2] 新建 `tests/contract/test_kb_versions_list.py`：按 [contracts/kb-versions-list.yaml](./contracts/kb-versions-list.yaml) 验证；覆盖 200（全量 + 三种过滤组合 + 分页元信息）、400 `INVALID_PAGE_SIZE`（page_size=500）、400 `INVALID_ENUM_VALUE`（status=pending）
- [X] T023 [US2] 新建 `tests/contract/test_kb_version_detail.py`：按 [contracts/kb-version-detail.yaml](./contracts/kb-version-detail.yaml) 验证；200 含 `dimensions_summary` 三字段；404 `KB_VERSION_NOT_FOUND`（version=999）

### Service 层 / Router 层实现

- [X] T024 [US2] 在 `src/services/knowledge_base_svc.py` 新增 `async def list_versions(session, tech_category: str | None, status: str | None, extraction_job_id: UUID | None, page: int, page_size: int) -> tuple[list[KbVersionItem], int]`：返回 (items, total)；在 SQL 层过滤 + COUNT(*) OVER()；按 `tech_category ASC, version DESC` 排序
- [X] T025 [US2] 在 `src/services/knowledge_base_svc.py` 新增 `async def get_version_detail(session, tech_category: str, version: int) -> KbVersionDetail | None`：主体 SELECT + 关联 `expert_tech_points` 聚合出 `dimensions_summary`（total_points / distinct dimensions / conflict_count）
- [X] T026 [US2] 在 `src/api/routers/knowledge_base.py` 新增 `GET /versions`（分页 + 过滤）和 `GET /versions/{tech_category}/{version}`；分页通过 `page(items, page=, page_size=, total=)` 构造器；枚举参数非法值抛 `AppException(ErrorCode.INVALID_ENUM_VALUE, details={"field":..., "allowed":[...], "got":...})`

### 兼容清理

- [X] T027 [US2] **保留哨兵**处理 `src/api/routers/knowledge_base.py` 中任何基于老主键（单列 version）的列表/详情路径：改为抛 `AppException(ErrorCode.ENDPOINT_RETIRED, details={"successor": "/api/v1/knowledge-base/versions[/{tech_category}/{version}]", "migration_note": "..."})`；同时更新 `src/api/routers/_retired.py::RETIREMENT_LEDGER` + `contracts/retirement-ledger.md`。**章程原则 IX：禁止物理删除**

**检查点**: US2 完成 → 可独立冒烟"列表 + 详情 + 追溯 job"（参照 quickstart 冒烟 2 前半）

---

## 阶段 5: US3 — 按单一技术类别构建技术标准（优先级 P1）

**目标**: `POST /standards/build` 强制携带 `tech_category`；基于该类别 active KB 构建新 standard；其它类别 0 影响；指纹相同时幂等拒绝。

**独立测试**: 正手攻球 active KB 存在 → build forehand_attack → 产出 tech_standards 新 active；再 build 同类别 → 返回 409 `STANDARD_ALREADY_UP_TO_DATE`；反手拉 tech_standards 未变动。

### 合约测试（先 Red）

- [X] T028 [US3] 新建 `tests/contract/test_standards_build_per_category.py`：按 [contracts/standards-build.yaml](./contracts/standards-build.yaml) 验证；覆盖 200（首次 build、二次 build 对应新版）、409 `NO_ACTIVE_KB_FOR_CATEGORY`、409 `STANDARD_ALREADY_UP_TO_DATE`、422 `VALIDATION_FAILED`（缺 tech_category）

### 单元测试

- [ ] T029 [US3] 新建 `tests/unit/test_tech_standard_builder_per_category.py`：覆盖 3 条分支——(a) 该类别首次 build（previous_version=null）；(b) 同类别再 build（new_version=previous+1，旧 active archived）；(c) 指纹一致（前后 active KB 下 expert_tech_points 集合未变）→ 抛 `STANDARD_ALREADY_UP_TO_DATE`

### Service 层 / Router 层实现

- [X] T030 [US3] 重构 `src/services/tech_standard_builder.py::build`：签名改为 `async def build(session, tech_category: str) -> TechStandard`；内部步骤——(1) SELECT 该类别 active KB，无则抛 `NO_ACTIVE_KB_FOR_CATEGORY`；(2) 聚合该 KB 下 expert_tech_points；(3) 按 FR-019 口径计算指纹 `sha256(sorted_json([(ep.id, ep.param_ideal, ep.extraction_confidence) for ep in points]))`；(4) 对比该类别现有 active standard 指纹（读 `tech_standards.source_fingerprint`；若列不存在在本任务同步新增该列，nullable=True），相同则抛 `STANDARD_ALREADY_UP_TO_DATE`；**(4b) 统计贡献教练数（`SELECT DISTINCT coach_id FROM expert_tech_points JOIN coach_video_classifications ... WHERE kb_tech_category=:tc AND kb_version=:v`）→ `coach_count = COUNT`；推导 `source_quality = 'multi_source' if coach_count>=2 else 'single_source'`**；(5) 写入新 tech_standard（version = per-category MAX+1，status 直接 'active'——见 FR-014a，不走 draft。同事务写入 source_quality / coach_count / point_count / source_fingerprint / built_at）；(6) 归档旧 active standard（仅同类别）
- [ ] T030a [US3] **合并入** `0017_kb_per_category_redesign.py`（不新建独立迁移，遵循 FR-025"单一迁移"约束）：`add_column('tech_standards', sa.Column('source_fingerprint', sa.String(64), nullable=True))` + 建局部唯一索引 `CREATE UNIQUE INDEX uq_ts_fingerprint_per_category ON tech_standards (tech_category, source_fingerprint) WHERE status='active'`。**避免指纹重复写入 + 为 FR-019 幂等检查提供 O(1) 查询**
- [X] T031 [US3] 重构 `src/api/routers/standards.py::POST /build`：Pydantic schema `BuildStandardRequest(tech_category: str)`（非空必填，缺失 422）；响应用 `ok(data)` 构造器；删除老的"tech_category 缺省就全量"的分支代码

**检查点**: US3 完成 → 可独立冒烟"按类别 build 标准 + 幂等拒绝"（参照 quickstart 冒烟 3）→ MVP P1 三故事全达成

---

## 阶段 6: US4 — 按单一技术类别管理教学提示（优先级 P2）

**目标**: `teaching_tips` 行内绑 `(kb_tech_category, kb_version)`；extraction_job 产出时 tips 落 status=draft；KB approve 联动 tips 状态迁移；诊断侧默认只读 active tips。

**独立测试**: extraction_job 产出 `(forehand_attack, v2)` draft KB + 12 条 draft tips → approve KB → 同类别 tips 组态变 active（human 行保留原态）→ GET `/teaching-tips?tech_category=forehand_attack` 默认返回 12 条 active。

### 单元测试（先 Red）

- [ ] T032 [US4] 新建 `tests/unit/test_teaching_tip_svc_lifecycle.py`：覆盖 4 条分支——(a) 该类别首批 KB approve（无旧 active tips 组 → archived_count=0）；(b) 同类别覆盖（旧组 archived、新组 activated）；(c) `source_type='human'` 行归档时保留（archived_count 仅计 auto）；(d) 跨类别独立（批 backhand tips 不影响 forehand tips）

### DAG 产出改造

- [ ] T033pre [US4] **探查**：在 `src/workers/kb_extraction_pipeline/` 下 `grep_search` 定位 persist_kb 实际 executor 文件名（plan.md 假定为 `step_executors/persist_kb.py`，实际路径以项目代码为准）；将真实路径记录到 T033 的文件路径位。若未找到独立 executor，则改修 `kb_extraction_task.py` 或等价主流程入口
- [ ] T033 [US4] 微调上一步探得的 KB 提取持久化等价入口文件：将现有"单次调用 create_draft_version 产出一条 KB"改为"按 `expert_tech_points.action_type` 分组 → 每类别一条 draft KB"；同事务内为每组 points 写入对应 `teaching_tips`（status=draft、kb_tech_category / kb_version 填当前 draft KB 的复合键、tech_category 填该组的 action_type）

### 路由层 / Service 层微调

- [ ] T034 [US4] 修改 `src/api/routers/teaching_tips.py`：列表接口默认 WHERE `status='active'`；支持 `?include_status=draft,archived` 放宽（CSV 解析）；字段响应去掉老 `action_type`、加 `tech_category` / `status`
- [ ] T034a [US4] 新建 `tests/contract/test_teaching_tips_default_active.py`：合约测试 **FR-023 默认只返 active**——3 用例——(a) 默认请求仅返 `status='active'` 行；(b) `?include_status=draft,archived` 返回这两状态；(c) `?include_status=pending` 非法笔举值返 400 + `INVALID_ENUM_VALUE`
- [ ] T035 [US4] 修改 `src/api/schemas/teaching_tip.py`：去掉 `action_type` 字段；加 `tech_category` / `status`；人工创建 tip 的请求 schema 新增 `tech_category` 必填 + `kb_tech_category` + `kb_version` 必填

**检查点**: US4 完成 → 可独立冒烟"tips 随 KB approve 联动激活"（参照 quickstart 冒烟 4）

---

## 阶段 7: US5 — extraction_job 反查 output_kbs（优先级 P2）

**目标**: `GET /api/v1/extraction-jobs/{id}` 响应新增 `output_kbs` 数组，列出该作业产出的全部 `(tech_category, version)` 记录。

**独立测试**: 任取一条 `status=succeeded` extraction_job → GET 详情 → `data.output_kbs` 非空数组 → 每条 `(tech_category, version)` 可回查 KB 列表命中。

### 合约测试（先 Red）

- [ ] T036 [US5] 新建 `tests/contract/test_extraction_job_detail.py`：按 [contracts/extraction-job-detail.yaml](./contracts/extraction-job-detail.yaml) 验证；覆盖 200（succeeded 含非空 output_kbs、running 含空数组）、404 `EXTRACTION_JOB_NOT_FOUND`

### 实现

- [ ] T037 [US5] 修改 `src/services/extraction_job_svc.py`（或等价 service）：详情查询在已有主体之外，补一次 `SELECT tech_category, version, created_at FROM tech_knowledge_bases WHERE extraction_job_id = :jid ORDER BY tech_category` → 填入响应 `output_kbs`
- [ ] T038 [US5] 修改 `src/api/schemas/extraction_job.py`：`ExtractionJobDetailResponse` 增 `output_kbs: list[OutputKbRef]`；`OutputKbRef` 含三字段 `tech_category/version/created_at`
- [ ] T039 [US5] 修改 `src/api/routers/extraction_jobs.py`：GET 详情路由使用新 schema；响应通过 `ok(data)` 构造

**检查点**: US5 完成 → 可独立冒烟"job → output_kbs 反查"（参照 quickstart 冒烟 2 后半）

---

## 阶段 8: 收尾与横切关注点

**目的**: 跨故事的最终打磨、文档同步、章程合规自检。

### 章程合规：业务流程文档双向同步（原则 X 强制）

- [ ] T040 **合并前必做** 运行 `/skills refresh-docs` 或人工编辑，同步 `docs/business-workflow.md`：§ 4.2 单 active 措辞 "全局单 active" → "per-(tech_category) 单 active"；§ 4.3 状态机注释补"作用域 = 单 tech_category"；**§ 7.2 步骤级指标 tag 补 `tech_category`**（`kb_version_activate_per_category` / `standards_build_per_category`）；§ 7.4 错误码表追加 4 个新 code（`KB_CONFLICT_UNRESOLVED` / `KB_EMPTY_POINTS` / `NO_ACTIVE_KB_FOR_CATEGORY` / `STANDARD_ALREADY_UP_TO_DATE`）。**章程原则 X 强制：本任务未完成前不得执行 T048 提交**
- [ ] T041 [P] 同步更新 `docs/architecture.md` 的 KB 实体图（主键变复合）与 `docs/features.md` 的 Feature-019 摘要；实体关系示意图新增 "tech_category / version" 双字段
- [ ] T042 [P] 新建 / 更新 `specs/019-kb-per-category-lifecycle/contracts/retirement-ledger.md`：记录 T020 / T027 中改为 ENDPOINT_RETIRED 哨兵的老路径（方法 + 路径 + successor + migration_note + 哨兵文件行数）；形式与 `specs/017-api-standardization/contracts/retirement-ledger.md` 对齐（只可追加、不可删除）

### 冲突检测端点联动（若 Feature-014 遗留 conflict-check 接口）

- [ ] T043 检查 `src/api/routers/knowledge_base.py` 中是否有老的 `POST /versions/{version}/conflict-check`；若有则签名同步改复合主键 `/versions/{tech_category}/{version}/conflict-check`；否则跳过

### 端到端冒烟

- [ ] T044 按 [quickstart.md](./quickstart.md) § 4 四个冒烟脚本手工执行一遍，确认 US1~US5 端到端通过；截图或日志归档到 `/tmp/feature-019-smoke-YYYYMMDD.log`

### 章程合规自检清单

- [ ] T045 运行 quickstart.md § 6 的 8 项章程检查清单，逐项打钩（含合约测试先 Red、信封构造器使用、`AppException` 使用、4 新 ErrorCode 三张表登记、迁移幂等 3 次等）

### 性能 / 准确性基准核对（SC-001~SC-007）

- [ ] T046 运行 `tests/integration/` 全量测试，额外针对 SC-002 跑一次"200 条 KB 记录列表接口 P95 延迟"脚本（可用 `hey` 或 `locust`，脚本放 `specs/019-kb-per-category-lifecycle/scripts/bench_kb_list.py`），断言 P95 ≤ 300 ms；针对 SC-004 跑一次 standards build 耗时断言 ≤ 10 s
- [ ] T047 运行全量单元 + 集成 + 合约测试（`/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/ -v`）；所有新增测试必须 Green；任何既有测试被本 Feature 间接打破（如 `test_kb_*` 老用例）必须同步更新或删除

### 提交

- [ ] T048 `git add specs/019-kb-per-category-lifecycle` + `git add src/` + `git add tests/` + `git add docs/`；提交消息遵循"Feature-019: <Task ID> <摘要>"；按 quickstart § 6 自检通过后推送远程 `git push origin 019-kb-per-category-lifecycle`

---

## 依赖关系图

```
          ┌─ T003 (0017 upgrade) ─┐
 T001→T002┤                       ├─→ T005/T006/T007/T008/T009/T010/T011 (ORM 模型 [P])
          └─ T004 (0017 downgrade)┘                     ↓
                                                     T012 (ErrorCode)
                                                        ↓
                                                     T013 (迁移 roundtrip test)
                                                        ↓
                                     ┌────────────┬─────┴─────┬──────────┬──────────┐
                                     ↓            ↓           ↓          ↓          ↓
                                 US1 [P1]      US2 [P1]   US3 [P1]   US4 [P2]   US5 [P2]
                        T014pre→T014-T021    T022-T027  T028-T031     T032-T035     T036-T039
                                                           +T030a     +T033pre
                                                                      +T034a

                                                                          ↓
                                                                    收尾 T040-T048
```

### 故事间依赖

- **US1、US2、US3、US4、US5 互相独立**：理论上可 5 个并行；实际建议顺序 US1 → US2 → US3 → US4 → US5（P1 先于 P2；US4 依赖 US1 的 `approve_version` 联动点 ⇒ 若并行需 mock）
- **US4 → US1 软依赖**：US4 T033 调用 US1 T018 `teaching_tip_svc.relink_on_kb_approve`；若 US4 先行，可用 stub 替代，US1 交付时替换成真实 service
- **收尾阶段 T040-T048 严格在 US1-US5 全部完成之后**

---

## 并行执行示例

### 在基础阶段并行跑 7 个 ORM 模型任务（T005-T011）

```bash
# 全部 7 个模型文件互相独立，可在 T003-T004 完成后同时开启
git worktree add ../w1 -b task-t005 && git worktree add ../w2 -b task-t006 ...
# 或用单 worktree 分 7 个 PR 并发 review（推荐后者）
```

### 在用户故事阶段并行跑合约测试（T014 / T022 / T023 / T028 / T036）

5 个合约测试分属 5 个故事，文件独立，可同时创建（TDD Red 阶段）。

### 在收尾阶段并行 T041 / T042

两个文档更新任务互不干扰。

---

## 实现策略

### MVP（最小可验证产物）= US1 + US2 + US3（三个 P1）

交付 MVP 的最短路径：
1. 阶段 1（T001-T002）+ 阶段 2（T003-T013，含迁移与基础测试）
2. 阶段 3 US1（T014-T021，approve 核心）
3. 阶段 4 US2（T022-T027，查询能力）
4. 阶段 5 US3（T028-T031，standards 按类别构建）
5. 收尾最小子集（T044 冒烟 1/2/3 + T047 测试全绿 + T048 提交）

**MVP 大约 30 个任务；US4/US5 + 完整收尾 18 个任务作为增量。**

### 增量交付节点

- **节点 α**：T001-T013 完成 ⇒ 迁移与 schema 就绪
- **节点 β**（MVP P1）：+ US1+US2+US3 ⇒ 核心业务诉求达成、可接入专家操作台
- **节点 γ**（完整 Feature）：+ US4+US5 + 收尾 ⇒ 教学提示联动 + 反查便利性

### 风险与回滚

- **风险等级**: low-risk（系统未上线）
- **单任务回滚**: 每个任务独立可 `git reset`
- **阶段回滚**: 任一故事失败 → `alembic downgrade -1` 回到 0016 + `git checkout master`
- **强制退路**: `/skills system-init` 清业务数据 + `alembic downgrade base && upgrade head` 重建

---

## 任务总览

| 阶段 | 任务数 | 故事 | 可并行任务数 |
|------|--------|------|-------------|
| 1 · 设置 | 2 | — | 0 |
| 2 · 基础 | 11 | — | 7（T005-T011） |
| 3 · US1 | 9 | P1 · US1 | 1（T021） |
| 4 · US2 | 6 | P1 · US2 | 0 |
| 5 · US3 | 5 | P1 · US3 | 0 |
| 6 · US4 | 6 | P2 · US4 | 0 |
| 7 · US5 | 4 | P2 · US5 | 0 |
| 8 · 收尾 | 9 | — | 2（T041/T042） |
| **合计** | **52** | — | **10** |

### 独立测试标准（每故事）

| 故事 | 独立测试脚本 | 冒烟场景 |
|------|-------------|---------|
| US1 | `tests/contract/test_kb_version_approve.py` + `tests/unit/test_approve_version_branches.py` | quickstart 冒烟 1 |
| US2 | `tests/contract/test_kb_versions_list.py` + `tests/contract/test_kb_version_detail.py` | quickstart 冒烟 2 前半 |
| US3 | `tests/contract/test_standards_build_per_category.py` + `tests/unit/test_tech_standard_builder_per_category.py` | quickstart 冒烟 3 |
| US4 | `tests/unit/test_teaching_tip_svc_lifecycle.py` | quickstart 冒烟 4 |
| US5 | `tests/contract/test_extraction_job_detail.py` | quickstart 冒烟 2 后半 |

### 格式验证

- ✅ 所有任务 T001-T048 主干 + T014pre / T030a / T033pre / T034a 4 条插入项（共 **52** 条）采用 `- [ ] T### [P?] [USn?] 描述 + 文件路径` 格式
- ✅ [P] 标记仅出现在不同文件、无依赖的任务（T005-T011、T041、T042）；T021 已由 T014pre 承接标为 [NOOP]
- ✅ [USn] 标签仅出现在阶段 3-7 的用户故事任务上
- ✅ 每个任务包含具体文件路径（.py / .md / .yaml）
- ✅ 设置 / 基础 / 收尾任务不带故事标签
- ✅ 章程合规：T020 / T027 采用 ENDPOINT_RETIRED 哨兵（原则 IX）；T040 作为 T048 合并前置（原则 X）

---

## 下一步

- **`/speckit.analyze`**：对 spec.md / plan.md / tasks.md 做跨制品一致性分析（推荐在开始实施前跑一次）
- **`/speckit.implement`**：分阶段按 T001-T048（含 T014pre / T030a / T033pre / T034a 4 条 analyze 阶段补入项）顺序开始实施（或直接跳到 US1 开始 MVP）
