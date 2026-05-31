# 实施计划: 技术分类体系重构与知识标准统一

**分支**: `023-tech-classification-rebuild` | **日期**: 2026-05-29 | **规范**: [spec.md](./spec.md)
**输入**: 来自 `/specs/023-tech-classification-rebuild/spec.md` 的功能规范

> 本计划由 `/speckit.plan` 命令生成；阶段 2（任务分解）由后续 `/speckit.tasks` 命令产出。

---

## 摘要

将系统现有的 **21 类扁平 `tech_category` 分类体系**整体重构为**严格四级 + 字典强约束的新分类体系**：CSV 第 1–4 列 `category_l1`（握拍方式）/ `category_l2`（胶皮类型）/ `category_l3`（手部技术·技术大类）/ `action`（具体动作名称，44 行作为唯一可选字典）。零兼容、不保留 aliases、不保留 `classifier_version` 切换路径。

技术方法（研究后确定）：
- **单一 Alembic 迁移 `0022_tech_taxonomy_rebuild.py`**（事务原子）—— 仅做 **schema 重建**：DROP 全部 `tech_category` 列与索引、CREATE `tech_actions` 字典表并 seed 44 行（CSV 加载时清洗 U+200B 零宽字符与"·"分隔符）、ADD `category_l1/l2/l3/action` 列与外键、把 `tech_knowledge_bases` 复合主键 `(tech_category, version)` 重命名为 `(action, version)`、4 张子表外键列 `kb_tech_category → kb_action`
- **业务数据清场移交 `system-init` skill**（已存在 `.codebuddy/skills/system-init/`）：迁移本身**不**做 TRUNCATE；执行顺序变为「停 worker → `alembic downgrade <prev>` → `alembic upgrade head` → 通过 system-init skill TRUNCATE 全部业务表 + reseed `task_channel_configs` → 启动 worker → 触发一次全量 COS 扫描重建数据」
- **TechClassifierV2** 替换 `TechClassifier`：keyword 匹配 → LLM 兜底，输出 `(category_l1, category_l2, category_l3, action)` 四元组；LLM 输出必须落在 `tech_actions` 字典内，否则降级 `unclassified`
- **业务流程文档（`docs/business-workflow.md`）深度同步**：§ 3.2 步骤 3 / § 4 KB 单 active 约束（per-tech_category → per-action）/ § 7.4 错误码表 / § 5.3 诊断（active standard 查询键）全部改写
- **错误码新增**：`ACTION_NOT_FOUND`(404) / `ACTION_DICTIONARY_VIOLATION`(400) / `STANDARD_NOT_AVAILABLE_FOR_ACTION`(503) 三项登记到 `src/api/errors.py` 三张表 + `contracts/error-codes.md`
- **system-init skill 同步扩展**：`reset_business_data.sql` 新增 `tech_actions` 表识别（保留 seed 不 TRUNCATE，本质是字典）

> ⚠️ **spec.md 已记载迁移编号 `0019`，本计划纠正为 `0022`**（实际 0019/0020/0021 已被占用，最新为 0021）。spec.md 将由 `/speckit.tasks` 阶段附带勘误说明，或在 PR 中显式标注；本 plan 一律以 `0022_tech_taxonomy_rebuild` 为准。

---

## 技术背景

**语言/版本**: Python 3.11（统一虚拟环境 `/opt/conda/envs/coaching/bin/python3.11`）
**主要依赖**: FastAPI 0.110+ / SQLAlchemy 2.x（async）/ Alembic / Celery 5.x / Pydantic v2 / Venus Proxy + OpenAI fallback
**存储**: PostgreSQL（主库）+ Redis（Celery broker / cache）+ 腾讯云 COS（视频与产物）
**测试**: pytest（含 contract / integration / unit 三层），合约测试 MUST 在路由实现前 RED
**目标平台**: Linux 服务器（Docker + systemd / setsid 守护）
**项目类型**: 后端 web 服务（无前端代码）
**性能目标**:
- 全量 COS 扫描 1015+ 视频 24h 内完成（沿用 spec SC-006）
- 单条 `classify_video` Celery 任务 LLM 兜底路径 p95 < 8s（含 Venus Proxy 调用）
- `GET /api/v1/classifications` 列表查询 p95 < 200ms（按 `action` 索引）
**约束条件**:
- 迁移期间所有 worker 停机；执行窗口 ≤ 30 分钟
- LLM 输出必须落在 `tech_actions` 字典 44 行内，否则视为失败 → `unclassified`
- 不引入 `classifier_version` / aliases / 任何旧 21 类兼容路径
**规模/范围**:
- 影响 8 张表的 schema 变更（`coach_video_classifications` / `video_classifications` / `expert_tech_points` / `tech_knowledge_bases` / `tech_standards` / `teaching_tips` / `diagnosis_reports` / `analysis_tasks`）
- 影响 4 张表的外键列重命名（`kb_tech_category → kb_action`）
- 影响约 15 个 Python 文件的代码改造（含 7 个 service / 3 个 router / 4 个迁移测试 fixture）

---

## 章程检查

> **门控**: 阶段 0 研究前通过 ✅；阶段 1 设计后重新评估见末尾「设计后章程复检」段。

### 章程合规验证

- ✅ **原则 I（规范驱动）**：spec.md 已完成 P1–P3 五个用户故事 + Clarifications 章节 4 条决策；本 plan 未引入 spec 之外的功能
- ✅ **原则 II（测试优先）**：阶段 1 将先产出 `contracts/` 与 `tests/contract/` 骨架，所有 V2 分类器与新错误码的合约测试 MUST 在实现前 RED
- ✅ **原则 III（增量交付）**：US1（分类器升级 + 数据迁移）→ US2（全量扫描）→ US3/US4（KB 标准化 + 术语归一化）→ US5（标准按 action 聚合）按 P1→P3 严格分层
- ✅ **原则 IV（YAGNI）**：拒绝引入 `classifier_version` 切换路径、aliases 兼容映射、子动作扩展槽位（spec Clarifications Q1 已明确严格四级）
- ✅ **原则 V（可观测性）**：`pipeline_steps.output_summary` 新增 `action_classified` / `terminology_normalized` 字段；`coach_video_classifications` 四级字段直接查询分类覆盖率
- ⚠️ **原则 VI（AI 模型治理）**：本 feature **不更换** LLM 模型，仅修改分类 prompt 与字典约束；模型版本不变，无需新增 `docs/models/` 登记。**TechClassifierV2 prompt schema 变更**视为算法精度可观测变化 → MUST 在 `docs/benchmarks/` 建立 V2 准确率基线（spec SC-002：85% 目标）
- ✅ **原则 VII（数据隐私）**：本 feature 不改变数据采集 / 存储 / 加密策略，无新增隐私敏感字段
- ✅ **原则 VIII（算法精准度）**：spec SC-002 量化目标 `准确率 ≥ 85%`、SC-004 `术语归一化覆盖率 ≥ 80%`；阶段 1 将创建 `data/eval/tech_classification_v2_eval.csv`（人工标注 ≥ 100 条，覆盖 44 个 action）
- ✅ **原则 IX（API 规范）**：见下「API 接口规范验证要点」
- ⚠️ **原则 X（业务流程对齐）**：本 feature 触发 § 4.2 单 active 约束作用域变更（per-tech_category → per-action）+ § 7.4 错误码表新增 + § 5.3 诊断查询键变更；MUST 同步更新 `docs/business-workflow.md` 后才能合并

### API 接口规范验证要点（原则 IX，v1.4.0 统一信封）

| 检查项 | 状态 | 说明 |
|---|---|---|
| 版本前缀 `/api/v1/` | ✅ | 复用现有 `classifications` / `standards` / `teaching-tips` 路由模块 |
| 路由按资源划分 | ✅ | 不新建路由文件；变更仅在 `classifications.py` / `standards.py` / `tasks.py` 路由内对响应字段调整 |
| 分页参数 `page` + `page_size` | ✅ | 现有接口已合规，无需调整 |
| 响应信封 `SuccessEnvelope[T]` | ✅ | 现有接口已使用 `ok()` / `page()` 构造器，新增字段仅扩展 `data` 内的 schema |
| 分层职责（路由层只做校验+组装） | ✅ | TechClassifierV2 / TerminologyNormalizer / ActionDictionaryService 均放 `src/services/` |
| 错误码集中化（`AppException` + `ErrorCode`） | ⚠️ 需扩展 | 新增 3 个枚举值 `ACTION_NOT_FOUND`(404) / `ACTION_DICTIONARY_VIOLATION`(400) / `STANDARD_NOT_AVAILABLE_FOR_ACTION`(503)；同步 3 张表 + `contracts/error-codes.md` |
| 接口下线物理删除（v2.0.0 原则 IX） | ✅ | 移除 `GET /api/v1/standards?tech_category=` 查询参数 = 路由层删除该 Query 参数与对应分支，**不**留哨兵；spec 业务阶段映射会一次性记录"已用 `?action=` 替代" |
| 合约测试前置 | ⚠️ 待创建 | `tests/contract/test_classifications_v2.py`、`tests/contract/test_standards_action_query.py`、`tests/contract/test_action_dictionary_violation.py` 在阶段 1 创建并 RED |

### 业务流程对齐验证要点（原则 X）

- ✅ **spec.md 已含「业务阶段映射」段**：跨阶段 CONTENT_PREP / TRAINING / STANDARDIZATION 三阶段已声明，DoD/可观测锚点/章程级约束影响/回滚剧本俱全
- ⚠️ **章程级约束双向同步清单**（PR 合并前 MUST 完成）：
  - § 3.2 步骤 3 `classify_video`：DoD 改写为 "`coach_video_classifications.action IS NOT NULL AND action != 'unclassified'`"
  - § 4 单 active 约束：作用域从 "per-tech_category" 改为 "per-action"；状态机分桶键同步
  - § 4.3 状态机表头加列说明（per-action 维度）
  - § 5.3 诊断查询：`STANDARD_NOT_AVAILABLE` 改为 `STANDARD_NOT_AVAILABLE_FOR_ACTION`，details 字段从 `tech_category` 改为 `action`
  - § 7.4 错误码表新增 3 行（`ACTION_NOT_FOUND` / `ACTION_DICTIONARY_VIOLATION` / `STANDARD_NOT_AVAILABLE_FOR_ACTION`）；删除 1 行（`STANDARD_NOT_AVAILABLE` 旧版）
  - § 9 优化杠杆表：`tech_actions` 字典视为「规则与 Prompt」杠杆类（重启 API 生效）
- ✅ **优化杠杆命中**：本 feature 主要落在「规则与 Prompt」类（CSV → `tech_actions` 字典 → V2 分类 prompt），无队列拓扑变化
- ✅ **回滚剧本**：spec 已声明「单一迁移对称 downgrade」+ 「业务数据不可回填」+ 「需重运行 system-init + 全量扫描」；本 plan 阶段 1 会在 § 10 业务流程文档新增剧本 R-023

### 章程门控结论

- 无 BLOCKING 违规
- ⚠️ 1 个 MUST-DO 待跟进：`docs/business-workflow.md` 同步更新（PR 合并前完成）
- ⚠️ 1 个 SHOULD-DO 待跟进：在 `docs/benchmarks/` 建立 V2 分类准确率基线（spec SC-002 量化指标）

→ **门控通过**，进入阶段 0。

---

## 项目结构

### 文档（本功能）

```
specs/023-tech-classification-rebuild/
├── spec.md                              # 已存在（含 Clarifications 4 条）
├── plan.md                              # 本文件
├── research.md                          # 阶段 0 输出（NEEDS CLARIFICATION 解析）
├── data-model.md                        # 阶段 1 输出（tech_actions 字典 + 4 表 schema 改造）
├── quickstart.md                        # 阶段 1 输出（迁移演练 + 全量扫描验证）
├── contracts/
│   ├── error-codes.md                   # 阶段 1 输出（3 个新错误码登记）
│   ├── tech-actions-seed.csv            # 阶段 1 输出（清洗后的 44 行字典 seed）
│   ├── classifications-v2.openapi.yaml  # 阶段 1 输出（GET /classifications 响应 schema）
│   └── standards-action-query.openapi.yaml  # 阶段 1 输出（GET /standards 查询参数）
└── tasks.md                             # 阶段 2 输出（/speckit.tasks 命令生成）
```

### 源代码（仓库根目录，单一项目结构）

```
src/
├── api/
│   ├── errors.py                        # ⚠️ 新增 3 个 ErrorCode 枚举 + 状态映射 + 默认消息
│   ├── routers/
│   │   ├── classifications.py           # ⚠️ 响应 schema 改 4 级字段；移除 tech_category 查询
│   │   ├── standards.py                 # ⚠️ Query 参数 tech_category → action（直接物理删除旧参数）
│   │   ├── tasks.py                     # ⚠️ task_kwargs 校验从 tech_category → action
│   │   ├── athlete_classifications.py   # ⚠️ 同上
│   │   └── diagnosis_reports.py         # ⚠️ 同上
│   └── schemas/
│       ├── classification.py            # ⚠️ ClassificationResponse 增 4 级字段，去 tech_category
│       ├── standards.py                 # ⚠️ 查询参数 schema 同步
│       └── tech_actions.py              # 🆕 字典表读模型（GET /admin/tech-actions 可选）
├── services/
│   ├── tech_classifier.py               # ⚠️ 整体重写为 TechClassifierV2（保留单类名 TechClassifier 但实现替换）
│   ├── action_dictionary_service.py     # 🆕 加载 tech_actions 字典并提供 (l1,l2,l3,action) 校验
│   ├── terminology_normalizer.py        # 🆕 口语化 → 标准术语映射 + LLM 兜底
│   ├── classification_service.py        # ⚠️ 持久化逻辑改写四级字段
│   ├── classification_gate_service.py   # ⚠️ 门槛改判 action != 'unclassified'
│   ├── tech_standard_builder.py         # ⚠️ 聚合粒度从 tech_category → action（FR-015）
│   ├── athlete_submission_service.py    # ⚠️ active standard 查询键改 action
│   ├── task_submission_service.py       # ⚠️ task_kwargs.tech_category → task_kwargs.action
│   ├── advice_generator.py              # ⚠️ TeachingTip 查询键改 action
│   ├── content_review/
│   │   ├── backlog_monitor.py           # ⚠️ 聚合 group by action
│   │   └── review_service.py            # ⚠️ filter.tech_category → filter.action
│   └── kb_extraction_pipeline/
│       └── step_executors/
│           ├── audio_kb_extract.py      # ⚠️ Prompt schema → spec FR-010 结构化输出
│           ├── visual_kb_extract.py     # ⚠️ 同上
│           └── merge_kb.py              # ⚠️ flush 时校验 action 字典；写 expert_tech_points.action
├── models/
│   ├── tech_action.py                   # 🆕 字典 ORM
│   ├── coach_video_classification.py    # ⚠️ 列改造：drop tech_category；add 4 级
│   ├── video_classification.py          # ⚠️ 同上
│   ├── expert_tech_point.py             # ⚠️ tech_category → action；submitted_tech_category → submitted_action
│   ├── tech_knowledge_base.py           # ⚠️ 复合主键 (tech_category,version) → (action,version)
│   ├── tech_standard.py                 # ⚠️ 唯一键改 action
│   ├── teaching_tip.py                  # ⚠️ tech_category → action；kb_tech_category → kb_action
│   └── diagnosis_report.py              # ⚠️ 同上
├── db/
│   └── migrations/
│       └── versions/
│           └── 0022_tech_taxonomy_rebuild.py   # 🆕 单一原子迁移
└── workers/
    └── classification_task.py           # ⚠️ scan_cos_videos 调用 V2 分类器；落库改四级字段

tests/
├── contract/
│   ├── test_classifications_v2.py       # 🆕 GET /classifications 响应 schema
│   ├── test_standards_action_query.py   # 🆕 GET /standards?action=
│   └── test_action_dictionary_violation.py  # 🆕 LLM 输出非字典 → 400 ACTION_DICTIONARY_VIOLATION
├── integration/
│   ├── test_migration_0022_taxonomy.py  # 🆕 迁移 upgrade/downgrade 端到端
│   ├── test_full_scan_v2.py             # 🆕 触发 scan_cos_videos 后核对四级字段填充率
│   └── test_kb_extraction_action_gate.py  # 🆕 门控改判 action 后 KB 抽取链路
├── unit/
│   ├── services/
│   │   ├── test_tech_classifier_v2.py   # 🆕 keyword + LLM fallback 二级降级
│   │   ├── test_action_dictionary_service.py  # 🆕 字典加载 + 校验
│   │   └── test_terminology_normalizer.py     # 🆕 静态映射 + LLM 兜底
│   └── api/
│       └── test_errors_action.py        # 🆕 新增 3 个错误码 status / message
└── fixtures/
    └── tech_actions_seed.csv            # 🆕 测试用字典样本（与 contracts/tech-actions-seed.csv 同源）

config/
└── terminology_mapping.json             # 🆕 口语化→标准术语静态映射（开发团队初版）

docs/
├── business-workflow.md                 # ⚠️ § 3.2 / § 4 / § 5.3 / § 7.4 / § 9 / § 10 同步
└── benchmarks/
    └── tech_classification_v2.md        # 🆕 V2 分类准确率基线（spec SC-002）

.codebuddy/skills/system-init/
├── SKILL.md                             # ⚠️ 新增「执行前校验：tech_actions 字典完整 44 行」步骤
└── reset_business_data.sql              # ⚠️ 排除 tech_actions（视为字典而非业务表）；TRUNCATE 清单核对

pp_book/
└── pp_tech_classification.csv           # ✅ 已存在（44 行权威源），seed 时 strip U+200B + "·" 分隔
```

**结构决策**: 沿用现有「单一项目」结构（章程附加约束 § 路径约定）。**不**新建路由文件；服务层新增 3 个文件（`action_dictionary_service` / `terminology_normalizer` / 模型 `tech_action`）。所有改造控制在现有目录内，无章程偏离。

---

## 阶段 0 — 研究 (research.md)

阶段 0 解析以下 3 个 NEEDS CLARIFICATION 类别 + 5 项最佳实践调研：

### 待解未知项（已在 spec Clarifications 决议但需技术细节落地）

| # | 主题 | 输出位置 | 决议方向（来自 Clarifications） |
|---|---|---|---|
| 1 | CSV 数据清洗策略（U+200B、"·"、空白） | research.md § 1 | strip ZWSP；"·" 作为 `category_l3` 内部 `hand·category` 的 sep；trim |
| 2 | 字典约束的 LLM Prompt 设计 | research.md § 2 | Prompt 中嵌入 44 行 action 列表（含 l1/l2/l3）；JSON Schema 限定 enum；输出后二次校验 |
| 3 | `tech_knowledge_bases` 复合主键改名的 FK 级联策略 | research.md § 3 | 4 张子表外键改名 `kb_tech_category → kb_action`；ON UPDATE CASCADE / ON DELETE 保留原策略 |

### 最佳实践调研

| # | 主题 | 决策建议 |
|---|---|---|
| 4 | Alembic 单一迁移内 PRIMARY KEY 重命名 | 使用 `op.execute("ALTER TABLE ... DROP CONSTRAINT pk_xxx CASCADE")` + 重建 PK；FK 级联在同一事务内 |
| 5 | PostgreSQL 字典表 + 业务表 FK 是否影响 system-init TRUNCATE | `tech_actions` 视为字典（类似 `task_channel_configs`），从 system-init TRUNCATE 清单中**排除**；TRUNCATE 业务表用 `RESTART IDENTITY CASCADE` 处理外键 |
| 6 | 全量扫描幂等性（`cos_object_key` upsert） | 沿用现有 `CosClassificationScanner._upsert` 路径；新增 `cos_object_key` 唯一索引保障 |
| 7 | LLM Prompt 字典 enum 的 token 成本 | 44 行 × 4 列 ≈ 600 token；可接受，无需向量化检索 |
| 8 | TerminologyNormalizer 静态映射初版规模 | 开发团队整理 30–50 条高频映射（如"包住球→摩擦加厚"）；LLM 兜底用于未命中条目 |

**输出**: `research.md` 写明 8 项 Decision / Rationale / Alternatives，所有 NEEDS CLARIFICATION 解决。

---

## 阶段 1 — 设计与契约

**前提条件**: research.md 已完成（阶段 0 输出）。

### 1.1 数据模型 (`data-model.md`)

#### 1.1.1 新增字典表 `tech_actions`

```sql
CREATE TABLE tech_actions (
    action       VARCHAR(64) PRIMARY KEY,          -- e.g. '高吊弧圈球'（44 行字典）
    category_l1  VARCHAR(32)  NOT NULL,            -- e.g. '横拍'
    category_l2  VARCHAR(32)  NOT NULL,            -- e.g. '反胶'
    category_l3  VARCHAR(64)  NOT NULL,            -- e.g. '正手·进攻' （hand·tech_class）
    created_at   TIMESTAMP DEFAULT now()
);
CREATE INDEX ix_tech_actions_l1l2l3 ON tech_actions (category_l1, category_l2, category_l3);
-- seed: 加载 pp_book/pp_tech_classification.csv，strip U+200B 后插入 44 行
```

#### 1.1.2 业务表 schema 改造（统一在 `0022_tech_taxonomy_rebuild`）

| 表 | 改造内容 |
|---|---|
| `coach_video_classifications` | DROP `tech_category`; ADD `category_l1` / `category_l2` / `category_l3` / `action` (FK→`tech_actions.action`, NULLABLE)；DROP idx `idx_cvclf_tech_category` / `idx_cvclf_review_state_tech` / `idx_cvclf_coach_tech`；CREATE `idx_cvclf_action` / `idx_cvclf_review_state_action` |
| `video_classifications` | DROP `tech_category`; ADD 4 级字段（同上） |
| `expert_tech_points` | RENAME `tech_category → action` (FK→`tech_actions.action`)；RENAME `submitted_tech_category → submitted_action`；RENAME `kb_tech_category → kb_action`（FK 一并改） |
| `tech_knowledge_bases` | DROP PK `pk_tech_kb_cat_ver` → 复合 PK 重建为 `(action, version)`；列名 `tech_category → action`（FK→`tech_actions.action`）；唯一索引 `ON (tech_category)` 改 `ON (action) WHERE status='active'` |
| `tech_standards` | RENAME `tech_category → action`（FK）；UNIQUE `uq_ts_tech_version → uq_ts_action_version` |
| `teaching_tips` | RENAME `tech_category → action`；RENAME `kb_tech_category → kb_action`（FK 复合外键改名） |
| `diagnosis_reports` | RENAME `tech_category → action`；RENAME idx `idx_dr_tech_category → idx_dr_action` |
| `analysis_tasks` | RENAME `kb_tech_category → kb_action`（FK 改名） |

**回滚（downgrade）**：对称重建 `tech_category` 列与索引；DROP `tech_actions` 与 4 级列；**业务数据不可回填**（已被 system-init 清空）。

#### 1.1.3 状态机变化（章程级约束）

- **per-tech_category 单 active 约束 → per-action 单 active 约束**：`tech_knowledge_bases` 同一 action 下任意时刻最多 1 行 `status='active'`；冲突门控按 `(action, version)` 分桶；`approve_version(action, version)` 替代旧 `approve_version(tech_category, version)`
- **DoD 表行同步**：`docs/business-workflow.md` § 2 阶段判据表全部 `tech_category` → `action`

---

### 1.2 接口契约 (`contracts/`)

#### 1.2.1 错误码登记 (`contracts/error-codes.md`)

| 新增 ErrorCode | HTTP | Default Message | 触发场景 |
|---|---|---|---|
| `ACTION_NOT_FOUND` | 404 | "动作不存在" | `GET /standards?action=xxx` 字典中无该 action |
| `ACTION_DICTIONARY_VIOLATION` | 400 | "action 不在字典内" | task_kwargs / 提交体的 action 字段不在 `tech_actions` 字典 |
| `STANDARD_NOT_AVAILABLE_FOR_ACTION` | 503 | "该动作暂无 active 技术标准" | 诊断提交时 `tech_standards` 无 `(action, status='active')` 行 |

| 移除 ErrorCode | HTTP | 替代 |
|---|---|---|
| `STANDARD_NOT_AVAILABLE`（旧版按 tech_category） | 503 | 由 `STANDARD_NOT_AVAILABLE_FOR_ACTION` 替代 |
| `NO_ACTIVE_KB_FOR_CATEGORY`（如存在） | 400 | 重命名为 `NO_ACTIVE_KB_FOR_ACTION`（保持语义） |

合约要求：`src/api/errors.py::ErrorCode` 枚举 + `ERROR_STATUS_MAP` + `ERROR_DEFAULT_MESSAGE` 三处同步登记；`tests/contract/test_action_dictionary_violation.py` 与 `tests/unit/api/test_errors_action.py` 先于实现 RED。

#### 1.2.2 OpenAPI 片段

**`contracts/classifications-v2.openapi.yaml`** —— `GET /api/v1/classifications` 响应 `data[*]` schema：
```yaml
properties:
  cos_object_key: {type: string}
  coach_name:     {type: string}
  category_l1:    {type: string, nullable: true}
  category_l2:    {type: string, nullable: true}
  category_l3:    {type: string, nullable: true}
  action:         {type: string, nullable: true}
  confidence:     {type: number}
  classification_source: {type: string, enum: [rule, llm, manual]}
  kb_extracted:   {type: boolean}
  review_state:   {type: string}
required: [cos_object_key, coach_name, action, confidence, classification_source, kb_extracted]
```

**`contracts/standards-action-query.openapi.yaml`** —— `GET /api/v1/standards` 查询参数：
```yaml
parameters:
  - name: action
    in: query
    schema: {type: string}
    description: 按具体动作（tech_actions 字典 44 行之一）过滤
  # 直接物理删除原 tech_category 参数（章程 v2.0.0 原则 IX）
```

**`contracts/tech-actions-seed.csv`** —— 清洗后的 44 行字典 seed（去掉 U+200B、`category_l3` 用 `·` 拼接 hand+class）。

#### 1.2.3 合约测试骨架（先于实现 RED）

```
tests/contract/test_classifications_v2.py
  - test_response_includes_four_level_fields
  - test_response_excludes_tech_category_field
  - test_action_field_value_must_be_in_dictionary

tests/contract/test_standards_action_query.py
  - test_action_param_filters_results
  - test_tech_category_param_returns_400_validation_failed   # 旧参数物理删除后默认 422

tests/contract/test_action_dictionary_violation.py
  - test_submit_task_with_invalid_action_returns_400
  - test_diagnosis_submit_no_active_standard_returns_503
```

---

### 1.3 quickstart.md（迁移演练 + 全量扫描验证）

```bash
# 1. 停 worker 与 API
pkill -f 'celery -A src.workers' && pkill -f 'uvicorn src.api.main'

# 2. 检查当前迁移版本
/opt/conda/envs/coaching/bin/alembic current   # 应当是 0021

# 3. 运行新迁移（含 tech_actions seed）
/opt/conda/envs/coaching/bin/alembic upgrade head   # 0022_tech_taxonomy_rebuild

# 4. 通过 system-init skill 清场业务数据 + reseed task_channel_configs
PGPASSWORD=password psql -h localhost -U postgres -d coaching_db \
  -v ON_ERROR_STOP=1 \
  -f .codebuddy/skills/system-init/reset_business_data.sql

# 5. 验证 tech_actions 字典完整
psql -c "SELECT count(*) FROM tech_actions;"   # 期望 44

# 6. 启动 API + 5 worker（按现有 setsid 流程）
# ... （省略，与项目规则一致）

# 7. 触发全量 COS 扫描
curl -X POST http://localhost:8080/api/v1/classifications/scan
# 查询进度
curl http://localhost:8080/api/v1/classifications/scan/<task_id>

# 8. 抽样核验四级字段填充率
psql -c "SELECT
  count(*) AS total,
  count(action) FILTER (WHERE action != 'unclassified') AS classified,
  100.0 * count(action) FILTER (WHERE action != 'unclassified') / NULLIF(count(*),0) AS coverage_pct
FROM coach_video_classifications;"
# 期望 coverage_pct >= 95%（spec SC-001）
```

---

### 1.4 代理上下文更新

阶段 1 末尾运行：
```bash
.specify/scripts/bash/update-agent-context.sh codebuddy
```
该脚本会把当前 plan 的「技术背景」段同步到 `.codebuddy/rules/` 项目规则文件（仅追加新技术，保留手动编辑标记区）。

---

## 设计后章程复检（阶段 1 完成后）

| 检查项 | 复检状态 | 备注 |
|---|---|---|
| 原则 II 测试优先 | ✅ | 阶段 1 输出包含 3 个 contract test 文件骨架，先于实现存在 |
| 原则 IV YAGNI | ✅ | 设计未引入 spec 之外的字段或服务（如未引入子动作扩展、未保留 aliases 表） |
| 原则 VIII 算法精度 | ✅ | `docs/benchmarks/tech_classification_v2.md` 任务已登记，阶段 2 拆解 |
| 原则 IX API 信封 + 错误码集中化 | ✅ | 3 个新错误码已规划同步至三张表 + `contracts/error-codes.md` |
| 原则 X 业务流程双向同步 | ✅ | `docs/business-workflow.md` § 3.2 / § 4 / § 5.3 / § 7.4 / § 9 / § 10 同步任务已列入项目结构 |
| 接口下线物理删除（v2.0.0） | ✅ | `?tech_category=` 查询参数直接物理删除，不留哨兵 |

→ **复检通过**，阶段 1 闭环。后续 `/speckit.tasks` 命令将根据本 plan 生成 `tasks.md`。

---

## 复杂度跟踪

| 违规 / 偏离 | 为什么需要 | 拒绝更简单替代方案的原因 |
|---|---|---|
| **`tech_knowledge_bases` 复合主键重命名**（章程级约束变化） | spec FR-007 / FR-015 + Clarifications Q4 单一原子迁移要求；零兼容意味着不能保留旧主键名作为别名 | 替代方案 A：保留旧 PK 名 `(tech_category,version)` 仅改语义 → 与 spec「物理删除 tech_category」直接矛盾；替代方案 B：双写过渡 → 与 spec「不考虑兼容」明确冲突 |
| **业务流程文档 § 4 单 active 约束作用域变更**（per-tech_category → per-action） | 一旦 `tech_knowledge_bases` 主键改为 `action`，状态机分桶键必须同步；这是业务语义变更而非纯实现 | 不可避免；唯一替代是放弃零兼容，与 Clarifications Q2 决策冲突 |
| **TRUNCATE 操作从迁移内挪出到 system-init skill** | 迁移文件不应承担数据清场职责（违反"迁移 = schema 变更"惯例）；system-init 已存在专责 skill | 替代方案：迁移内 TRUNCATE → 违反 Alembic 最佳实践，downgrade 时无法恢复，且与 system-init 职责重叠 |
| **错误码新增 3 个**（章程级约束变化） | spec FR-005 / FR-018 直接产生新错误场景；原 `STANDARD_NOT_AVAILABLE` 语义已不准确（不再按 tech_category） | 替代方案：复用旧错误码 → 违反"已发布的错误码禁止改名或更换 HTTP 状态" |
| **错误码物理删除 2 个**（`STANDARD_NOT_AVAILABLE` / `NO_ACTIVE_KB_FOR_CATEGORY`） | 本 feature 走"章程级约束变化"例外通道：旧错误码的语义载体（`tech_category` / per-category 单 active 约束）本身被 spec FR-007 / FR-015 物理删除，错误码在代码中再无任何分支可触发；按章程 v2.0.0 接口下线策略一次性物理删除（在 [contracts/error-codes.md](./contracts/error-codes.md) 中显式登记） | 替代方案：保留为哨兵枚举 → 违反 YAGNI（属死代码）；保留为别名映射到新错误码 → 违反"已发布的错误码禁止改名" |

> 本 feature 不引入新依赖、不新建路由文件、不引入新队列；仅是 schema + 字典 + 错误码 + 业务文档 4 个维度的同步重构。

---

## 阶段 2 之后

`/speckit.plan` 在阶段 1 末尾停止。下一步：
- **`/speckit.tasks`** — 将本 plan 拆解为按用户故事 P1→P3 优先级排列的有序任务清单（`tasks.md`），每个任务挂接 contract test 文件 + 实现文件 + 验证步骤
- **可选：`/speckit.checklist`** — 为「迁移演练」「全量扫描验证」「业务流程文档同步」三个领域生成审计清单

---

## 制品清单（本次 `/speckit.plan` 产出）

| 制品 | 路径 | 状态 |
|---|---|---|
| plan.md | `specs/023-tech-classification-rebuild/plan.md` | ✅ 本文件 |
| research.md | `specs/023-tech-classification-rebuild/research.md` | ⏳ 阶段 0 待生成（next step） |
| data-model.md | `specs/023-tech-classification-rebuild/data-model.md` | ⏳ 阶段 1 待生成 |
| contracts/error-codes.md | `specs/023-tech-classification-rebuild/contracts/error-codes.md` | ⏳ 阶段 1 待生成 |
| contracts/tech-actions-seed.csv | `specs/023-tech-classification-rebuild/contracts/tech-actions-seed.csv` | ⏳ 阶段 1 待生成 |
| contracts/classifications-v2.openapi.yaml | `specs/023-tech-classification-rebuild/contracts/classifications-v2.openapi.yaml` | ⏳ 阶段 1 待生成 |
| contracts/standards-action-query.openapi.yaml | `specs/023-tech-classification-rebuild/contracts/standards-action-query.openapi.yaml` | ⏳ 阶段 1 待生成 |
| quickstart.md | `specs/023-tech-classification-rebuild/quickstart.md` | ⏳ 阶段 1 待生成 |

> 本计划文件已锁定整体技术路径与章程门控。下一步 plan 工作流要求**实际**生成 research.md / data-model.md / contracts/ / quickstart.md 4 类制品。
