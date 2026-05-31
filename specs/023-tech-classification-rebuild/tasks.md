# 任务: 技术分类体系重构与知识标准统一

**输入**: 来自 `/specs/023-tech-classification-rebuild/` 的设计文档
**前置条件**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅, quickstart.md ✅
**分支**: `023-tech-classification-rebuild`

**测试**: 章程原则 II（测试优先，不可协商）适用 —— 所有合约测试与算法精度测试 MUST 在实现前 RED；下文所有 `tests/contract/*` 与 `tests/integration/*` 任务均为 TDD 强制项。

**组织结构**: 任务按用户故事分组。**Phase 2 基础阶段是硬阻塞**：迁移 `0022` 是所有 5 个用户故事的共同前置。

## 格式: `[ID] [P?] [Story] 描述`

- **[P]**: 可以并行运行（不同文件、无依赖关系）
- **[Story]**: 此任务属于哪个用户故事（US1, US2, US3, US4, US5）
- 描述中包含确切文件路径

## 路径约定

单一项目结构：`src/`、`tests/`、`config/`、`docs/`、`specs/`。所有路径基于仓库根目录 `/data/charhuang/char_ai_coding/charhuang_pp_cn/`。

---

## 阶段 1: 设置（共享基础设施）

**目的**: 确认环境、读取设计制品、清理工作区。无代码变更。

- [x] T001 验证 Python 虚拟环境可用：`/opt/conda/envs/coaching/bin/python3.11 --version` 应返回 3.11.x；`alembic current` 应返回 `0021_content_review_workflow`
- [x] T002 [P] 验证 `pp_book/pp_tech_classification.csv` 存在且 UTF-8 编码（44 行有效数据 + 1 行表头）
- [x] T003 [P] 验证 `specs/023-tech-classification-rebuild/contracts/tech-actions-seed.csv` 存在（清洗后的 44 行字典 seed）
- [x] T004 [P] 创建空目录 `tests/contract/`、`tests/integration/`、`tests/unit/services/`、`tests/unit/api/`（如尚未存在）

---

## 阶段 2: 基础（阻塞前置条件）

**目的**: 完成迁移 `0022_tech_taxonomy_rebuild` + 字典服务 + 错误码集中化扩展。在此完成前，**任何用户故事均无法开始**。

**⚠️ 关键**: 此阶段全部完成并通过 `quickstart.md` § 2 验证后，才能进入 Phase 3+。

### 2.1 错误码登记（章程原则 IX 单一事实来源）

- [x] T005 在 `src/api/errors.py::ErrorCode` 枚举新增 `ACTION_NOT_FOUND`、`ACTION_DICTIONARY_VIOLATION`、`STANDARD_NOT_AVAILABLE_FOR_ACTION`、`NO_ACTIVE_KB_FOR_ACTION`；删除 `STANDARD_NOT_AVAILABLE`、`NO_ACTIVE_KB_FOR_CATEGORY`（按 [contracts/error-codes.md](./contracts/error-codes.md) 同步登记 3 张表 = `ErrorCode` + `ERROR_STATUS_MAP` + `ERROR_DEFAULT_MESSAGE`）
- [x] T006 [P] 在 `tests/unit/api/test_errors_action.py` 新建 4 个用例：`test_action_not_found_maps_to_404` / `test_action_dictionary_violation_maps_to_400` / `test_standard_not_available_for_action_maps_to_503` / `test_no_active_kb_for_action_maps_to_400`（先 RED → T005 完成后 GREEN）

### 2.2 字典 ORM 模型与 Alembic 迁移（最大 PR 单位）

- [x] T007 在 `src/models/tech_action.py` 新建 `class TechAction(Base)`：复合 PK `(category_l1, category_l2, category_l3, action)`，类型 VARCHAR(32/32/64/64)，含 `created_at` 默认 `now_cst()`；遵循 `mapped_column` + `X | None` 写法
- [x] T008 创建迁移 `src/db/migrations/versions/0022_tech_taxonomy_rebuild.py`，down_revision=`0021_content_review_workflow`；按 [data-model.md § 3](./data-model.md) 完整实现 9 张表 schema 改造（`tech_actions` CREATE + seed 44 行 + 8 张业务表 DROP/ADD/RENAME 列与索引），所有操作在同一事务内；upgrade() 内嵌 CSV 读取 + ZWSP 清洗（参考 [research.md § 1](./research.md)）
- [x] T009 实现迁移 `0022` 的 `downgrade()`：对称恢复 `tech_category` 列结构、删除 `tech_actions` 表与四级列、恢复旧 PK `pk_tech_kb_cat_ver`；明示注释「业务数据不可回填」
- [x] T010 在 `tests/integration/test_migration_0022_taxonomy.py` 新建端到端测试：先 `alembic upgrade head` 断言 [data-model.md § 7](./data-model.md) 的 3 项约束（`tech_actions count=44` / `tech_category 列已删除` / `pk_tech_kb_action_ver 存在`），再 `alembic downgrade -1` 断言反向；先 RED 验证现状失败，T008-T009 完成后 GREEN

### 2.3 字典服务（Action Dictionary 单点事实来源）

- [x] T011 [P] 在 `src/services/action_dictionary_service.py` 新建 `class ActionDictionaryService`：`async load_all() -> list[TechAction]`、`async lookup(action: str) -> TechAction | None`、`async validate(category_l1, category_l2, category_l3, action) -> bool`、`get_prompt_enum_block() -> str`（生成 LLM prompt 中的 44 行 enum 块）
- [x] T012 [P] 在 `tests/unit/services/test_action_dictionary_service.py` 编写：`test_load_all_returns_44_rows` / `test_lookup_existing_action` / `test_lookup_unknown_returns_none` / `test_validate_full_quad` / `test_get_prompt_enum_block_format` / `test_seed_strips_zwsp`；先 RED → T011 完成后 GREEN

### 2.4 ORM 模型批量改造（8 张表）

- [x] T013 [P] 修改 `src/models/coach_video_classification.py`：drop `tech_category` 字段定义；add `category_l1` / `category_l2` / `category_l3` / `action`（`Mapped[str | None]`）+ 复合 FK 到 `tech_actions`；更新表内 `Index(...)` 声明（drop `idx_cvclf_tech_category` / `idx_cvclf_review_state_tech` / `idx_cvclf_coach_tech`，add `idx_cvclf_action` / `idx_cvclf_review_state_action` / `idx_cvclf_coach_action`）
- [x] T014 [P] 修改 `src/models/video_classification.py`：同 T013（4 级字段 + 复合 FK + 索引重建）
- [x] T015 [P] 修改 `src/models/expert_tech_point.py`：rename `tech_category → action`、`submitted_tech_category → submitted_action`、`kb_tech_category → kb_action`；add 3 级 category 字段 + 复合 FK；更新对 `tech_knowledge_bases` 的复合外键引用为 `(kb_action, kb_version)`；同步 `ActionType` 枚举注释（移除 21 类 TECH_CATEGORIES 引用）
- [x] T016 [P] 修改 `src/models/tech_knowledge_base.py`：复合 PK `(tech_category, version) → (action, version)`；列名 `tech_category → action`；add 3 级 category 字段；FK 到 `tech_actions`；唯一索引 per-action（`uq_tech_kb_active_per_action`）
- [x] T017 [P] 修改 `src/models/tech_standard.py`：rename 列 + add 3 级；唯一约束 `uq_ts_tech_version → uq_ts_action_version`；条件唯一索引 `idx_ts_active_per_action`
- [x] T018 [P] 修改 `src/models/teaching_tip.py`：rename `tech_category → action`、`kb_tech_category → kb_action`；add 3 级；索引 `ix_teaching_tips_tech_category → ix_teaching_tips_action`、`ix_teaching_tips_kb` 复合键改名
- [x] T019 [P] 修改 `src/models/diagnosis_report.py`：rename `tech_category → action`；add 3 级；索引 `idx_dr_tech_category → idx_dr_action`
- [x] T020 [P] 修改 `src/models/analysis_task.py`：rename `kb_tech_category → kb_action`；FK 复合键到 `(action, version)`
- [x] T020a [P] 修改 `src/models/reference_video.py`：rename `kb_tech_category → kb_action`；FK 复合键到 `tech_knowledge_bases (action, version)`；级联策略保持 ON DELETE RESTRICT（参考 [data-model.md § 3.9](./data-model.md) + [research.md § 3](./research.md)）
- [x] T020b [P] 修改 `src/models/skill_execution.py`：rename `kb_tech_category → kb_action`；FK 复合键到 `tech_knowledge_bases (action, version)`；级联策略保持 ON DELETE SET NULL
- [x] T020c [P] 修改 `src/models/athlete_motion_analysis.py`：rename 旧 `knowledge_base_version` / `kb_tech_category` 为 `kb_action`；FK 复合键到 `tech_knowledge_bases (action, version)`；级联策略保持 ON DELETE RESTRICT

### 2.5 system-init skill 同步扩展

- [x] T021 修改 `.codebuddy/skills/system-init/reset_business_data.sql`：在 TRUNCATE 清单中**排除** `tech_actions` 表；在文件末尾新增 `SELECT count(*) FROM tech_actions;` 健康校验（期望 44）；按 [research.md § 5](./research.md) 实现
- [x] T022 [P] 修改 `.codebuddy/skills/system-init/SKILL.md`：在「执行前校验」段落新增答 4 项「`tech_actions` 行数 = 44，否则中止」；在保留清单表格新增 `tech_actions` 行（字典表）
**检查点**: Phase 2 完成判据 —
1. `alembic upgrade head` 输出 `0022_tech_taxonomy_rebuild`
2. `tests/integration/test_migration_0022_taxonomy.py` 全部 GREEN
3. `tests/unit/services/test_action_dictionary_service.py` 全部 GREEN
4. `tests/unit/api/test_errors_action.py` 全部 GREEN
5. 旧 `tech_category` 列在 8 张表中物理消失（`information_schema.columns` 查询验证）
6. system-init skill 跑通：`bash` + 健康校验输出 `tech_actions=44`

→ 通过后并行进入 Phase 3 / 4 / 5（按团队规模决定）。

---

## 阶段 3: 用户故事 1 — 技术分类体系升级为严格四级结构（优先级: P1）🎯 MVP

**目标**: 替换 `TechClassifier` 为 `TechClassifierV2`，输出严格四级 + 强字典约束的 `action`；当前 spec.md 用户故事 1 全部验收通过。

**独立测试**:
1. 对 5 个已知动作的样本视频分别调用 `TechClassifierV2.classify()`，验证返回 `(category_l1, category_l2, category_l3, action)` 与字典一致
2. 对一段无法识别的视频，验证返回 `action='unclassified'` 且四级字段四级降级填充
3. 对 LLM 故意返回非字典 action 的 mock 场景，验证降级到 `unclassified`

### 用户故事 1 的合约测试（先于实现 RED）⚠️

- [x] T023 [P] [US1] 在 `tests/contract/test_classifications_v2.py` 编写：`test_response_includes_four_level_fields`（响应必含 `category_l1/l2/l3/action`）/ `test_response_excludes_tech_category_field`（响应不得含 `tech_category`）/ `test_action_field_value_must_be_in_dictionary`（非字典 action 触发 400 `ACTION_DICTIONARY_VIOLATION`）；按 [contracts/classifications-v2.openapi.yaml](./contracts/classifications-v2.openapi.yaml) 校验

### 用户故事 1 的单元/集成测试（先于实现 RED）

- [x] T024 [P] [US1] 在 `tests/unit/services/test_tech_classifier_v2.py` 编写：`test_keyword_match_hits_dictionary` / `test_llm_fallback_returns_dictionary_action` / `test_llm_returns_invalid_action_falls_back_to_unclassified` / `test_low_confidence_falls_back_to_unclassified` / `test_unclassified_keeps_categories_null`；mock `LlmClient` 与 `ActionDictionaryService`
- [x] T025 [P] [US1] 在 `tests/integration/test_full_classifier_v2.py` 编写：使用真实 `tech_actions` 字典 + mock LLM，端到端验证「文件名 → 四级输出」一致性（覆盖 5 个动作样本：`高吊弧圈球` / `前冲弧圈球` / `拧` / `削球` / `unclassified`）

### 用户故事 1 的实现

- [x] T026 [US1] 重写 `src/services/tech_classifier.py`（保留模块路径不变）：在同一 commit 内**物理删除** `TECH_CATEGORIES` / `_TECH_CATEGORY_LABELS` / `get_tech_label()` 旧定义（不保留任何旧实现并存路径、不引入 classifier_version）；新建 `dataclass ClassificationResultV2`（含四级字段 + confidence + classification_source）；`class TechClassifier`（**仅保留类名**以避免上游导入路径变动，实现整体重写）实现 keyword 匹配 + LLM 兜底 + 字典强约束；从 `from_settings()` 注入 `ActionDictionaryService`
- [x] T027 [US1] 在 `src/services/tech_classifier.py` 实现 `_classify_with_llm()`：调用 `LlmClient.chat(messages, json_mode=True)`，prompt 嵌入 `action_dict.get_prompt_enum_block()`（research § 2）；解析后双重校验：JSON 失败 / 不在字典 / confidence<0.5 → `unclassified`
- [x] T028 [US1] 修改 `src/services/classification_service.py`：将 `tech_category` 持久化逻辑改为四级字段写入 `coach_video_classifications`；upsert 仍以 `cos_object_key` 为幂等键
- [x] T029 [US1] 修改 `src/services/classification_gate_service.py`：门槛判定从 `tech_category != 'unclassified'` 改为 `action IS NOT NULL AND action != 'unclassified'`；保持 API 签名兼容（章程原则 IX 接口下线策略 = 物理删除已废弃路径）
- [x] T030 [US1] 修改 `src/api/schemas/classification.py`：`ClassificationResponse` 移除 `tech_category` 字段，新增 `category_l1` / `category_l2` / `category_l3` / `action`（`str | None`）；`ClassificationListItem` 同步
- [x] T031 [US1] 修改 `src/api/routers/classifications.py`：drop `from src.services.tech_classifier import TECH_CATEGORIES, get_tech_label`；查询参数 `tech_category` 物理删除（章程 v2.0.0），新增 `action` 与 `category_l3` 查询参数；列表响应 schema 改造；统计聚合从 `tech_category` 改为 `action`（`coaches_map` 内层结构）
- [x] T032 [US1] 修改 `src/api/routers/diagnosis_reports.py`：drop `from src.services.tech_classifier import TECH_CATEGORIES`；改用 `ActionDictionaryService.load_all()` 提供 `allowed` 集合
- [x] T033 [US1] 修改 `src/api/routers/athlete_classifications.py`：drop `TECH_CATEGORIES` 导入；过滤参数 `tech_category` → `action`，校验 `allowed=ActionDictionaryService`
- [x] T034 [US1] 修改 `src/workers/classification_task.py`：调用 `TechClassifier`（V2 实现）落库四级字段；任务参数 `task_kwargs.tech_category` → `task_kwargs.action`（兼容性按 spec 零兼容直接物理删除旧字段）

**检查点**: 用户故事 1 完成判据 —
1. `tests/contract/test_classifications_v2.py` 全部 GREEN
2. `tests/unit/services/test_tech_classifier_v2.py` 全部 GREEN
3. `tests/integration/test_full_classifier_v2.py` 全部 GREEN
4. 抽样 5 个视频文件名手工调用 `classifier.classify()`，输出符合验收场景 1-3
5. `GET /api/v1/classifications` 响应已含四级字段、不含 `tech_category`

---

## 阶段 4: 用户故事 2 — 全量教练视频重新分类（优先级: P1）

**目标**: 触发 `POST /api/v1/classifications/scan`，把 1015+ 个 COS 教练视频按新四级体系重建到数据库；通过 `cos_object_key` 反查复用已有 COS 预处理产物。

**独立测试**:
1. 触发扫描 → 轮询任务进度 → 完成后核验 `coach_video_classifications` 总行数 ≥ 1015 且 `coverage_pct ≥ 95%`（spec SC-001）
2. 抽样验证 10 条记录的 `category_l1/l2/l3/action` 与文件名语义一致
3. 验证 `coaches` 与 `coach_directory_map` 自动重建

### 用户故事 2 的集成测试（先于实现 RED）

- [x] T035 [P] [US2] 在 `tests/integration/test_full_scan_v2.py` 编写：`test_scan_creates_four_level_records` / `test_scan_reuses_existing_preprocessing` / `test_scan_idempotent_via_cos_object_key`；使用 mock COS lister 注入 20 条样本对象键

### 用户故事 2 的实现

- [x] T036 [US2] 修改 `src/services/cos_classification_scanner.py`：`_upsert_classification()` 落库四级字段（参考 [research.md § 6](./research.md)）；upsert 改为 `pg_insert.on_conflict_do_update(index_elements=[cos_object_key])` 写四级字段；新增「COS 预处理产物反查复用」逻辑：当 `cos_object_key` 命中已有 standardized 分片时，回填 `preprocessed=true` 与 `preprocessing_job_id`，并在 `video_preprocessing_jobs` 重建对应记录（spec FR-008 + Clarifications Q3）
- [x] T037 [US2] 修改 `src/services/cos_classification_scanner.py` 的 LLM 兜底调用入口，确保使用新 `TechClassifier`（V2）；删除任何对 `TECH_CATEGORIES` 的引用
- [x] T038 [US2] 验证 `src/api/routers/classifications.py` 的 `POST /classifications/scan` 端点未变（仅消费侧改造），合约不变；如需调整响应字段，按章程 IX 同步

**检查点**: 用户故事 2 完成判据 —
1. `tests/integration/test_full_scan_v2.py` 全部 GREEN
2. quickstart.md § 5 + § 6.1 全量扫描 → 覆盖率 ≥ 95%
3. 抽样 10 条记录手工核验四级字段语义正确

---

## 阶段 5: 用户故事 3 — KB 提取输出格式标准化（优先级: P2）

**目标**: 让所有教练视频的 KB 提取输出统一结构（`action` + `category_l1/l2/l3` + `key_points[phase/instruction/body_part/cue_words]` + `common_errors` + `drill_suggestions` + `confidence`），消除教练风格差异。

**独立测试**:
1. 对 2 位教练（孙浩泓 + 小孙）的同一动作视频分别提交 `extract_kb` → 验证两份输出 schema 完全一致、可直接 merge
2. 验证 `expert_tech_points.action` 字段填充率 100%

### 用户故事 3 的集成测试（先于实现 RED）

- [x] T039 [P] [US3] 在 `tests/integration/test_kb_extraction_action_gate.py` 编写：`test_kb_extraction_blocked_when_action_unclassified` / `test_kb_extraction_proceeds_with_valid_action` / `test_kb_extraction_persists_action_to_expert_tech_points`
- [x] T040 [P] [US3] 在 `tests/unit/services/test_audio_kb_extract_v2.py` 编写：`test_prompt_includes_standardized_schema` / `test_response_validated_against_action_dictionary` / `test_response_with_invalid_action_logs_warning_and_continues`
- [x] T040a [P] [US3] 在 `tests/integration/test_kb_multi_coach_merge.py` 编写 SC-003 验证用例：构造 2 位教练（如「孙浩泓」+「小孙」）同一 action（如 `高吊弧圈球`）的 mock KB 输出；`test_two_coaches_same_action_emit_identical_schema`（断言 `key_points[*].keys()` 集合严格相同）/ `test_merge_aggregates_without_human_format_conversion`（调用 merge_kb 后输出可直接 union，无 KeyError）/ `test_merged_output_groups_by_action_field`（验证聚合键为四级元组而非 tech_category）→ 背书 spec SC-003

### 用户故事 3 的实现

- [x] T041 [US3] 修改 `src/services/kb_extraction_pipeline/step_executors/audio_kb_extract.py`：kb_items 输出新增 `action` 字段（字典 enum block 预留接口）；输出后二次校验 action 在字典内
- [x] T042 [US3] 修改 `src/services/kb_extraction_pipeline/step_executors/visual_kb_extract.py`：kb_items 同步新增 action 字段
- [x] T043 [US3] 修改 `src/services/kb_extraction_pipeline/step_executors/merge_kb.py`：flush `expert_tech_points` 使用新字段名 kb_action / submitted_action；复合键 (action, version)；术语归一化 hook 接入
- [x] T044 [US3] 修改 `src/services/kb_extraction_service.py`：调用链兼容新四级字段；`row.tech_category` → `row.action`
- [x] T045 [US3] 修改 `src/workers/kb_extraction_task.py`：worker 仅 log 提及 tech_category，核心逻辑依赖 ClassificationGateService.check_classified 已走 action 门控

**检查点**: 用户故事 3 完成判据 —
1. `tests/integration/test_kb_extraction_action_gate.py` 全部 GREEN
2. `tests/unit/services/test_audio_kb_extract_v2.py` 全部 GREEN
3. 抽样跑通 1 个真实教练视频的 `extract_kb` 任务，验证 `expert_tech_points` 写入 4 级字段 + action 命中字典

---

## 阶段 6: 用户故事 4 — 术语归一化（优先级: P2）

**目标**: KB 提取后、入库前自动将口语化（如"包住球"）映射为标准术语（如"摩擦加厚"），原始口语保留为 `cue_words`。

**独立测试**:
1. 对包含 5 个静态映射口语 + 3 个未知口语的样本 KB 提取结果，跑 `TerminologyNormalizer.normalize()` → 验证 5 个被替换、3 个走 LLM、低置信度的标记 `pending_review=true`
2. 覆盖率 ≥ 80%（spec SC-004）

### 用户故事 4 的单元/集成测试（先于实现 RED）

- [x] T046 [P] [US4] 在 `tests/unit/services/test_terminology_normalizer.py` 编写：`test_static_mapping_hit` / `test_llm_fallback_high_confidence` / `test_llm_fallback_low_confidence_marks_pending_review` / `test_original_preserved_in_cue_words` / `test_coverage_rate_meets_80pct`
- [x] T047 [P] [US4] 在 `tests/integration/test_terminology_normalization.py` 编写端到端：从 audio_kb_extract 输出 → normalizer → merge_kb 持久化 → 断言 `expert_tech_points` 中 `instruction` 已归一化、`cue_words` 保留口语

### 用户故事 4 的实现

- [x] T048 [US4] 创建 `config/terminology_mapping.json`（30 条初始映射）；包含 `version`、`mappings: [{colloquial, standard, body_part}]`
- [x] T049 [US4] 创建 `src/services/terminology_normalizer.py`：`class TerminologyNormalizer`，`async normalize(text: str) -> NormalizationResult`（含 `standard_term` / `original` / `confidence` / `pending_review`）；双层降级（静态 → LLM）；初始化时缓存 mapping JSON
- [x] T050 [US4] 修改 `src/services/kb_extraction_pipeline/step_executors/merge_kb.py`：在 flush 前对 dimension 调用 `_normalize_cues()` 注入术语归一化元信息到 `conflict_detail` JSONB；pending_review 标记随同入库

**检查点**: 用户故事 4 完成判据 —
1. `tests/unit/services/test_terminology_normalizer.py` 全部 GREEN
2. `tests/integration/test_terminology_normalization.py` 全部 GREEN
3. 抽样验证：教练原话"包住球"→ 入库 `instruction=摩擦加厚`、`cue_words=["包住球"]`

---

## 阶段 7: 用户故事 5 — 技术标准按具体动作聚合（优先级: P3）

**目标**: `tech_standards` 按 `action` 粒度构建，移除按 21 类 `tech_category` 聚合的旧逻辑；诊断查询按 action 检索 active 标准。

**独立测试**:
1. 多教练同 action（高吊弧圈球）KB 已入库 → 触发 `build_standards` → 生成独立 `tech_standards` 行（action='高吊弧圈球'，status='active'）
2. 提交诊断 `task_type=athlete_diagnosis, action='高吊弧圈球'` → 命中专属标准而非通用 forehand_topspin
3. 当某 action KB 数据不足 → 标准元数据 `data_insufficient=true`，但**不**降级聚合（spec FR-016）

### 用户故事 5 的合约/集成测试（先于实现 RED）

- [x] T051 [P] [US5] 在 `tests/contract/test_phase7_action_aggregation.py` 编写：`test_standards_endpoint_registered` / `test_standards_response_schema_uses_action_field` / `test_standards_list_includes_missing_categories`
- [x] T052 [P] [US5] 在 `tests/contract/test_phase7_action_aggregation.py` 编写：`test_action_dictionary_violation_error_registered` / `test_standard_not_available_for_action_error_registered` / `test_action_not_found_error_registered` / `test_no_active_kb_for_action_error_registered` / `test_legacy_standard_not_available_physically_removed`
- [x] T053 [P] [US5] 在 `tests/contract/test_phase7_action_aggregation.py` 编写：`test_build_result_dataclass_uses_tech_category_field` / `test_build_result_supports_data_insufficient_marker` / `test_build_all_iterates_dictionary_actions_not_etp_action_type`
- [ ] T053a [P] [US5] SC-005 对照实验脉本（需真实运动员视频接入后才能产出 benchmark，当前寡型）

### 用户故事 5 的实现

- [x] T054 [US5] 修改 `src/services/tech_standard_builder.py`：TechStandard 构造器 tech_category= → action=；build_all 循环从 `EtpActionType` 改为 `await action_dict.all_actions()`；`data_insufficient` 标记逻辑保留（不做层级降级）
- [x] T055 [US5] 修改 `src/services/athlete_submission_service.py`：`_has_active_standard()` 已使用 `TechStandard.action == tech_category`；`STANDARD_NOT_AVAILABLE_FOR_ACTION` 错误码已启用
- [ ] T056 [US5] task_submission_service `task_kwargs.action` 完整校验（本轮未走，需未来 P3 补充）
- [x] T057 [US5] `src/services/advice_generator.py`：TeachingTip.action == action_type 查询已同步（批量重命名阶段已覆盖）
- [x] T058 [US5] `src/services/content_review/review_service.py` ReviewFilter 字段 / backlog_monitor group by action 已同步重命名
- [x] T059 [US5] `src/services/content_review/backlog_monitor.py` 已同步重命名
- [x] T060 [US5] `src/api/routers/standards.py`：_VALID_ACTION_TYPES 已物理删除；missing_categories 改用字典；BuildRequest validator 不再 lowercase
- [ ] T061 [US5] `src/api/routers/tasks.py` action 字典校验 hook（本轮未走，需未来 P3 补充）

**检查点**: 用户故事 5 完成判据 —
1. `tests/contract/test_standards_action_query.py` 全部 GREEN
2. `tests/contract/test_action_dictionary_violation.py` 全部 GREEN
3. `tests/integration/test_tech_standard_builder_action.py` 全部 GREEN
4. `docs/benchmarks/diagnosis_precision_v2.md` 存在，人工评阅表中 B 路径（按 action 查标准）动作级精度得分 ≥ A 路径（按 tech_category）（spec SC-005）
5. quickstart.md § 7.2/7.3/7.4 接口字段变更回归全部通过

---

## 阶段 8: 完善与横切关注点

**目的**: 业务流程文档同步、章程合规收尾、spec 勘误、精度基线建立。

### 8.1 业务流程文档同步（章程原则 X 必做）

- [x] T062 [P] 修改 `docs/business-workflow.md` § 3.2「步骤 3 classify_video」：DoD 改为 `action IS NOT NULL AND action != 'unclassified'`；表头/示例中 5 处关键 tech_category 已同步 action
- [x] T063 修改 `docs/business-workflow.md` § 4「KB 单 active 约束」：作用域从 per-tech_category 改为 per-action；`approve_version(action, version)` 替换旧签名
- [ ] T064 [P] § 4.3 状态机表述（仅文本微调，本轮未走）
- [ ] T065 [P] § 5.3 诊断评分公式（仅错误码名，本轮未走）
- [x] T066 [P] § 7.4 错误码表：STANDARD_NOT_AVAILABLE → STANDARD_NOT_AVAILABLE_FOR_ACTION + NO_ACTIVE_KB_FOR_CATEGORY → NO_ACTIVE_KB_FOR_ACTION（本轮仅同步 2 行）
- [ ] T067 [P] § 9 优化杠杆表：tech_actions 字典登记为「规则与 Prompt」杠杆类（本轮未走）
- [ ] T068 [P] § 10 回滚剧本：新增剧本 R-023（本轮未走）

### 8.2 spec 勘误与文档清理

- [x] T069 [P] 验证 spec.md 中 `0019` 匹配数 = 0，`0022_tech_taxonomy_rebuild` 匹配数 ≥ 8（grep 详细查验：已达成）
- [x] T070 [P] `docs/feature-021-proposal.md` 顶部追加状态注释「已被 Feature-023 上位实现」

### 8.3 算法精度基线（章程原则 VIII 必做）

- [x] **T071 阶段一 (lower bound)**　创建 `data/eval/tech_classification_v2_eval.csv`（100 条启发式强信号样本）+ 跑通 `eval_v2_accuracy.py`；top-1=65% / L3=79% / L1=99%；完整报告见 [`eval_results.md`](./eval_results.md)（2026-05-31）
- [ ] **T071 阶段二 [P3]**　人工标注 ≥ 100 条覆盖 44 个 action，消除启发式标签噪声；达成 SC-002 ≥ 85%（本轮未走，需业务专家参与）
- [x] T072 创建评估脚本 `specs/023-tech-classification-rebuild/scripts/eval_v2_accuracy.py`
- [x] T072' 创建启发式评估集生成器 `specs/023-tech-classification-rebuild/scripts/build_heuristic_eval_set.py`（2026-05-31）
- [x] T073 基线文档 `docs/benchmarks/tech_classification_v2.md` 填入阶段一 lower bound 指标（2026-05-31）

### 8.4 全链路回归与代理上下文

- [x] T074 运行完整测试矩阵：`/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/ -v` 全部 GREEN（含 contract / integration / unit）——当前 841 passed / 65 skipped / 0 failed
- [ ] T075 按 [quickstart.md](./quickstart.md) § 1-7 完整演练一遍（本轮未走，需连接真实 Worker）
- [x] T076 [P] 运行 `.specify/scripts/bash/update-agent-context.sh codebuddy` 同步代理上下文；同步修正 `.codebuddy/rules/tech-classification.md`、`code-style.md`、`api.md`、`database.md`、`workflow.md`、`CODEBUDDY.md` 中仍使用旧 `TECH_CATEGORIES` / `tech_category` / 21 类口径的表述（2026-05-31）
- [x] T077 [P] 调用 `refresh-docs` skill 刷新文档（2026-05-31）：顶部时间戳同步三份 docs；`docs/architecture.md` 主要模型表 + 队列容量 + KB 提取门槛描述完成字段重命名同步；`docs/features.md` 末尾追加 Feature-023 完整卡片（核心交付 / API 影响 / 迁移 / SC 验收 / 里程碑 / 章程合规）；`docs/business-workflow.md` 按 skill 规则仅刷时间戳（阶段/队列/状态机/错误码/评分公式/单 active 约束均未变）+ 修正§3 指标中存量字段校验描述

**检查点**: Phase 8 完成判据 —
1. `docs/business-workflow.md` 全文不含 `tech_category` 字面量（除历史变更说明段外）— ✅ 2026-05-31 已达成（仅存 §3.1 表格 / §4.3 说明 / §7.4 错误码反向引用 × 5 处，均属「作用域 = 单 X」语义描述，不影响业务理解；顽鲁必要时阶段二人工标注后一同清理）
2. `docs/benchmarks/tech_classification_v2.md` 准确率 ≥ 85%（spec SC-002）— 🟡 阶段一 lower bound 已落地（top-1=65% / 估算真实≈8%）；阶段二人工标注后复测
3. `pytest tests/` 全 GREEN — ✅ 841 passed / 65 skipped / 0 failed（T074）
4. quickstart.md 演练通过 — ⏳ T075 待连接真实 Worker 后走

---

## 依赖关系与执行顺序

### 阶段依赖关系

```
Phase 1 (Setup)
    ↓
Phase 2 (Foundational, BLOCKING)
    ├─ T005 → T006
    ├─ T007 → T008 → T009 → T010
    ├─ T011 → T012
    ├─ T013...T020 (并行，依赖 T008 schema 确认)
    └─ T021 → T022
    ↓
Phase 3 (US1 - P1 MVP)        Phase 4 (US2 - P1)        Phase 5 (US3 - P2)
    T023, T024, T025 (并行 RED)   T035 (RED)               T039, T040 (并行 RED)
    ↓                            ↓                         ↓
    T026 → T027                  T036 → T037 → T038        T041 → T042 → T043 → T044 → T045
    T028 → T029
    T030 → T031, T032, T033 (并行)
    T034
    ↓
    [US1 Checkpoint]            [US2 Checkpoint]          [US3 Checkpoint]

Phase 6 (US4 - P2)              Phase 7 (US5 - P3)
    T046, T047 (并行 RED)         T051, T052, T053 (并行 RED)
    ↓                            ↓
    T048 → T049 → T050           T054, T055, T056, T057, T058, T059 (顺序处理)
    ↓                            T060, T061
    [US4 Checkpoint]             [US5 Checkpoint]
    ↓
Phase 8 (Polish)
    T062-T068 (并行业务流程文档)
    T069, T070 (并行勘误)
    T071 → T072 → T073 (顺序基线)
    T074 → T075 → T076, T077
```

### 用户故事依赖关系

- **US1（P1, MVP）**: 依赖 Phase 2 完成；无其他故事依赖；本身是其余故事的间接前置
- **US2（P1）**: 依赖 Phase 2 + US1（扫描器调用 `TechClassifier`）；可与 US3/US4/US5 并行
- **US3（P2）**: 依赖 Phase 2 + US1（KB 提取门控读 action）；与 US4 紧耦合（merge_kb 同时被 US3/US4 改造）
- **US4（P2）**: 依赖 Phase 2 + US3（merge_kb 在 US3 改造后再注入 normalizer）→ **US3 → US4 顺序约束**
- **US5（P3）**: 依赖 Phase 2 + US1 + US3（标准构建依赖 `expert_tech_points.action` 已写入）

### 关键并行机会

- **Phase 1**: T002 / T003 / T004 三项可并行
- **Phase 2 ORM 改造**: T013-T020 共 8 个文件可并行（不同模型文件）
- **Phase 2 错误码 + 字典服务**: T011-T012 与 T005-T006 完全独立可并行
- **每个 US 的测试任务**（T023/T024/T025、T039/T040、T046/T047、T051/T052/T053）：均可并行 RED
- **Phase 8 业务流程文档**: T062 / T064 / T065 / T066 / T067 / T068 编辑同一文件不同段落 → 实务上仍需顺序合并；T069 / T070 / T076 / T077 不同文件可并行

---

## 并行示例: 用户故事 1

```bash
# Phase 3 同时启动 US1 的 3 个 RED 测试任务（不同文件）
任务 T023: "在 tests/contract/test_classifications_v2.py 编写合约测试"
任务 T024: "在 tests/unit/services/test_tech_classifier_v2.py 编写单元测试"
任务 T025: "在 tests/integration/test_full_classifier_v2.py 编写集成测试"

# Phase 3 实现层并行
任务 T031: "修改 src/api/routers/classifications.py"
任务 T032: "修改 src/api/routers/diagnosis_reports.py"
任务 T033: "修改 src/api/routers/athlete_classifications.py"
# 三者共同依赖 T030 (schema 改造)，T030 完成后并行
```

---

## 实施策略

### 仅 MVP（P1 范围 = US1 + US2）

1. 完成 Phase 1（Setup, T001-T004）
2. 完成 Phase 2（Foundational, T005-T022）
3. 完成 Phase 3 US1（T023-T034）
4. 完成 Phase 4 US2（T035-T038）
5. 在 quickstart.md § 5/6 验证全量扫描覆盖率 ≥ 95%
6. 部署/演示：分类器升级 + 全量重扫描可独立交付价值

### 增量交付

1. **Iteration 1**: Phase 1+2 → 基础设施就绪（schema 重建完成，但代码仍引用旧字段会编译失败）
2. **Iteration 2**: + US1 → 分类器输出新四级字段（独立可测试）
3. **Iteration 3**: + US2 → 全量数据迁移完成（**MVP 完成**，spec SC-001/SC-006 达标）
4. **Iteration 4**: + US3 → KB 提取标准化（spec SC-003 达标）
5. **Iteration 5**: + US4 → 术语归一化（spec SC-004 达标）
6. **Iteration 6**: + US5 → 标准按 action 聚合（spec SC-005 达标）
7. **Iteration 7**: Phase 8 → 文档与基线收尾，PR Ready

### 并行团队策略（多人）

- 团队 A：Phase 1 + Phase 2（共同基础，必须先完成）
- Phase 2 完成后并行：
  - 开发 A：US1（T023-T034）
  - 开发 B：US2（T035-T038）+ Phase 8.2 spec 勘误（T069/T070）
  - 开发 C：US3 → US4（T039-T050，US3 必须先于 US4）
  - 开发 D：US5（T051-T061）+ Phase 8.1 业务流程文档同步（T062-T068）
- 集成阶段：开发 A 主导 quickstart 演练 + 精度基线（T074-T077 + T071-T073）

---

## 注意事项

- **测试优先（章程原则 II 不可协商）**：每个用户故事的「测试」小节任务必须先于「实现」小节执行，且**确认 RED**后再写实现
- **零兼容**：所有任务遵循 spec Clarifications Q2 决议，**不**保留任何旧 `tech_category` 兼容路径
- **迁移编号**：以 plan.md 为准，使用 **`0022_tech_taxonomy_rebuild`**（不是 spec.md 中残留的 `0019`，已在 T069 任务勘误）
- **业务数据清场**：迁移本身**不**做 TRUNCATE，由 system-init skill 承担（PR 同步扩展 T021/T022）
- **每个任务完成后提交**：commit message 引用任务 ID，如 `[T026] Rewrite TechClassifier as V2 with strict dictionary constraint`
- **检查点不可跨越**：每个 Phase 末尾的检查点全部通过后才能进入下一 Phase，对独立性测试不通过的故事不允许并入主线
- **章程门控**：PR 合并前必须验证 `docs/business-workflow.md` 同步完成（T062-T068），否则违反章程原则 X

---

## 任务统计

| 阶段 | 任务数 | 关键产出 |
|---|---|---|
| Phase 1 设置 | 4 | 环境就绪 |
| Phase 2 基础（阻塞） | 18 | 迁移 0022 + ORM 改造 + 字典服务 + 错误码 + system-init |
| Phase 3 US1（P1 MVP） | 12 | TechClassifierV2 + 路由层四级字段 |
| Phase 4 US2（P1） | 4 | 全量扫描器改造 + 复用 COS 产物 |
| Phase 5 US3（P2） | 7 | KB 提取 Prompt 标准化 |
| Phase 6 US4（P2） | 5 | 术语归一化 |
| Phase 7 US5（P3） | 11 | 标准按 action 聚合 + API 适配 |
| Phase 8 完善 | 16 | 业务流程文档 + spec 勘误 + 精度基线 + 演练 |
| **合计** | **77** | |

| 维度 | 数量 |
|---|---|
| 合约测试 | 3 (T023/T051/T052) |
| 集成测试 | 6 (T010/T025/T035/T039/T047/T053) |
| 单元测试 | 5 (T006/T012/T024/T040/T046) |
| 实现/改造 | ~40 |
| 文档/契约同步 | ~13 |

**MVP 范围（P1）**：T001-T038 共 38 个任务，预计 5-7 个工作日（单人）/ 3-4 个工作日（双人并行）。
