# 功能规范: 技术分类体系重构与知识标准统一

**功能分支**: `023-tech-classification-rebuild`
**创建时间**: 2026-05-29
**状态**: 草稿
**输入**: 用户描述: "根据pp_book/pp_tech_classification.csv文件，重构当前技术分类系统和知识标准，使用更规范更完整的方式构建"

---

## Clarifications

### Session 2026-05-29

- Q: 分类粒度边界 — 新分类体系的最细粒度应停留在哪一级？是否允许在 `action` 之下再扩展子动作或在 `action` 缺失时降级到 `category_l3`？ → A: **严格四级 + 字典强约束**。最细粒度即 CSV 第 4 列 `action`（初期 44 行，现拓展为 56 行，作为唯一可选字典）；不引入子动作；当视频无法匹配任何 action 时直接归入 `unclassified`，不做层级降级。`category_l1/l2/l3/action` 四列均为新分类的事实来源。
- Q: 旧数据的零兼容范围 — "全部清理、不考虑兼容"具体覆盖哪些表？教练表与目录映射、通道配置等"非分类语义"数据是否一并清空？ → A: **全表 TRUNCATE，不区分分类语义**。`coach_video_classifications` / `video_classifications` / `expert_tech_points` / `tech_knowledge_bases` / `analysis_tasks` / `extraction_jobs` / `pipeline_steps` / `kb_conflicts` / `coaches` / `coach_directory_map` / `video_preprocessing_jobs` / `video_preprocessing_segments` 全部 TRUNCATE；`coaches` 与 `coach_directory_map` 由后续全量扫描自动重建；`task_channel_configs` 通过 system-init skill 重新 seed 默认通道配置。
- Q: COS 预处理产物的处理策略 — 已存在的 `video_preprocessing_jobs` 与 COS 上的标准化分片是否要一并销毁重做？ → A: **保留 COS 物理产物，仅清空业务表**。COS 上已生成的 standardized 片段不做删除（避免 ffmpeg 重算）；新一轮全量扫描通过 `cos_object_key` 反查命中已有 COS 产物时，直接回填 `preprocessed=true` 与 `preprocessing_job_id`，并在 `video_preprocessing_jobs` 中重建对应记录。
- Q: 执行机制 — 表清理 + 列变更 + 字典 seed 是分多步迁移还是单一原子迁移？ → A: **单一 Alembic 迁移 `0022_tech_taxonomy_rebuild.py`，事务原子执行 schema 变更**。upgrade 顺序：① DROP `coach_video_classifications.tech_category` 等旧列 → ② CREATE `tech_actions` 字典表并 seed 56 行 → ③ ADD `action` / `category_l1` / `category_l2` / `category_l3` 列与外键。downgrade 函数对称恢复列结构（数据无法回填，仅恢复 schema）。**注**：业务表 TRUNCATE 不在迁移内执行，由 system-init skill 在迁移完成后承担（参考 plan.md 摘要段；避免迁移文件承担数据清场职责）。

---

## 用户场景与测试 *(必填)*

### 用户故事 1 — 技术分类体系升级为严格四级结构 (优先级: P1)

运营人员希望系统使用更规范、更完整的严格四级分类体系（握拍方式 → 胶皮类型 → 手部技术·技术大类 → 具体动作名称）对教练视频进行分类，以便后续知识库提取和诊断能精确到具体动作级别（如"高吊弧圈球"vs"前冲弧圈球"），而不是粗粒度的 21 类扁平标签。

> **术语澄清**：四级 = `category_l1`（握拍方式）/ `category_l2`（胶皮类型）/ `category_l3`（手部技术·技术大类，以「·」拼接两个语义维度但仍为单列）/ `action`（具体动作名称）。Clarifications Q1 明确「严格四级」，不引入第 5 列字段、不做层级降级。

**优先级原因**: 这是整个重构的基础，后续所有故事（KB 提取标准化、术语归一化、标准构建精细化）都依赖于此严格四级分类体系的落地。

**独立测试**: 可以通过对一批已有教练视频重新运行分类，验证输出结果包含完整的四级字段（如 `横拍 / 反胶 / 正手·进攻 / 高吊弧圈球`），并与旧 21 类标签做对比，确认分类精度提升。

**验收场景**:

1. **给定** 一段正手拉下旋的教练视频，**当** 系统对其进行技术分类，**那么** 分类结果输出严格四级字段 `category_l1=横拍`、`category_l2=反胶`、`category_l3=正手·进攻`、`action=高吊弧圈球`（`action` 命中 `tech_actions` 字典表 56 行之一）
2. **给定** 一段反手拧拉的教练视频，**当** 系统对其进行技术分类，**那么** 分类结果输出 `category_l1=横拍`、`category_l2=反胶`、`category_l3=反手·进攻`、`action=拧`
3. **给定** 一段无法匹配 `tech_actions` 字典中任何 action 的视频，**当** 系统对其进行技术分类，**那么** 系统将 `action` 置为 `unclassified`，记录置信度低于阈值的原因，**不**对 `category_l1/l2/l3` 做层级降级填充
4. **给定** 数据库迁移 `0022_tech_taxonomy_rebuild` 已执行，**当** 查询任意业务表，**那么** 旧 `tech_category` 列已被物理删除，旧 21 类标签数据完全不存在（零兼容）

---

### 用户故事 2 — 全量教练视频重新分类 (优先级: P1)

运营人员希望能够触发一次全量重扫描，将 COS 中所有 1015+ 个教练视频按新严格四级分类体系重新分类，并将新的分类结果持久化到数据库，以便后续知识库提取使用精确的动作级分类。

**优先级原因**: 新分类体系落地后，历史数据必须同步更新，否则新旧数据混用会导致知识库质量不一致。

**独立测试**: 可以通过 `POST /api/v1/classifications/scan` 触发全量扫描，查询扫描进度，完成后验证数据库中所有记录均包含新的四级分类字段。

**验收场景**:

1. **给定** 迁移 `0022_tech_taxonomy_rebuild` 已执行且 system-init skill 已对业务表执行 TRUNCATE、COS 中存在 1015+ 个教练视频，**当** 运营人员触发全量扫描，**那么** 系统异步处理所有视频，从零写入 `coach_video_classifications`（含四级分类字段）并自动重建 `coaches` 与 `coach_directory_map`
2. **给定** 全量扫描正在进行中，**当** 运营人员查询扫描进度，**那么** 系统返回已处理数量、总数量、当前状态
3. **给定** 某 `cos_object_key` 在 COS 上已存在历史预处理产物（standardized 分片），**当** 扫描器处理该视频，**那么** 系统通过 `cos_object_key` 反查复用已有产物，回填 `preprocessed=true` 与 `preprocessing_job_id`，跳过 ffmpeg 重算

---

### 用户故事 3 — KB 提取输出格式标准化 (优先级: P2)

知识库管理员希望不同教练视频提取出的技术要点遵循统一的结构化格式（包含动作阶段、身体部位、教练原话关键词等），以便后续技术标准构建时能够跨教练聚合同一具体动作的要点，消除因教练风格差异导致的格式不一致问题。

**优先级原因**: 当前不同教练的 KB 提取结果格式差异大，导致技术标准构建时聚合困难，精度低。标准化后可显著提升标准构建质量。

**独立测试**: 可以对同一动作（如"高吊弧圈球"）的多位教练视频分别提取 KB，验证所有结果均包含相同的结构化字段（phase、instruction、body_part、cue_words），并能成功聚合。

**验收场景**:

1. **给定** 孙浩泓教练的"高吊弧圈球"视频，**当** 系统提取知识库，**那么** 输出包含按动作阶段（准备/引拍/击球/随挥/还原）组织的技术要点，每个要点含身体部位和教练原话关键词
2. **给定** 小孙教练的"高吊弧圈球"视频，**当** 系统提取知识库，**那么** 输出格式与孙浩泓的结果结构完全一致，可直接合并聚合
3. **给定** 两位教练对同一动作的 KB 提取结果，**当** 系统构建技术标准，**那么** 标准按具体动作（`高吊弧圈球`）而非粗粒度类别（`forehand_topspin`）聚合，精度更高

---

### 用户故事 4 — 术语归一化：口语化表达映射到标准术语 (优先级: P2)

知识库管理员希望系统能自动将教练口语化表达（如"包住球"、"亮板"、"收小臂"）映射到标准技术术语（如"摩擦加厚"、"拍面打开"、"前臂内收"），以便知识库中的术语统一，支持跨教练的精确检索和对比。

**优先级原因**: 术语不统一是当前知识库质量的主要瓶颈之一，归一化后可提升诊断建议的专业性和一致性。

**独立测试**: 可以提交包含口语化表达的教练视频进行 KB 提取，验证最终写入知识库的术语已被归一化为标准表达，同时原始口语化表达作为 `cue_words` 保留。

**验收场景**:

1. **给定** 教练说"包住球"，**当** 系统提取知识库，**那么** 标准术语字段写入"摩擦加厚"，原始表达"包住球"保留在 `cue_words` 中
2. **给定** 教练说"亮板"，**当** 系统提取知识库，**那么** 标准术语字段写入"拍面打开"
3. **给定** 一个未在映射表中的口语化表达，**当** 系统提取知识库，**那么** 系统通过 LLM 尝试归一化，若置信度不足则保留原始表达并标记为待审核

---

### 用户故事 5 — 技术标准按具体动作聚合 (优先级: P3)

运动员诊断系统希望技术标准能按具体动作（如"高吊弧圈球"、"前冲弧圈球"）分别构建，而不是按粗粒度的 21 类聚合，以便诊断时能给出更精确的偏差分析和改进建议。

**优先级原因**: 依赖 P1/P2 故事完成后才能实施，但对诊断精度提升有直接价值。

**独立测试**: 可以查询"高吊弧圈球"的技术标准，验证其参数范围（如击球时机、拍面角度）与"前冲弧圈球"的标准有明显区分，而非合并在同一个 `forehand_topspin` 标准下。

**验收场景**:

1. **给定** 多位教练的"高吊弧圈球"KB 已提取完成，**当** 系统构建技术标准，**那么** 生成独立的"高吊弧圈球"标准，包含该动作特有的参数范围
2. **给定** 运动员提交"高吊弧圈球"诊断请求，**当** 系统执行诊断，**那么** 使用"高吊弧圈球"专属标准进行对比，而非通用的 `forehand_topspin` 标准
3. **给定** 某具体动作尚无足够教练 KB 数据，**当** 系统尝试构建该动作标准，**那么** 系统降级使用父类别（如"进攻"）的聚合标准，并标记为"数据不足"

---

### 边界情况

- 当 CSV 文件中的动作名称包含特殊字符（如空格、括号）时，系统如何处理 `tech_actions` 字典表的主键编码？
- 当 LLM 给出的 `action` 不在 `tech_actions` 字典 56 行内时，系统直接拒绝并归入 `unclassified`（强字典约束）
- 当全量扫描中途失败时，已写入的记录保留；重启扫描通过 `cos_object_key` 幂等跳过
- 当同一视频被多次分类时，按 `cos_object_key` upsert，最新结果覆盖旧值
- 当 LLM 对某动作的置信度低于阈值（< 0.5）时，统一归入 `unclassified`，**不**保留最高置信度猜测

---

## 需求 *(必填)*

### 功能需求

**分类体系升级**

- **FR-001**: 系统必须基于 `pp_book/pp_tech_classification.csv` 构建严格四级分类体系（`category_l1` 握拍方式 → `category_l2` 胶皮类型 → `category_l3` 手部技术·技术大类 → `action` 具体动作名称），字典初期从 CSV 的 44 行（横拍反胶正手 22 个 + 反手 22 个）扩展为 **56 行**——新增 12 个 action 覆盖三个辅助 L3：`正手·防御`/`反手·防御` 补充《勈长》/《摆短》（3+3=4 个外加原有 0），新引入 `正手·步法`/`反手·步法`/`通用·教学辅助` 三个 L3；`action` 取值必须命中 `tech_actions` 字典表（强字典约束）
- **FR-002**: 系统必须将 56 行 action 通过迁移 seed 写入新建的 `tech_actions` 字典表（含 `action` 主键、`category_l1/l2/l3` 列），作为分类结果的唯一可选取值集合
- **FR-003**: 系统必须在分类结果中输出严格四级字段：`category_l1`、`category_l2`、`category_l3`、`action`，禁止引入旧 21 类 `tech_category` 字段（已在迁移中物理删除）
- **FR-004**: 系统必须支持二级匹配降级策略：层级树关键词匹配 → LLM 兜底分类（LLM 输出必须落在 `tech_actions` 字典内，否则视为失败）
- **FR-005**: 当分类置信度低于 **0.5**（分类阈值）或 LLM 输出不在字典内时，系统必须将 `action` 置为 `unclassified`，且 `category_l1/l2/l3` 留空（不做层级降级填充）

  > **阈值澄清**：本 feature 涉及两个独立的置信度阈值——分类阈值 `0.5`（FR-005，LLM 分类输出）与术语归一化阈值 `0.7`（FR-014，口语→标准术语映射）；二者互不影响，分别配置。

**数据持久化**

- **FR-006**: 系统必须在 `coach_video_classifications` 表中新增四级分类字段：`category_l1`、`category_l2`、`category_l3`、`action`（外键复合指向 `tech_actions (category_l1, category_l2, category_l3, action)`），并通过迁移 `0022_tech_taxonomy_rebuild` 完成
- **FR-007**: 系统必须在迁移 `0022_tech_taxonomy_rebuild` 中物理删除 `coach_video_classifications.tech_category` 列及其相关索引，KB 提取门控逻辑（`ClassificationGateService`）改为基于 `action IS NOT NULL AND action != 'unclassified'`

**全量重扫描**

- **FR-008**: 系统必须支持通过现有 `POST /api/v1/classifications/scan` 接口触发全量扫描，扫描器统一使用新四级分类体系；扫描时通过 `cos_object_key` 反查复用已有 COS 预处理产物，回填 `preprocessed=true` 与 `preprocessing_job_id`
- **FR-009**: 系统不引入 `classifier_version` 参数（零兼容，不存在新旧分类器并存）；扫描器统一调用 `TechClassifierV2`

**KB 提取标准化**

- **FR-010**: 系统必须为每次 KB 提取输出统一的结构化格式，包含：具体动作（`action`）、四级分类（`category_l1/l2/l3`）、按阶段组织的技术要点列表（`key_points`，每项含 `phase`/`instruction`/`body_part`/`cue_words`）、常见错误（`common_errors`）、练习建议（`drill_suggestions`）、置信度（`confidence`）
- **FR-011**: 系统必须在 `expert_tech_points` 表中新增 `action` 字段（外键指向 `tech_actions.action`），并在迁移中物理删除原有 `tech_category` 关联列（如有）

**术语归一化**

- **FR-012**: 系统必须在 KB 提取完成后、写入数据库前，对教练口语化表达执行术语归一化，将其映射到标准技术术语
- **FR-013**: 系统必须保留原始口语化表达作为 `cue_words`，标准术语写入独立字段
- **FR-014**: 当口语化表达无法通过静态映射表匹配时，系统必须通过 LLM 尝试归一化；若 LLM 置信度不足，保留原始表达并标记为待审核

**技术标准构建**

- **FR-015**: 系统必须按具体动作（`action` 字段）聚合技术标准（按 21 类聚合的旧逻辑随 `tech_category` 列删除一并废弃）
- **FR-016**: 当某具体动作的 KB 数据不足（**默认阈值：教练 KB 数 < 3**，可在 `config/standard_builder.json` 中配置）时，系统必须在标准元数据中标记 `data_insufficient=true` 但**不**做层级降级聚合（保持四级严格约束）

**API 适配**

- **FR-017**: `GET /api/v1/classifications` 接口响应必须输出 `category_l1` / `category_l2` / `category_l3` / `action` 四级字段，并移除原 `tech_category` 字段
- **FR-018**: `GET /api/v1/standards` 接口必须支持按 `action` 参数查询具体动作的技术标准；移除原 `tech_category` 查询参数

### 关键实体 *(如果功能涉及数据则包含)*

- **TechAction（技术动作字典表）**: 新建 `tech_actions` 表，**复合主键 `(category_l1, category_l2, category_l3, action)`**（CSV 中存在跨手部重名 action，如「高吊弧圈球」既属正手·进攻也属反手·进攻，故单列 `action` 不能保证唯一）；由迁移 `0022_tech_taxonomy_rebuild` seed CSV 初期 44 行后拓展至 56 行，作为分类与 KB 提取的唯一可选取值集合
- **ClassificationResult（分类结果）**: 单次视频分类的输出，包含 `category_l1` / `category_l2` / `category_l3` / `action` 四级字段与置信度（不含任何旧 21 类字段）
- **ExpertTechPoint（专家技术要点）**: KB 提取的最小单元，`action` 字段外键关联 `tech_actions`
- **TechStandard（技术标准）**: 多教练 KB 聚合后的参数范围，按 `action` 粒度构建（不再有按 `tech_category` 聚合的并行逻辑）
- **TerminologyMapping（术语映射）**: 口语化表达到标准术语的静态映射表，支持 LLM 动态扩充

### 业务阶段映射 *(必填)*

- **所属阶段**: 跨阶段功能，涉及 `CONTENT_PREP`（技术分类升级）、`TRAINING`（KB 提取标准化）、`STANDARDIZATION`（技术标准按动作聚合）三个阶段
  - 用户故事 1、2（分类体系升级 + 全量重扫描）→ `CONTENT_PREP` 阶段，步骤 3 `classify_video`
  - 用户故事 3、4（KB 提取标准化 + 术语归一化）→ `TRAINING` 阶段，步骤 6 `extract_kb`
  - 用户故事 5（技术标准按动作聚合）→ `STANDARDIZATION` 阶段，步骤 `build_standards`

- **所属步骤**:
  - `classify_video`（步骤 3）：分类器升级为 TechClassifierV2，输出严格四级字段与强字典约束的 `action`
  - `extract_kb`（步骤 6）：KB 提取 Prompt 标准化 + 术语归一化层插入
  - `build_standards`（步骤 7/8 后）：聚合粒度从 21 类改为 `action` 具体动作

- **DoD 引用**:
  - CONTENT_PREP 完成判据：`coach_video_classifications.action IS NOT NULL AND action != 'unclassified'` 且 `review_state='approved'`
  - TRAINING 完成判据：`extraction_jobs.status=success` 且 `expert_tech_points.action IS NOT NULL`
  - STANDARDIZATION 完成判据：`tech_knowledge_bases.status=active` 且标准按 `action` 粒度构建完成

- **可观测锚点**:
  - § 7.2 步骤级：`pipeline_steps.output_summary` 新增 `action_classified` / `terminology_normalized` 字段
  - `coach_video_classifications` 表：四级分类字段直接查询分类覆盖率
  - `expert_tech_points` 表：`action` 字段填充率作为 KB 提取质量指标

- **章程级约束影响**:
  - `TECH_CATEGORIES` 枚举废弃：`src/services/tech_classifier.py` 中旧 21 类枚举随实现一并删除，替换为基于 `tech_actions` 字典表的运行时加载
  - `tech_category` 字段物理删除：KB 提取门控逻辑（`ClassificationGateService`）改判 `action`
  - 技术标准构建 API（`GET /api/v1/standards`）的 `tech_category` 查询参数移除，新增 `action`，需同步更新 `business-workflow.md` § 4 建标阶段描述
  - 无队列拓扑变更、无状态机枚举变更、无错误码前缀变更

- **回滚剧本**:
  - 单一迁移 `0022_tech_taxonomy_rebuild` 提供对称 downgrade：仅恢复 `tech_category` 列结构、删除 `tech_actions` 表与新增四级列；**业务数据不可回填**（已被 system-init skill 清空）
  - 迁移本身不做 TRUNCATE，因此 downgrade 也不会重建任何业务数据；回滚后业务表为空，需重新运行 system-init skill seed `task_channel_configs`，并触发一次全量 COS 扫描
  - 不存在新旧分类器并存路径，因此不提供热切换回退；如需回退必须执行 `alembic downgrade -1` 后重启全部 worker

---

## 成功标准 *(必填)*

### 可衡量的结果

- **SC-001**: 全量重扫描完成后，`coach_video_classifications` 表中 95% 以上的记录包含非空的 `category_l1` 与 `action` 字段（当前 1015+ 条记录）
- **SC-002**: 新分类体系对已知动作的分类准确率（与人工标注对比）达到 85% 以上，高于旧 21 类体系的 70%
- **SC-003**: KB 提取后，同一具体动作（如"高吊弧圈球"）的多教练结果可直接合并聚合，无需人工格式转换
- **SC-004**: 术语归一化覆盖率达到 80% 以上（即 80% 的口语化表达能通过静态映射表或 LLM 成功归一化）
- **SC-005**: 技术标准按具体动作聚合后，诊断建议的动作级精度提升，运动员能收到"高吊弧圈球"而非"正手拉球"级别的具体改进建议
- **SC-006**: 全量重扫描（1015+ 视频）在 24 小时内完成，不影响现有诊断服务的正常运行

---

## 假设

- 假设 `pp_book/pp_tech_classification.csv` 是权威的分类体系来源，初期版本覆盖横拍反胶正手/反手共 44 个 action；后期拓展至 56 行（新增「勈长/摆短」、「并步/交叉步/推侧扑」等步法以及「接发球/握拍站位/教学概述」辅助类目，仍限定在横拍/反胶体系下），直拍（penhold）和颗粒胶（pips）分支暂不在本功能范围内
- 假设 `category_l3` 不局限于「·进攻/·防御/·发球」三种，可包含「·步法/·教学辅助」等辅助类目；KB 提取门控逻辑需额外过滤「·教学辅助」等不适合提取技术要点的 L3
- 假设旧 21 类 `TECH_CATEGORIES` 枚举在本功能落地后**完全废弃**（迁移中物理删除 `tech_category` 列与相关代码），不保留任何兼容路径
- 假设迁移 `0022_tech_taxonomy_rebuild` 执行期间，所有 Celery worker 与 API 服务停机；执行完成后通过 system-init skill 完成业务表 TRUNCATE 与 `task_channel_configs` reseed，再启动一次全量 COS 扫描以重建数据
- 假设 COS 上已存在的 standardized 视频分片**不删除**，新扫描通过 `cos_object_key` 反查复用，避免 ffmpeg 重算
- 假设术语归一化的静态映射表初始版本由开发团队根据教练常用口语整理，后续可通过运营人员审核扩充
- 假设 `video_classifications` 表（Feature-004 yaml 规则，12 教练）也在 TRUNCATE 范围内（零兼容统一清理），但**不**升级其 schema（仅清空数据）
- 假设 LLM 服务（Venus Proxy / OpenAI fallback）在全量扫描期间稳定可用
- 假设 Feature-017（API 标准化）已完成，本功能的 API 变更遵循现有信封规范
