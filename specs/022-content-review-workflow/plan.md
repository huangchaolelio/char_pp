# 实施计划: 业务流程四阶段化 + 内容准备阶段引入审核门

**分支**: `022-content-review-workflow` | **日期**: 2026-05-28 | **规范**: [spec.md](./spec.md)
**输入**: 来自 `/specs/022-content-review-workflow/spec.md` 的功能规范（已澄清，5 个问题已闭环）

## 摘要

把现有 `business_phase_enum`（3 阶段：TRAINING / STANDARDIZATION / INFERENCE）扩展为 4 阶段（**新增 `CONTENT_PREP`** + 既有 3 阶段），并把 `scan_cos_videos` / `preprocess_video` / `classify_video` / `curate_segments` 四个既有步骤的 `business_phase` 重新归属到 `CONTENT_PREP`；同时新增第 9 步 `content_review` 作为 `CONTENT_PREP` 阶段的最终判据步骤。

技术方法：
- **承载方式**：审核状态字段直接挂在既有 `coach_video_classifications` 表上（澄清 Q1：粒度=整段视频条目，与该表行 1:1），不另建子粒度表；决策留痕表 `content_review_decisions` 独立建表（承载多版本审核历史）
- **审核门接入**：仿 Feature-021 `evaluate_curation_gate` 风格，新增 `evaluate_review_gate(cos_object_key)`，在 `submit_kb_extraction` / `submit_kb_extraction_batch` 路由 + DAG 第一步双点拦截
- **运行时开关**：复用 Feature-018 lever 体系，`settings.kb_extraction_bypass_review_gate` + 数据库行级 hot-config（用 `task_channel_configs` 同款热刷新机制）
- **API 路由**：新增独立资源路由 `src/api/routers/content_reviews.py`（前缀 `/api/v1/content-reviews`），鉴权沿用 `X-Admin-Token`（项目无登录态）
- **错误码**：在 `src/api/errors.py` 集中新增 `CONTENT_NOT_REVIEWED` / `CONTENT_REVIEW_REJECTED` / `CONTENT_REVIEW_STALE`，全部映射 409
- **迁移**：`0021_content_review_workflow.py`（扩 enum + 加列 + 加索引 + 建审核决策表）

## 技术背景

**语言/版本**: Python 3.11（项目虚拟环境 `/opt/conda/envs/coaching/bin/python3.11`，对齐章程「Python 环境隔离」）
**主要依赖**: FastAPI、SQLAlchemy 2.x（async）、Alembic、Pydantic v2、Celery、Redis（broker）
**存储**: PostgreSQL（业务数据 + Alembic 迁移）；COS（视频对象存储）；Redis（Celery broker + 通道槽位）
**测试**: pytest（unit / integration / contract 三层）；最低运行环境 = `tests/contract/` 必须先于实现创建并失败
**目标平台**: Linux 服务器（与既有 Feature 002–021 同构）
**项目类型**: 单体后端服务（`src/` + `tests/`，无前端任务，章程附加约束）
**性能目标**:
  - 审核工作台列表 `page_size ≤ 50` P95 < 500 ms（FR-017 / 澄清 Q4）
  - `page_size = 100` P95 < 1 s
  - 审核决策提交（POST `/content-reviews/{id}/decisions`）P95 < 200 ms
  - KB 抽取入口审核门校验追加延迟 < 20 ms（一次 PK lookup）
**约束条件**:
  - 项目处于测试阶段（章程原则 XI），**不为存量数据回填审核状态写迁移脚本**；存量行新增 `review_state` 列默认 `pending_review`
  - 项目无用户登录体系（仅 `X-Admin-Token` 头），`reviewer_id` 由请求体或 header `X-Reviewer-Id` 显式传入
  - 不新增 Celery 队列（审核为人工动作，不入队列）
**规模/范围**:
  - 累计待审核条目 ≤ 50 万条（澄清 Q4）
  - 日新增 50–200 条（与现有教练视频归集吞吐对齐）
  - 决策留痕表预估 < 100 万行（每条审核条目期望 ≤ 2 次决策：1 次首审 + 偶发重洗后再审）

## 章程检查

> **门控**：必须在阶段 0 研究前通过；阶段 1 设计后重新检查。

**章程合规验证**:
- ✅ **原则 I 规范驱动开发**：spec.md 已就位且含「业务阶段映射」段；本计划直接基于已澄清规范展开
- ✅ **原则 II 测试优先**：所有新增接口（5 个新 endpoint）将先在 `tests/contract/` 创建合约测试再写实现；状态机校验与审核门走集成测试 `tests/integration/test_022_content_review_gate.py`
- ✅ **原则 III 增量交付**：4 个用户故事按 P1 / P1 / P2 / P3 排序，对应 `tasks.md` 的 4 个独立交付段；P1 的两个故事（阶段重构 + 审核门）共同构成 MVP
- ✅ **原则 IV 简洁性 / YAGNI**：审核状态承载在既有 `coach_video_classifications` 表（不另建子粒度表）；不引入二审/分级/申诉等扩展（澄清 Q2）；不级联中止已派发任务（澄清 Q3）
- ✅ **原则 V 可观测性**：所有审核动作走结构化日志；阶段级 / 步骤级监控锚点已在 FR-012/013 定义
- ✅ **原则 VI AI 模型治理**：本功能不涉及新模型，N/A
- ✅ **原则 VII 隐私与安全**：审核操作走 `X-Admin-Token` 鉴权；`note` 字段视为审计内容，按现有日志策略不脱敏写入 DB（与 spec 假设一致）
- ✅ **原则 VIII 算法精准度**：本功能为流程编排类，无算法精度指标，但 SC-003（审核门 100% 生效）已替代充当合规判据
- ✅ **原则 IX 接口规范统一**（v1.4.0）：
  - 新路由文件 `src/api/routers/content_reviews.py`，前缀 `/api/v1/content-reviews`，资源化 + kebab-case 复数
  - 分页参数 `page` / `page_size`（默认 20、最大 100）；越界返回 400 + `INVALID_PAGE_SIZE`
  - 响应统一信封：成功用 `ok(data)` / `page(...)`；错误统一抛 `AppException(ErrorCode.X)`
  - 错误码集中登记（见下方"错误码新增"）
  - 接口下线策略：本 Feature **只新增不删除**，无下线项；现有 `submit_kb_extraction` 入口的旧"清洗门"保留，新增"审核门"作为第二道关卡
- ✅ **原则 X 业务流程对齐**：
  - spec.md「业务阶段映射」段完整（phase=CONTENT_PREP / step=content_review / DoD / 可观测锚点 / 章程级约束影响 / 回滚剧本）
  - **章程级双向同步**（必须在 plan 阶段或 tasks 阶段执行）：
    - `docs/business-workflow.md`：§ 1 概述 / § 2 阶段判据 / § 3.x 步骤章节归属 / § 7 可观测 / § 9 调参杠杆映射 / § 10 回滚剧本（新增"审核门绕过"剧本）/ § 11 交叉索引
    - `.specify/memory/constitution.md`：原则 X 措辞从"三阶段"修订为"四阶段"，版本 v2.1.0 → v2.2.0（MINOR：原则实质性扩展）
    - `business_phase_enum` ALTER TYPE ADD VALUE `'CONTENT_PREP'`
  - **优化活动命中三种杠杆**：本 Feature 主要用"运行时参数"杠杆（审核门绕过开关 + 积压红线阈值）+ "规则/Prompt"杠杆（拒绝原因码枚举可后续扩展），均落在 § 9 已定义范围
  - **回滚剧本显式化**：FR-014「审核门绕过」是显式的运营级回滚开关，将登记到业务流程文档 § 10 新增章节
- ✅ **原则 XI 测试阶段功能兼容性**：项目处于测试阶段，**不写存量数据回填脚本**；新增列默认 `pending_review`，由运营按需通过审核门绕过开关临时降级或集中补审

**API 接口规范验证要点（原则 IX，v1.4.0 统一信封）**: 全部满足（见上方逐项）。

**业务流程对齐验证要点（原则 X，v1.5.0）**: 全部满足；本 Feature 触发原则 X 的章程级双向同步，已在 tasks.md 中预排专项任务（详见阶段 2 规划）。

**结论**: 章程检查通过，无违规，可进入阶段 0 研究。

## 项目结构

### 文档（此功能）

```
specs/022-content-review-workflow/
├── plan.md              # 此文件（/speckit.plan 命令输出）
├── spec.md              # 功能规范（已澄清）
├── research.md          # 阶段 0 输出（/speckit.plan 命令）
├── data-model.md        # 阶段 1 输出（/speckit.plan 命令）
├── quickstart.md        # 阶段 1 输出（/speckit.plan 命令）
├── contracts/           # 阶段 1 输出（/speckit.plan 命令）
│   ├── content-reviews.yaml      # 5 个新 endpoint 的 OpenAPI 片段
│   └── error-codes.md            # 8 个新错误码登记（详见 contracts/error-codes.md）
├── checklists/
│   └── requirements.md
└── tasks.md             # 阶段 2 输出（/speckit.tasks 命令）
```

### 源代码（仓库根目录，单体后端）

```
src/
├── api/
│   ├── errors.py                        # 扩展 ErrorCode 枚举 + 2 张映射表
│   ├── main.py                          # 注册新 router
│   ├── routers/
│   │   ├── content_reviews.py           # ★ 新增：审核工作台 5 个 endpoint
│   │   └── tasks.py                     # 修改：在 KB 抽取入口插入审核门校验
│   └── schemas/
│       └── content_reviews.py           # ★ 新增：审核相关请求/响应 schema
├── models/
│   ├── coach_video_classification.py    # 修改：新增 review_state / review_version / last_decision_id / pending_since 4 列
│   └── content_review_decision.py       # ★ 新增：审核决策留痕表 ORM
├── services/
│   ├── curation/
│   │   └── kb_gate.py                   # 已有清洗门，作为审核门蓝本
│   └── content_review/                  # ★ 新增 service 包
│       ├── __init__.py
│       ├── review_gate.py               # ★ 审核门：evaluate_review_gate(cos_object_key)
│       ├── review_service.py            # ★ 审核工作台业务（list/decide/stats）
│       └── stale_handler.py             # ★ 重洗回调：清洗成功后置 review_state=stale
└── db/
    └── migrations/versions/
        └── 0021_content_review_workflow.py   # ★ 新增迁移

tests/
├── contract/
│   └── test_022_content_reviews_contract.py   # ★ 5 个 endpoint 契约
├── integration/
│   ├── test_022_review_gate_blocks_kb.py      # ★ FR-008/009 集成
│   ├── test_022_stale_after_recurate.py       # ★ FR-011/011a 集成
│   └── test_022_bypass_switch.py              # ★ FR-014 集成
└── unit/
    └── test_022_review_service.py             # ★ 业务逻辑单测

docs/
└── business-workflow.md                       # 修改：四阶段同步（任务 T001 处理）

.specify/memory/
└── constitution.md                            # 修改：原则 X 措辞同步（任务 T002 处理）
```

**结构决策**: 沿用项目既有的"单体后端"布局（章程附加约束的"标准后端: src/、tests/"），不引入 service 子目录之外的新顶层目录；新增 `src/services/content_review/` 包以隔离审核业务，与 `src/services/curation/` 平级。

## 阶段 0：大纲与研究

> 输出：[research.md](./research.md)

阶段 0 已识别如下决策点（从 `Technical Context` 推导）：

| 序号 | 研究主题 | 性质 | 决策结果 |
|------|---------|------|---------|
| R1 | `business_phase_enum` 扩值方式（PostgreSQL ALTER TYPE） | 依赖最佳实践 | 决策记录到 research.md |
| R2 | `coach_video_classifications` 新增列 vs 子表的取舍 | 数据建模 | 决策：新增列（澄清 Q1） |
| R3 | 审核决策留痕表是否复用 `pipeline_steps` | 数据建模 | 决策：独立建表（粒度不同） |
| R4 | 审核门绕过开关的承载位置 | 运行时配置 | 决策：复用 Feature-018 lever 体系 |
| R5 | 审核员鉴权（项目无登录体系）的最简方案 | 集成模式 | 决策：`X-Admin-Token` + `X-Reviewer-Id` header |
| R6 | 重洗后审核失效的触发点 | 集成模式 | 决策：在 `curation_service` 成功回调中调用 `stale_handler` |
| R7 | 审核统计接口的 SQL 形态（避免对 50 万行做全表聚合） | 性能 | 决策：用 `(review_state, decided_at)` 索引 + 时间窗 WHERE |

**输出**: [research.md](./research.md) 包含上述 7 项决策的 Decision / Rationale / Alternatives considered。

## 阶段 1：设计与契约

> 前提：research.md 已完成
> 输出：[data-model.md](./data-model.md)、[contracts/](./contracts/)、[quickstart.md](./quickstart.md)

### 1.1 数据模型摘要（详见 data-model.md）

**修改实体**：
- `coach_video_classifications` 新增 4 列：
  - `review_state` ENUM(`pending_review` / `approved` / `rejected` / `stale`) NOT NULL DEFAULT `pending_review`
  - `review_version` INTEGER NOT NULL DEFAULT 0（每次状态变更 +1，用作乐观锁/审计序）
  - `last_decision_id` UUID NULL（FK → `content_review_decisions.id`，最近一次决策）
  - `pending_since` TIMESTAMP NULL（进入 `pending_review` 时刻，用于积压告警）
- 新增复合索引（FR-018）：
  - `idx_cvclf_review_state_decided` ON `(review_state, last_decision_id)` — 默认列表
  - `idx_cvclf_coach_review` ON `(coach_name, review_state)` — 教练筛选
  - `idx_cvclf_tech_review` ON `(tech_category, review_state)` — 类别筛选
  - `idx_cvclf_pending_since` ON `(pending_since)` WHERE `review_state='pending_review'` — 积压告警

**新增实体**：
- `content_review_decisions` 表
  - `id` UUID PK
  - `cvclf_id` UUID FK → `coach_video_classifications.id` ON DELETE CASCADE
  - `cleansing_version` UUID FK → `video_curation_jobs.id`（决策针对的清洗版本）
  - `decision` ENUM(`approved` / `rejected`)
  - `reason_code` VARCHAR(64) NULL（拒绝必填，从下方枚举取值）
  - `note` TEXT NULL（自由文本备注）
  - `reviewer_id` VARCHAR(64) NOT NULL
  - `decided_at` TIMESTAMP NOT NULL DEFAULT now()
  - `superseded_at` TIMESTAMP NULL（被新决策覆盖时填）

**枚举定义**：
- `business_phase_enum` ADD VALUE `'CONTENT_PREP'`（迁移 0021 的第一步）
- `reason_code`（应用层 `Enum`，不入 DB enum 以便平滑扩展）：`quality_low` / `tech_irrelevant` / `coach_unauthorized` / `content_duplicated` / `other`

### 1.2 接口契约摘要（详见 contracts/content-reviews.yaml）

> **基础约定**：所有接口走 `/api/v1/` 前缀；分页参数 `page`/`page_size`；统一信封 `success/data/meta` | `success/error`；错误统一 `AppException`。

| # | 方法 | 路径 | 说明 | 用户故事 |
|---|------|------|------|---------|
| EP-1 | GET | `/api/v1/content-reviews` | 列出审核条目（支持 `state` / `coach_name` / `tech_category` / `from` / `to` 过滤；默认列表过滤 `rejected`） | US3 |
| EP-2 | GET | `/api/v1/content-reviews/{cvclf_id}` | 单条详情（含清洗摘要） | US3 |
| EP-3 | POST | `/api/v1/content-reviews/{cvclf_id}/decisions` | 提交"通过/拒绝"决策（请求体含 `decision` / `reason_code` / `note` / `expected_review_version`）；版本不一致返回 409 `REVIEW_VERSION_CONFLICT` | US2 |
| EP-4 | GET | `/api/v1/content-reviews/stats` | 时间窗审核统计（总量/通过率/平均时延/人均吞吐） | US3 |
| EP-5 | PATCH | `/api/v1/admin/review-gate` | 审核门开关（severe/bypass）；走 `X-Admin-Token` | US4 |

**KB 抽取入口的修改**（已有路由 `tasks.py`）：
- `submit_kb_extraction` / `submit_kb_extraction_batch` 在现有 `evaluate_curation_gate` 调用之后追加 `evaluate_review_gate`；按返回 decision 抛对应错误码
- `extract_kb` DAG 的 `download_video` 步骤起始处再做一次审核门校验，作为防御性二点拦截（与清洗门"两点对接"风格一致）

**错误码新增**（[contracts/error-codes.md](./contracts/error-codes.md) — 共 8 个新错误码）：

| ErrorCode | HTTP | 默认消息 | 触发场景 |
|-----------|------|---------|---------|
| `CONTENT_NOT_REVIEWED` | 409 | 视频尚未通过内容审核，请先在审核工作台提交决策 | KB 抽取入口校验：来源条目 `review_state=pending_review` |
| `CONTENT_REVIEW_REJECTED` | 409 | 视频审核已被拒绝，无法进入训练阶段 | KB 抽取入口校验：来源条目 `review_state=rejected` |
| `CONTENT_REVIEW_STALE` | 409 | 视频已重新清洗，原审核结论已失效，请重新审核 | KB 抽取入口校验：来源条目 `review_state=stale` |
| `REVIEW_VERSION_CONFLICT` | 409 | 审核条目已被他人更新，请刷新后重试 | 决策提交时 `expected_review_version` 与服务端值不一致（乐观锁） |
| `REVIEW_NOT_PENDING` | 409 | 该条目当前不在待审核状态，无法决策 | 决策提交时 `review_state` 不在 `{pending_review, stale}` |
| `INVALID_REVIEWER_IDENTITY` | 400 | 请求头与请求体中的审核员标识不一致 | `X-Reviewer-Id` header 与请求体 `reviewer_id` 不等 |
| `REJECTED_REQUIRES_REASON` | 400 | 拒绝决策必须提供 reason_code | `decision=rejected` 但缺少 `reason_code` |
| `REVIEW_GATE_INVALID_STATE` | 400 | 审核门开关参数非法 | `PATCH /admin/review-gate` 请求体校验失败 |

> **附加响应 header（非错误码）**：审核门绕过命中时在 KB 抽取放行响应中追加 `X-Review-Gate-Bypass: true`，仅作 warning 用，不入 ErrorCode 枚举。

### 1.3 quickstart 摘要（详见 quickstart.md）

提供 5 步端到端验证流程：
1. 触发新 COS 视频分类（`POST /api/v1/classifications/scan`）
2. 完成清洗（`POST /api/v1/tasks` type=`video_curation`）→ 自动落 `review_state=pending_review`
3. 提交 KB 抽取（应被 `CONTENT_NOT_REVIEWED` 拒绝）
4. 调用 `POST /content-reviews/{id}/decisions` 通过审核
5. 重新提交 KB 抽取（应入队成功）

### 1.4 代理上下文更新

执行 `.specify/scripts/bash/update-agent-context.sh codebuddy`，仅添加本 Feature 引入的新模块（`src/services/content_review/`、`src/api/routers/content_reviews.py`、`tests/contract/test_022_*`），保留手动添加内容。

## 设计后章程检查（重新评估）

| 检查项 | 结论 |
|-------|------|
| 原则 I（规范驱动） | ✅ 仍合规 |
| 原则 II（测试优先） | ✅ 5 个 endpoint 的合约测试均在 `tests/contract/` 列表中，先于实现 |
| 原则 III（增量交付） | ✅ MVP = US1+US2，US3/US4 可独立增量发布 |
| 原则 IV（简洁性） | ✅ 不另建子粒度表；仅 1 张新表 + 4 个新列；不引入新队列 |
| 原则 IX（接口规范） | ✅ 路由资源化 + 分页统一 + 信封统一 + AppException + 错误码集中 |
| 原则 X（业务流程对齐） | ✅ 章程级双向同步任务已规划（T001/T002）；FR-014 回滚剧本将登记到 § 10 |
| 原则 XI（测试阶段兼容） | ✅ 不写存量回填脚本，新列默认值即可工作 |

**结论**：设计后章程检查通过，无违规需要进入复杂度跟踪表。

## 复杂度跟踪

> 仅在章程检查有必须证明的违规时填写。

| 违规 | 为什么需要 | 拒绝更简单替代方案的原因 |
|-----|----------|---------------------|
| —    | —        | 本 Feature 设计未引入章程违规 |

## 阶段 2 概览（由 /speckit.tasks 落地）

阶段 2 已生成 [tasks.md](./tasks.md)，按以下分组组织任务（详细任务列表与依赖关系见 tasks.md）：

- **阶段 1 设置 / 章程级前置（T001-T003）**：
  - T001 同步 `docs/business-workflow.md`（四阶段化 + 新增 `content_review` 步骤 + § 10 回滚剧本新增）
  - T002 同步 `.specify/memory/constitution.md` 原则 X 措辞 + 升版到 v2.2.0（含模板与文档双向同步）
  - T003 工作目录完整性自检
- **阶段 2 基础 / 数据层与错误码（T004-T010）**：迁移 `0021_*` + ORM 模型 + 8 个错误码登记 + Pydantic Schema + 配置项 + 迁移上线验证
- **阶段 3 US1 业务流程升级（T011-T013，P1🎯MVP）**：阶段路由调整 + 集成测试
- **阶段 4 US2 审核门 KB 拦截（T014-T022，P1🎯MVP）**：合约测试先行 + 审核门服务 + stale 处理 + 双点拦截 + 集成测试
- **阶段 5 US3 审核工作台（T023-T029，P2）**：审核服务 + 5 个 endpoint + 单元测试 + 性能验证
- **阶段 6 US4 可观测与回滚（T030-T035，P3）**：阶段/步骤级指标 + 积压告警 + 绕过开关 + 集成测试
- **阶段 7 完善与横切关注点（T036-T042）**：features.md / architecture.md 同步 + quickstart 演练 + 状态机单测 + 错误码一致性核查 + 迁移回滚验证 + 全量测试

**任务总数 42，MVP 边界 = T001-T022（阶段 1 + 阶段 2 + US1 + US2，共 22 个任务）**。MVP 完成后即可让
