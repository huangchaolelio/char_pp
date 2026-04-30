# 功能规范: 处理流程规范化（Workflow Standardization）

**功能分支**: `018-workflow-standardization`
**创建时间**: 2026-04-30
**状态**: 草稿
**输入**: 用户描述: "根据当前业务架构和已有系统设计，优化处理流程，使其更规范、清晰、简洁"

## 澄清（Clarifications）

### Session 2026-04-30

- **Q1**（领域 / 数据模型 — `business_step` 取值域）：「关键实体」节最初列了 9 个步骤名（含 `generate_report`），但 `docs/business-workflow.md § 5.1` 的步骤总览表只把 `diagnose_athlete` 列为独立编号步骤，`generate_report` 仅作为该步骤内部的产物出现。这会影响迁移回填 SQL、`business_step` 枚举值域以及漂移扫描脚本的对照表。
  - **决议（选项 B）**：`business_step` 值域收敛到 **8 个**，与 § 5.1 完全对齐；`generate_report` 作为 `diagnose_athlete` 步骤内部的子产物（`overall_score + dims + advice`），**不**占独立 `business_step` 值。
  - **影响位置**：本 spec「关键实体 / BusinessStep 字符串集合」小节同步收敛至 8 值；FR-008 漂移扫描的 `business_step` 枚举白名单同步按 8 值校验；未来如需把生成报告独立成步骤，须先走 `/speckit.constitution` 章程 MINOR 升级。

- **Q2**（集成 / 依赖 — 漂移扫描的 CI 触发时机与运行范围）：FR-011 要求 CI 在每个 PR 上运行漂移扫描与 Spec 合规扫描并阻断合并，但未量化「触发时机 / 运行范围 / 本地可回放方式」三要素，这直接决定研发体感与阻断力的平衡。
  - **决议（选项 A）**：**PR 触发 + 只扫 diff 涉及清单 + 本地可预演 + master 合并前全量闸门**。具体：
    1. 每次 push 到 PR 分支时触发，脚本默认以 `--changed-only` 模式运行，仅校验本次 PR 触达的清单项（文档命中路径 + 代码命中模块）；
    2. 脚本同时支持 `--full` 全量模式；master 合并前的最终闸门流水线 MUST 以 `--full` 模式运行一次；
    3. 研发本地可通过 `python -m scripts.audit.workflow_drift --changed-only` 和 `python -m scripts.audit.spec_compliance --changed-only` 预演，输出格式与 CI 完全一致。
  - **影响位置**：FR-011 改写为明确三层策略（PR渐进扫描 + master 全量闸门 + 本地 CLI 契合）；`scripts/audit/workflow_drift.py` 与 `scripts/audit/spec_compliance.py` MUST 支持 `--changed-only` / `--full` 两个互斥模式，默认 `--full`（CI/master），研发本地可指定 `--changed-only`。

- **Q3**（非功能 / 规模 — SC-003 与「> 1000 万行」边界情况之间的灰色区间）：SC-003 只定义了 ≤ 100 万行的 P95 目标，边界情况节只定义了 > 1000 万行走物化视图；**100 万 ~ 1000 万** 区间未定义，实施时无规则可依。
  - **决议（选项 B）**：**灰色区间自动降级**。具体：
    1. `analysis_tasks` 行数 **≤ 100 万**：完整返回 `{pending, processing, success, failed, p50_seconds, p95_seconds, recent_24h_completed}`，**P95 ≤ 500ms / P99 ≤ 1s**（维持 SC-003）；
    2. `analysis_tasks` 行数在 **(100 万, 1000 万]** 区间：**自动降级**——保留计数与 `recent_24h_completed`，**省略 `p50_seconds` / `p95_seconds` 字段**，`meta.degraded=true`、`meta.degraded_reason="row_count_exceeds_latency_budget"`，**P95 ≤ 1s / P99 ≤ 2s**；
    3. `analysis_tasks` 行数 **> 1000 万**：本 Feature 范围外，由后续「物化视图」Feature 交付（维持 Assumption 第 5 条）。
  - **影响位置**：FR-007 增补灰色区间降级规则；「阶段 overview 聚合超时」边界情况条目补灰色区间条款；SC-003 增加 (100 万, 1000 万] 区间的放宽目标与 `meta.degraded` 契约要求。

- **Q4**（数据模型 / 架构 — `business_phase` / `business_step` NOT NULL 的写入入口覆盖）：FR-001 要求四表两列「MUST NOT NULL」，FR-003 只列出 5 个 HTTP 写入入口；但 Celery worker 内部的衍生写入（如 `extract_kb` 任务内在创建 `extraction_jobs` 行）不走 FR-003 列出的入口，会被 NOT NULL 直接拒绝；且未来新增第 6 个入口时容易漏填，造成生产写入报错。
  - **决议（选项 A）**：**ORM `before_insert` 钩子自动派生 + 列级 NOT NULL 兜底**。具体：
    1. 在 `src/models/analysis_task.py` / `extraction_job.py` / `video_preprocessing_job.py` / `tech_knowledge_base.py` 中，基于 SQLAlchemy `event.listen(Model, "before_insert", _assign_phase_step)` 注册钩子；
    2. 钩子依据行的 `task_type`（`analysis_tasks`）/ 表名默认（`extraction_jobs` 固定 `TRAINING/extract_kb`、`video_preprocessing_jobs` 固定 `TRAINING/preprocess_video`、`tech_knowledge_bases` 固定 `STANDARDIZATION/kb_version_activate`）自动填入 `business_phase` / `business_step`；**如调用方显式传值则以调用方为准**（预留未来跨阶段任务的扩展口）；
    3. 钩子遍历不到派生规则时 MUST 抛 `ValueError("PHASE_STEP_UNMAPPED")`，使任何新增任务类型在写入时立刻被发现（fail-fast）而非落盘后静默 NULL；
    4. 列级约束保留 `nullable=False`，作为钩子失效时的最后兜底；单测 MUST 覆盖「直接 ORM 写入不传 phase/step」的场景，断言钩子生效。
  - **影响位置**：FR-001 增补「ORM 钩子是唯一派生入口 + 列级 NOT NULL 为兜底」双层约束；FR-003 重写为「所有写入路径（含 HTTP 入口、Celery 内部衍生写入、迁移脚本、直接 ORM）MUST 由钩子居中处理，禁止客户端在请求体中自行指定 `business_phase` / `business_step`」；新增作用点：`src/models/_phase_step_hook.py`（新增模块，集中注册钩子与派生表）；单测新增于 `tests/unit/models/test_phase_step_hook.py`。

- **Q5**（边界情况 / 错误处理 — 漂移扫描是否提供豁免（waiver）机制）：FR-008 ~ FR-012 要求 CI 对漂移 / Spec 合规扫描一律阻断合并，但 hotfix 、跨 Feature 过渡期可能产生短期漂移；是否引入结构化的 waiver 清单 + 时效 + 审计闭环。
  - **决议（选项 B — 系统未上线，尽可能简单）**：**不引入 waiver 机制**，所有漂移 / Spec 合规违规一律硬失败。具体：
    1. `scripts/audit/workflow_drift.py` 与 `scripts/audit/spec_compliance.py` 不支持任何行内 `# drift-ignore` 注释、不读取任何运行期豁免文件；任何违规 ⇒ `exit 1` ⇒ CI 阻断合并；
    2. hotfix 场景统一靠「代码修改 + 文档同步」的**原子 PR** 解决（章程 v1.5.0 第 3 则「文档与代码同步」原则已覃盖）；
    3. 跨 Feature 过渡期场景：如若 Feature-020 新增步骤未发布，新步骤字符串 MUST 先落地到「业务流程文档 § 3–§ 5 八步骤名单」与「关键实体 BusinessStep 清单」后才能在代码中引用；即「文档先行，代码跟进」；
    4. FR-012 的 `specs/001-*/` ~ `specs/017-*/` 静态排除清单不属于 waiver（那是「历史范围排除」的一次性白名单，与运行期豁免无关），以免歧义将文件名从 `.waiver.yml` 改为 `.scan-exclude.yml`。
  - **影响位置**：FR-011 添加「MUST NOT 提供行内注释 / 运行期豁免配置文件；违规 ⇒ exit 1」显式约束；FR-012 的 `.waiver.yml` 重命名为 `.scan-exclude.yml` 并注明「静态历史排除、非运行期豁免」。

- **Q6**（集成 / 依赖 — CI 守卫的落地平台）：tasks.md T037 原文写「在 `.github/workflows/` 或项目实际使用的 CI 配置中新增两个 job」；但仓库现状为：无 `.github/workflows/`、无 `.gitlab-ci.yml` / `Jenkinsfile` / `Makefile`，历史规范检查（`scripts/lint_api_naming.py` / `scripts/lint_error_codes.py`）靠研发本地手动执行。此现状下 Q2 决议的「CI 两层守卫（PR 渐进 + master 全量）」需要具体落地平台，否则 T037 无法验收。
  - **决议（选项 A — `Makefile` + `pre-push` Git hook 本地双层）**：零外部依赖、与现有 `lint_*.py` 策略同构。具体：
    1. 仓库根新增 `Makefile`，声明 3 个目标：`make drift-changed`（= `$PYBIN -m scripts.audit.workflow_drift --changed-only && $PYBIN -m scripts.audit.spec_compliance --changed-only`）、`make drift-full`（= 两个脚本 `--full`）、`make spec-compliance`（单独跑 `spec_compliance.py --full`）；`$PYBIN` 默认取 `/opt/conda/envs/coaching/bin/python3.11`，可被环境变量覆盖；
    2. 仓库根新增 `scripts/install-git-hooks.sh`（幂等），把 `.git/hooks/pre-push` 软链到仓库内的 `scripts/git-hooks/pre-push`；研发首次 clone 后执行一次安装即可；
    3. `scripts/git-hooks/pre-push` 内以 `--changed-only` 模式运行两个扫描，违规 `exit 1` 阻断 push；未安装 hook 的研发靠代码评审人工把关；
    4. 未来若项目引入 GitHub Actions / GitLab CI / Jenkins 之一，**不重写脚本**，只需在对应 CI 配置中调用 `make drift-full` 与 `make spec-compliance` 两个目标即可对接；
    5. 不引入 `.github/workflows/`、不引入托管 CI 平台配置（YAGNI，原则 IV）。
  - **影响位置**：tasks.md T037 重写为「落地 `Makefile` + `scripts/git-hooks/pre-push` + `scripts/install-git-hooks.sh`；不新增 `.github/workflows/` 目录」；quickstart.md § 5–6 示例命令改用 `make drift-changed` / `make drift-full`；plan.md 「CI 守卫落地方式」段落如有对 GitHub Actions 的隐式假设需改为「本地 Makefile + 可选 pre-push hook；未来可挂接任意托管 CI」。

- **Q7**（非功能 / 可度量性 — SC-007「开发体验」的度量方式）：原 SC-007 要求「研发同学新写一个 Feature 时遵循『业务阶段映射』的平均填写时间 ≤ 3 分钟，30 天内 ≥ 3 名研发主观反馈」。两项缺陷：平均时间无测量工具（纯主观估计，违反章程原则 VIII「量化精准度」）、样本量 3 人偏低；且与 SC-005「Spec 合规率 = 100%」语义重叠。
  - **决议（选项 A — 合并入 SC-005，删除 SC-007）**：与「系统未上线、尽可能简单」（Q5）路线一致。具体：
    1. 直接删除 SC-007 条目；`## 可衡量的结果` 条目数由 9 条减为 8 条（SC-001 ~ SC-006 + SC-008 + SC-009）；
    2. 为保留历史引用、不扰动下游工具链，**编号不重排**：SC-007 空缺，文档侧允许跳号；
    3. 在 SC-005 末尾追加一句：「合规率由 `scripts/audit/spec_compliance.py` 自动统计，任何新 Feature 在 PR 阶段若缺失『业务阶段映射』六项子标签将被硬失败（无豁免，Q5）；即 SC-005 既覆盖『是否写了』又隐含覆盖『研发首次提交即合规的体验』，无需再单列 SC-007」；
    4. 不引入新的主观反馈收集流程 / 不新增问卷脚本；研发体验通过"合规率 = 100%"与"首次提交即通过 CI 的比例"间接度量。
  - **影响位置**：spec.md `## 可衡量的结果` 删除 SC-007 条目，编号跳号保留占位注释；SC-005 文本增补一段"合规率由 spec_compliance.py 自动统计 + 六项子标签硬失败"说明；plan.md / tasks.md 中如有对 SC-007 的显式引用同步移除。

- **Q8**（架构 / 可维护性 — 「业务阶段映射」六项子标签字面量的集中定义）：tasks.md T034 要求 `spec_compliance.py` 检查每个 `specs/019+/spec.md` 是否包含六项子标签（所属阶段 / 所属步骤 / DoD 引用 / 可观测锚点 / 章程级约束影响 / 回滚剧本）。但这六个字符串当前仅"口头约定"，分散在三处：`.specify/templates/spec-template.md` 模板落位点、`docs/business-workflow.md` 章程附录提及、`spec_compliance.py` 扫描硬编码（待实施）。未来若模板措辞改动（如"回滚剧本" → "回退方案"）而三方未同步，扫描会全量误报。
  - **决议（选项 A — 集中至 `scripts/audit/_spec_sections.py` + 纳入漂移扫描）**：章程原则 IX「集中化单一事实来源」的推广应用。具体：
    1. 新建 `scripts/audit/_spec_sections.py`，定义 `REQUIRED_BUSINESS_STAGE_FIELDS: tuple[str, ...] = ("所属阶段", "所属步骤", "DoD 引用", "可观测锚点", "章程级约束影响", "回滚剧本")` 为模块级常量；
    2. `scripts/audit/spec_compliance.py` `from scripts.audit._spec_sections import REQUIRED_BUSINESS_STAGE_FIELDS`，禁止在 `spec_compliance.py` 内硬编码这六个字符串；
    3. `scripts/audit/workflow_drift.py` 追加一条扫描类目：校验 `.specify/templates/spec-template.md` 含「业务阶段映射」小段且六项子标签齐全，若模板侧字符集与 `REQUIRED_BUSINESS_STAGE_FIELDS` 不一致 ⇒ 输出 `DRIFT: spec_template_fields <missing_field>`；
    4. tasks.md T034 追加子条目「`from scripts.audit._spec_sections import REQUIRED_BUSINESS_STAGE_FIELDS`；禁止硬编码六项字符串」；data-model.md § 9 `DriftReport.kind` 枚举追加 `spec_template_fields` 取值。
  - **影响位置**：新增 `scripts/audit/_spec_sections.py`；tasks.md T033 / T034 同步补充该常量的引用；data-model.md § 9 扩 `kind` 枚举；未来如新增或改名子标签，MUST 同时改 `_spec_sections.py` + `.specify/templates/spec-template.md`（漂移扫描会阻断单边改动）。

## 用户场景与测试 *(必填)*

<!--
  本 Feature 为「平台级规范化」Feature：不新增业务能力，而是把章程 v1.5.0
  原则 X 定义的「三阶段八步骤」业务执行模型显式落到系统的四个面：
    1) 数据模型侧（增加阶段/步骤标签列）
    2) 对外可观测侧（按阶段聚合的只读 API）
    3) 治理侧（章程级约束的 CI 守卫 + Spec 合规扫描）
    4) 优化杠杆侧（§ 9 三类杠杆入口统一台账）
  用户视角是「运营/SRE/研发」三类内部用户，不涉及终端 C 端用户。
-->

### 用户故事 1 — 按业务阶段/步骤查询任务与作业（优先级: P1）

运营同学每天要回答一个问题：**"现在系统里训练阶段跑了多少、建标阶段卡了多少、诊断阶段的端到端耗时是多少？"**。
今天他需要同时打开 `/api/v1/tasks`、`/api/v1/extraction-jobs`、`/api/v1/knowledge-base/versions`、
`/api/v1/classifications/scan/{task_id}` 四组接口，拿到四份不同的 status 枚举，再在 Excel 里按自己
记忆里的对应关系聚合成三阶段视图。本用户故事把"业务阶段 / 业务步骤"两个字段下沉到每一条任务与
作业记录上，并提供一个**按阶段聚合的总览接口**，让运营一次请求就能拿到三阶段快照。

**优先级原因**: 这是原则 X 落地的入口，其他故事（CI 守卫、杠杆台账）都需要依赖"阶段/步骤"
字段已经铺到底层记录上；无此铺垫，SRE 的排障成本线性增长，运营每日晨会需要 10+ 分钟手动聚合。

**独立测试**: 通过向 `GET /api/v1/business-workflow/overview` 发起一次 HTTP 请求，断言返回的
信封 `data` 中包含 `TRAINING` / `STANDARDIZATION` / `INFERENCE` 三个阶段键，每个键下含该阶段
涉及步骤的 `pending / processing / success / failed` 计数、P50/P95 耗时、最近 24h 吞吐；
同时断言 `GET /api/v1/tasks?business_phase=TRAINING&business_step=extract_kb` 仅返回
对应步骤的任务。

**验收场景**:

1. **给定** 系统内存在 10 条 `kb_extraction` 任务、5 条 `athlete_diagnosis` 任务、2 条预处理任务，
   **当** 调用 `GET /api/v1/business-workflow/overview`，**那么** 响应包含 3 个阶段键，
   `TRAINING.steps.extract_kb.total = 10`、`INFERENCE.steps.diagnose_athlete.total = 5`、
   `TRAINING.steps.preprocess_video.total = 2`，且 `meta.generated_at` 为 CST 时间戳。
2. **给定** 上述数据，**当** 调用 `GET /api/v1/tasks?business_phase=INFERENCE`，
   **那么** 仅返回 5 条 `athlete_diagnosis` 任务；响应信封 `meta.total = 5`。
3. **给定** 一个不合法的阶段值 `?business_phase=FOO`，**当** 调用任何含阶段筛选的接口，
   **那么** 返回 `400 INVALID_ENUM_VALUE`，`details.allowed` 列出 3 个合法阶段。

---

### 用户故事 2 — 章程级约束与业务流程文档双向同步的 CI 守卫（优先级: P2）

章程 v1.5.0 原则 X 规定：**队列拓扑 / 状态机枚举 / 错误码前缀 / 评分公式 / 单 active 与冲突门控
等章程级约束的变更 MUST 同步修改 `docs/business-workflow.md`**。但此约束今天只靠"人脑"执行，
PR 合并时无自动拦截。本用户故事在 CI 中引入**漂移检测脚本**，当代码侧的"集中清单"
（错误码前缀总集、TaskType 枚举、ExtractionJobStatus 枚举、`diagnosis_scorer` 阈值常量等）
与 `docs/business-workflow.md` 对应章节的值不一致时，**构建失败并指向差异行**。

**优先级原因**: 没有守卫 ⇒ 漂移随时间线性积累 ⇒ 运营按文档排障但代码实际行为已变 ⇒ 事故。
P2 而非 P1 是因为它依赖 US1 先把阶段/步骤字段固化为唯一事实来源。

**独立测试**: 在本地分支修改 `src/models/analysis_task.py::TaskStatus` 枚举（例如新增
`archived` 值），**不**修改 `docs/business-workflow.md`；运行 CI 脚本
`python -m scripts.audit.workflow_drift`，断言退出码 `!= 0` 且 stdout 含 `DRIFT: TaskStatus`
并列出新增值 `archived`。将同一枚举变更回滚，再运行脚本，退出码应为 0。

**验收场景**:

1. **给定** 代码侧错误码清单新增 `WHISPER_GPU_OOM` 前缀但 § 7.4 错误码表未同步，
   **当** CI 运行漂移扫描，**那么** 扫描失败、输出含 `DRIFT: error_code_prefix WHISPER_GPU_OOM`，
   退出码为 1；修复文档后重跑，退出码为 0。
2. **给定** 新建 Feature 目录 `specs/019-foo/spec.md` **未包含**「业务阶段映射」小段，
   **当** CI 运行规范合规扫描，**那么** 扫描失败、输出含 `MISSING_SECTION: 业务阶段映射`，
   并指向 `specs/019-foo/spec.md`。
3. **给定** `diagnosis_scorer` 的 `half_width` 分段阈值 `1.5` 改为 `1.8`，但 § 5.3 未同步，
   **当** CI 运行漂移扫描，**那么** 扫描失败并输出 `DRIFT: scorer_threshold half_width_slope`。

---

### 用户故事 3 — 优化杠杆入口统一台账（优先级: P3）

业务流程文档 § 9 定义三类优化杠杆：运行时参数（`task_channel_configs`）、算法/模型（`.env`）、
规则/Prompt（`config/*.json`、prompt 常量）。今天三类入口散落在 `PATCH /admin/channels/{...}`、
`.env` 文件、`config/tech_classification_rules.json`、`src/services/**/prompts.py` 等多个位置，
运营不知道哪些键可调、不知道改了谁负责审批、不知道生效时间。本用户故事建立一个**只读总览接口**
`GET /api/v1/admin/levers`，把三类可调入口的元信息（键名、当前值、杠杆类型、生效方式、
最近一次修改人与时间）汇集成一份台账。

**优先级原因**: 对日常业务运行不是阻塞路径，属于"运营体验 + 审计"增强；有了 US1/US2 的骨架后，
再补这一层"调参面板"会更自然。

**独立测试**: 调用 `GET /api/v1/admin/levers?phase=TRAINING`，断言返回含
`runtime_params`、`algorithm_models`、`rules_prompts` 三类分组，每项含 `key` / `current_value` /
`source` / `effective_in_seconds` / `last_changed_at` / `last_changed_by` 字段。

**验收场景**:

1. **给定** `task_channel_configs` 表中 `kb_extraction.concurrency=2`，
   **当** 调用 `GET /api/v1/admin/levers`，**那么** 响应 `runtime_params` 分组中包含一条
   `key="task_channel_configs.kb_extraction.concurrency"`、`current_value=2`、
   `effective_in_seconds=30`、`source="db_table"` 的条目。
2. **给定** 运营通过 `PATCH /api/v1/admin/channels/kb_extraction {concurrency: 4}` 改配置，
   **当** 30 秒后再次调用总览，**那么** 对应条目 `current_value=4`、`last_changed_at` 更新。
3. **给定** `.env` 中 `POSE_BACKEND=auto`，**当** 调用总览，**那么** `algorithm_models` 分组
   包含 `key="POSE_BACKEND"`、`effective_in_seconds=null`（需重启 worker 生效）、
   `restart_scope="worker"`。

---

### 边界情况

- **历史任务无阶段字段**：迁移上线瞬间历史 `analysis_tasks` 行 `business_phase` 为空；
  回填策略 MUST 在迁移脚本中原子执行（依据 `task_type` → 阶段的静态映射表，见 § 关键实体）。
- **一条任务跨多阶段**：`scan_cos_videos` 的结果会同时被"预处理 → 分类 → 抽取"三步骤消费，
  但每条 `analysis_tasks` 行只归属**一个**步骤；跨步骤关联仍通过外键（如 `extraction_job_id`）表达。
- **阶段 overview 聚合超时**：聚合性能按 `analysis_tasks` 行数分三档（见澄清 Q3）：
  - ≤ 100 万行：完整返回耗时百分位，P95 ≤ 500ms；
  - (100 万, 1000 万] 行：**自动降级**——省略 `p50_seconds`/`p95_seconds`、`meta.degraded=true`、`meta.degraded_reason="row_count_exceeds_latency_budget"`，P95 ≤ 1s；
  - > 1000 万行：超出本 Feature 范围，由后续物化视图 Feature 承担。
  所有档位 MUST 限定聚合窗口为**最近 24 小时**，全量视图不在本 Feature 范围。
- **漂移扫描的误报**：业务流程文档中允许存在"建议补强的三类指标"（§ 7.5）等**非规范性**内容；
  扫描脚本 MUST 明确区分"规范区"与"建议区"，仅对前者做 diff。
- **杠杆台账敏感数据**：`.env` 中的 `VENUS_TOKEN` / `OPENAI_API_KEY` MUST NOT 出现在台账响应中，
  只显示 `key` 与 `is_configured: bool`。
- **阶段筛选与既有筛选组合**：`GET /api/v1/tasks?business_phase=TRAINING&task_type=athlete_diagnosis`
  属于**逻辑矛盾**（诊断任务不在训练阶段），MUST 返回 `400 INVALID_PHASE_STEP_COMBO`，
  `details.conflict = "phase_step_task_type_mismatch"`，不能静默返回空列表。

## 需求 *(必填)*

### 功能需求

#### FR-001 ~ FR-004: 业务阶段/步骤字段下沉（US1）

- **FR-001**: 系统 MUST 为 `analysis_tasks`、`extraction_jobs`、`video_preprocessing_jobs`、
  `tech_knowledge_bases` 四张核心业务表分别新增两列：`business_phase`（枚举：`TRAINING` /
  `STANDARDIZATION` / `INFERENCE`）与 `business_step`（字符串，取值限定于业务流程文档 § 3–§ 5
  的八步骤名单）。两列 MUST 采用**双层约束**来保障「0% NULL」（见澄清 Q4）：
  1. **ORM 钩子是唯一派生入口**：四个模型 MUST 注册 `before_insert` 事件钩子，钩子集中注册于 `src/models/_phase_step_hook.py`；钩子按表名 + `task_type` 自动填入两列，派生规则不覆盖调用方显式传入的值；派生不到规则时抛 `PHASE_STEP_UNMAPPED`。
  2. **列级 NOT NULL 兜底**：`nullable=False` 作为钩子失效时的最后防线，写入时直接被 Postgres 拒绝，避免静默 NULL 落盘。
  历史数据由迁移脚本回填（见 FR-002）后再添加 `NOT NULL` 约束；迁移脚本 MUST 原子执行「回填 → 添加约束」。
- **FR-002**: 迁移脚本 MUST 依据以下静态映射回填历史数据：
  - `analysis_tasks.task_type=video_classification` → `TRAINING / scan_cos_videos`（若 `parent_scan_task_id IS NULL`）或 `TRAINING / classify_video`（否则）
  - `analysis_tasks.task_type=video_preprocessing` → `TRAINING / preprocess_video`
  - `analysis_tasks.task_type=kb_extraction` → `TRAINING / extract_kb`
  - `analysis_tasks.task_type=athlete_diagnosis` → `INFERENCE / diagnose_athlete`
  - `extraction_jobs` 所有行 → `TRAINING / extract_kb`
  - `video_preprocessing_jobs` 所有行 → `TRAINING / preprocess_video`
  - `tech_knowledge_bases` 所有行 → `STANDARDIZATION / kb_version_activate`
- **FR-003**: 所有导致上述四表写入的路径 MUST 由 ORM `before_insert` 钩子居中处自动派生 `business_phase` / `business_step`，覆盖下列全部形态：
  1. **HTTP 入口**：`POST /api/v1/tasks`（所有任务类型）、`POST /api/v1/extraction-jobs/{id}/rerun`、`POST /api/v1/classifications/scan`、`POST /api/v1/knowledge-base/{version}/approve`；
  2. **Celery 内部衍生写入**：如 `extract_kb` 任务运行中对 `extraction_jobs` / `pipeline_steps` 相关表的中间创建；
  3. **迁移脚本与直接 ORM 写入**：包含单测 / 修复脚本 / 未来新增的第 6+ 个写入入口。
  客户端 MUST NOT 在 HTTP 请求体中自行传入 `business_phase` / `business_step`——Pydantic 请求 Schema 将这两个字段显式排除（`Field(exclude=True)` 或等效策略），传入即由 FastAPI 校验层返回 `422 VALIDATION_FAILED`（复用章程 IX 既有错误码，遵循原则 IV YAGNI，不新增一次性错误码）；`details.loc` 会自动标出两列名。服务层内部的显式传入视为「授权覆写」，钩子 MUST 尊重。
- **FR-004**: 列表类接口（`GET /api/v1/tasks`、`GET /api/v1/extraction-jobs`、
  `GET /api/v1/knowledge-base/versions`）MUST 支持可选查询参数 `business_phase` 与 `business_step`，
  参数值按小写下划线归一化；非法值返回 `400 INVALID_ENUM_VALUE`，`details.allowed` 列出合法集合。

#### FR-005 ~ FR-007: 业务流程总览接口（US1）

- **FR-005**: 系统 MUST 提供只读接口 `GET /api/v1/business-workflow/overview`，返回统一响应信封，
  `data` 结构包含 `TRAINING` / `STANDARDIZATION` / `INFERENCE` 三个键，每个键下列出该阶段涉及
  的步骤及其计数：`{pending, processing, success, failed}`、耗时 `{p50_seconds, p95_seconds}`、
  最近 24h 吞吐 `recent_24h_completed`。
- **FR-006**: 总览接口 MUST 在 `meta.generated_at` 中返回 CST 时间戳，并在 `meta.window_hours`
  中明确本次聚合窗口（默认 24，允许通过 `?window_hours=` 指定 1–168 范围，越界 400）。
- **FR-007**: 总览接口性能按 `analysis_tasks` 行数分档（详见澄清 Q3）：
  - **≤ 100 万行**：完整响应（含 `p50_seconds` / `p95_seconds`），P95 ≤ 500ms、P99 ≤ 1s；
  - **(100 万, 1000 万] 行**：**自动降级响应**——省略 `p50_seconds` / `p95_seconds`，`meta.degraded` MUST = `true`、`meta.degraded_reason` MUST = `"row_count_exceeds_latency_budget"`，P95 ≤ 1s、P99 ≤ 2s；
  - **> 1000 万行**：超出本 Feature 范围，由后续物化视图 Feature 承担（参 Assumption § 5）。
  聚合 MUST 基于现有索引 `idx_analysis_tasks_channel_counting` 扩展，不允许全表扫描；降级阈值判断 MUST 走 Postgres `pg_class.reltuples` 估算值，避免 `COUNT(*)` 本身拉高 P95。

#### FR-008 ~ FR-012: 章程级约束漂移守卫（US2）

- **FR-008**: 系统 MUST 在 `scripts/audit/workflow_drift.py` 下提供漂移扫描脚本，扫描对象共 8 类：
  - 代码侧错误码前缀总集（`src/services/kb_extraction_pipeline/error_codes.py::ALL_ERROR_CODES`
    + `src/services/preprocessing/error_codes.py`）与 `docs/business-workflow.md § 7.4` 的表格内容
  - `src/models/analysis_task.py::TaskStatus` / `TaskType` 枚举值与 § 7.1 的 `status ∈ {...}` 列表
  - `src/models/extraction_job.py::ExtractionJobStatus` 与 § 2 阶段 DoD 表
  - `src/models/tech_knowledge_base.py::KBStatus` 与 § 4.3 状态机
  - `diagnosis_scorer` 分段阈值常量（`half_width_*` / `slope_*`）与 § 5.3 公式
  - `task_channel_configs` 种子（队列名 + 默认容量）与 § 3.1 / § 5.1 表格
  - `config/optimization_levers.yml` 的 `key` 列表与 `docs/business-workflow.md § 9` 三类杠杆表格（FR-014 双向同步）
  - `.specify/templates/spec-template.md` 的「业务阶段映射」六项子标签字面量与 `scripts/audit/_spec_sections.py::REQUIRED_BUSINESS_STAGE_FIELDS` 常量（Clarification Q8）
- **FR-009**: 漂移扫描 MUST 以**集中清单 → 文档**的单向语义进行：代码侧是事实来源，文档侧必须同步。
  对每项差异输出机器可读行 `DRIFT: <kind> <identifier> [code_side=<v>] [doc_side=<v>]`，
  退出码 `0`（无差异）或 `1`（有差异）。
- **FR-010**: 系统 MUST 在 `scripts/audit/spec_compliance.py` 下提供 Spec 合规扫描脚本，扫描所有
  `specs/*/spec.md`，断言每份 spec 含**「业务阶段映射」**小段且字段完整（阶段、步骤、DoD 引用、
  可观测锚点、约束影响、回滚剧本六项）。对缺失 spec 输出 `MISSING_SECTION: <feature_id> 业务阶段映射`。
- **FR-011**: 守卫 MUST 分两层运行 FR-008 与 FR-010 扫描，任一层失败即阻断，详见澄清 Q2 + Q6：
  1. **PR 渐进扫描（pre-push 阶段）**：以 `--changed-only` 模式运行，仅校验本次改动涉及的清单项（文档命中路径 + 代码命中模块），兼顾阻断力与研发体感；由仓库内 `scripts/git-hooks/pre-push` + `Makefile` 目标 `make drift-changed` 落地（Clarification Q6 选项 A，**不引入托管 CI 平台配置**）。
  2. **master 全量闸门**：合并到 master 前 MUST 运行一次 `make drift-full`（= 两脚本 `--full` 模式），确保全视图无漂移；研发本地通过 `make drift-full` 触发，未来接入任意托管 CI（GitHub Actions / GitLab / Jenkins）时仅需在配置中调用这两个 Makefile 目标即可对接。
  3. **本地可预演**：`scripts/audit/workflow_drift.py` 与 `scripts/audit/spec_compliance.py` MUST 支持 `--changed-only` / `--full` 两个互斥模式（默认 `--full`），本地执行输出格式与未来 CI 一致。
  4. **无 waiver 约束（澄清 Q5）**：两个脚本 MUST NOT 支持行内 `# drift-ignore` 注释、MUST NOT 读取任何运行期豁免配置；任何违规 ⇒ `exit 1` ⇒ 硬失败；hotfix 与跨 Feature 过渡期统一靠「代码+文档原子 PR」解决。
  失败时 MUST 在 stdout/stderr 明示违规行与本次运行模式（`changed-only` 或 `full`），便于本地终端定位与未来 CI 日志 artifact 收集；若未来接入托管 CI，SHOULD 将 stdout 前 50 行粘回 PR 评论。
- **FR-012**: 历史已合并的 `specs/001-*/` ~ `specs/017-*/` 不在本 Feature 范围内回填
  「业务阶段映射」；扫描 MUST 支持**静态历史排除清单**机制，初始清单即这 17 个目录，由
  `scripts/audit/.scan-exclude.yml` 维护。此文件是「历史范围静态排除」，不是运行期豁免
  （澄清 Q5 已明确否决 waiver 机制）；新增/修订规范不在排除清单内。

#### FR-013 ~ FR-016: 优化杠杆统一台账（US3）

- **FR-013**: 系统 MUST 提供只读接口 `GET /api/v1/admin/levers`，返回统一信封，`data` 含
  `runtime_params` / `algorithm_models` / `rules_prompts` 三类分组。每条目字段：
  `key` / `current_value` / `source` / `effective_in_seconds` / `restart_scope` /
  `last_changed_at` / `last_changed_by` / `business_phase`（该杠杆影响哪些阶段，可多选）。
  **`last_changed_at` / `last_changed_by` 取值语义**（遵循原则 IV，不引入新表存历史）：
  - `source=db_table` 条目：取 `task_channel_configs.updated_at` / `updated_by`（既有列，无值返回 `null`）
  - `source=config_file` 条目：派生自 `git log -1 --format='%ai|%an' -- <file>`（子进程调用，失败返回 `null`）
  - `source=env` 条目：`.env` 无内置 author 元数据，两字段 MUST 返回 `null`（Schema 允许可空）
- **FR-014**: 台账条目清单 MUST 在 `config/optimization_levers.yml` 中显式枚举，禁止反射式扫描
  `.env` 所有键；新增杠杆入口 MUST 先登记到该 YAML 再入代码。YAML 条目 MUST 与业务流程文档 § 9
  表格双向同步（纳入 FR-008 漂移扫描范围）。
- **FR-015**: 敏感值（在 YAML 中标记 `sensitive: true` 的键）MUST NOT 在响应中返回 `current_value`，
  仅返回 `is_configured: bool`。当前已知敏感键：`VENUS_TOKEN` / `OPENAI_API_KEY` /
  `COS_SECRET_KEY` / `POSTGRES_PASSWORD`。
- **FR-016**: 台账接口支持 `?phase=TRAINING|STANDARDIZATION|INFERENCE` 过滤；
  当条目 `business_phase` 字段包含所查询阶段时才返回。

#### FR-017: 认证与权限

- **FR-017**: 新增接口中 `GET /api/v1/business-workflow/overview` 无需鉴权（只读聚合，与现有
  `GET /api/v1/task-channels` 一致）；`GET /api/v1/admin/levers` MUST 复用现有
  `ADMIN_TOKEN` 鉴权机制（与 `PATCH /api/v1/admin/channels/*` 一致），无令牌返回
  `401 ADMIN_TOKEN_INVALID`。

### 关键实体 *(涉及数据)*

- **BusinessPhase 枚举**：三值枚举 `TRAINING` / `STANDARDIZATION` / `INFERENCE`，作为 Postgres
  enum type `business_phase_enum` 在迁移中创建，复用到四张业务表与两个新接口的查询参数。
- **BusinessStep 字符串集合**：取值限定于业务流程文档 § 3.1、§ 4.1、§ 5.1 三张步骤表的 **8 个**名称（与文档 § 5.1 完全对齐，见澄清 Q1）：
  `scan_cos_videos` / `preprocess_video` / `classify_video` / `extract_kb` /
  `review_conflicts` / `kb_version_activate` / `build_standards` / `diagnose_athlete`。
  `generate_report` **不**在此集合内——它是 `diagnose_athlete` 步骤内部的子产物（`overall_score + dims + advice`），不独立占 `business_step` 值。步骤名称作为文本列存储（未来扩展友好），由 Pydantic Schema 的 `Literal` 守卫值域。
- **OptimizationLever 条目**：来自 `config/optimization_levers.yml` 的 YAML 对象，字段
  `key` / `type`（`runtime_params` / `algorithm_models` / `rules_prompts` 三选一）/
  `source`（`db_table` / `env` / `config_file`）/ `effective_in_seconds` / `restart_scope` /
  `business_phase`（列表）/ `sensitive`（布尔）。加载时做 schema 校验失败则 API 启动失败（fail-fast）。
- **WorkflowOverviewSnapshot**：纯响应 DTO，不落库；每次请求实时聚合生成。
- **DriftReport**：扫描脚本产出的结构化报告 JSON（供 CI 后续可视化用），字段
  `scanned_at` / `kind` / `identifier` / `code_side` / `doc_side` / `severity`。写入
  `/tmp/workflow_drift_report.json`，CI 收集为 artifact。

### 业务阶段映射 *(必填 - 原则 X / 章程 v1.5.0)*

- **所属阶段**: **横跨三阶段**（本 Feature 的本质是把"三阶段八步骤"语义落到系统各面上），
  但按章程 X 要求"跨阶段功能 MUST 拆成独立用户故事分别声明"——故：
  - 用户故事 1（阶段字段下沉 + 总览接口）→ 影响 TRAINING / STANDARDIZATION / INFERENCE 全部三阶段
  - 用户故事 2（CI 漂移守卫）→ 元级别，不归属任一阶段（治理层）
  - 用户故事 3（杠杆台账）→ 影响 TRAINING / INFERENCE 两阶段（§ 9 三杠杆主要作用于这两阶段）
- **所属步骤**: 本 Feature 本身**不新增业务步骤**，而是在既有八步骤外围补"观测与治理"平面。
  US1 覆盖所有八步骤的元数据标注；US2 覆盖章程级约束的文档同步；US3 覆盖 § 9 杠杆登记。
- **DoD 引用**: 业务流程文档 § 2 阶段 DoD 表不变（本 Feature 不修改任何阶段判据），新增本 Feature
  自身的 DoD：**所有四张业务表的 `business_phase` 列回填率 = 100%，漂移扫描脚本在 master 分支
  退出码 = 0，`GET /api/v1/business-workflow/overview` P95 ≤ 500ms**（详见 SC 节）。
- **可观测锚点**: § 7.1（任务级）新增 `business_phase` / `business_step` 两个标注维度；
  § 7.2（步骤级）不变；§ 7.4（错误码表）纳入 FR-008 漂移扫描；新增 § 7.6「业务阶段总览」
  子节文档化 `/business-workflow/overview` 接口（需在 refresh-docs 时添加）。
- **章程级约束影响**: 本 Feature **不改变**任一既有章程级约束（队列拓扑 / 状态机枚举 /
  错误码前缀 / 评分公式 / 单 active 或冲突门控），仅把这些约束的"集中清单"与文档同步**自动化**。
  `docs/business-workflow.md` 需要在 § 7.1 / § 7.4 两处追加脚注说明"对应清单由 CI 漂移扫描守护"，
  以及新增 § 7.6 业务阶段总览子节。
- **回滚剧本**: **low-risk with explicit rollback**。
  - US1 数据迁移可回滚：迁移脚本 MUST 支持 `alembic downgrade -1` 删除两列与 enum type
    （同时恢复原 `task_status_enum` 不受影响）；回滚后 API 层的新字段返回 `null`，
    不影响既有读写流程。
  - US1 新接口可灰度：`/business-workflow/overview` 为新增只读接口，回滚即下线，无数据残留。
  - US2 CI 守卫无 waiver（澄清 Q5）：本 Feature 明确不引入运行期豁免机制；漂移误报靠「修订集中清单 或 补全文档」的原子 PR 解决，而非提供绕过通道。
    `scripts/audit/.scan-exclude.yml` 是「历史静态排除」（初始即 `specs/001-*/` – `specs/017-*/` 17 个目录），非运行期豁免。
  - US3 杠杆台账可降级：`GET /admin/levers` 出现异常时 MUST 返回部分数据（`data.errors[]`
    字段收集失败分组），而非整体 503。
  - 无涉及业务流程文档 § 10 的 KB 版本回滚、通道熔断等高危操作。

## 成功标准 *(必填)*

### 可衡量的结果

- **SC-001（语义覆盖率）**: 迁移上线后，`analysis_tasks` / `extraction_jobs` /
  `video_preprocessing_jobs` / `tech_knowledge_bases` 四张表的 `business_phase` 与
  `business_step` 列 NULL 率 = **0%**（历史回填 + 新写入入口双重保障）。
- **SC-002（运营查询时效）**: 运营获取"当前三阶段全景"的操作成本从今天的"4 次 API + 人工聚合
  ≥ 3 分钟"下降到"1 次 API + 肉眼可读 ≤ 10 秒"。
- **SC-003（总览接口性能）**: `GET /api/v1/business-workflow/overview` 分档性能目标（详见澄清 Q3 与 FR-007）：
  - `analysis_tasks` ≤ 100 万、窗口 24h：**P95 ≤ 500ms / P99 ≤ 1s**，QPS ≥ 20 不降级，响应含完整耗时百分位；
  - `analysis_tasks` 在 (100 万, 1000 万]、窗口 24h：**P95 ≤ 1s / P99 ≤ 2s**，响应 `meta.degraded=true` 且省略耗时百分位；
  - 两档响应 MUST 100% 通过合约测试断言 `meta.degraded` 契约（降级档必须显式为 `true`，非降级档必须为 `false` 或省略）。
- **SC-004（漂移守卫有效性）**: 漂移扫描在上线后 30 天内至少拦截 1 次真实漂移 PR
  （由漂移扫描日志统计），**假阳性率 ≤ 5%**；误报靠「修订集中清单或补全文档」的原子 PR 解决，本 Feature 不引入 waiver 机制（澄清 Q5）。
- **SC-005（Spec 合规率）**: 自 Feature-018 合并后，**所有新建/修订的 specs（019+）
  的「业务阶段映射」段落完整率 = 100%**；在 30 天内监测为 0 例绕过。
  合规率由 `scripts/audit/spec_compliance.py` 自动统计，任何新 Feature 在 PR 阶段若缺失
  「业务阶段映射」六项子标签（所属阶段 / 所属步骤 / DoD 引用 / 可观测锚点 / 章程级约束影响 / 回滚剧本）
  将被硬失败（无豁免，Q5）；六项子标签字面量由 `scripts/audit/_spec_sections.py`
  集中定义并被漂移守卫守护（Q8）。SC-005 既覆盖「是否写了」，也隐含覆盖「研发首次提交即合规」
  的开发体验，无需再单列 SC-007。
- **SC-006（杠杆台账完整度）**: 台账覆盖率 ≥ **90%**（至少覆盖 § 9 表格中枚举的所有
  杠杆键 + 通过漂移扫描验证与 § 9 双向一致）。
- **SC-007**: _已于 Clarification Q7 决议（选项 A）合并入 SC-005，本条目作废但保留编号以避免下游引用失效。`scripts/audit/spec_compliance.py` 默认允许此类"作废占位"条目（通过正则 `^- \*\*SC-\d+\*\*: _已于` 匹配豁免），无需白名单配置。_
- **SC-008（无回归）**: 本 Feature 合并后 7 天内，业务流程四阶段的端到端任务成功率
  **相对基线波动 ≤ 1%**（由 `business-workflow/overview` 自身的 `recent_24h_completed` 对比基线）。
- **SC-009（信封一致性）**: 新增三个接口（`overview` / `/admin/levers` / `?business_phase=` 筛选）
  100% 使用 `SuccessEnvelope` / `ErrorEnvelope` 信封（原则 IX），合约测试覆盖 200 / 400 / 401 /
  500 四个状态码。

## 假设

- **业务流程文档稳定性**：假设 `docs/business-workflow.md` 的 § 1–§ 11 章节号与三阶段八步骤
  清单在本 Feature 实施期间（预计 ≤ 4 周）保持稳定；若文档期间发生重大改版，spec 与
  漂移扫描脚本需同步修订。
- **八步骤完备性**：假设现有业务流程覆盖系统 100% 的业务路径；`scan_cos_videos` 被视为
  TRAINING 阶段第 1 步（隐式步骤）。若后续出现"跨阶段桥接任务"（如反向从诊断数据回流改进 KB），
  本 Feature 不负责建模，留给后续 Feature。
- **历史数据清洁**：假设历史 `analysis_tasks` 的 `task_type` 字段 NULL 率 = 0（该约束由
  Alembic 0001 保证），回填脚本可直接按枚举值映射。
- **CI 基础设施**：假设项目 CI 已具备执行 `pytest` 和任意 Python 脚本的能力；
  本 Feature 不引入 CI 平台迁移成本。
- **规模边界**：假设 `analysis_tasks` 表在未来 12 个月内行数 ≤ 1000 万；超过该规模后
  总览接口 MUST 依赖物化视图，由后续 Feature 交付。
- **§ 9 杠杆封闭性**：假设未来 6 个月不引入第四类杠杆；若需引入，MUST 先扩展
  `docs/business-workflow.md § 9` 表格并升章程 MINOR 版本。
- **对 Feature-017 的依赖**：假设 Feature-017（API 标准化）已完成，`SuccessEnvelope` /
  `AppException` / `ErrorCode` 三件套已稳定可用；本 Feature 完全复用不重建。
- **鉴权模型不变**：假设现有 `ADMIN_TOKEN` Bearer 方案满足本 Feature 的 `/admin/levers`
  接口鉴权需要，不新增 RBAC / OAuth2 等复杂方案。

## 范围外 *(显式排除)*

以下条目**不在本 Feature 范围内**，需要时另开 Feature：

- 物化视图 / 离线报表（> 1000 万行规模的聚合）
- 前端可视化面板（章程明确排除前端代码）
- 基于阶段字段的实时告警 / webhook（属于 Observability Feature，不属于规范化）
- 反向数据流（诊断结果回流改进 KB 的闭环）
- 多语言文档漂移扫描（当前仅扫描中文 `docs/business-workflow.md`）
- 章程自身的版本漂移（由 `/speckit.constitution` 单独治理）
