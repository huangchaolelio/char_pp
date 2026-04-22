# 任务: 多教练知识库提炼与技术校准

**输入**: 来自 `/specs/006-multi-coach-kb/` 的设计文档
**前置条件**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅

**组织结构**: 任务按用户故事分组，每个故事可独立实施和测试。

## 格式: `[ID] [P?] [Story] 描述`
- **[P]**: 可以并行运行（不同文件，无依赖关系）
- **[Story]**: 此任务属于哪个用户故事

---

## 阶段 1: 设置

**目的**: 项目基础设施已就绪，此功能无需额外框架配置。

- [ ] T001 创建 `src/models/coach.py` 骨架文件（仅 import + 空 Coach 类占位）
- [ ] T002 创建 `src/api/routers/coaches.py` 骨架文件（仅 router 定义）
- [ ] T003 [P] 创建 `src/api/routers/calibration.py` 骨架文件（仅 router 定义）
- [ ] T004 [P] 创建 `src/api/schemas/coach.py` 骨架文件（仅 import）

---

## 阶段 2: 基础（阻塞前置条件）

**目的**: DB 迁移和 Coach ORM 模型，所有用户故事均依赖此基础。

**⚠️ 关键**: 在此阶段完成之前，无法开始任何用户故事工作。

- [ ] T005 在 `src/db/migrations/versions/0007_multi_coach_kb.py` 编写 Alembic 迁移：
  - CREATE TABLE `coaches`（id UUID PK, name VARCHAR(255) UNIQUE NOT NULL, bio TEXT, is_active BOOLEAN NOT NULL DEFAULT true, created_at TIMESTAMPTZ NOT NULL DEFAULT now()）
  - CREATE INDEX `ix_coaches_name`（UNIQUE）
  - CREATE INDEX `ix_coaches_is_active`
  - ALTER TABLE `analysis_tasks` ADD COLUMN `coach_id UUID REFERENCES coaches(id) ON DELETE SET NULL`
  - CREATE INDEX `ix_analysis_tasks_coach_id`
- [ ] T006 在 `src/models/coach.py` 实现 Coach ORM 模型（SQLAlchemy 2.0 mapped_column 风格），字段：id, name, bio, is_active, created_at，relationship 到 AnalysisTask
- [ ] T007 在 `src/models/analysis_task.py` 添加 `coach_id` 可空外键字段和 `coach` relationship
- [ ] T008 在 `src/models/__init__.py` 导出 Coach 模型
- [ ] T009 执行迁移并验证：运行 `alembic upgrade head` 确认 coaches 表和 analysis_tasks.coach_id 字段创建成功

**检查点**: 数据库 coaches 表就绪，ORM 模型可用 → 可以开始并行实施用户故事

---

## 阶段 3: 用户故事 1 — 识别并记录教练身份 (P1) 🎯 MVP

**目标**: 管理员能够创建教练、为任务指定教练，知识库条目通过 JOIN 携带教练标识

**独立测试**: 创建教练 → 为任务指定教练 → 查询 teaching_tips 时返回 coach_id/coach_name 字段；历史无教练任务数据正常可用

### 用户故事 1 的合约测试（TDD 先行）

- [ ] T010 [US1] 在 `tests/contract/test_coaches_api.py` 编写 Coach CRUD 合约测试：
  - `POST /coaches` 成功创建（201）
  - `POST /coaches` 重复姓名返回 409
  - `GET /coaches` 返回活跃教练列表
  - `GET /coaches/{id}` 返回单个教练 / 404
  - `PATCH /coaches/{id}` 修改姓名
  - `DELETE /coaches/{id}` 软删除（204）/ 404
  - `PATCH /tasks/{task_id}/coach` 关联教练（200）/ 422（教练已软删除）
  - 在实现前运行测试确认失败

### 用户故事 1 的实现

- [ ] T011 [US1] 在 `src/api/schemas/coach.py` 实现 Pydantic schemas：CoachCreate, CoachUpdate, CoachResponse, TaskCoachUpdate, TaskCoachResponse
- [ ] T012 [US1] 在 `src/api/routers/coaches.py` 实现 Coach CRUD 端点（依赖 T006, T011）：
  - `POST /coaches`（201，姓名冲突返回 409）
  - `GET /coaches`（支持 `include_inactive` 查询参数）
  - `GET /coaches/{coach_id}`（404 处理）
  - `PATCH /coaches/{coach_id}`（姓名冲突 409，404 处理）
  - `DELETE /coaches/{coach_id}`（软删除 204，已删除返回 409，404 处理）
- [ ] T013 [US1] 在 `src/api/routers/coaches.py` 新增 `PATCH /tasks/{task_id}/coach` 端点：关联/解除教练，验证教练 is_active=true（422 若已软删除），404 若任务/教练不存在
- [ ] T014 [US1] 在 `src/api/main.py` 注册 coaches router（prefix="/api/v1"）
- [ ] T015 [US1] 添加结构化日志：Coach 创建/修改/软删除/任务关联操作均记录 INFO 日志

**检查点**: `POST /coaches`、`GET /coaches`、`PATCH /tasks/{id}/coach` 可用，合约测试全部通过

---

## 阶段 4: 用户故事 2 — 按教练查询技术要点和教学建议 (P2)

**目标**: 现有查询接口支持 `coach_id` 过滤参数，返回结果携带教练信息字段

**独立测试**: 带 coach_id 参数查询 `/teaching-tips` 只返回该教练数据；不传参数返回全部（含无教练历史数据）；不存在 coach_id 返回空列表不报错

### 用户故事 2 的合约测试（TDD 先行）

- [ ] T016 [US2] 在 `tests/contract/test_teaching_tips_api.py` 扩展已有合约测试，新增：
  - `GET /teaching-tips?coach_id={id}` 只返回该教练数据
  - `GET /teaching-tips`（无参数）返回全部数据，每条含 `coach_id` 和 `coach_name` 字段（历史数据为 null）
  - `GET /teaching-tips?coach_id={不存在的uuid}` 返回空列表（200）
  - 在实现前运行测试确认失败

### 用户故事 2 的实现

- [ ] T017 [US2] 在 `src/api/schemas/teaching_tip.py` 的 TeachingTip 响应 schema 中新增 `coach_id: Optional[UUID]` 和 `coach_name: Optional[str]` 字段
- [ ] T018 [US2] 在 `src/api/routers/teaching_tips.py` 的 `GET /teaching-tips` 端点新增可选 `coach_id` 查询参数，SQL 查询通过 JOIN（teaching_tips → analysis_tasks → coaches）过滤并返回教练信息

**检查点**: 按教练过滤查询正常工作，历史无教练数据返回 coach_id=null，合约测试通过

---

## 阶段 5: 用户故事 3 — 同一技术的多教练校准视图 (P2)

**目标**: 校准接口传入 action_type + dimension，返回各教练的参数对比；支持教学建议（文本型）的多教练分组对比

**独立测试**: 调用 `/calibration/tech-points?action_type=forehand_topspin&dimension=elbow_angle` 返回包含两位教练数据的对比结构；只有一位教练有数据时也能正常返回；无数据时返回空 coaches 列表

### 用户故事 3 的合约测试（TDD 先行）

- [ ] T019 [US3] 在 `tests/contract/test_calibration_api.py` 编写校准接口合约测试：
  - `GET /calibration/tech-points?action_type=X&dimension=Y` 两位教练数据时返回结构化对比
  - `GET /calibration/tech-points?action_type=X&dimension=Y` 仅一位教练时返回单条不报错
  - `GET /calibration/tech-points?action_type=X&dimension=Y` 无数据时返回 `{"coaches": []}`
  - `GET /calibration/teaching-tips?action_type=X&tech_phase=Y` 按教练分组返回
  - 缺少必填参数返回 422
  - 在实现前运行测试确认失败

### 用户故事 3 的实现

- [ ] T020 [US3] 在 `src/api/schemas/coach.py` 新增校准视图 schemas：CoachTechPointEntry, TechPointCalibrationView, CoachTipGroup, TeachingTipCalibrationView
- [ ] T021 [US3] 在 `src/api/routers/calibration.py` 实现 `GET /calibration/tech-points`（依赖 T020）：
  - action_type + dimension 必填（422 if missing）
  - 查询 expert_tech_points JOIN analysis_tasks JOIN coaches，按教练聚合（取 AVG 或最新一条参数，多条时 source_count 累加）
  - 过滤 is_active coaches（软删除教练的历史数据也纳入，但标注 is_active=false）
- [ ] T022 [US3] 在 `src/api/routers/calibration.py` 实现 `GET /calibration/teaching-tips`（依赖 T020）：
  - action_type + tech_phase 必填（422 if missing）
  - 查询 teaching_tips JOIN analysis_tasks JOIN coaches，按教练分组
- [ ] T023 [US3] 在 `src/api/main.py` 注册 calibration router（prefix="/api/v1"）

**检查点**: 校准接口可用，返回结构化对比数据，合约测试通过

---

## 阶段 6: 用户故事 4 — 基于指定教练的运动员建议生成 (P3)

**目标**: 运动员分析任务支持可选 coach_id 参数，偏差检测使用该教练的技术要点范围

**独立测试**: 提交运动员任务时传 coach_id，偏差报告中使用的 param_min/ideal/max 与所选教练的数据一致；不传 coach_id 时行为与现有 Feature-005 相同（向后兼容）

### 用户故事 4 的合约测试（TDD 先行）

- [ ] T024 [US4] 在 `tests/contract/test_api_contracts.py` 或新建文件扩展，新增：
  - `POST /tasks`（athlete_video 类型）接受可选 `coach_id` 字段
  - 任务详情 `GET /tasks/{id}` 返回 `coach_id` 字段
  - 在实现前运行测试确认失败

### 用户故事 4 的实现

- [ ] T025 [US4] 在 `src/api/schemas/task.py` 的任务创建/响应 schema 中新增可选 `coach_id: Optional[UUID]` 字段
- [ ] T026 [US4] 在 `src/api/routers/tasks.py` 的 `POST /tasks` 处理 `coach_id` 字段：存入 analysis_tasks.coach_id（验证教练存在且 is_active=true，否则 422）
- [ ] T027 [US4] 在 `src/services/deviation_analyzer.py`（或 `src/workers/athlete_video_task.py`）修改偏差分析逻辑：若任务有 coach_id，则只查询该教练的 expert_tech_points 作为基准；若无 coach_id，保持现有逻辑（使用 active KB 全局数据）

**检查点**: 指定教练的运动员分析任务使用对应教练的技术标准，无教练时行为不变，合约测试通过

---

## 阶段 7: 收尾与向后兼容验证

**目标**: 确保所有现有测试通过（SC-005），补充集成测试，清理

- [ ] T028 [P] 在 `tests/integration/test_coach_kb_pipeline.py` 编写集成测试：
  - 创建 Coach → 创建 ExpertVideoTask 并关联 → 提炼后查询 teaching_tips 携带 coach_name
  - 历史无 coach_id 的任务数据在新接口下正常可用（不报错，coach_name=null）
  - 软删除 Coach 后历史任务数据不受影响
- [ ] T029 [P] 运行现有全量测试套件，确认无回归：`/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/ -x --tb=short`
- [ ] T030 修复 T029 中发现的任何回归问题
- [ ] T031 在 `specs/006-multi-coach-kb/plan.md` 的章程检查节补充"阶段 1 设计后重检"结论（全部通过）

---

## 依赖关系图

```
T001-T004 (设置) → 可并行
     ↓
T005-T009 (基础: 迁移 + ORM)
     ↓
┌────────────────────────────────────┐
│  T010-T015 (US1: Coach CRUD)       │  ← 必须最先完成（P1 MVP）
│  阶段 3 完成后可并行启动阶段 4/5/6  │
└────────────────────────────────────┘
     ↓ (US1 完成后可并行)
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ T016-T018    │  │ T019-T023    │  │ T024-T027    │
│ (US2: 过滤)  │  │ (US3: 校准)  │  │ (US4: 分析)  │
└──────────────┘  └──────────────┘  └──────────────┘
          ↓              ↓               ↓
     T028-T031 (收尾: 集成测试 + 回归验证)
```

## 并行执行机会

- **阶段 1**: T001-T004 全部可并行
- **阶段 2**: T005（迁移）必须先完成 → T006/T007/T008 可并行 → T009 最后
- **阶段 3 完成后**: 阶段 4、5、6 可并行启动（各 US 无相互依赖）
- **收尾**: T028（集成测试编写）和 T029（全量测试）可并行

## 实施策略

**MVP 范围（阶段 1-3）**: Coach CRUD + 任务教练关联，可独立演示和交付
- 演示方式：`POST /coaches` 创建教练 → `PATCH /tasks/{id}/coach` 关联 → `GET /teaching-tips?coach_id={}` 验证过滤

**增量交付顺序**:
1. 阶段 2（基础迁移）→ 阶段 3（MVP）→ 演示 US1
2. 阶段 4（US2 过滤）→ 演示 US2
3. 阶段 5（US3 校准）→ 演示 US3
4. 阶段 6（US4 分析集成）→ 演示 US4
5. 阶段 7（收尾验证）→ 合并准备

## 统计

| 指标 | 数值 |
|------|------|
| 总任务数 | 31 |
| 阶段 1（设置） | 4 |
| 阶段 2（基础） | 5 |
| 阶段 3 US1（P1 MVP） | 6 |
| 阶段 4 US2 | 3 |
| 阶段 5 US3 | 5 |
| 阶段 6 US4 | 4 |
| 阶段 7（收尾） | 4 |
| 可并行任务数 | 15 |
| 合约测试任务 | 4（T010, T016, T019, T024） |
| 集成测试任务 | 1（T028） |
