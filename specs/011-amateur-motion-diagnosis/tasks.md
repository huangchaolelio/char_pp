# 任务: 非专业选手动作诊断与评分

**输入**: 来自 `/specs/011-amateur-motion-diagnosis/` 的设计文档
**前置条件**: plan.md ✅, spec.md ✅

**测试**: 规范要求 TDD（章程原则 II），包含契约测试、集成测试和单元测试任务。

**组织结构**: 任务按用户故事分组，每个故事独立实施和测试。

## 格式: `[ID] [P?] [Story] 描述`
- **[P]**: 可以并行运行（不同文件，无依赖关系）
- **[Story]**: 此任务属于哪个用户故事（US1, US2, US3）
- 描述中包含确切的文件路径

---

## 阶段 1: 设置

**目的**: 确认目录结构存在，无需新建顶层目录

- [ ] T001 确认 src/models/, src/services/, src/api/routers/, tests/unit/, tests/integration/, tests/contract/ 目录已存在且无需新建

---

## 阶段 2: 基础（阻塞前置条件）

**目的**: 新表迁移和 ORM 模型，所有用户故事的共同依赖

**⚠️ 关键**: 在此阶段完成之前，无法开始任何用户故事工作

- [ ] T002 在 src/db/migrations/versions/0011_diagnosis_report.py 中创建 Alembic 迁移，新建 diagnosis_reports 表（字段：id UUID PK DEFAULT gen_random_uuid(), tech_category VARCHAR(64) NOT NULL, standard_id BIGINT FK→tech_standards.id ON DELETE RESTRICT, standard_version INTEGER NOT NULL, video_path TEXT NOT NULL, overall_score FLOAT NOT NULL, strengths_summary TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()；索引 idx_dr_tech_category ON (tech_category), idx_dr_created_at ON (created_at DESC)）

- [ ] T003 在同一迁移文件 src/db/migrations/versions/0011_diagnosis_report.py 中新建 diagnosis_dimension_results 表（字段：id BIGSERIAL PK, report_id UUID FK→diagnosis_reports.id ON DELETE CASCADE NOT NULL, dimension VARCHAR(128) NOT NULL, measured_value FLOAT NOT NULL, ideal_value FLOAT NOT NULL, standard_min FLOAT NOT NULL, standard_max FLOAT NOT NULL, unit VARCHAR(32), score FLOAT NOT NULL, deviation_level VARCHAR(20) NOT NULL CHECK(deviation_level IN ('ok','slight','significant')), deviation_direction VARCHAR(10) CHECK(deviation_direction IN ('above','below','none')), improvement_advice TEXT；唯一约束 uq_ddr_report_dimension ON (report_id, dimension)；索引 idx_ddr_report ON (report_id)）（依赖 T002）

- [ ] T004 在 src/models/diagnosis_report.py 中实现 DiagnosisReport SQLAlchemy ORM 模型（对应 diagnosis_reports 表，UUID PK，包含关系 dimensions: List[DiagnosisDimensionResult]，lazy="selectin"）以及 DeviationLevel enum（ok/slight/significant）和 DiagnosisDimensionResult ORM 模型（对应 diagnosis_dimension_results 表，包含 FK 关系 report: DiagnosisReport）（依赖 T002, T003）

- [ ] T005 在 src/models/__init__.py 中导出 DiagnosisReport 和 DiagnosisDimensionResult（依赖 T004）

- [ ] T006 运行迁移验证：执行 alembic upgrade head 并确认两张表及所有索引创建成功（依赖 T003, T005）

**检查点**: 数据库表已创建，ORM 模型可用 → 可以开始用户故事实施

---

## 阶段 3: 评分逻辑（US1 + US2 核心，纯函数，可无 DB 单元测试）

**目标**: 实现偏差等级判断、维度得分计算、综合评分函数，无任何 DB 依赖

**独立测试**: 给定 measured_value, standard_min, standard_max, ideal_value，验证 deviation_level、score、overall_score 计算正确

### 评分逻辑的测试 ⚠️ 先编写，确认失败后再实施

- [ ] T007 [P] [US1] 在 tests/unit/test_diagnosis_scorer.py 中编写 diagnosis_scorer 单元测试，覆盖：
  (a) 值在 [min, max] 内 → deviation_level=ok, score=100, direction=none；
  (b) 值超出 min/max 但在 1.5 倍半宽内 → deviation_level=slight, score 在 [60, 100) 线性插值；
  (c) 值超出 1.5 倍半宽 → deviation_level=significant, score 在 [0, 60) 线性插值；
  (d) direction 判断：measured > max → above；measured < min → below；其他 → none；
  (e) 综合评分 = 各维度得分简单平均；
  (f) 空维度列表时综合评分 = 0；
  (g) 半宽为零（min==max）时的边界处理（不抛除零异常）

### 评分逻辑的实施

- [ ] T008 [US1] 在 src/services/diagnosis_scorer.py 中实现：
  - `DeviationLevel` enum（ok/slight/significant）
  - `DeviationDirection` enum（above/below/none）
  - `DimensionScore` dataclass（dimension, measured_value, ideal_value, standard_min, standard_max, unit, score, deviation_level, deviation_direction）
  - `compute_dimension_score(measured, std_min, std_max, ideal, unit, dimension) -> DimensionScore`：实现线性插值评分，半宽=0 时降级处理
  - `compute_overall_score(dimension_scores: list[DimensionScore]) -> float`：等权平均，忽略无测量值的维度
  （依赖 T004，纯函数不依赖 DB）

**检查点**: T007 单元测试全部通过，评分逻辑可独立验证

---

## 阶段 4: LLM 改进建议生成（US1 + US2，可 mock 单元测试）

**目标**: 针对偏差维度生成自然语言改进建议，复用 LlmClient

**独立测试**: mock LlmClient.chat()，验证 prompt 包含维度名、测量值、理想值、偏差方向；达标维度不调用 LLM

### LLM advisor 的测试 ⚠️ 先编写，确认失败后再实施

- [ ] T009 [P] [US1] 在 tests/unit/test_diagnosis_llm_advisor.py 中编写 diagnosis_llm_advisor 单元测试，覆盖：
  (a) 偏差维度（slight/significant）调用 LlmClient.chat()，返回建议文本；
  (b) 达标维度（ok）不调用 LLM，improvement_advice=None；
  (c) prompt 中包含 dimension 名称、measured_value、ideal_value、deviation_direction（above/below）；
  (d) LlmClient 抛出 LlmError 时降级为默认模板文本（不抛异常给上层）；
  (e) tech_category 和 dimension 的中文名称映射在 prompt 中正确体现

### LLM advisor 的实施

- [ ] T010 [US1] 在 src/services/diagnosis_llm_advisor.py 中实现：
  - `generate_improvement_advice(dimension_score: DimensionScore, tech_category: str, llm_client: LlmClient) -> str | None`
    - deviation_level=ok → 返回 None（不调用 LLM）
    - 偏差维度：构造中文 prompt（含技术类别名、维度名、测量值、理想值、标准范围、偏差方向），调用 llm_client.chat()，temperature=0.3
    - LlmError 时返回 fallback 模板字符串（含偏差方向和数值差）
  - `DIMENSION_CN_NAMES: dict[str, str]`：维度中文名称映射
  - `ACTION_CN_NAMES: dict[str, str]`：技术类别中文名称映射（复用 advice_generator.py 中的映射）
  （依赖 T008）

**检查点**: T009 单元测试全部通过，LLM advisor 可独立 mock 测试

---

## 阶段 5: 诊断主服务与 API 路由（US1 MVP 核心）

**目标**: DiagnosisService 编排完整诊断流程；POST /api/v1/diagnosis 端点同步返回报告

**独立测试**: 提交一段 forehand_topspin 视频路径，POST /api/v1/diagnosis 返回包含 overall_score、dimensions[] 的完整报告；无标准时返回 404

### 诊断服务与路由的测试 ⚠️ 先编写，确认失败后再实施

- [ ] T011 [P] [US1] 在 tests/unit/test_diagnosis_scorer.py 中（或新建文件）补充 DiagnosisService 的单元级测试（mock pose_estimator + tech_extractor + llm_advisor + DB session），验证：
  (a) 无 active 标准时抛出 StandardNotFoundError；
  (b) 视频提取无有效动作时抛出 ExtractionFailedError；
  (c) 正常流程返回 DiagnosisReportData（含 overall_score, dimensions, strengths）

- [ ] T012 [P] [US1] 在 tests/contract/test_diagnosis_contract.py 中编写 POST /api/v1/diagnosis 契约测试：
  验证 200 响应包含 report_id(UUID), tech_category, standard_id, standard_version, overall_score(float 0-100), strengths(list[str]), dimensions[](每项含 dimension, measured_value, ideal_value, standard_min, standard_max, unit, score, deviation_level, deviation_direction, improvement_advice), created_at；
  验证 422 响应（tech_category 非法）；
  验证 404 响应结构（error, detail 字段，无标准时）

- [ ] T013 [P] [US1] 在 tests/integration/test_diagnosis_api.py 中编写 US1 集成测试（真实 DB + mock 视频处理）：
  给定 DB 中存在 forehand_topspin 的 active tech_standard，mock tech_extractor 返回固定维度值，
  调用 POST /api/v1/diagnosis，验证：返回 200，overall_score 在 [0, 100]，dimensions[] 非空，report 已持久化到 DB

### 诊断服务与路由的实施

- [ ] T014 [US1] 在 src/services/diagnosis_service.py 中实现 DiagnosisService：
  ```
  async def diagnose(
      session: AsyncSession,
      tech_category: str,
      video_path: str,
  ) -> DiagnosisReportData:
  ```
  流程：
  1. 查询 active TechStandard（tech_category + status=active），无则抛出 StandardNotFoundError
  2. 下载/本地化视频到临时目录（COS key 通过 cos_client 下载）
  3. 调用 pose_estimator.estimate_pose(video_path)，获取帧关键点
  4. 调用 tech_extractor 提取维度测量值（ExtractionResult），无有效动作则抛出 ExtractionFailedError
  5. 遍历 standard.points，调用 diagnosis_scorer.compute_dimension_score()
  6. 对偏差维度调用 diagnosis_llm_advisor.generate_improvement_advice()（run_in_executor 包装）
  7. 调用 diagnosis_scorer.compute_overall_score()
  8. 持久化 DiagnosisReport + DiagnosisDimensionResult，session.flush()
  9. 返回 DiagnosisReportData dataclass
  清理：finally 块删除临时文件
  （依赖 T005, T008, T010）

- [ ] T015 [US1] 在 src/api/routers/diagnosis.py 中实现 POST /api/v1/diagnosis 端点：
  - 请求模型 DiagnosisRequest（tech_category: str 必填，video_path: str 必填）
  - 响应模型 DiagnosisResponse（含所有报告字段）
  - 调用 DiagnosisService.diagnose()
  - 异常处理：StandardNotFoundError → 404；ExtractionFailedError → 400；其他 → 500
  - tech_category 校验：非法值返回 422（复用 ActionType enum 校验，参考 standards.py）
  （依赖 T014）

- [ ] T016 [US1] 在 src/api/main.py 中注册 diagnosis router，前缀 /api/v1（依赖 T015）

- [ ] T017 [US1] 在 src/services/diagnosis_service.py 中添加结构化日志：记录每次诊断的 tech_category, standard_id, overall_score, dimensions_count, deviations_count, llm_calls, elapsed_ms（依赖 T014）

**检查点**: POST /api/v1/diagnosis 可用，T011/T012/T013 测试全部通过，US1 MVP 可演示

---

## 阶段 6: 维度详细偏差展示（US2）

**目标**: 确认响应中每维度已包含完整偏差详情（measured/ideal/range/level/direction/advice），补充 US2 专项测试

**独立测试**: 给定已知测量值的请求，验证每维度的偏差等级与人工预期一致（SC-003 ≥ 90%）

### US2 的测试 ⚠️ 先编写，确认失败后再实施

- [ ] T018 [P] [US2] 在 tests/unit/test_diagnosis_scorer.py 中补充 US2 边界用例：
  (a) 在标准范围恰好边界时（measured == min 或 measured == max）→ deviation_level=ok；
  (b) measured 超出范围 1.5 倍半宽恰好边界时 → deviation_level=slight/significant 正确切换；
  (c) deviation_direction 字段在所有 level 中均正确设置

- [ ] T019 [P] [US2] 在 tests/integration/test_diagnosis_api.py 中补充 US2 集成测试：
  (a) 达标维度在 strengths[] 中出现，improvement_advice=null；
  (b) 偏差维度 improvement_advice 非空且包含偏差方向描述；
  (c) 给定全部维度达标的理想输入，overall_score=100；
  (d) 给定全部维度明显偏差，overall_score 较低（≤ 40）

### US2 的实施（通常无需新增代码，通过测试即验证完成）

- [ ] T020 [US2] 确认 DiagnosisResponse 的 dimensions[] 每项已包含 measured_value, ideal_value, standard_min, standard_max, unit, score, deviation_level, deviation_direction, improvement_advice；如响应序列化缺字段则更新 src/api/routers/diagnosis.py 中的响应模型（依赖 T015, T018）

**检查点**: US2 维度详情完整，T018/T019 测试通过

---

## 阶段 7: 教练视频验证（US3）

**目标**: 使用系统内已有教练视频验证诊断基准可信度（SC-001：评分 ≥ 80 分）

**独立测试**: 提交数据库中已有的 forehand_topspin 教练视频路径，验证系统返回完整报告且评分 ≥ 80

### US3 的测试 ⚠️ 先编写，确认失败后再实施

- [ ] T021 [P] [US3] 在 tests/integration/test_diagnosis_api.py 中编写 US3 集成测试：
  (a) 提交已有 forehand_topspin active 标准对应的教练视频路径，验证返回 200、报告格式完整；
  (b) 验证标准版本字段 standard_version 与 DB 中 active 版本一致；
  (c) 如 DB 中有真实教练视频 COS 路径（从 coach_video_classifications 或 analysis_tasks 查询），
      提交诊断，验证 overall_score ≥ 80（SC-001），此测试可标记 @pytest.mark.slow 可选运行

### US3 的实施（无需新增代码，主要为测试数据准备）

- [ ] T022 [US3] 确认 POST /api/v1/diagnosis 支持通过 video_path 传入 COS key（形如 "cos://bucket/key.mp4" 或直接 object key），DiagnosisService 中 cos_client.download 能正确处理；如需补充 COS key 解析逻辑则在 T014 基础上扩展（依赖 T015）

**检查点**: 教练视频诊断流程可用，T021 测试通过，SC-001 可验证

---

## 阶段 8: 错误处理与边界情况

**目标**: 无标准返回 404、视频无效返回 400、维度数据不足时的降级处理

### 错误处理的测试 ⚠️ 先编写，确认失败后再实施

- [ ] T023 [P] 在 tests/integration/test_diagnosis_api.py 中补充边界用例：
  (a) tech_category 对应无 active 标准 → 返回 404，body 含 error=standard_not_found；
  (b) tech_category 非法值 → 422；
  (c) video_path 指向不存在文件（mock）→ 400 或 500 含明确 error 字段；
  (d) 视频提取出 0 个有效动作 → 400，body 含 error=extraction_failed

### 错误处理的实施

- [ ] T024 在 src/services/diagnosis_service.py 中确认自定义异常类 StandardNotFoundError 和 ExtractionFailedError 已定义（可在文件顶部定义），并在路由层正确映射为 HTTP 404/400（依赖 T015）

**检查点**: 所有错误路径有明确的 HTTP 状态和错误结构，T023 通过

---

## 阶段 9: 收尾与横切关注点

**目的**: 验证、日志、性能确认、手工 curl 验证

- [ ] T025 [P] 手工 curl 验证：启动 `uvicorn src.api.main:app --reload`，执行：
  (1) POST /api/v1/diagnosis（有效请求）→ 200 含完整报告；
  (2) POST /api/v1/diagnosis（无标准类别）→ 404；
  (3) POST /api/v1/diagnosis（tech_category=invalid）→ 422；
  记录端到端响应时间，确认 ≤ 60 秒（SC-002）

- [ ] T026 [P] 确认 DiagnosisService 中 LLM 调用通过 asyncio.get_event_loop().run_in_executor(None, ...) 包装（或等效方式），不阻塞 FastAPI 事件循环；如未实现则在 T014 基础上修正（依赖 T014）

- [ ] T027 [P] 确认 diagnosis_service.py 中 finally 块正确清理临时视频文件；补充 tests/unit/test_diagnosis_service_cleanup.py 测试（mock os.unlink，验证临时文件总是被删除，包括异常路径）（依赖 T014）

- [ ] T028 [P] 在 tests/contract/test_diagnosis_contract.py 中确认所有契约测试覆盖规范中要求的字段（FR-006）：综合评分、各维度测量值与标准对比、优点列表（strengths）、改进建议列表（improvement_advice）；补充缺失字段的契约断言

---

## 依赖关系与执行顺序

### 阶段依赖关系

- **设置（阶段 1）**: 无依赖，立即开始
- **基础（阶段 2）**: 依赖阶段 1 → 阻塞所有用户故事
- **评分逻辑（阶段 3）**: 依赖阶段 2（ORM 模型导入），但评分函数为纯函数，可与 T006/T007 并行
- **LLM advisor（阶段 4）**: 依赖阶段 3（DimensionScore 类型）
- **主服务与路由（阶段 5）**: 依赖阶段 2 + 3 + 4 全部完成
- **US2（阶段 6）**: 依赖阶段 5 完成，大部分为补充测试
- **US3（阶段 7）**: 依赖阶段 5 完成
- **错误处理（阶段 8）**: 依赖阶段 5 完成
- **收尾（阶段 9）**: 依赖所有阶段完成

### 用户故事内部顺序

- TDD：每个阶段的测试任务（[P]）必须先于实施任务完成并确认失败
- ORM 模型 → 评分服务 → LLM advisor → 主服务 → API 路由 → 注册

### 并行机会

- T007, T009, T011, T012, T013 可并行（不同文件，均为测试编写）
- T018, T019, T021, T023 可并行（补充测试，不同故事）
- T025, T026, T027, T028 可并行（收尾验收）
- 阶段 2 完成后：阶段 3 和阶段 4 的测试编写可立即并行启动

---

## 实施策略

### MVP（US1）

1. 完成阶段 1: 设置
2. 完成阶段 2: 基础（T002~T006）
3. 完成阶段 3: 评分逻辑（T007~T008）
4. 完成阶段 4: LLM advisor（T009~T010）
5. 完成阶段 5: 主服务与路由（T011~T017）
6. **停止并验证**: POST /api/v1/diagnosis 完整流程可用
7. MVP 可演示

### 增量交付

1. MVP（US1）→ 可演示核心价值
2. 添加 US2 详细偏差展示（T018~T020）→ 维度级别可见
3. 添加 US3 教练视频验证（T021~T022）→ 基准可信度验证
4. 补充错误处理（T023~T024）→ 生产就绪
5. 收尾（T025~T028）→ 最终交付

---

## 注意事项

- [P] 任务 = 不同文件，无依赖关系，可并行
- 每个 [US] 标签确保任务与用户故事可追溯
- TDD：测试任务必须在对应实施任务之前完成并确认失败
- LLM 调用是同步的，通过 run_in_executor 包装，确保不阻塞事件循环
- ActionType 枚举沿用 src/models/expert_tech_point.py 中的定义（12 个类别）
- 评分插值系数（得分区间 100/60/0）定义为 diagnosis_scorer.py 顶部常量，可调参
- 临时文件务必在 finally 块清理，避免磁盘泄漏
- US4（历史查询）明确不在本功能范围内，tasks.md 不含相关任务
