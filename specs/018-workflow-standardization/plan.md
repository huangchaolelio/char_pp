# 实施计划: 处理流程规范化（Workflow Standardization）

**分支**: `018-workflow-standardization` | **日期**: 2026-04-30 | **规范**: [spec.md](./spec.md)
**输入**: 来自 `/specs/018-workflow-standardization/spec.md` 的功能规范（Clarifications Q1–Q5 已决议）

## 摘要

本 Feature 把章程 v1.5.0 原则 X 定义的**三阶段（TRAINING / STANDARDIZATION / INFERENCE）八步骤**业务执行模型显式落到系统四个面：
1. **数据模型侧**：四张核心业务表（`analysis_tasks` / `extraction_jobs` / `video_preprocessing_jobs` / `tech_knowledge_bases`）新增 `business_phase` + `business_step` 双列，以 ORM `before_insert` 钩子集中派生 + 列级 `NOT NULL` 双层约束保障 0% NULL（Clarification Q4）。
2. **对外可观测侧**：新增只读聚合接口 `GET /api/v1/business-workflow/overview`，一次请求返回三阶段全景（计数 + 耗时百分位 + 24h 吞吐），并按 `analysis_tasks` 行数自动降级（≤100 万完整 / (100 万, 1000 万] 降级 / >1000 万外包给后续物化视图 Feature，见 Q3）。
3. **治理侧**：在 `scripts/audit/` 下新增 `workflow_drift.py` 与 `spec_compliance.py` 两脚本，扫描「代码集中清单 ↔ `docs/business-workflow.md`」漂移与「`specs/*/spec.md` 是否含『业务阶段映射』段落」合规；CI 分两层闸门（PR `--changed-only` + master `--full`），本地可预演；**不引入 waiver 运行期豁免机制**（Q5），违规即硬失败。
4. **优化杠杆侧**：新增只读接口 `GET /api/v1/admin/levers`，聚合 § 9 三类杠杆（运行时参数 / 算法与模型 / 规则与 Prompt）统一台账，台账条目通过 `config/optimization_levers.yml` 显式枚举并纳入漂移扫描。

本 Feature **不新增业务步骤**，仅在既有八步骤外围补「观测与治理」平面；亦 **不改变**任一既有章程级约束（队列拓扑 / 状态机 / 错误码前缀 / 评分公式），仅把它们的「集中清单 → 文档」同步自动化。

## 技术背景

**语言/版本**: Python 3.11（`/opt/conda/envs/coaching/bin/python3.11`）
**主要依赖**:
- Web: FastAPI（复用 Feature-017 `SuccessEnvelope` / `AppException` / `ErrorCode` 三件套）
- ORM: SQLAlchemy 2.x + `AsyncSession`（`async_session_factory`）；**本 Feature 引入 `sqlalchemy.event` 钩子**
- 迁移: Alembic（当前 head `0015_kb_audit_and_expand_action_types`，本 Feature 新增 `0016_business_phase_step`；`down_revision = "0015"`）
- 扫描脚本: 纯 Python 3.11 标准库 + PyYAML（读取 `config/optimization_levers.yml` / `scripts/audit/.scan-exclude.yml`）+ `pathlib.Path.read_text` 解析 markdown
- 测试: pytest / pytest-asyncio（`tests/unit` + `tests/integration` + `tests/contract`）

**存储**: PostgreSQL（既有）；本 Feature 仅增列与索引，无新增表。

**测试**: `/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/ -v`

**目标平台**: Linux 服务器（uvicorn + Celery 5 队列多 worker）

**项目类型**: 单一后端服务（后端 API + Celery workers + 离线脚本）；**无前端代码**（章程附加约束）

**性能目标**（详见 spec FR-007 / SC-003）:
- `GET /api/v1/business-workflow/overview`（≤ 100 万行）：P95 ≤ 500ms / P99 ≤ 1s，QPS ≥ 20
- `GET /api/v1/business-workflow/overview`（(100 万, 1000 万]）：P95 ≤ 1s / P99 ≤ 2s，响应 `meta.degraded=true`
- `GET /api/v1/business-workflow/overview`（> 1000 万行）：**超出本 Feature 范围**（Assumption § 5），由后续物化视图 Feature 交付；本 Feature 遇此场景 MAY 返回 `meta.degraded=true` + `meta.degraded_reason="row_count_exceeds_latency_budget"`，不承诺 SLA
- `GET /api/v1/admin/levers`：无并发压力（运维侧低频访问），P95 ≤ 200ms
- `scripts/audit/workflow_drift.py --changed-only` pre-push 运行 ≤ 30s；`--full` ≤ 120s

**约束条件**:
- 列级 `NOT NULL` + ORM 钩子双层，写入路径全覆盖（HTTP 入口、Celery 内部衍生写入、迁移脚本、直接 ORM）
- 聚合 MUST 基于 `idx_analysis_tasks_channel_counting(task_type, status)`；降级阈值判断走 `pg_class.reltuples`，禁止 `COUNT(*)`
- 漂移扫描「代码 → 文档」单向语义；退出码 0 / 1；输出格式机器可读
- 无 waiver 运行期豁免（Q5）；`.scan-exclude.yml` 仅用于 17 个历史 Feature 目录静态排除
- 敏感杠杆键（`VENUS_TOKEN` / `OPENAI_API_KEY` / `COS_SECRET_KEY` / `POSTGRES_PASSWORD`）响应中仅返回 `is_configured: bool`
- **CI 守卫落地方式（Clarification Q6 选项 A）**：本 Feature **不引入任何托管 CI 平台配置**（不新增 `.github/workflows/` / `.gitlab-ci.yml` / `Jenkinsfile`），透过仓库根 `Makefile` + `scripts/git-hooks/pre-push` + `scripts/install-git-hooks.sh` 在本地实现双层闸门；未来引入任意托管 CI 时仅需在配置中调用 `make drift-changed` / `make drift-full` 目标（YAGNI）

**规模/范围**:
- 8 个 `business_step` 枚举值（Q1 决议，对齐 § 5.1）
- 4 张业务表 × 2 新列（共 8 列）
- 3 个新接口（`business-workflow/overview` / `admin/levers` / 既有列表接口扩展 4 组 `?business_phase=` / `?business_step=` 查询参数）
- 2 个新脚本（`workflow_drift.py` / `spec_compliance.py`）
- 1 个新静态配置（`config/optimization_levers.yml`）+ 1 个新排除清单（`scripts/audit/.scan-exclude.yml`）
- 17 个历史 Feature 目录列入静态排除；019+ 新 Feature 强制合规

## 章程检查（阶段 0 前）

**章程合规验证**（逐项对照 v1.5.0）:
- ✅ **原则 VIII（量化精准度）**: spec 含 SC-001 ~ SC-009 共 9 条可量化成功标准；涉及性能的 SC-003 按行数分两档精确给出 P95/P99 预算；涉及合规率的 SC-004/005/006 量化为百分比与条数
- ✅ **附加约束（无前端）**: 本 Feature 零前端代码，新接口为 JSON 只读，与章程约束自洽
- ✅ **原则 VI（AI 模型治理）**: 本 Feature 不涉及 AI 模型推理路径，仅登记 `POSE_BACKEND` / Whisper 模型大小等"杠杆键"的元信息，**只读不执行**，无模型版本或精度回归风险
- ✅ **原则 VII（隐私与安全）**: 台账接口严格屏蔽敏感键值（FR-015）；`/overview` 仅返回聚合计数，不泄露用户视频内容；`/admin/levers` 复用既有 `ADMIN_TOKEN` 鉴权
- ✅ **原则 IX（API 规范统一）**:
  - 三个新接口均落在 `/api/v1/**`；路由按资源划分：
    * `src/api/routers/business_workflow.py`（新增，前缀 `/business-workflow`）
    * `src/api/routers/admin.py`（扩展，新增 `GET /admin/levers`）
    * `src/api/routers/tasks.py` / `extraction_jobs.py` / `knowledge_base.py`（扩展查询参数）
  - 分页参数 `page` / `page_size` 仅适用于 `?business_phase=` 筛选后的列表接口，沿用既有契约
  - **响应信封**: 所有新接口 100% 使用 `SuccessEnvelope[T]` + `ok()` / `page()` 构造器；不新增顶层字段
  - **错误响应**: 统一抛 `AppException`；新增 3 个错误码 `INVALID_PHASE_STEP_COMBO`（400，`phase + step + task_type` 矛盾）/ `PHASE_STEP_UNMAPPED`（500，钩子派生失败的内部 fail-fast 信号）/ `OPTIMIZATION_LEVERS_YAML_INVALID`（500，台账 YAML 启动时 schema 校验失败）；降级状态通过 200 响应的 `meta.degraded=true` 表达，**不引入独立错误码**（YAGNI）
  - **错误码集中化**: 3 个新 code 同步登记 `src/api/errors.py::ErrorCode` + `ERROR_STATUS_MAP` + `ERROR_DEFAULT_MESSAGE` + `specs/018-workflow-standardization/contracts/error-codes.md`
  - **哨兵路由**: 本 Feature 不下线任何接口
  - 新增/变更接口契约落在 `specs/018-workflow-standardization/contracts/`；合约测试先于实现（原则 II）
- ✅ **原则 X（业务流程对齐）**:
  - `spec.md` 已含「业务阶段映射」段落，六项齐全（阶段 / 步骤 / DoD / 可观测锚点 / 约束影响 / 回滚剧本）
  - 队列拓扑 / 状态机 / 错误码前缀 / 评分公式 / 单 active / 冲突门控——本 Feature **不修改任一章程级约束**，仅把它们的「代码侧集中清单」与 `docs/business-workflow.md` 的同步**自动化**（这是 US2 本身的交付物）
  - 优化活动：本 Feature 不做性能优化，而是建立"三类杠杆"的入口登记（US3）；未来对台账本身的增减，将明确命中 § 9 「规则/Prompt」或「运行时参数」对应类别
  - 回滚剧本：spec「回滚剧本」节已列出 US1 迁移可 `alembic downgrade -1`、US1 接口可直接下线、US2 CI 守卫无 waiver（靠原子 PR 修复）、US3 接口可局部降级（`data.errors[]` 字段）；无涉及 § 10 高危操作

**门控结论**: ✅ **全部通过**，阶段 0 可进入研究。

## 项目结构

### 文档（此功能）

```
specs/018-workflow-standardization/
├── plan.md                              # 本文件（/speckit.plan 输出）
├── spec.md                              # 功能规范（已含 Clarifications Q1–Q5）
├── research.md                          # 阶段 0 输出（本次生成）
├── data-model.md                        # 阶段 1 输出（本次生成）
├── quickstart.md                        # 阶段 1 输出（本次生成）
├── contracts/
│   ├── business-workflow-overview.yaml  # 阶段 1 OpenAPI 片段
│   ├── admin-levers.yaml                # 阶段 1 OpenAPI 片段
│   ├── error-codes.md                   # 阶段 1 新增错误码登记
│   └── retirement-ledger.md             # 阶段 1（本 Feature 无下线条目，保留占位）
└── tasks.md                             # 阶段 2 输出（/speckit.tasks 生成）
```

### 源代码（仓库根目录）

> 本项目为**单一后端服务**（`src/` + `tests/`），无前端目录。以下仅列出本 Feature 新增或修改的文件。

```
src/
├── api/
│   ├── errors.py                         # 修改：新增 3 个 ErrorCode + 映射
│   ├── routers/
│   │   ├── business_workflow.py          # 新增：GET /api/v1/business-workflow/overview
│   │   ├── admin.py                      # 修改：新增 GET /api/v1/admin/levers
│   │   ├── tasks.py                      # 修改：扩展 ?business_phase= / ?business_step= 查询参数
│   │   ├── extraction_jobs.py            # 修改：扩展查询参数
│   │   └── knowledge_base.py             # 修改：扩展查询参数
│   ├── schemas/
│   │   ├── business_workflow.py          # 新增：WorkflowOverviewSnapshot / PhaseSnapshot / StepSnapshot
│   │   └── admin_levers.py               # 新增：LeverEntry / LeverGroups
│   └── main.py                           # 修改：include_router(business_workflow.router, prefix="/api/v1")
├── models/
│   ├── _phase_step_hook.py               # 新增：集中注册 before_insert 钩子 + 派生表
│   ├── analysis_task.py                  # 修改：新增 business_phase / business_step 两列 + 绑定钩子
│   ├── extraction_job.py                 # 修改：同上
│   ├── video_preprocessing_job.py        # 修改：同上
│   └── tech_knowledge_base.py            # 修改：同上
├── services/
│   ├── business_workflow_service.py      # 新增：聚合查询 + 降级判断（reltuples）
│   └── optimization_levers_service.py    # 新增：加载 YAML + 运行时值收集 + 敏感过滤
└── db/migrations/versions/
    └── 0016_business_phase_step.py       # 新增：四表增列 + 回填 SQL + 添加 NOT NULL + 索引
                                          # （当前 head = 0015_kb_audit_and_expand_action_types，
                                          #  本迁移 `revision="0016"` / `down_revision="0015"`）

config/
└── optimization_levers.yml                # 新增：三类杠杆键的静态登记台账

scripts/audit/
├── workflow_drift.py                     # 新增：代码 ↔ 文档漂移扫描
├── spec_compliance.py                     # 新增：specs/*/spec.md 合规扫描
├── _spec_sections.py                      # 新增：REQUIRED_BUSINESS_STAGE_FIELDS 常量（Q8）
├── .scan-exclude.yml                      # 新增：初始 17 个历史 Feature 目录静态排除
└── __init__.py                            # 新增：包标记

scripts/git-hooks/
└── pre-push                               # 新增：shell 脚本，push 前执行 `make drift-changed`（Q6）

scripts/
└── install-git-hooks.sh                   # 新增：幂等软链安装器（首次 clone 后手执一次）

Makefile                                   # 新增：声明 drift-changed / drift-full / spec-compliance 三目标（Q6）

tests/
├── unit/
│   ├── models/test_phase_step_hook.py    # 新增：覆盖直接 ORM 写入 fail-fast
│   ├── services/test_business_workflow_service.py
│   ├── services/test_optimization_levers_service.py
│   └── audit/test_workflow_drift_parser.py
├── contract/
│   ├── test_business_workflow_overview_contract.py
│   ├── test_admin_levers_contract.py
│   └── test_tasks_phase_step_filter_contract.py
└── integration/
    ├── test_phase_step_migration.py      # Alembic upgrade → 回填 → NOT NULL 端到端
    ├── test_workflow_overview_degradation.py
    └── test_drift_scan_end_to_end.py

docs/
└── business-workflow.md                  # 修改：§ 7.1 / § 7.4 追加"由 CI 漂移扫描守护"脚注；
                                          #       新增 § 7.6「业务阶段总览」接口文档
                                          # （通过 refresh-docs skill 在 Feature 合并后刷新）
```

**结构决策**: 沿用既有「单一后端服务」结构（章程附加约束「路径约定」），无需切换到多服务或移动端结构。新增模块严格落位到对应的 `src/api/routers/` / `src/api/schemas/` / `src/services/` / `src/models/` / `scripts/audit/` 分层，符合项目规则 1「分层职责」。

## 阶段 0 — 研究

**研究主题清单**（Feature-018 无 NEEDS CLARIFICATION 项——Q1–Q5 已全部决议；此处为"最佳实践 + 风险摸底"任务）:

1. **SQLAlchemy `before_insert` 事件钩子与 `Enum` 默认值的协同** — 验证钩子在 `session.add()` 之后、INSERT flush 之前执行的时机稳定性；以及显式传入 `phase`/`step` 时 `assigned` 属性的判定语义。
2. **PostgreSQL `pg_class.reltuples` 估算稳定性与 `ANALYZE` 频率** — 确定降级阈值判断在大表上的可靠性与偏差率（FR-007）。
3. **Alembic「ADD COLUMN → 回填 → ALTER NOT NULL」原子事务策略** — 在 2600 万行量级下是否需要 `CREATE TABLE AS ... SELECT ... WITH DATA` 加速；对 Feature-013 / Feature-014 已在位的索引是否产生锁表冲击。
4. **漂移扫描的 markdown 解析策略** — 选择基于正则 + anchored-section 切片 的轻量解析 vs 引入 `mistletoe`/`markdown-it-py` 完整 AST；本 Feature 只扫单文件故采用前者。
5. **`config/optimization_levers.yml` 的敏感位标记语义** — 对齐现有 `.env` 敏感变量集与 `src/config/settings.py` 的 `SecretStr` 用法。
6. **FastAPI 查询参数级联校验**（`?business_phase=TRAINING&task_type=athlete_diagnosis` 逻辑矛盾） — 在 `Depends` 依赖函数集中校验还是在 service 层校验；决定是否新增 `INVALID_PHASE_STEP_COMBO` 错误码。
7. **CI `--changed-only` 模式的"涉及清单"计算算法** — 基于 `git diff --name-only origin/master...HEAD` 做代码侧命中；对文档侧按 anchored-section 计算命中；本地可 `--commit-range` 覆写。

**输出**: `specs/018-workflow-standardization/research.md`（阶段 0 产物，本次与阶段 1 产物一并在下一轮工具调用中生成）。

## 阶段 1 — 设计与契约

**前置**: 阶段 0 `research.md` 完成（所有"最佳实践"项形成 Decision / Rationale / Alternatives 三元组）。

**产物**:

1. **`data-model.md`** — 完整描述：
   - `BusinessPhase` Postgres enum type（3 值）
   - 四张业务表各自的 `business_phase` / `business_step` 列定义（含 index 建议）
   - `_phase_step_hook.py` 派生表（`task_type` → `(phase, step)` / 空 `task_type` 表的默认映射）
   - `analysis_tasks` 迁移回填 SQL（基于 spec FR-002 映射表 + `parent_scan_task_id` 判定）
   - `OptimizationLever` YAML schema（字段约束与加载时校验规则）
   - `WorkflowOverviewSnapshot` DTO（降级档的字段省略契约）

2. **`contracts/business-workflow-overview.yaml`** — OpenAPI 3.1 片段：
   - `GET /api/v1/business-workflow/overview?window_hours={1-168}`
   - 200 响应：完整信封 + 降级信封两个 `examples`
   - 400 错误：`INVALID_ENUM_VALUE`（`window_hours` 越界）

3. **`contracts/admin-levers.yaml`** — OpenAPI 3.1 片段：
   - `GET /api/v1/admin/levers?phase={TRAINING|STANDARDIZATION|INFERENCE}`
   - 200 响应 `examples`：含敏感键被屏蔽的示例
   - 401 错误：`ADMIN_TOKEN_INVALID`
   - 500 错误：`ADMIN_TOKEN_NOT_CONFIGURED`（fail-safe）

4. **`contracts/error-codes.md`** — 新增 3 个错误码登记：
   - `INVALID_PHASE_STEP_COMBO`（400，`?business_phase=` 与 `?task_type=` / `?business_step=` 语义矛盾）
   - `PHASE_STEP_UNMAPPED`（500，钩子派生失败的内部 fail-fast 信号，透给监控而不透给终端用户）
   - `OPTIMIZATION_LEVERS_YAML_INVALID`（500，`config/optimization_levers.yml` 启动时 schema 校验失败，API 启动失败）

5. **`contracts/retirement-ledger.md`** — 占位（本 Feature 不下线任何接口）：
   ```md
   # Feature-018 下线接口台账
   本 Feature 未下线任何接口。
   ```

6. **`quickstart.md`** — 面向研发与 SRE 的 15 分钟上手：
   - 本地运行漂移扫描：`python -m scripts.audit.workflow_drift --changed-only`
   - 构造 workflow overview mock 数据：`pytest tests/integration/test_workflow_overview_degradation.py -k "happy_path"`
   - 触发 `PHASE_STEP_UNMAPPED` 故障注入样例
   - 台账接口鉴权示例（`curl -H "X-Admin-Token: $ADMIN_TOKEN" ...`）

7. **代理上下文更新**: 运行 `.specify/scripts/bash/update-agent-context.sh codebuddy`，仅从本计划中追加新技术（SQLAlchemy 事件钩子 + PyYAML + reltuples 估算）。

**设计后的章程复查**（阶段 1 完成后再评估）:
- ✅ 原则 II（TDD）: 契约测试先于实现（`tests/contract/*` 在 plan 的阶段 1 产物中即登记）
- ✅ 原则 IV（YAGNI）: 不引入 waiver 机制、不引入 markdown AST 重量级依赖、不预留"第四类杠杆"扩展点（Q5 / 研究 4 / 假设 6）
- ✅ 原则 IX: 新增 3 个错误码同步登记 3 张表 + `contracts/error-codes.md`，对齐单一事实来源
- ✅ 原则 X: 新增 § 7.6「业务阶段总览」子节需要在 Feature 合并后通过 refresh-docs skill 同步到 `docs/business-workflow.md`，已在 spec「回滚剧本」+ 本 plan 「结构决策」中显式登记

**停止条件**: 本命令在阶段 2 规划后结束（不进入 `/speckit.tasks`）。

## 复杂度跟踪

> **无需填写**——阶段 0 前与阶段 1 后两次章程检查均通过，无必须证明的违规；本 Feature 严格遵循章程 v1.5.0 简洁优先（YAGNI）与原则 IX / X。

| 违规 | 为什么需要 | 拒绝更简单替代方案的原因 |
|------|------------|-------------------------------------|
| （无） | — | — |

