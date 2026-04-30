# 功能规范: 运动员推理流水线 · COS 扫描 → 预处理 → 姿态/动作提取 → 标准对比 → 改进建议

**功能分支**: `020-athlete-inference-pipeline`
**创建时间**: 2026-04-30
**状态**: 草稿
**输入**: 用户描述: "运动员诊断流程中，也增加运动员视频路径（和全量教学视频路径类似），前面的COS扫描、视频预处理及姿态动作提取，复用训练的流程，多增加一个和标准对比生成改进建议的过程。同时对运动员的诊断任务，也增加到任务监控和训练教学任务区分开。"

## Clarifications

### Session 2026-04-30

- Q: 运动员目录 → 姓名映射的数据源如何维护？ → A: 独立静态 JSON 映射 `config/athlete_directory_map.json` + 扫描器自动 upsert 独立 `athletes` 表（与教练侧 `coach_directory_map.json` + `coaches` 范式对称）。
- Q: `AthleteVideoClassification` 的唯一性键与扫描幂等语义如何设计？ → A: `cos_object_key` 单列 UNIQUE；扫描器按 `cos_object_key` upsert；与 `coach_video_classifications` 完全对称。
- Q: 同一运动员素材重复提交诊断的语义？ → A: 每次提交都新建诊断任务与报告；通过 `cos_object_key` 聚合查看历史版本；与 F-013 异步诊断通道现行行为一致。
- Q: 目标技术标准缺失时诊断任务的行为？ → A: 立即置 `failed`，错误码 `STANDARD_NOT_AVAILABLE`（新增于 `src/api/errors.py`），`details` 带 `tech_category`；前端可引导运营到 KB 管理页发布对应 `tech_category` 的 published 标准；禁止降级/挂起/用 draft 兜底。
- Q: 运动员视频/诊断历史的"遗忘权"删除能力范围？ → A: 本 feature 不提供遗忘权接口，由运维在 COS + DB 层手工执行；并在 Assumptions 中显式声明范围边界，合规需求到来时另开独立 Feature 承载。

## 用户场景与测试 *(必填)*

### 用户故事 1 — 运动员视频素材归集与自动分类 (优先级: P1)

运营管理员把一批业余运动员的练习视频按"选手/主题"目录结构上传到对象存储的**运动员根路径**（与专家教学视频根路径物理隔离），然后一键触发扫描；系统自动枚举所有视频、识别每条视频对应的技术类别（如"正手攻球""反手拨球"），并落库登记，形成运动员视频素材清单，方便后续批量诊断。

**优先级原因**：没有"素材清单"，下游诊断无法批量驱动，只能一条一条手工塞 `video_storage_uri`，与专家侧 F-008 的工作模式严重不对称；补齐它才能让运动员视频批量化、可追溯，是整个流水线的入口。

**独立测试**：向运动员根路径上传若干 `.mp4`，调用扫描接口，等扫描完成后查询运动员视频清单接口，校验每条记录都带有 `athlete_name / tech_category / cos_object_key / classification_source / classification_confidence`，且运动员清单**不污染**教练侧 `coach_video_classifications` 表。

**验收场景**：

1. **给定** 运动员根路径下有 N 条新 `.mp4` 文件且尚未扫描；**当** 触发"运动员素材全量扫描"接口；**那么** 任务进入 `default` 队列异步执行，完成后运动员视频清单表里至少有 N 条记录，每条都有非空 `tech_category`（无匹配时落 `unclassified`）。
2. **给定** 运动员根路径下已有部分视频已扫描过；**当** 再次触发扫描；**那么** 只增量处理 `cos_object_key` 未入库的视频，已存在记录不重复插入、不回写。
3. **给定** 运动员目录名匹配不到任何映射；**当** 扫描到该目录下的视频；**那么** `athlete_name` 回退为目录名（`name_source=fallback`），扫描不中断。
4. **给定** 扫描路径下存在零字节 COS 目录占位符或非 `.mp4` 文件；**当** 扫描执行；**那么** 这些对象自动跳过，不产生错误记录。

---

### 用户故事 2 — 运动员视频标准化预处理 (优先级: P1)

对每一条已入库的运动员视频，系统能像教练视频那样进入统一的预处理管道（下载 → 编码/分辨率/时长探测 → 转码标准化 → 分段 → 分段并发上传 COS），产出"标准化分段"与探测元信息（`fps / duration / resolution / has_audio` 等）。诊断阶段消费的是预处理产物，而不是原始 COS 对象。

**优先级原因**：原视频编码/分辨率/帧率杂乱，直接喂给姿态估计会让维度测量误差不可控、`tech_extractor` 稳定性差；复用 F-016 的预处理通道同时能避免运动员/专家两条流水线的算法实现分叉。

**独立测试**：手工指定一条运动员视频素材 ID（或 `cos_object_key`）提交预处理任务，任务进 `preprocessing` 队列执行完成后，校验：(a) 产出了 `preprocessed/{athlete_cos_key}/jobs/{job_id}/seg_NNNN.mp4` 分段对象且可下载；(b) 运动员素材记录上带有对应 `preprocessing_job_id` 与 `preprocessed=true` 标记；(c) 原始 COS 原视频保留不动。

**验收场景**：

1. **给定** 运动员素材 A 尚未预处理；**当** 提交预处理任务；**那么** 任务进入 `preprocessing` 队列并在成功后把 A 标记为 `preprocessed=true`，关联 `preprocessing_job_id`。
2. **给定** 运动员素材 A 已 `preprocessed=true`；**当** 再次提交预处理且未开启 `force`；**那么** 请求被幂等短路（沿用既有 job）或直接拒绝重复提交，不产生新 job。
3. **给定** 一次批量提交 M 条运动员素材；**当** 通道未满；**那么** 所有条目并行进入 `preprocessing` 队列、各自隔离；单条失败不拖垮同批其他条目。

---

### 用户故事 3 — 运动员诊断任务端到端自动编排与姿态/动作提取 (优先级: P1)

运营管理员针对一条（或一批）已预处理好的运动员素材，直接提交"运动员诊断"；系统内部自动完成：读取预处理分段 → 姿态估计 → 动作维度测量 → 加载**对应 `tech_category` 的 active 技术标准** → 计算维度得分与偏差 → LLM 生成改进建议 → 综合评分 → 落库诊断报告。整个过程以"运动员素材 ID"为输入即可启动，无需再手工拼装 `video_storage_uri`。

**优先级原因**：把"先扫码、再预处理、再诊断"这条原本要分三次手工调用的链路压成一次自然的业务动作，是这次 feature 的核心价值；也直接闭合章程三阶段中的 INFERENCE DoD。

**独立测试**：选一条既通过 US1 扫描、又通过 US2 预处理的素材 X，调用"运动员诊断批量提交"接口把 X 丢进去；等任务完成，查询诊断报告接口，校验：(a) `diagnosis_reports` 存在一行关联到 X；(b) `overall_score / dimensions[] / improvement_advice` 三项齐全；(c) 报告引用的 `standard_id / standard_version` 与当时该 `tech_category` 的 active 标准一致。

**验收场景**：

1. **给定** 运动员素材 X 的 `tech_category` 在建标阶段已有 active `tech_standards`；**当** 以素材 ID 提交诊断；**那么** 任务成功、诊断报告生成、综合得分 0–100、每个维度有量测值 + 理想区间 + 偏差等级 + 文字改进建议。
2. **给定** 运动员素材 X 的 `tech_category` 没有任何 active 标准；**当** 以素材 ID 提交诊断；**那么** 任务在 `diagnosis` 队列以业务错误失败并返回"标准不存在"的明确错误码，不产生空诊断报告。
3. **给定** 运动员素材 X 尚未完成预处理；**当** 以素材 ID 提交诊断；**那么** 系统拒绝提交并返回明确的"预处理未完成"错误，避免诊断读到不一致数据（禁止静默回退到原视频）。
4. **给定** 姿态估计因视频帧画面过差导致全程无可用关键点；**当** 诊断执行；**那么** 任务失败而不是产出 0 分报告，错误信息注明"姿态提取失败"。

---

### 用户故事 4 — 训练侧与诊断侧任务在监控面清晰区分 (优先级: P2)

运营在任务监控列表里可以一眼分辨"教学/训练侧"任务（COS 扫描 / 预处理 / 分类 / KB 抽取）与"诊断侧"任务（运动员素材扫描 / 运动员预处理 / 运动员诊断），可以按**业务阶段**（训练 vs 推理）或**任务类型**筛选，不再混杂在同一列表里。

**优先级原因**：当前监控把所有 `task_type` 塞在一起，诊断任务容易被大批 KB 抽取吞没；区分之后支持快速盯 SLA、快速查失败原因。

**独立测试**：并发跑 1 条运动员诊断 + 若干条 KB 抽取；在任务列表查询接口里分别用 `business_phase=TRAINING` 与 `business_phase=INFERENCE`（或等效筛选参数）拉两次，两次结果互斥、总数一致、可分页。

**验收场景**：

1. **给定** 系统里同时存在训练类与诊断类任务；**当** 以"诊断侧"维度筛选任务列表；**那么** 返回结果只包含运动员素材扫描 / 运动员预处理 / 运动员诊断三类任务，不出现 KB 抽取 / 教练视频分类。
2. **给定** 任一条诊断任务；**当** 打开任务详情；**那么** 能直接看到它关联的运动员素材 ID、`tech_category`、所用标准版本（如已完成）。

---

### 用户故事 5 — 运动员素材/报告的可追溯与清单化查询 (优先级: P3)

对已完成的诊断，运营可以按"运动员"或"技术类别"批量查看历史诊断结果与报告清单，并能从报告反向追溯到具体的运动员素材 COS 对象 + 预处理 job + 使用的 active 标准版本。

**优先级原因**：教练侧已经能"从一条诊断结果追溯到源视频片段"；运动员侧同构能力能让教练复盘学员进步曲线，但不是 MVP 必需，可放到 P3。

**独立测试**：对同一运动员连续做 2 次诊断（不同日期），调用"按运动员查报告"接口，确认能按时间倒序列出 2 条记录，并各自能点进去看到指向 `cos_object_key / preprocessing_job_id / standard_version` 的完整反查链路。

**验收场景**：

1. **给定** 运动员 A 已完成 k 次诊断；**当** 调用按运动员筛选诊断报告清单；**那么** 返回 k 条记录，含时间、技术类别、综合得分、标准版本。
2. **给定** 诊断报告 R；**当** 打开报告详情；**那么** 能看到 `cos_object_key` / `preprocessing_job_id` / `standard_id + version` 三个追溯锚点。

---

### 边界情况

- 运动员根路径为空 / 不可读 / 凭证错误：扫描任务以业务错误失败而不是空成功；错误原因可观测。
- 同名运动员多目录：沿用专家侧的"第 1 个保持原名，后续加 `_2 / _3` 后缀"规则，避免主键冲突。
- 运动员素材文件名无任何技术类别关键词命中：分类落 `unclassified`，不阻断流水线；允许运营后续人工改派或重跑。
- 预处理产物过期/被清理后再次发起诊断：服务端**以 409 `ATHLETE_VIDEO_NOT_PREPROCESSED` 显式拒绝**并提示调用方重跑预处理，禁止直接读已过期 artifact，也禁止后端静默自动重跑（避免隐式副作用）。
- 视频无音频轨：不影响诊断（诊断路径不依赖音频）。
- 提交的诊断 batch 中部分素材未预处理：整批不得原子失败；已预处理的正常执行、未预处理的单条失败并返回具体错误码。
- 任务监控分页 / 排序参数越界：沿用章程 v1.4.0 — `page_size` 越界直接 422，不静默截断。
- `task_type` 枚举扩展导致监控接口旧客户端未识别：以"新增向后兼容"方式扩展，已发布枚举值禁止改名。

## 需求 *(必填)*

### 功能需求

- **FR-001**: 系统必须支持配置一条**独立的运动员视频根路径**，与专家教学视频根路径（`COS_VIDEO_ALL_COCAH`）物理隔离；扫描逻辑只遍历该运动员根路径。
- **FR-002**: 系统必须支持**运动员素材全量扫描**与**增量扫描**，枚举 `.mp4` 文件、跳过零字节占位符，异步执行并返回任务 ID 可查进度。
- **FR-003**: 系统必须基于运动员目录名通过静态映射解析 `athlete_name`，无匹配时回退使用目录名并标记 `name_source=fallback`；同名冲突时按插入顺序追加 `_2 / _3` 后缀。
- **FR-004**: 系统必须为每条扫描到的运动员视频分类到 `TECH_CATEGORIES` 枚举中的一项（规则命中优先、LLM 兜底；置信度 < 0.5 落 `unclassified`），分类结果与教练侧复用同一份 `tech_category` 字典。
- **FR-005**: 系统必须把运动员素材清单写入**独立的数据实体**，禁止与 `coach_video_classifications` 合表；清单至少包含：运动员视频 ID、`cos_object_key`、`athlete_name`、`tech_category`、`classification_source` (`rule / llm / fallback`)、`classification_confidence`、`preprocessed` 布尔、关联 `preprocessing_job_id`。
- **FR-006**: 系统必须允许对运动员素材**复用 F-016 视频预处理管道**（`preprocessing` 队列 / `video_preprocessing_jobs` 状态机），产物按 `jobs/{job_id}/seg_NNNN.mp4` 隔离并与原视频路径分桶存放。
- **FR-007**: 系统必须支持以"运动员视频 ID"为主输入提交诊断任务（单条 + 批量），内部自动解析出应读取的预处理产物；禁止绕过预处理直接读原视频。
- **FR-008**: 系统必须在诊断执行中加载**与该素材 `tech_category` 对应的 active `tech_standards`**，无 active 标准时任务失败并返回明确错误码；诊断只读 active、不得读 draft。
- **FR-009**: 系统必须在诊断报告中落库：综合得分、每维度量测值 / 理想区间 / 偏差等级 / 方向 / LLM 改进建议；同时落三要素反查锚点：`cos_object_key` / `preprocessing_job_id` / `standard_id + version`。
- **FR-010**: 系统必须为运动员诊断沿用既有 `diagnosis` 队列（并发上限 2、容量上限 20），不得新增 Celery 队列；批量提交遵循 F-013 通道容量门控。
- **FR-011**: 任务监控列表接口必须支持按"业务阶段"（训练 / 推理）与"任务类型"两种维度筛选，默认不再将诊断任务与训练类任务混排；诊断任务 `business_phase` 必须为 `INFERENCE`。
- **FR-012**: 任务监控详情必须对运动员诊断任务直接暴露：运动员视频 ID、`tech_category`、使用的标准版本（完成后填入），可从任务一跳查到诊断报告。
- **FR-013**: 所有运动员侧接口必须遵循章程 v2.0.0 统一响应信封 + 统一错误码映射，错误码不使用裸字符串；已发布错误码不得改名。
- **FR-014**: 系统必须支持按"运动员"与"技术类别"筛选诊断报告清单并分页（P3 场景）；分页参数越界直接 422，不得静默截断。
- **FR-015**: 系统必须把运动员素材扫描 / 预处理 / 诊断三类任务纳入现有"孤儿任务回收"与"中间产物清理"周期作业的覆盖范围，不新建独立清理任务。

### 关键实体 *(涉及数据)*

- **AthleteVideoClassification（运动员素材清单）**：运动员侧与 `coach_video_classifications` 对称的新表；关键字段包括 `cos_object_key / athlete_id / athlete_name / tech_category / classification_source / classification_confidence / preprocessed / preprocessing_job_id / last_diagnosis_report_id / created_at / updated_at`。禁止与教练侧合表。
- **Athlete（运动员）**：与 `coaches` 对称，记录独立运动员实体；由扫描器按目录映射自动同步；不与 `coaches` 合表（两者权限边界、来源不同）。
- **PreprocessingJob（复用）**：直接复用 F-016 `video_preprocessing_jobs` + `video_preprocessing_segments`；不新增表。
- **DiagnosisReport（扩展）**：复用 F-011 / F-013 现有 `diagnosis_reports` + `diagnosis_dimension_results`，但需额外持久化三要素反查锚点（`cos_object_key / preprocessing_job_id / standard_version`）；如现表已含同义列则无需迁移，否则需补列。
- **AnalysisTask（`task_type` 受控扩展）**：沿用现有 `analysis_tasks` 表，**新增 2 个 `task_type` 枚举值** `athlete_video_classification` / `athlete_video_preprocessing`，使 `_phase_step_hook` 派生矩阵保持"`task_type` → `(phase, step)`"的单一事实来源（决策依据见 `research.md § R3`）；`athlete_diagnosis` 复用既有枚举值。

### 业务阶段映射 *(必填 - 原则 X / 章程)*

- **所属阶段**: `INFERENCE`（本 feature 全部子流程最终都服务于诊断阶段；前置的运动员素材扫描与运动员预处理虽形似 TRAINING 的工作方式，但输入源与用途都归属 INFERENCE，不产生 KB / standards，不能归到 TRAINING）
- **所属步骤**: business-workflow.md § 5 的 `diagnose_athlete`（步骤 8）；同时新增两个 **INFERENCE 阶段前置编排步骤**：`scan_athlete_videos` 与 `preprocess_athlete_video`（**两者 MUST 先在 business-workflow.md 扩展 § 5 后再进入 /speckit.plan**）
- **DoD 引用**: business-workflow.md § 2 阶段分界线中 **诊断** 行——`diagnosis_reports` 行存在且 `overall_score != null`；作为本 feature 完成判定条件
- **可观测锚点**:
  - § 7.1 任务级：新增 `scan_athlete_videos / preprocess_athlete_video / diagnose_athlete` 三个 `analysis_tasks` 记录，`business_phase=INFERENCE`
  - § 7.2 步骤级：`preprocess_athlete_video` 复用 `video_preprocessing_jobs / _segments`；`diagnose_athlete` 沿用现有日志锚点（`diagnosis_complete` 结构化日志）
  - § 7.3 诊断级：`diagnosis_reports + diagnosis_dimension_results` 扩充 3 个追溯字段（`cos_object_key / preprocessing_job_id / standard_version`），保证报告可反查
- **章程级约束影响**:
  - 队列拓扑：**不新增 Celery 队列**（运动员素材扫描复用 `default`，预处理复用 `preprocessing`，诊断复用 `diagnosis`）；business-workflow.md § 3.1 / § 5.1 队列列需补注"运动员侧复用同队列"说明
  - 状态机枚举：不变；`tech_knowledge_bases` / `tech_standards` 状态机不受影响
  - 错误码前缀：需新增 `ATHLETE_*` 前缀错误码 + `STANDARD_NOT_AVAILABLE`（详见 `research.md § R9` 的 5 条登记清单），集中登记到 `src/api/errors.py`
  - 评分公式：沿用，无变化
  - 单 active / 冲突门控：无变化；本 feature 纯只读消费 active 标准
- **回滚剧本**:
  - 低风险：运动员素材扫描 / 预处理 — 出问题直接停 Celery 任务、删当次扫描写入的素材清单行、无跨阶段副作用；**无需章程级回滚剧本**
  - 低风险：运动员诊断 — 诊断报告只写不改 KB / standards，失败删报告行即可；**无需章程级回滚剧本**
  - 中风险：若新增 `AthleteVideoClassification` 表后有数据回写 `Athlete` 表 — 回滚时需同步清理；需在 plan.md 中给出 `DROP TABLE athlete_video_classifications; DELETE FROM athletes WHERE created_via='athlete_scan'` 的手册化步骤

## 成功标准 *(必填)*

### 可衡量的结果

- **SC-001**: 运营只需一次"扫描 → 批量诊断"两步调用，就能从运动员根路径上传的原始视频产出带综合得分的诊断报告，**全链路不需要手工填写任何 `video_storage_uri`**。
- **SC-002**: 运动员根路径下 95% 以上的 `.mp4` 在一次全量扫描后被成功分类（含 `unclassified` 兜底），失败率 ≤ 5%。
- **SC-003**: 对 10 条合法素材的批量诊断，全部任务能并发进入 `diagnosis` 队列；总耗时 p95 ≤ `(10 / 并发数) × 单条 p95 × 1.2`（含队列调度开销系数 1.2），按当前 `diagnosis` 并发=2 即 ≤ 6 × 单条 p95，比串行 (10 × 单条 p95) 提速 ≥ 40%。
- **SC-004**: 任务监控在按"业务阶段=推理"过滤时，返回列表 100% 只包含运动员侧三类任务，**零误混训练类任务**。
- **SC-005**: 任一条诊断报告都能在 ≤ 1 次接口跳转内追溯到它依赖的原始 `cos_object_key` + 预处理 job + 标准版本。
- **SC-006**: 运动员素材清单与教练素材清单**零交叉污染**——教练侧接口读不到运动员行，反之亦然（架构/数据边界测试）。
- **SC-007**: 章程 INFERENCE DoD 对本 feature 100% 满足：所有成功诊断任务对应 `diagnosis_reports.overall_score IS NOT NULL`。
- **SC-008**: 诊断精准度**复用 F-011 / F-013 已立基准**（维度关键点可用率、综合评分一致性），本 feature 不新立算法基准、亦不得引入相对 F-013 基线的回退；发布前以集成测试样本集对照基线无统计显著退化。

## 假设

- 运动员视频通过 COS 上传并组织到"根路径 / 运动员目录 / 视频.mp4"的两级结构；与教练侧同构，便于复用 F-008 扫描骨架（但实现需分叉到独立类/表，不要侵入 `CosClassificationScanner`）。
- 运动员视频的技术类别枚举与教练侧完全共享（`TECH_CATEGORIES` 21 类），不引入第二套分类字典。
- 视频预处理复用 F-016 流程；运动员侧**不新增专用预处理策略**，帧率/分辨率标准化参数与教练侧一致。
- 诊断阶段使用 active KB 派生的 `tech_standards`，不支持指定 `draft` 标准；版本粒度与 F-019 的"每 `tech_category` 独立版本链"一致。
- 任务监控接口改造遵循 F-018 `business_phase` 字段的既有约定；如需筛选诊断任务，直接用 `business_phase=INFERENCE` + `task_type=athlete_diagnosis` 组合即可，不引入新的筛选参数。
- 清理/回收作业沿用现有 `cleanup_intermediate_artifacts` / `sweep_orphan_jobs` Beat 任务；不新增 Beat 条目。
- 错误码扩展遵循章程 v2.0.0 原则——集中登记到 `src/api/errors.py`，禁止业务层裸字符串。
- 本 feature 不涉及对外（第三方 / 公网用户）的身份鉴权与配额管理，继续沿用现有内部运营运行时环境的访问约束。
- **范围外 · 运动员数据遗忘权**：本 feature **不提供**运动员素材 / 诊断报告的删除接口（无软删 / 硬删 / 级联删等任何接口语义）；如合规上确有删除诉求，运维直接在 COS + 数据库层执行脚本清理。未来如需官方能力，另开独立 Feature（预计 Feature-021 类）承载，不绑定本 feature 的工期。

## 迁移说明（T064）

本 feature **无接口下线 / 字段废弃**，迁移只需 2 步：

1. **应用数据库迁移**：
   ```bash
   alembic upgrade head   # 到 0018_athlete_inference_pipeline
   ```
   新增 2 表 1 列（`athlete_video_classifications` / `athletes`（若未有）/ `diagnosis_reports.cos_object_key+preprocessing_job_id+source`）；**无破坏性变更**，教练侧表零触碰。

2. **补充 `.env`**：
   ```bash
   COS_VIDEO_ALL_ATHLETE=charhuang/tt_video/athletes/
   ```
   （与 `COS_VIDEO_ALL_COCAH` 并列，无覆盖；未配置时运动员扫描接口返回 `ATHLETE_ROOT_UNREADABLE` 400 错误。）

3. **目录映射**（可选）`config/athlete_directory_map.json`：若不提供，默认走 `name_source=fallback` 以目录名作为运动员名，功能等价。

> **回滚**：直接 `alembic downgrade -1` 即回到 0017；运动员侧表被 DROP、`diagnosis_reports` 新增列回退，**不影响教练侧 / KB / standards / teaching_tips 任何数据**。

