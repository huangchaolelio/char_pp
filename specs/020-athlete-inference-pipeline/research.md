# 阶段 0 研究: 运动员推理流水线

**Feature**: 020-athlete-inference-pipeline
**日期**: 2026-04-30
**目的**: 解决本 feature 在 `plan.md` 技术背景中产生的所有 NEEDS CLARIFICATION、依赖选型与集成模式。

> `spec.md` 的 5 条业务澄清（Q1–Q5）已闭环并落到 `Clarifications` 段落；本文件只记录**技术层研究决策**。

---

## R1 · 运动员素材清单表与教练侧表的隔离实现

**问题**：`coach_video_classifications` 已经承载 21 类分类 + `kb_extracted` 字段；运动员侧是否直接加 `source_type` 区分，还是建新表？

**Decision**：建新表 `athlete_video_classifications`（与 `coaches` 同理建 `athletes`）。

**Rationale**：
- 章程附加约束明文"两张视频分类表并存（禁止合并）"（已在项目规则 4 生效）
- 两表生命周期差异：教练侧以 `kb_extracted` 为终点，运动员侧以 `last_diagnosis_report_id` 为可选回溯点，字段演化节奏不同
- `business_phase` 已承担"两侧任务区分"责任（TRAINING vs INFERENCE），数据表也保持镜像隔离，可避免一个迁移动两侧 schema 的连坐风险
- Q2 澄清决议：`cos_object_key` 单列 UNIQUE，`upsert` 语义与教练侧对称，即可满足幂等

**Alternatives considered**：
- 合表 + `source_type` 鉴别：**拒绝**。违反章程附加约束；索引/FK 分叉复杂度更高
- 物理视图分流：**拒绝**。丢失两侧独立扩列能力

---

## R2 · `athletes` 表是否合并到 `coaches`

**问题**：`coaches` 表已经由扫描器自动 upsert；要不要直接存运动员？

**Decision**：不合表，另建 `athletes`；与 `coaches` **结构对称**（`id / name / bio / created_via / created_at / updated_at`）。

**Rationale**：
- 权限边界不同：教练用于 KB 抽取贡献追溯 & teaching_tips 关联；运动员用于诊断历史归档。两者读写路径、生命周期、潜在合规分级都不同
- Q1 澄清决议已明确"独立 JSON 映射 + 独立 `athletes` 表"
- 未来若需要给运动员加"会员等级 / 训练计划"等字段，不应污染 `coaches`

**Alternatives considered**：
- 合表 + `role` 列：**拒绝**。外键关系分叉会导致查询层大量 `WHERE role=...`，`teaching_tips.coach_id` / `coach_video_classifications.coach_name` 等既有关联会被迫引入鉴别分支

---

## R3 · `TaskType` 枚举扩展 vs. 沿用教练侧值 + 其他字段鉴别

**问题**：运动员素材扫描任务与预处理任务应该用什么 `task_type`？

**Decision**：**新增两个 `TaskType` 枚举值**——`athlete_video_classification` + `athlete_video_preprocessing`；同步扩 `_phase_step_hook` 派生矩阵。

**Rationale**：
- `_phase_step_hook` 现有的"单一事实来源"是 `task_type → (phase, step)` 映射。若沿用既有 `video_classification` / `video_preprocessing`，hook 必须引入第二维鉴别（如 `submitted_via` 或根路径前缀），映射从 4 行升到 8 行，复杂度双倍
- `business_workflow_service._PHASE_STEP_TASK_TYPE_MATRIX` 三元组校验也只接受 `task_type` 粒度的区分，不接受"同 task_type 分叉"
- 任务监控列表已支持 `task_type` + `business_phase` 组合筛选（`tasks.py` line 90/92），新增枚举**零改造路由**，前端按新 enum 值筛选即可
- 章程原则 IX "已发布错误码不得改名，只允许新增" 的精神对枚举同样适用——新增比复用分流更安全

**Alternatives considered**：
- 复用 `video_classification` + `submitted_via='athlete_scan'` 区分：**拒绝**。hook 分支翻倍，矩阵复杂度翻倍
- 复用 + `parent_scan_task_id IS NULL/NOT NULL` 语义重载：**拒绝**。该字段语义已被教练侧占用（`TRAINING.scan_cos_videos` vs `TRAINING.classify_video` 分叉）

---

## R4 · 运动员 COS 根路径与教练侧根路径的配置隔离

**问题**：教练侧用 `COS_VIDEO_ALL_COCAH`；运动员侧走同路径还是独立 env？

**Decision**：新增 `.env` key `COS_VIDEO_ALL_ATHLETE`（独立路径，如 `charhuang/tt_video/athletes/`），`src/config/settings.py` 增加 `cos_video_all_athlete: str` 字段。

**Rationale**：
- `spec.md` FR-001 硬约束"物理隔离"
- SC-006 硬约束"两侧素材清单零交叉污染"；根路径层面隔离是最底层保障
- 保留未来按根路径做合规差异化策略的空间（例如运动员侧更短保留期）

**Alternatives considered**：
- 共用根路径 + 按一级目录前缀白名单：**拒绝**。代码层误操作可能让教练目录被运动员扫描器扫到，SC-006 无法 DB 之外额外加保险

---

## R5 · 诊断入口统一还是分叉

**问题**：现有 `POST /api/v1/tasks/diagnosis` 接受 `video_storage_uri` 作为主输入；运动员侧"以素材 ID 提交"是新开一个路由还是扩同一个路由？

**Decision**：**新开 `POST /api/v1/tasks/athlete-diagnosis`**（单 + 批量两条子路径），旧的 `/tasks/diagnosis` 保留（兼容内部 debug / F-013 异步诊断通道老调用者）。

**Rationale**：
- 章程原则 IX "路由组织：每个路由文件对应且仅对应一个资源"；`tasks.py` 已经很臃肿，新分支独立命名 `athlete-diagnosis` 语义更清晰
- 新入口只接受 `athlete_video_classification_id`，禁止接受 `video_storage_uri`——与 spec FR-007 "禁止绕过预处理直接读原视频"一致；旧入口保留 `video_storage_uri` 字段兼容契约，不接收 `classification_id`
- 批量通过 `items[]` 数组，复用 F-013 通道容量门控与错误聚合语义

**Alternatives considered**：
- 扩 `/tasks/diagnosis` 增加 `athlete_video_classification_id` 字段：**拒绝**。混合语义导致后端需要三路分支（`video_storage_uri` / `classification_id` / 二者都传），Pydantic `model_config = ConfigDict(extra="forbid")` + 互斥校验成本高
- 下线 `/tasks/diagnosis` 合并到新路由：**拒绝**。会破坏现有测试与 F-013 契约，不属于本 feature 范围（章程原则 IV "重构 MUST 局限于当前任务范围"）

---

## R6 · 预处理入口统一还是分叉

**问题**：同上，`POST /api/v1/tasks/preprocessing` 现在以 `cos_object_key` 为输入；运动员侧要不要分叉？

**Decision**：**新开 `POST /api/v1/tasks/athlete-preprocessing`**，与 F-016 现有 `/tasks/preprocessing` 平行。

**Rationale**：
- 现有 `/tasks/preprocessing` 在服务层入口（`preprocessing_service.create_or_reuse`）会做 `coach_video_classifications` 存在性校验（`CosKeyNotClassifiedError`）。运动员侧的门控是 `athlete_video_classifications` 存在性，校验规则不同
- 分叉路由可以把"该 COS key 在哪张分类表里"作为路由层的契约事实，下游服务只需看自己那张表
- 底层 Celery 编排复用 F-016 `preprocess_video` task 与 `run_preprocessing(job_id)` 管道；**另新增 1 个轻量回写 task `mark_athlete_preprocessed`**（路由到 `default` 队列），通过 Celery `chain(preprocess_video.si(...), mark_athlete_preprocessed.s(classification_id))` 串到主预处理成功之后，负责把 `athlete_video_classifications.preprocessed=true` / `preprocessing_job_id` 回写到运动员侧素材表——这一 task 不改动 F-016 既有代码路径，仅作为运动员侧专用的后继钩子，是分叉在 Celery 层的唯一新增物

**Alternatives considered**：
- 扩 `/tasks/preprocessing` 并根据 `cos_object_key` 自动路由到哪张表：**拒绝**。路由层做"查两张表看谁有"违反原则 IX 分层职责（路由只做参数校验与响应组装）

---

## R7 · 诊断反查三要素（`cos_object_key` / `preprocessing_job_id` / `standard_version`）的持久化位置

**问题**：已有 `diagnosis_reports.standard_id + standard_version`；需要额外补两列还是放进 JSON？

**Decision**：给 `diagnosis_reports` 直接加列 `cos_object_key VARCHAR(1024) NULL` + `preprocessing_job_id UUID NULL`。`standard_version` 已存在于现表中，复用。

**Rationale**：
- 列化而非 JSON，让"按素材追报告"与"按预处理 job 追报告"查询都能走索引
- NULLABLE 保持向后兼容：F-011 / F-013 的旧诊断记录没有这两个锚点，留 NULL；本 feature 生成的行必填
- 索引规划：`(cos_object_key, created_at DESC)` 满足 US5 "按运动员查诊断报告按时间倒序"的 P3 场景；`preprocessing_job_id` 单列索引满足"从 preprocessing job 倒查报告"

**Alternatives considered**：
- JSONB `trace_anchors` 列：**拒绝**。丢失索引能力，违反 SC-005 "≤ 1 次接口跳转反查"
- 建新从表 `diagnosis_report_traces`：**拒绝**。对 2–3 个字段新建 1:1 从表过度抽象，违反章程原则 IV YAGNI

---

## R8 · `diagnosis_service.diagnose_athlete_video` 的素材 ID 入口改造

**问题**：现有 `diagnose_athlete_video(session, task_id, video_storage_uri, kb_version)` 只接 uri；如何改造以支持"以素材 ID 提交"？

**Decision**：新增并行入口 `diagnose_athlete_by_classification_id(session, task_id, classification_id, *, force=False)`：
1. 查 `athlete_video_classifications` 得 `cos_object_key / tech_category / preprocessing_job_id / preprocessed`
2. `preprocessed != true` → 立即 `failed` + `ATHLETE_VIDEO_NOT_PREPROCESSED`
3. 从 `video_preprocessing_segments` 取所有成功分段的 COS key（按 `segment_index` 升序）作为诊断输入（而非原视频）
4. 查 `tech_standards` active by `tech_category`；空 → `STANDARD_NOT_AVAILABLE`
5. 其余 pose / extractor / scorer / advisor 完全复用
6. 持久化 `diagnosis_reports` 时同时写 `cos_object_key` + `preprocessing_job_id` + `standard_version`

旧的 `diagnose_athlete_video(..., video_storage_uri)` 保留不动（不破坏 F-013）。

**Rationale**：
- "预处理未完成不得诊断"是 spec FR-007 硬约束
- 从 `segments` 表读分段 COS key 让底层 pose/extractor **完全不关心原视频**，避免"预处理走形式"
- 并行入口而非修改旧入口，符合原则 IV "重构局限于当前任务范围"

**Alternatives considered**：
- 修改旧入口为 polymorphic：**拒绝**。旧入口正被 F-013 task 使用，风险放大

---

## R9 · 新错误码登记

**问题**：spec.md Q4 / 假设 / FR-008/FR-013 共需登记哪些新错误码？

**Decision**：5 个新 `ErrorCode`，全部加到 `src/api/errors.py`、business-workflow.md § 7.4、本 feature `contracts/error-codes.md`：

| ErrorCode | HTTP | 默认消息（ERROR_DEFAULT_MESSAGE） | 触发点 |
|-----------|------|----------------------------------|--------|
| `ATHLETE_ROOT_UNREADABLE` | 502 | 运动员视频根路径不可读或凭证无效 | scanner `_list_all_mp4s` 抛 `CosServiceError` |
| `ATHLETE_DIRECTORY_MAP_MISSING` | 500 | 运动员目录映射配置文件缺失 | `CosAthleteScanner.from_settings()` 找不到 `config/athlete_directory_map.json` |
| `ATHLETE_VIDEO_NOT_PREPROCESSED` | 409 | 运动员视频尚未完成预处理，不能直接诊断 | 提交诊断时 `preprocessed != true` |
| `STANDARD_NOT_AVAILABLE` | 409 | 该技术类别暂无可用的激活版标准 | `DiagnosisService` 查不到 active `tech_standards`（本 feature 重命名旧 `StandardNotFoundError` 走公共信封；老错误消息文本兼容） |
| `ATHLETE_VIDEO_POSE_UNUSABLE` | 422 | 运动员视频姿态提取全程无可用关键点 | `pose_estimator` 全帧返回空骨架 |

**Rationale**：
- 章程原则 IX "错误码集中化"：所有 code 集中登记 `src/api/errors.py`，禁止裸字符串
- 章程原则 IX "只允许新增"：5 个全为新增，不改动任何既有错误码
- HTTP 状态选型：
  - 根路径问题归 502（上游 COS 不可达，可 I/O 重试）
  - 缺配置归 500（部署问题，fail-fast）
  - 业务状态冲突（未预处理 / 无标准）归 409
  - 算法质量不足归 422（输入质量不够，与现有 `VIDEO_QUALITY_REJECTED` 对齐）

**Alternatives considered**：
- 复用现有 `NO_ACTIVE_KB_FOR_CATEGORY`（KB 没激活）代替 `STANDARD_NOT_AVAILABLE`（standards 没构建）：**拒绝**。两个概念不同层级：KB 是草稿上游，standards 是聚合下游；同一个错误码对运维定位不够精准

---

## R10 · 运动员视频 `course_series` 语义

**问题**：教练侧的 `course_series` 是 COS 目录第二段（如"孙浩泓"）；运动员侧要不要这个字段？

**Decision**：**不设 course_series 字段**。运动员侧 `athlete_video_classifications` 直接存 `athlete_name` 即可（从目录名 + `athlete_directory_map.json` 派生），目录名兜底落 `athlete_name` with `name_source='fallback'`。

**Rationale**：
- 教练侧 `course_series` 存在原因是 tech_classifier 规则对某些教练系列名有特殊命中权重；运动员侧无此规则
- 少一列减少无用数据；后续若需要"课程系列"语义，可以从 `cos_object_key.rsplit('/', 2)[0]` 临时 parse

**Alternatives considered**：
- 保留 `course_series` 保持两表字段结构完全对称：**拒绝**。YAGNI

---

## R11 · 诊断批量通道容量沿用

**问题**：批量运动员诊断（spec US3 AC1/AC3）是否需要独立的 `task_channel_configs` 条目？

**Decision**：**不新增**。沿用现有 `athlete_diagnosis` 通道（默认并发=2、容量=20、`task_channel_configs` seed 行），批量请求按现有 F-013 通道门控逻辑原子拒绝（超容直接 503 `CHANNEL_QUEUE_FULL`）。

**Rationale**：
- 队列物理是 `diagnosis`，同一 worker 处理，无法按"教练诊断 / 运动员诊断"再分通道
- 现有 `task_channel_configs.athlete_diagnosis.concurrency` 已经是本 feature 的唯一调节杠杆（business-workflow.md § 9 运行时参数杠杆），运营可热调

**Alternatives considered**：
- 分配独立通道 `athlete_batch_diagnosis`：**拒绝**。队列物理不分，无法给独立并发，只会制造假象

---

## R12 · 测试策略

**Decision**：
- **合约测试**（`tests/contract/`，TDD 红 → 绿前置）：每条新路由 1 个文件，覆盖成功信封 / 422 校验 / 404 / 409 / 503 五类场景
- **集成测试**（`tests/integration/`）：4 个——扫描端到端 / 预处理端到端 / 诊断端到端 / 监控筛选两侧互斥（SC-004 / SC-006）
- **单元测试**（`tests/unit/`）：`CosAthleteScanner` 分类与目录映射 / `AthleteSubmissionService` 批量错误聚合 / `_phase_step_hook` 新映射 / `errors.py` 新 code 的 HTTP 状态与默认消息

**Rationale**：章程原则 II "测试优先"：合约测试 MUST 在路由实现之前创建且初始失败。集成测试覆盖 SC-001 / SC-003 / SC-004 / SC-005 / SC-006 五项可衡量成功标准。

---

## R13 · 迁移编号

**Decision**：`0018_athlete_inference_pipeline`（当前迁移 head 为 `0017_kb_per_category_redesign`，下一个可用编号为 `0018`）。

> **Gate**：在阶段 2 `/speckit.tasks` 开始前，执行 `alembic heads` 再次确认 `0017` 仍是 head；若因其他 feature 合并导致 `0018` 被占用，改用下一个可用编号并在 task 清单登记变更。

---

## 小结

12 + 1 条研究决策全部闭环；`plan.md` 技术背景内无残留 NEEDS CLARIFICATION，可进入阶段 1 设计。
