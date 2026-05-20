# 功能规范: 教练视频内容清洗与有效片段筛选规范

**功能分支**: `021-video-content-curation`
**创建时间**: 2026-05-18
**状态**: 草稿
**输入**: 用户描述: "对网上采集的视频,不同教练风格不同,导致提取知识离散,先对内容清洗,筛选出有效片段再提供给下游使用.并对清洗过程建立规范."

---

## Clarifications

### Session 2026-05-18

- Q: 清洗规范文件的存储与版本化模型如何选型？ → A: 仅 Git 文件 + DB 存版本号引用——规范 YAML/JSON 放 `src/config/curation_rubric/`，文件内带 `version: vN`；运营改规则走 PR + CI schema 校验合并发版；DB `video_curation_jobs.curation_rubric_version` 仅存版本字符串，回查按版本号读 git 历史文件。
- Q: 清洗任务复用哪条 Celery 队列？ → A: 复用 `default` 队列（concurrency=1，与 `scan_cos_videos` / `housekeeping` 同列），不新增 `curation` 队列、不挤占 `kb_extraction` 主链路、不与 `preprocessing` ffmpeg 任务竞争。
- Q: `low_quality` 与下游 KB 抽取的判定阈值？ → A: 双阈值——`accepted_duration_ratio == 0` ⇒ KB 抽取以 `LOW_QUALITY_SKIP` 业务跳过；`accepted_duration_ratio < 0.3` ⇒ 视频级摘要标 `low_quality=true`，KB 抽取仍执行但在 `extraction_jobs` 上写 warning 标记供运营二次审；其它情况正常抽取。
- Q: 清洗判定的算法骨架（自动 `auto_decision` 怎么产生）？ → A: 规则优先 + LLM 兜底——按规范文件做关键词命中 / 时长 / 教练主导 / 主题相关性等结构化判定先行；规则路得分明确（`validity_score ≥ 0.7` 或 `≤ 0.3`）直接定案；落在 `(0.3, 0.7)` 模糊区间的分段才调一次 LLM 复核（Venus 优先 → OpenAI fallback）；LLM 不可用时模糊区间分段一律落 `uncertain`，不阻断作业。
- Q: 下游 KB 抽取消费"被覆盖分段"时的口径？ → A: 不自动触发重抽——人工覆盖只更新 `effective_decision` 与视频级摘要；如需重抽 KB，运营按需调 `POST /extraction-jobs/{id}/rerun` 显式发起；监控页对存在覆盖且未重抽的视频暴露"KB 使用旧口径"提示标记，避免静默不一致也不引入级联自动副作用。

---

## 用户场景与测试 *(必填)*

### 用户故事 1 — 自动识别并筛选"有效教学片段" (优先级: P1)

运营管理员对一批已经预处理完成的教练视频（来源不同、风格各异：有的开头闲聊 30 秒，有的中间穿插赛事录像，有的整段是采访不打球，有的多教练交替出镜），希望系统自动从视频分段中识别出"真正具备技术教学价值"的片段，把闲聊 / 比赛集锦 / 采访 / 空镜 / 重复演示等无效或低价值片段标记为"已剔除"，只让有效片段进入下游知识库抽取。整个动作以一条视频或一批视频为单位异步执行，完成后产出**逐分段的有效性判定结果**与一份**整段视频的有效率摘要**。

**优先级原因**：当前 KB 抽取直接消费全量预处理分段，把闲聊 / 赛事集锦 / 采访 / 重复镜头当成与技术讲解平权的输入丢给 LLM，是知识"离散化"的最大根因——清洗补齐之后，下游 LLM 的输入信噪比立刻提升，是本 feature 的核心价值切片。

**独立测试**：选一条已知含有混合内容（闲聊 + 真讲解 + 比赛回放）的预处理完成视频，提交"内容清洗"任务，等任务完成后查询逐分段的清洗结果接口，校验：(a) 闲聊 / 比赛回放对应分段被标记为 `rejected` 且带有具体 `rejection_reason`；(b) 真讲解分段被标记为 `accepted` 且带有 `validity_score`；(c) 整段视频的有效率（accepted_duration / total_duration）落在合理范围（0~1）；(d) 原始预处理分段对象在 COS 中保留不动，清洗只读不删。

**验收场景**:

1. **给定** 视频 V 已完成 F-016 预处理且产出 N 个分段；**当** 提交"内容清洗"任务并指定 V；**那么** 任务异步执行完成后，逐分段表里恰好有 N 行清洗结果，每行至少含 `decision ∈ {accepted, rejected, uncertain}` / `rejection_reason` / `validity_score ∈ [0,1]`，且不修改预处理分段表本身。
2. **给定** 视频 V 整段是赛事回放或采访（无技术讲解）；**当** 清洗执行；**那么** 视频级摘要 `accepted_duration_ratio == 0`，`low_quality=true`，下游 KB 抽取必须以 `LOW_QUALITY_SKIP` 业务跳过；若 `accepted_duration_ratio` 落在 `(0, 0.3)`，`low_quality=true` 但 KB 抽取仍执行并打 warning。
3. **给定** 视频 V 整段都是高质量正手攻球讲解；**当** 清洗执行；**那么** `accepted_duration_ratio` ≥ 0.8，`low_quality=false`，视频进入"可直接用于 KB 抽取"队列。
4. **给定** 视频 V 中段有明显教练切换（出镜人员变化）；**当** 清洗执行；**那么** 切换边界两侧分段被分别标记，且仅与目标教练（与 `coach_video_classifications.coach_name` 一致）匹配的分段才可能被 `accepted`，其余落 `rejected:other_speaker`。

---

### 用户故事 2 — 建立可审计的"内容清洗规范" (优先级: P1)

运营 / 算法负责人需要一份**与代码同源、版本化、可审计**的"内容清洗规范"，明确定义什么算"有效教学片段"、什么算"应剔除"，以及每条规则的判定优先级、置信度阈值、人审兜底策略；规范一旦发布即被清洗执行器逐字段执行，避免"每个程序员/每个教练自己心里一把尺"导致的下游知识离散。规范本身要能被运营在不改代码的前提下小幅调整（如关键词、阈值），并保留变更历史。

**优先级原因**：用户原始诉求里"对清洗过程建立规范"是与"清洗本身"并列的硬要求；没有规范，清洗结果不可解释、不可复现、不可争议；规范化也是后续多教练 KB 收敛、术语归一（Feature-021 提案 Phase F 的延续）的前提。

**独立测试**：把规范文件以版本号 `v1` 发布，跑一次清洗；把规范微调（如把"采访关键词列表"再加 3 个词）发布为 `v2`，对**同一视频**再跑一次清洗；校验：(a) 两次清洗结果各自记录所用 `curation_rubric_version`；(b) 旧任务结果不被覆盖；(c) 通过版本对比能列出受影响的分段差异。

**验收场景**:

1. **给定** 当前生效规范为 `v1`；**当** 任意清洗任务执行完成；**那么** 每条逐分段结果与视频级摘要必须持久化 `curation_rubric_version=v1`，事后可审计具体用的哪一版判据。
2. **给定** 规范文件被运营更新到 `v2`；**当** 提交一个新清洗任务；**那么** 新任务用 `v2` 执行；旧任务的历史结果保持 `v1` 不变，任何按视频回查得到的旧记录都能复原当时判据。
3. **给定** 规范文件含有破坏既有判据的语法错误或缺字段；**当** 系统加载规范；**那么** 加载失败并以明确错误码拒绝清洗任务提交，禁止以"半坏"规范跑清洗。
4. **给定** 规范要求"分段须出现明确技术术语关键词"；**当** 一条分段的转录文本完全无技术术语；**那么** 该分段在缺少其他强信号的情况下应落 `rejected:no_tech_terms`，可在结果中查到该 reason code。

---

### 用户故事 3 — 下游知识库抽取自动消费"已清洗的有效片段" (优先级: P1)

知识库抽取（F-014 DAG）当前消费整段视频的全部预处理分段；引入清洗后，KB 抽取必须**默认**只读取被清洗判为 `accepted` 的分段集合，被 `rejected` 的分段不再参与 LLM 提示拼装，也不进入姿态分析的统计聚合。当某条视频整体 `low_quality=true`，KB 抽取应能自动跳过（并在抽取作业里清晰标注"因清洗判低质而跳过"），而不是产出零知识点的成功作业。

**优先级原因**：清洗的真正业务价值只有在下游真消费时才兑现；如果 KB 抽取仍读全量分段，本 feature 等于白做；这一切片把"清洗 → 消费"链路完整闭合，是 DoD 必经环节。

**独立测试**：拿 US1 跑过的视频 V，提交一个 KB 抽取作业；校验：(a) 抽取过程中读取的分段集合 = 清洗结果中的 `accepted` 集合；(b) 当 `accepted` 集合为空（极端低质视频）时，KB 抽取作业落 `failed:NO_VALID_SEGMENTS`（或同等约定的"业务跳过"状态），禁止落空白成功；(c) 单测/集成测试有断言验证"被 rejected 的分段从未被读取"。

**验收场景**:

1. **给定** 视频 V 已完成清洗，`accepted` 分段集合非空；**当** 触发 KB 抽取；**那么** DAG 内部读取分段列表 = `accepted` 列表，被 `rejected` 的分段不出现在任何子步骤的输入中。
2. **给定** 视频 V 整体 `accepted_duration_ratio == 0`；**当** 触发 KB 抽取；**那么** 作业以 `LOW_QUALITY_SKIP` 业务结果立即结束（不耗费 LLM Token），并在 `extraction_jobs.error_code` 写入约定的错误码；当 `accepted_duration_ratio` 落在 `(0, 0.3)` 时 KB 抽取仍执行但落 warning 标记，可被监控筛出供运营二次审。
3. **给定** 视频 V 尚未跑清洗就被提交 KB 抽取；**当** 系统发现没有清洗记录；**那么** 任务以 `CURATION_REQUIRED` 错误立即拒绝（避免悄悄退化到"读全量分段"的旧行为）；运营按提示先跑清洗再重提抽取。

---

### 用户故事 4 — 人工复核与覆盖个别分段的清洗判定 (优先级: P2)

对于自动判定为 `rejected` 但运营经验认为"实际是有效片段"的少数边缘情况（或反之），运营可以在不重跑清洗的前提下，对单个视频的逐分段结果进行**人工覆盖**（`override_decision`），并填写覆盖理由；下游 KB 抽取以"最终决策 = 人工覆盖优先于自动决策"为口径消费。

**优先级原因**：自动清洗永远会有边界误判，提供人审通道是把"可争议"转成"可解释"的关键；P2 而非 P1 是因为 MVP 阶段可以用"调规范阈值"打个折扣过渡。

**独立测试**：选一条已清洗视频里被自动判 `rejected` 的分段 S，调用人工覆盖接口设为 `accepted`；再次发起 KB 抽取，校验：(a) 抽取读取的分段集合包含 S；(b) 视频级摘要的 `accepted_duration_ratio` 自动重算；(c) 覆盖记录留痕（`override_user / override_reason / overridden_at`）。

**验收场景**:

1. **给定** 分段 S 自动结果为 `rejected:other_speaker`；**当** 运营覆盖为 `accepted` 并填理由；**那么** 该分段最终 `effective_decision=accepted` 且 KB 抽取读它；同时仍保留原始 `auto_decision=rejected` 用作对比。
2. **给定** 一条视频的多个分段被覆盖；**当** 重新查询视频级摘要；**那么** `accepted_duration_ratio` / `low_quality` 标志按"覆盖后"重新计算，下游一致采用最新口径；若该视频此前已成功跑过 KB 抽取，则覆盖**不自动触发**重抽，但任务监控对该视频暴露 `kb_stale_after_override=true` 提示，运营可按需调用 `POST /extraction-jobs/{id}/rerun` 显式重跑。
3. **给定** 覆盖记录违反枚举值（如 `effective_decision=foo`）；**当** 提交覆盖；**那么** 接口立即 422 拒绝；不接受非枚举值。

---

### 用户故事 5 — 清洗效果观测与跨教练有效率对比 (优先级: P3)

运营可以在监控面查看：(a) 全量库里每位教练的"平均视频有效率"（按时长与分段两种口径）；(b) 每个 `tech_category` 的有效率分布；(c) 不同 `curation_rubric_version` 的整体效果差异。借此反向迭代规范阈值、识别"风格特别离群"的教练，必要时对该教练的视频做特别处置。

**优先级原因**：让"清洗规范"这件事从"一次性立规"变成"可持续优化"的依据，是 P3 锦上添花的能力；MVP 不阻断。

**独立测试**：跑完一批清洗任务后，调用聚合查询接口，能按 `coach_name` 与 `tech_category` 两个维度返回平均有效率与样本量，且支持按 `curation_rubric_version` 对比同一批视频两版规范的差异。

**验收场景**:

1. **给定** 系统已清洗 ≥ 30 条不同教练的视频；**当** 调用按教练聚合的有效率接口；**那么** 返回每位教练的样本数、平均 `accepted_duration_ratio`、平均 `validity_score`，**且支持分页或限定 top-N**。
2. **给定** 同一批视频先后用 `v1` 和 `v2` 规范各跑过一次；**当** 调用规范版本对比接口；**那么** 能看到两版规范下平均有效率差异、改判分段数（accepted↔rejected）、净影响时长。

---

### 边界情况

- **完全无音频轨**：清洗仍可基于视觉与时长 / 帧画面规则给出判定（不强依赖音频）；视频级摘要标 `audio_unavailable=true`。
- **预处理过期被清理**：清洗任务以 `PREPROCESSING_NOT_AVAILABLE` 业务错误失败而非静默回退；提示先重跑预处理。
- **同一视频重复提交清洗**：默认幂等（短路返回最近一次结果与所用规范版本）；带 `force=true` 强制重跑时新建一份记录，旧记录保留以供版本对比。
- **规范升级期间正在执行的清洗任务**：仍以任务**启动时**锁定的规范版本执行，不在中途切版；版本号写入结果。
- **极端短视频（< 30 秒）**：仍正常清洗；但视频级摘要在样本量统计里单独标识 `short_video=true`，避免拉偏聚合数据。
- **多教练同框 / 旁观者讲解**：判定规则按"目标教练为主导"判别；若无法确定主导教练，落 `rejected:speaker_ambiguous`，不强行接受。
- **比赛 / 集锦 / 慢动作回放**：默认归 `rejected:non_teaching_content`，避免污染 KB；可在规范里调阈值。
- **运动员（业余）视频混入教练根路径**：清洗仍执行，但视频级摘要标识 `wrong_root_suspected=true` 提示运维核查；与 Feature-020 运动员侧分支严格隔离，不污染对方表。
- **覆盖记录与重跑清洗的冲突**：`force=true` 重跑时，旧覆盖默认**保留**并继续按 `cos_object_key+segment_index` 对齐到新结果上；若新结果不存在该分段（如分段重切），覆盖自动作废并留痕。
- **任务监控分页 / 排序参数越界**：沿用章程 v1.4.0 — `page_size` 越界直接 422，禁止静默截断。

---

## 需求 *(必填)*

### 功能需求

- **FR-001**: 系统必须提供一个**独立的"内容清洗"任务类型**（与 `video_preprocessing` / `kb_extraction` / `video_classification` 等已有类型并列），执行 `coach_video_classifications` 中已完成预处理的视频内容清洗；禁止把清洗逻辑悄悄塞进 `preprocess_video` 或 `extract_kb` 既有任务里。
- **FR-002**: 系统必须支持单条与批量两种提交方式，批量遵循现有通道容量门控（沿用 F-013），并在通道未满时并行入队。
- **FR-003**: 清洗执行单元必须以"预处理分段"为最小判定粒度，对每个分段产出至少：`decision ∈ {accepted, rejected, uncertain}`、`validity_score ∈ [0,1]`、`rejection_reason`（仅在非 accepted 时填）、所用 `curation_rubric_version`。判定算法采用"规则优先 + LLM 兜底"两层骨架：第 1 层按规范文件做结构化规则命中（关键词 / 时长 / 教练主导 / 主题相关性等），`validity_score ≥ 0.7` 直接落 `accepted`、`≤ 0.3` 直接落 `rejected`；第 2 层只对落在 `(0.3, 0.7)` 模糊区间的分段调一次 LLM 复核（Venus 优先 → OpenAI fallback，统一走 `src/services/llm_client.py`），LLM 给出最终 `validity_score` 与 `decision`；LLM 不可用时模糊区间分段一律落 `uncertain` 并落具体原因码，不阻断整个作业。
- **FR-004**: 系统必须为每条视频整体产出"视频级清洗摘要"，至少含：`accepted_duration_ratio`、`accepted_segment_count`、`rejected_segment_count`、`low_quality` 布尔、`audio_unavailable` 布尔、`short_video` 布尔；其中 `low_quality` 由统一阈值派生：`accepted_duration_ratio < 0.3` 时为 `true`，否则 `false`；这些字段是下游 KB 抽取调度的判据。
- **FR-005**: 系统必须把"清洗规范"以一份**带版本号、可机读、与代码同源**的配置文件维护，存放在代码仓 `src/config/curation_rubric/` 目录下（YAML/JSON 由 plan 阶段在 A 选型框架内决定），文件头部含 `version: vN` 字段；运营调整规则走 PR + CI schema 校验合并发版，禁止任何"线上编辑接口"；任意一次清洗结果必须持久化所用规范版本字符串到 `video_curation_jobs.curation_rubric_version`，事后按版本号回查 git 历史还原判据快照。
- **FR-006**: 清洗规范必须明确至少以下判据维度（缺一不可）：教学内容关键词命中、非教学内容（赛事 / 采访 / 闲聊）排除规则、目标教练主导判别、单分段最短时长、单分段无音频策略、目标 `tech_category` 主题相关性；每个判据必须可独立开关与调阈值。
- **FR-007**: 系统加载规范时必须做 schema 校验，缺字段 / 类型错误 / 阈值越界一律拒绝加载，并以明确错误码阻止清洗任务排队，禁止以"半坏"规范运行。
- **FR-008**: KB 抽取（F-014）必须默认仅消费 `effective_decision=accepted` 的分段集合；`rejected` 分段不得出现在任一 DAG 子步骤的输入中（视觉路、音频路、合并路一致）。
- **FR-009**: 当一条视频的 `accepted_duration_ratio == 0`（即清洗后无任何 `effective_decision=accepted` 的分段）时，KB 抽取必须以业务结果短路结束（不再调 LLM），错误码 `LOW_QUALITY_SKIP`，可被任务监控明确识别为"清洗判低质跳过"，不得落"零知识点的成功作业"。当 `accepted_duration_ratio < 0.3`（`low_quality=true` 但仍有可用分段）时，KB 抽取**继续执行**但在 `extraction_jobs` 关联字段上落一个可观测的 warning 标记，供运营二次审；不阻断流程。
- **FR-010**: 当一条视频尚未跑过清洗就被提交 KB 抽取，系统必须以业务错误立即拒绝（错误码 `CURATION_REQUIRED`），禁止悄悄退化到读全量分段的旧行为。
- **FR-011**: 系统必须提供逐分段的人工覆盖能力：运营可对单个视频的某分段把 `effective_decision` 改为 `accepted` 或 `rejected`，必须留痕 `auto_decision / override_decision / override_user / override_reason / overridden_at`；下游一律以 `effective_decision = override_decision ?? auto_decision` 消费。覆盖**不自动触发**对该视频已存在 KB 抽取作业的重抽；如需重抽，运营按既有 `POST /extraction-jobs/{id}/rerun` 接口显式发起；任务监控必须对"存在覆盖且未重抽的视频"暴露明确的 `kb_stale_after_override` 提示标记，避免静默口径不一致。
- **FR-012**: 视频级摘要必须在任何分段覆盖发生后**自动重算**（`accepted_duration_ratio` / `low_quality` 等），下游消费的是最新口径。
- **FR-013**: 系统必须把清洗任务纳入既有"任务监控 / 失败重试 / 孤儿回收 / 中间产物清理"周期作业的覆盖范围；不新增 Beat 条目、不新增 Celery 队列。
- **FR-014**: 系统必须支持按 `coach_name` 与 `tech_category` 聚合视频级有效率（P3）；支持按 `curation_rubric_version` 维度做版本对比；接口分页遵循章程统一约定。
- **FR-015**: 所有清洗相关接口必须遵循章程 v2.0.0 统一响应信封 + 集中错误码登记（`src/api/errors.py`），禁止业务层裸字符串错误；清洗专属错误码以约定前缀（如 `CURATION_*` / `RUBRIC_*`）登记。
- **FR-016**: 清洗结果与人工覆盖记录必须可追溯到**三要素锚点**：`cos_object_key`（原视频）、`preprocessing_job_id`（预处理产物）、`curation_rubric_version`（判据版本）；任意一条结果可在一次接口跳转内复盘判定来源。
- **FR-017**: 清洗任务必须遵循 F-013 通道容量与并发门控；当通道满时按既有策略入队等待或拒绝，禁止旁路新增队列。
- **FR-018**: 同一视频重复提交清洗时默认幂等短路；带 `force=true` 时新建一份新结果，旧结果保留并保留与新结果的关联（用于规范版本对比 P3）；新旧结果不得相互覆盖。
- **FR-019**: 与 Feature-020 运动员侧表 / 路径严格隔离：清洗只读 / 只写教练侧素材清单与教练侧预处理产物，禁止扫描 / 写入运动员侧。
- **FR-020**: 清洗任务的执行时间上限须由全局可配置项保护（沿用 KB 提取 / 预处理的"作业级 + 步骤级"双层超时模式），超时后任务以 `failed:CURATION_TIMEOUT` 结束并被孤儿回收作业兜底。

### 关键实体 *(涉及数据)*

- **VideoCurationJob（视频清洗作业）**：以"视频"为单位的清洗任务记录；至少含 `id` / `cos_object_key` / `preprocessing_job_id` / `curation_rubric_version` / 状态机字段（`pending/running/success/failed`）/ `submitted_at` / `started_at` / `completed_at` / `error_code` / `error_message` / 视频级摘要字段（`accepted_duration_ratio` / `low_quality` 等）。
- **VideoCurationSegmentResult（分段清洗结果）**：与预处理分段一一对应；至少含 `job_id`（FK）/ `segment_index` / `auto_decision` / `validity_score` / `rejection_reason` / `effective_decision`（最终决策，由覆盖派生）。覆盖字段同行扩展即可，避免过度建模。
- **CurationRubric（清洗规范，版本化）**：版本化的"判据快照"概念实体；至少含 `version` / `published_at` / `published_by` / `rules`（结构化规则集合）/ `schema_validated` 布尔。版本一旦发布应**只读**；任何调整必须发新版本。
- **AnalysisTask（受控扩展）**：沿用现有 `analysis_tasks` 表，新增 1 个 `task_type` 枚举值（如 `video_curation`）以让任务监控按既有 `_phase_step_hook` 派生 `(phase, step)`；`business_phase=TRAINING`。
- **CoachVideoClassification（受控扩展，可选）**：可考虑增加 `last_curation_job_id` / `low_quality` 摘要字段以便扫库筛选；具体字段在 plan 阶段决定，但不得改动现有列含义。
- **ExtractionJob / PipelineSteps（沿用）**：消费侧只读清洗结果集合，不修改其表结构；仅在错误码上扩展两个值（`CURATION_REQUIRED` / `LOW_QUALITY_SKIP`）。

### 业务阶段映射 *(必填 - 原则 X / 章程 v1.5.0)*

- **所属阶段**: `TRAINING`（清洗作用对象是教练侧素材清单 + 教练侧预处理产物，最终目的是为 KB 抽取提供高信噪比输入；不产生 KB / standards、不进诊断链路；不归 STANDARDIZATION 也不归 INFERENCE）
- **所属步骤**: 在 `business-workflow.md § 3` 阶段一总览中**新增 1 个步骤** `curate_segments`，位置在 `preprocess_video`（步骤 2）与 `classify_video`（步骤 3）之后、`extract_kb`（步骤 4）之前；MUST 先在 business-workflow.md 扩展 § 3.1 八步骤总览（含队列、并发、产物、状态表）后再进入 `/speckit.plan`。
- **DoD 引用**: business-workflow.md § 2 阶段分界线 **训练** 行——`extraction_jobs.status=success` 且 `coach_video_classifications.kb_extracted=true`；本 feature 的 DoD 收敛到既有训练 DoD 上（清洗成功不是终点，只有 KB 抽取消费清洗结果后成功才闭合）。
- **可观测锚点**:
  - § 7.1 任务级：新增 `video_curation` `task_type` 的 `analysis_tasks` 记录，`business_phase=TRAINING`
  - § 7.2 步骤级：清洗内部的若干判据（关键词 / 主题相关性 / 教练主导）以子步骤或 `output_summary` JSON 字段形式暴露最小可观测；清洗作业必须暴露分段判定计数、所用规范版本、视频级摘要快照
  - § 7.4 错误码前缀：新增 `CURATION_*`（清洗本身错误）+ `RUBRIC_*`（规范加载 / 校验错误）+ `LOW_QUALITY_SKIP` / `CURATION_REQUIRED`（消费侧短路），全部集中登记到 `src/api/errors.py`，CI 由 `scripts/audit/workflow_drift.py` 守护
- **章程级约束影响**:
  - 队列拓扑：**不新增 Celery 队列**；清洗复用现有 `default` 队列（与 `scan_cos_videos` / `housekeeping` / `cleanup_*` / `sweep_orphan_jobs` 同列，concurrency=1）；business-workflow.md § 3.1 队列列需补 `curate_segments` 行，标注 "队列=`default`、并发=1"
  - 状态机枚举：`analysis_tasks.task_type` 新增 `video_curation`；不改既有枚举值
  - 错误码前缀：新增 `CURATION_*` / `RUBRIC_*` / `LOW_QUALITY_SKIP` / `CURATION_REQUIRED`，全部集中登记
  - 评分公式：本 feature 的 `validity_score` 计算法需以"清洗规范"形式落档；与诊断侧 § 5.5 评分公式独立，不交叉
  - 单 active / 冲突门控：无变化
  - 跨阶段副作用：消费侧 `extract_kb` 增加"必须先跑清洗"前置门，等价于在 § 3.1 步骤 4 前置条件列补一项"`video_curation` 已成功"
- **回滚剧本**:
  - 中风险：清洗规则误伤导致 KB 抽取读到的有效片段过少 — 回滚以"对该批次视频整体禁用清洗门"作为应急（运营开关），下游临时回退到读全量分段；MUST 在 plan 阶段提出明确的"禁用门"设计与开关位置（不得是隐式 Feature flag），并在 business-workflow.md § 10 登记新剧本
  - 低风险：清洗结果或人工覆盖数据本身错误 — 直接删除 `VideoCurationJob` 当次记录后重跑清洗即可，**不影响 KB / standards / teaching_tips**

---

## 成功标准 *(必填)*

### 可衡量的结果

- **SC-001**: 在一组**人工标注的混杂教练视频样本（≥ 30 条，覆盖闲聊 / 赛事 / 采访 / 真讲解多种内容形态）**上，自动清洗对"无效分段"的召回率 ≥ 0.85，对"有效分段"的精确率 ≥ 0.85（同一份样本与人工标注互对照）。
- **SC-002**: 清洗后送入 KB 抽取的 LLM 输入 token 量相对清洗前下降 ≥ 30%（在样本视频集上口径一致测量），证明"无效片段不再消耗 LLM"。
- **SC-003**: 清洗后下游 KB 抽取每条视频产出的"高一致性技术要点"（同一 `tech_category` 多条视频间的关键术语重叠率）相对清洗前提升 ≥ 20%（在样本视频集上口径一致测量），证明"知识离散度被收敛"。
- **SC-004**: 任意一次历史清洗结果可在 ≤ 1 次接口跳转内复盘出"哪一版规范、哪条 COS 视频、哪个预处理 job、每个分段的判定与理由"，**100% 可追溯**。
- **SC-005**: 当规范文件被替换为破损版本时，系统 100% 拒绝以新规范启动清洗任务（不接受半坏规范），且既有运行中任务 100% 不被中途切版影响。
- **SC-006**: 在 10 条混合素材的批量清洗场景下，整批清洗任务的总耗时 p95 ≤ `(10 / 并发数) × 单条 p95 × 1.2`（含调度开销系数 1.2，沿用 Feature-020 SC-003 计法），相对串行执行提速 ≥ 40%。
- **SC-007**: 业务监控按"训练阶段 / `task_type=video_curation`"筛选任务列表时返回 100% 只含清洗任务，**零误混**预处理 / 分类 / KB 抽取 / 诊断任务。
- **SC-008**: 章程 TRAINING DoD 在引入清洗后 100% 仍可达成：所有走过清洗的视频，最终 `extraction_jobs.status=success` 比例不低于清洗前的同类基线（不得因清洗反向拉低 KB 完成率）。

---

## 假设

- 视频已经走过 F-016 视频预处理流水线，本 feature 的清洗以"分段"为最小粒度，不再做编码 / 帧率 / 时长标准化。
- `tech_category` 体系沿用现有 21 类（`TECH_CATEGORIES`），不引入新分类字典；如未来引入 Feature-021 提案的 V2 层级分类，规范文件将以"按 V2 路径键入"的方式扩展，不在本 feature 内一并升级。
- 规范文件初版以"关键词 + 阈值 + 规则开关"的可读文本结构（YAML / JSON 之一，由 plan 阶段决定），存放在代码仓 `src/config/curation_rubric/` 目录下，与代码同源；运营任何调整都通过 PR + CI schema 校验合并发版，**不提供线上编辑接口**；DB 仅记录所用版本号，规范快照按版本号回查 git。
- 清洗算法初版基于"规则优先 + LLM 兜底"两层骨架：转录文本（已由 F-002 提供 Whisper）+ 教练 / 主体识别启发式 + 关键词命中 + 时长规则构成第 1 层结构化规则；第 2 层 LLM 仅作用于规则得分落入模糊区间 `(0.3, 0.7)` 的分段；LLM 不可用时模糊区间一律落 `uncertain`，不阻断作业。
- 复用 LLM 调用一律走 `src/services/llm_client.py`（Venus 优先 → OpenAI fallback）；本 feature 不新增 LLM 客户端封装。
- 与 Feature-020 运动员侧实体物理隔离；本 feature 一律不读 / 不写 `athlete_video_classifications` / `athletes` / 运动员根路径。
- 清洗失败 / 超时不阻断后续运维流程；用现有 `sweep_orphan_jobs` Beat 任务回收，不新增 Beat 条目。
- 任务监控接口改造遵循 Feature-018 `business_phase` + `task_type` 双维度筛选既有约定。
- 本 feature 不提供"删除清洗结果"的对外接口（同 Feature-020 风格的"无遗忘权 API"），如合规上确需，运维直接在 DB 层手工执行；未来如需正式能力另开独立 Feature 承载。
- 本 feature 不提供清洗规范的 Web 编辑 UI，也不提供"线上发布规范"的 API 接口；规范以"提交到代码仓 → CI 校验 schema → 合并发版"的方式滚动，避免"线上能编辑但与代码不同源"。
