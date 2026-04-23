# Feature 011 任务列表: 非专业选手动作诊断与评分

## Phase 1: 设置

**目标**: 确认目录结构，创建规范文件

- [x] T001 确认 src/services/, src/api/routers/, tests/unit/, tests/integration/, tests/contract/ 目录均存在

---

## Phase 2: 基础（数据库迁移与模型）

**目标**: 建立 diagnosis_reports 和 diagnosis_dimension_results 两张表，并完成 ORM 模型

- [x] T002 在 src/db/migrations/versions/0011_diagnosis_report.py 中创建 Alembic 迁移，新建 diagnosis_reports 表（字段：id UUID PK DEFAULT gen_random_uuid(), tech_category VARCHAR(64) NOT NULL, standard_id BIGINT FK→tech_standards.id ON DELETE RESTRICT, standard_version INTEGER NOT NULL, video_path TEXT NOT NULL, overall_score FLOAT NOT NULL, strengths_summary TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()；索引 idx_dr_tech_category ON (tech_category), idx_dr_created_at ON (created_at DESC)）
- [x] T003 在同一迁移文件 src/db/migrations/versions/0011_diagnosis_report.py 中新建 diagnosis_dimension_results 表（字段：id BIGSERIAL PK, report_id UUID FK→diagnosis_reports.id ON DELETE CASCADE NOT NULL, dimension VARCHAR(128) NOT NULL, measured_value FLOAT NOT NULL, ideal_value FLOAT NOT NULL, standard_min FLOAT NOT NULL, standard_max FLOAT NOT NULL, unit VARCHAR(32), score FLOAT NOT NULL, deviation_level VARCHAR(20) NOT NULL CHECK(deviation_level IN ('ok','slight','significant')), deviation_direction VARCHAR(10) CHECK(deviation_direction IN ('above','below','none')), improvement_advice TEXT；唯一约束 uq_ddr_report_dimension ON (report_id, dimension)；索引 idx_ddr_report ON (report_id)）（依赖 T002）
- [x] T004 在 src/models/diagnosis_report.py 中实现 DiagnosisReport SQLAlchemy ORM 模型（对应 diagnosis_reports 表，UUID PK，包含关系 dimensions: List[DiagnosisDimensionResult]，lazy="selectin"）以及 DeviationLevel enum（ok/slight/significant）和 DiagnosisDimensionResult ORM 模型（对应 diagnosis_dimension_results 表，包含 FK 关系 report: DiagnosisReport）（依赖 T002, T003）
- [x] T005 在 src/models/__init__.py 中导出 DiagnosisReport 和 DiagnosisDimensionResult（依赖 T004）
- [x] T006 运行迁移验证：执行 alembic upgrade head 并确认两张表及所有索引创建成功（依赖 T003, T005）

### 补充 TDD 测试（需补写，对应已完成实现）

- [ ] T007 [P] 补写 tests/unit/test_migration_011.py，验证 migration 0011 执行后 diagnosis_reports 和 diagnosis_dimension_results 表结构符合预期（列类型、约束、索引、FK），使用 sqlalchemy inspect 方式验证（依赖 T006）
- [ ] T008 [P] 补写 tests/unit/test_diagnosis_model.py，验证 DiagnosisReport 和 DiagnosisDimensionResult ORM 模型字段映射正确、relationship lazy=selectin 正常加载，使用 factory_boy 或手工构造对象验证（依赖 T004）

---

## Phase 3: 评分算法（US1/US2 基础）(P1)

**故事目标**: 实现 AD-003 线性插值评分算法，支持 ok/slight/significant 三级偏差判断和 above/below/none 方向识别

**独立测试标准**: pytest tests/unit/test_diagnosis_scorer.py 全部通过（27 tests）

### 测试（TDD — 先写测试）

- [x] T009 [P] [US1] 编写 tests/unit/test_diagnosis_scorer.py，覆盖 AD-003 算法所有场景：a) 在范围内→ok score=100；b) slight 范围→score∈[60,100]；c) significant 范围→score∈[0,60]；d) 偏差方向 above/below/none；e) overall_score=维度均值；f) 空列表→0.0；g) min==max 特殊情况

### 实现

- [x] T010 [US1] 实现 src/services/diagnosis_scorer.py，包含 compute_dimension_score() 和 compute_overall_score()，以及 DimensionScore dataclass、DeviationLevel/DeviationDirection enum（依赖 T009）

---

## Phase 4: LLM 建议生成（US1/US2 基础）(P1)

**故事目标**: 使用 LlmClient 为偏差维度动态生成改进建议，LLM 失败时降级模板

**独立测试标准**: pytest tests/unit/test_diagnosis_llm_advisor.py 全部通过（11 tests）

### 测试（TDD — 先写测试）

- [x] T011 [P] [US1] 编写 tests/unit/test_diagnosis_llm_advisor.py，场景：a) ok→返回 None 不调用 LLM；b) slight→LLM 调用返回建议；c) significant→LLM 调用返回建议；d) LLM 失败→降级模板不抛异常；e) 提示词包含维度名/实测值/范围/技术类别

### 实现

- [x] T012 [US1] 实现 src/services/diagnosis_llm_advisor.py，包含 generate_improvement_advice(dim, tech_category, llm_client)，ok 返回 None，slight/significant 调用 LLM，LlmError 降级模板（依赖 T011）

---

## Phase 5: 诊断服务与 API（US1 — 核心流程）(P1)

**故事目标**: POST /api/v1/diagnosis 端点同步返回完整诊断报告，持久化到 DB，≤60s 响应

**独立测试标准**:
- pytest tests/unit/test_diagnosis_service.py 全部通过
- pytest tests/contract/test_diagnosis_contract.py 全部通过（17 tests）
- pytest tests/integration/test_diagnosis_api.py::TestUS1FullFlow 全部通过

**检查点**: POST /api/v1/diagnosis 可用，US1 MVP 可演示

### 测试（TDD — 先写测试）

- [x] T013 [P] [US1] 编写 tests/contract/test_diagnosis_contract.py，场景：a) 200 响应包含所有 FR-006+FR-007 字段；b) 422 无效 tech_category；c) 404 StandardNotFoundError；d) 400 ExtractionFailedError；使用 TestClient + patch AsyncMock，不依赖真实 DB
- [x] T014 [P] [US1] 编写 tests/integration/test_diagnosis_api.py 中的 TestUS1FullFlow，使用真实 PostgreSQL + savepoint 隔离，mock _localize_video 和 _extract_measurements，验证：1) 返回 200；2) 报告写入 diagnosis_reports；3) standard_id 匹配 active 标准
- [ ] T015 [P] [US1] 补写 tests/unit/test_diagnosis_service.py，mock 所有外部依赖（pose_estimator、tech_extractor、llm_advisor、AsyncSession），验证场景：a) 无 active 标准→StandardNotFoundError；b) 空测量值→ExtractionFailedError；c) 正常流程→返回 DiagnosisReportData；d) LLM 通过 run_in_executor 调用；e) finally 块清理 tmp_path（依赖 T010, T012）

### 实现

- [x] T016 [US1] 实现 src/services/diagnosis_service.py：DiagnosisService.diagnose() 完整流程（加载标准→本地化视频→提取测量→评分→LLM建议→持久化），StandardNotFoundError/ExtractionFailedError 异常，run_in_executor 包装 CPU 密集操作，finally 清理临时文件（依赖 T010, T012）
- [x] T017 [US1] 实现 src/api/routers/diagnosis.py：DiagnosisRequest/DiagnosisResponse/DimensionResultResponse Pydantic 模型，POST "" 端点，StandardNotFoundError→404/ExtractionFailedError→400/Exception→500，session commit（依赖 T016）
- [x] T018 [US1] 在 src/api/main.py 中注册 diagnosis_router，prefix="/api/v1"（依赖 T017）

---

## Phase 6: 维度详情（US2）(P1)

**故事目标**: 每个维度返回 FR-007 所有 10 个字段（含 improvement_advice），优点维度建议为 null

**独立测试标准**: pytest tests/integration/test_diagnosis_api.py::TestUS2DimensionDetails 全部通过（3 tests）

### 测试（TDD）

- [x] T019 [P] [US2] 编写 tests/integration/test_diagnosis_api.py 中的 TestUS2DimensionDetails：a) ok 维度在 strengths 中且 advice=null；b) 全部 ideal 值→overall_score=100；c) 偏差维度有非空 improvement_advice（依赖 T014）

### 验证

- [x] T020 [US2] 确认 DimensionResultResponse 包含 FR-007 全部 10 字段：dimension, measured_value, ideal_value, standard_min, standard_max, unit, score, deviation_level, deviation_direction, improvement_advice（依赖 T017）

---

## Phase 7: 标准版本追溯（US2.5）(P2)

**故事目标**: 响应中包含 standard_id 和 standard_version，支持跨用户结果比较

**独立测试标准**: pytest tests/integration/test_diagnosis_api.py::TestUS1FullFlow::test_diagnosis_standard_id_matches_active 通过

- [x] T021 [US2.5] 确认 DiagnosisResponse 包含 standard_id 和 standard_version 字段，且值与 DB 中 active 标准一致（已由 test_diagnosis_standard_id_matches_active 覆盖）（依赖 T018）

---

## Phase 8: 错误处理验证（US1/FR-010）(P1)

**故事目标**: 无活跃标准→404，无效类别→422，空测量值→400，全部有明确 error code

**独立测试标准**: pytest tests/integration/test_diagnosis_api.py::TestErrorHandling 全部通过（3 tests）

- [x] T022 [P] [US1] 编写 tests/integration/test_diagnosis_api.py 中的 TestErrorHandling：a) 无标准→404 standard_not_found；b) 无效 tech_category→422；c) 空测量→400 extraction_failed（依赖 T014）
- [x] T023 [US1] 确认 StandardNotFoundError→404，ExtractionFailedError→400，Exception→500 internal_error 在 src/api/routers/diagnosis.py 中映射正确（依赖 T017）

---

## Phase 9: 结构化日志与可观测性（NFR-001）

**目标**: 每次诊断记录结构化日志，字段：tech_category, standard_id, overall_score, dimensions_count, deviations_count, llm_calls, elapsed_ms

- [x] T024 在 src/services/diagnosis_service.py 的 DiagnosisService.diagnose() 中添加结构化日志（logger.info），已包含所有 NFR-001 必需字段（依赖 T016）

---

## Phase 10: NFR 验证与收尾

**目标**: 验证 NFR-002~NFR-004，补写缺失测试，执行 curl 冒烟测试

### NFR 补充测试

- [ ] T025 [P] 补写 tests/unit/test_diagnosis_service_cleanup.py，mock os.unlink，验证：a) 正常流程后 tmp_path 被删除；b) 异常流程（ExtractionFailedError）后 tmp_path 仍被清理（finally 块）（依赖 T016）
- [ ] T026 [P] 验证 src/services/diagnosis_service.py 中 generate_improvement_advice 通过 asyncio.get_event_loop().run_in_executor(None, ...) 调用（NFR-004），若已存在则在 test_diagnosis_service.py 中补充对应断言（依赖 T015）

### COS 路径验证

- [ ] T027 验证 src/services/diagnosis_service.py 中 _download_from_cos 的 cos:// 路径解析：cos://bucket-name/path/to/video.mp4→key: path/to/video.mp4；cos://bucket/key.mp4→key: key.mp4（可新增 tests/unit/test_diagnosis_service.py 中的 test_cos_key_parsing 测试）（依赖 T016）

### 手动验收

- [ ] T028 启动 uvicorn src.api.main:app --reload，执行三条 curl 验证：1) 无效类别→422；2) forehand_loop_underspin 无标准→404 standard_not_found；3) 计时正常请求确认 ≤60s（SC-002）（依赖 T018）

### 教练视频验证（SC-001，阻塞于真实 pipeline）

- [ ] T029 [US3] 使用教练视频 COS 路径（或本地路径）发起真实诊断请求，验证 overall_score ≥ 80（SC-001）、standard_id 匹配 active 标准（SC-003）。*注意: 此任务阻塞于 estimate_pose/segment_actions/classify_segments/extract_tech_points pipeline 完整实现，如 pipeline 未就绪则标注为人工验收项*（依赖 T028）

---

## 任务摘要

| Phase | 任务数 | 状态 |
|-------|--------|------|
| Phase 1: 设置 | T001 | ✅ 完成 |
| Phase 2: 基础（DB+模型）| T002-T008 | T002-T006 ✅，T007-T008 待补写 |
| Phase 3: 评分算法 | T009-T010 | ✅ 完成（27 tests） |
| Phase 4: LLM 建议 | T011-T012 | ✅ 完成（11 tests） |
| Phase 5: 服务+API（US1） | T013-T018 | T013-T014/T016-T018 ✅，T015 待补写 |
| Phase 6: 维度详情（US2） | T019-T020 | ✅ 完成 |
| Phase 7: 标准版本（US2.5）| T021 | ✅ 完成 |
| Phase 8: 错误处理 | T022-T023 | ✅ 完成 |
| Phase 9: 结构化日志 | T024 | ✅ 完成 |
| Phase 10: NFR+收尾 | T025-T029 | 全部待完成 |

**当前通过测试**: 64 tests（unit 38 + contract 17 + integration 9）

---

## 依赖关系

- Phase 2 (DB) → Phase 3 (Scorer) → Phase 5 (Service)
- Phase 4 (LLM) → Phase 5 (Service)
- Phase 5 (US1) → Phase 6 (US2) → Phase 7 (US2.5)
- Phase 5 → Phase 8 (错误处理)
- Phase 5 → Phase 9 (日志)
- Phase 5 → Phase 10 (收尾)
- T029 阻塞于外部 pipeline 就绪

## 并行执行机会

- T007, T008 可并行（不同文件，均为补写测试）
- T009, T011 可并行（在原始 TDD 实现时，均为测试编写不同文件）
- T025, T026, T027 可并行（均为收尾验证，不同文件）

## 实现策略

**MVP 范围（Phase 3-8 — US1+US2 已完成）:**
- POST /api/v1/diagnosis 同步返回完整诊断报告
- 维度详情含全部 10 字段（FR-007）
- StandardNotFoundError/ExtractionFailedError 明确错误码
- 评分算法 + LLM 建议 + 结构化日志

**待完成项（Phase 2 补写 + Phase 10）:**
- T007/T008: migration/model 测试（TDD 补写）
- T015: DiagnosisService 单元测试（mock pipeline）
- T025: 临时文件清理测试
- T026: run_in_executor 断言
- T027: COS 路径解析测试
- T028: 手动 curl 冒烟测试
- T029: 教练视频 SC-001 验证（依赖 pipeline）
