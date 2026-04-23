# 任务: 构建单项技术标准知识库

**输入**: 来自 `/specs/010-build-technique-standard/` 的设计文档
**前置条件**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅

**测试**: 规范要求 TDD（章程原则 II），包含契约测试、集成测试和单元测试任务。

**组织结构**: 任务按用户故事分组，每个故事独立实施和测试。

## 格式: `[ID] [P?] [Story] 描述`
- **[P]**: 可以并行运行（不同文件，无依赖关系）
- **[Story]**: 此任务属于哪个用户故事（US1, US2, US3, US4）
- 描述中包含确切的文件路径

---

## 阶段 1: 设置

**目的**: 确认项目结构，无需新增顶层目录（复用现有 src/, tests/ 结构）

- [x] T001 确认 src/models/, src/services/, src/api/routers/, tests/unit/, tests/integration/, tests/contract/ 目录存在且无需新建

---

## 阶段 2: 基础（阻塞前置条件）

**目的**: 新表迁移和 ORM 模型，所有用户故事的共同依赖

**⚠️ 关键**: 在此阶段完成之前，无法开始任何用户故事工作

- [x] T002 在 src/db/migrations/versions/0010_tech_standard.py 中创建 Alembic 迁移，新建 tech_standards 表（字段：id BIGSERIAL PK, tech_category VARCHAR(64) NOT NULL, version INTEGER NOT NULL DEFAULT 1, status VARCHAR(16) NOT NULL DEFAULT 'active', source_quality VARCHAR(16) NOT NULL, coach_count INTEGER NOT NULL, point_count INTEGER NOT NULL, built_at TIMESTAMPTZ NOT NULL DEFAULT now()；唯一约束 (tech_category, version)；索引 idx_ts_tech_status ON (tech_category, status), idx_ts_tech_version ON (tech_category, version DESC)）
- [x] T003 在同一迁移文件 src/db/migrations/versions/0010_tech_standard.py 中新建 tech_standard_points 表（字段：id BIGSERIAL PK, standard_id BIGINT FK→tech_standards.id NOT NULL, dimension VARCHAR(128) NOT NULL, ideal FLOAT NOT NULL, min FLOAT NOT NULL, max FLOAT NOT NULL, unit VARCHAR(32), sample_count INTEGER NOT NULL, coach_count INTEGER NOT NULL；唯一约束 (standard_id, dimension)；索引 idx_tsp_standard ON (standard_id)）（依赖 T002）
- [x] T004 在 src/models/tech_standard.py 中实现 TechStandard SQLAlchemy ORM 模型（对应 tech_standards 表，包含关系 points: List[TechStandardPoint]，status 枚举 active/archived，source_quality 枚举 multi_source/single_source）
- [x] T005 在 src/models/tech_standard.py 中实现 TechStandardPoint SQLAlchemy ORM 模型（对应 tech_standard_points 表，包含 FK 关系 standard: TechStandard）（依赖 T004）
- [x] T006 在 src/models/__init__.py 中导出 TechStandard 和 TechStandardPoint（依赖 T005）
- [x] T007 运行迁移验证：执行 alembic upgrade head 并确认两张表和所有索引创建成功（依赖 T003, T006）

**检查点**: 数据库表已创建，ORM 模型可用 → 可以开始用户故事实施

---

## 阶段 3: 用户故事 1 - 生成单项技术综合标准 (优先级: P1) 🎯 MVP

**目标**: 针对指定技术类别，从 ExpertTechPoint 聚合（中位数+P25/P75），生成并持久化技术标准版本

**独立测试**: 调用 POST /api/v1/standards/build {"tech_category": "forehand_topspin"}，任务完成后 GET /api/v1/standards/forehand_topspin 返回包含各维度 ideal/min/max 的标准记录

### 用户故事 1 的测试 ⚠️ 先编写，确认失败后再实施

- [x] T008 [P] [US1] 在 tests/unit/test_tech_standard_builder.py 中编写 TechStandardBuilder 单元测试，覆盖：(a) 中位数+P25/P75 聚合计算正确性（给定 5 个值，验证 ideal=中位数、min=P25、max=P75）；(b) conflict_flag=true 的点被排除；(c) confidence<0.7 的点被排除；(d) 教练数<2 时返回 source_quality=single_source；(e) 教练数=0 时触发跳过逻辑
- [x] T009 [P] [US1] 在 tests/contract/test_standards_contract.py 中编写 POST /api/v1/standards/build（单技术）的契约测试，验证响应结构包含 task_id、mode、tech_category、status 字段
- [x] T010 [P] [US1] 在 tests/integration/test_tech_standard_api.py 中编写 US1 集成测试：给定 DB 中存在来自 3 位教练的 forehand_topspin ExpertTechPoint（confidence≥0.7，conflict_flag=false），触发构建，查询结果包含正确维度的 ideal/min/max

### 用户故事 1 的实施

- [x] T011 [US1] 在 src/services/tech_standard_builder.py 中实现 TechStandardBuilder.build_standard(tech_category: str, session) -> BuildResult：从 expert_tech_points 查询（action_type=tech_category, extraction_confidence≥0.7, conflict_flag=False），按 dimension 分组，用 numpy.median/percentile 计算 ideal/min/max，统计 coach_count（去重 coach_name），生成 TechStandard + TechStandardPoints 并持久化，版本递增时将旧 active 版本改为 archived（依赖 T006）
- [x] T012 [US1] 在 src/services/tech_standard_builder.py 中实现 TechStandardBuilder.build_all(session) -> BatchBuildResult：遍历 21 类技术，逐一调用 build_standard，收集 success/skipped/failed 结果，tech_category 定义从 src/services/tech_classifier.py 的 TECH_CATEGORIES 复用（依赖 T011）
- [x] T013 [US1] 在 src/api/routers/standards.py 中实现 POST /api/v1/standards/build 端点：接收可选 tech_category，调用 build_standard 或 build_all，以同步方式返回 202 响应（task_id 可简单用 UUID，结果直接附在响应中或存临时 dict）；验证 tech_category 合法性（422 if invalid）（依赖 T012）
- [x] T014 [US1] 在 src/api/main.py（或等效路由注册文件）中注册 standards router，前缀 /api/v1/standards（依赖 T013）
- [x] T015 [US1] 在 src/services/tech_standard_builder.py 中添加结构化日志：记录每次构建的 tech_category、version、coach_count、point_count、dimension_count 及跳过原因（依赖 T013）

**检查点**: POST /api/v1/standards/build 可触发构建，T008/T009/T010 测试全部通过

---

## 阶段 4: 用户故事 2 - 查询技术标准 (优先级: P1)

**目标**: 按技术类别查询最新 active 标准，返回所有维度的标准参数及元数据

**独立测试**: GET /api/v1/standards/forehand_topspin 返回含 dimensions 列表的完整标准；GET /api/v1/standards/nonexistent_tech 返回 404；GET /api/v1/standards 返回摘要列表

### 用户故事 2 的测试 ⚠️ 先编写，确认失败后再实施

- [x] T016 [P] [US2] 在 tests/contract/test_standards_contract.py 中编写 GET /api/v1/standards/{tech_category} 契约测试：验证 200 响应包含 tech_category, standard_id, version, source_quality, coach_count, point_count, built_at, dimensions[]（每项含 dimension, ideal, min, max, unit, sample_count, coach_count）；验证 404 响应包含 error, detail 字段
- [x] T017 [P] [US2] 在 tests/contract/test_standards_contract.py 中编写 GET /api/v1/standards 契约测试：验证响应包含 standards[], total, missing_categories[] 字段
- [x] T018 [P] [US2] 在 tests/integration/test_tech_standard_api.py 中编写 US2 集成测试：(a) 已有 active 标准时查询返回正确数据；(b) 无标准时返回 404；(c) 标准更新后旧版本 archived、新版本 active

### 用户故事 2 的实施

- [x] T019 [US2] 在 src/api/routers/standards.py 中实现 GET /api/v1/standards/{tech_category} 端点：查询 tech_standards 表 status=active + tech_category 匹配，join tech_standard_points，返回完整标准结构；无记录时返回 404 + error/detail（依赖 T014）
- [x] T020 [US2] 在 src/api/routers/standards.py 中实现 GET /api/v1/standards 端点：查询所有 active 标准（含 dimension_count 统计），计算 missing_categories（21 类 - 已有 active 的类别），支持可选 source_quality 过滤参数（依赖 T019）

**检查点**: 查询接口全部可用，T016/T017/T018 测试通过；US1 + US2 共同构成完整 MVP

---

## 阶段 5: 用户故事 3 - 批量构建所有技术的标准 (优先级: P2)

**目标**: 一次性触发全量构建，返回各技术构建结果摘要（成功/跳过/失败）

**独立测试**: POST /api/v1/standards/build {}（省略 tech_category）触发全量构建，响应中 summary.success_count + summary.skipped_count + summary.failed_count = 21

### 用户故事 3 的测试 ⚠️ 先编写，确认失败后再实施

- [x] T021 [P] [US3] 在 tests/integration/test_tech_standard_api.py 中编写 US3 集成测试：触发全量构建，验证 results 数组包含 21 条目（或实际技术类别数），有数据的技术 result=success，教练数=0 的技术 result=skipped

### 用户故事 3 的实施

- [x] T022 [US3] 在 src/api/routers/standards.py 中扩展 POST /api/v1/standards/build 端点：当请求体为空或 tech_category 未提供时，调用 build_all，响应 results[] 包含每个技术的 result/reason/standard_id/version/dimension_count/coach_count，以及 summary（success_count/skipped_count/failed_count）（依赖 T012, T014）

**检查点**: 全量构建端点可用，T021 测试通过

---

## 阶段 6: 用户故事 4 - 标准置信度与参与教练数可见 (优先级: P3)

**目标**: 查询标准时返回 source_quality（multi_source/single_source）字段及各维度的 coach_count

**独立测试**: 查询由 5 位教练数据构建的标准，响应 source_quality=multi_source，coach_count=5；由 1 位教练构建的标准，source_quality=single_source

### 用户故事 4 的测试 ⚠️ 先编写，确认失败后再实施

- [x] T023 [P] [US4] 在 tests/unit/test_tech_standard_builder.py 中补充单元测试：验证 coach_count=5 时 source_quality=multi_source，coach_count=1 时 source_quality=single_source；验证 TechStandardPoint.coach_count 字段值与参与该维度的不同教练数一致

### 用户故事 4 的实施

- [x] T024 [US4] 确认 src/services/tech_standard_builder.py 中 source_quality 计算逻辑已在 T011 中实现（coach_count≥2 → multi_source，=1 → single_source）；确认每个 TechStandardPoint 的 coach_count 字段已在 T011 中按维度统计不同教练数；如未实现则补充
- [x] T025 [US4] 确认 GET /api/v1/standards/{tech_category} 响应的 dimensions[] 每项中已包含 coach_count 字段；如响应序列化未包含则更新 src/api/routers/standards.py 中的响应模型（依赖 T019, T024）

**检查点**: source_quality 和 per-dimension coach_count 在查询响应中正确体现，T023 通过

---

## 阶段 7: 收尾与横切关注点

**目的**: 验证、错误处理补全、quickstart 手工验证

- [x] T026 [P] 在合约文档中记录 GET /api/v1/standards/build/{task_id} N/A（POST /build 采用同步实现，无需轮询）（如阶段 3 中采用异步方式则实现任务状态查询；如采用同步则记录 N/A 并确认契约文档已更新）
- [x] T027 [P] 在 src/services/tech_standard_builder.py 中补充错误处理：构建过程中单个维度计算失败不影响其他维度：构建过程中单个维度计算失败不影响其他维度；整个技术构建失败时记录 error 日志并在批量结果中标记 result=failed
- [x] T028 按照 specs/010-build-technique-standard/quickstart.md 手工执行 4 条 curl 命令，验证全部返回预期响应（需启动 API 服务：`uvicorn src.api.main:app --reload`）

---

## 依赖关系与执行顺序

### 阶段依赖关系

- **设置（阶段 1）**: 无依赖，立即开始
- **基础（阶段 2）**: 依赖阶段 1 → 阻塞所有用户故事
- **US1（阶段 3）**: 依赖基础完成
- **US2（阶段 4）**: 依赖基础完成，可与 US1 并行（但 US2 查询端点依赖 US1 构建能力验证）
- **US3（阶段 5）**: 依赖基础完成，US3 的 build_all 逻辑依赖 US1 中的 build_standard
- **US4（阶段 6）**: 依赖 US1（source_quality 在构建时写入），可在 US1 实施后立即验证
- **收尾（阶段 7）**: 依赖所有用户故事完成

### 用户故事内部顺序

- 测试先于实现（TDD：先写测试，确认失败，再实现）
- ORM 模型 → 构建服务 → API 端点 → 日志

### 并行机会

- T008, T009, T010 可并行（不同测试文件）
- T016, T017, T018 可并行
- T021, T023 可并行
- T026, T027, T028 可并行
- 基础阶段完成后：US1 与 US2 测试编写可并行启动

---

## 并行示例：用户故事 1

```bash
# 同时启动 US1 的三个测试文件编写：
任务 T008: tests/unit/test_tech_standard_builder.py（聚合逻辑单元测试）
任务 T009: tests/contract/test_standards_contract.py（POST /build 契约测试）
任务 T010: tests/integration/test_tech_standard_api.py（US1 集成测试）

# 测试失败确认后，启动实现：
任务 T011 → T012 → T013 → T014 → T015（有序）
```

---

## 实施策略

### MVP（仅 US1 + US2）

1. 完成阶段 1: 设置
2. 完成阶段 2: 基础（T002~T007）
3. 完成阶段 3: US1 构建能力（T008~T015）
4. 完成阶段 4: US2 查询能力（T016~T020）
5. **停止并验证**: POST /build + GET /{tech_category} 完整流程可用
6. MVP 可演示

### 增量交付

1. MVP（US1+US2）→ 可演示核心价值
2. 添加 US3（批量构建）→ 运营效率提升
3. 添加 US4（可信度字段）→ 诊断质量增强
4. 收尾（错误处理+quickstart 验证）

---

## 注意事项

- [P] 任务 = 不同文件，无依赖关系，可并行
- 每个 [US] 标签确保任务与用户故事可追溯
- TDD：T008/T009/T010 必须在 T011~T015 之前完成并确认失败
- 聚合依赖 numpy.percentile：安装前确认 `uv run python -c "import numpy"` 成功
- 21 类技术 ID 从 src/services/tech_classifier.py 的 TECH_CATEGORIES 复用，避免重复定义
- 版本化：同一 tech_category 构建新版本时，先 UPDATE 旧 active → archived，再 INSERT 新 active（建议在事务中执行）
