# 任务: 全量任务查询接口

**输入**: 来自 `/specs/012-task-query-all/` 的设计文档
**前置条件**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/ ✓, quickstart.md ✓

**测试**: 规范要求 SC-003（参数校验 100% 覆盖），包含契约测试和集成测试任务。

**组织结构**: 任务按用户故事分组，每个故事可独立实施和测试。

## 格式: `[ID] [P?] [Story] 描述`
- **[P]**: 可以并行运行（不同文件，无依赖关系）
- **[Story]**: 此任务属于哪个用户故事（US1, US2, US3）
- 描述中包含确切文件路径

---

## 阶段 1: 设置（共享基础设施）

**目的**: 本功能为纯后端扩展，无需新建项目结构；仅需确认测试目录存在。

- [x] T001 确认 tests/contract/ 和 tests/integration/ 目录存在，如不存在则创建并添加 `__init__.py`

---

## 阶段 2: 基础（阻塞性前置条件）

**目的**: 新增 Pydantic Schema（TaskListItemResponse、TaskListResponse、TaskSummary）以及扩展 TaskStatusResponse，这是所有用户故事端点的共同依赖。

**⚠️ 关键**: 在此阶段完成之前，无法开始任何用户故事的端点实现。

- [x] T002 [P] 在 src/api/schemas/task.py 中新增 `TaskSummary` dataclass（字段：tech_point_count, has_transcript, semantic_segment_count, motion_analysis_count, deviation_count, advice_count，全部 int/bool，默认 0/False）
- [x] T003 [P] 在 src/api/schemas/task.py 中新增 `TaskListItemResponse`（字段见 data-model.md：task_id, task_type, status, video_filename, video_storage_uri, video_duration_seconds, progress_pct, error_message, knowledge_base_version, coach_id, coach_name, created_at, started_at, completed_at）
- [x] T004 在 src/api/schemas/task.py 中新增 `TaskListResponse`（字段：items: list[TaskListItemResponse], total: int, page: int, page_size: int, total_pages: int）
- [x] T005 在 src/api/schemas/task.py 中扩展现有 `TaskStatusResponse`，追加可选字段 `summary: TaskSummary | None = None`

**检查点**: Schema 层就绪，可开始端点实现和关联统计查询逻辑

---

## 阶段 3: 用户故事 1 - 管理员查看全部任务列表 (优先级: P1) 🎯 MVP

**目标**: 新增 `GET /api/v1/tasks` 列表端点，支持分页、返回基础任务字段 + 教练姓名。

**独立测试**: `curl "http://localhost:8080/api/v1/tasks?page=1&page_size=5"` 返回 items 列表和分页元数据；空表返回 `{"items":[],"total":0,...}`。

### 用户故事 1 的契约测试

> **先编写测试，确保在实施前失败**

- [x] T006 [P] [US1] 在 tests/contract/test_task_list_api.py 中为 `GET /tasks` 编写契约测试：验证响应包含 items/total/page/page_size/total_pages 字段，items 中每条记录含 task_id/task_type/status/video_filename/video_storage_uri/coach_name 等字段
- [x] T007 [P] [US1] 在 tests/contract/test_task_list_api.py 中为空结果场景编写契约测试：无任务时 total=0, items=[], total_pages=0

### 用户故事 1 的实现

- [x] T008 [US1] 在 src/api/routers/tasks.py 中新增 `GET /tasks` 端点函数 `list_tasks`（query params: page=1, page_size=20, sort_by="created_at", order="desc"；仅基础分页，暂不含筛选）
- [x] T009 [US1] 在 `list_tasks` 中实现 SQLAlchemy 查询：SELECT analysis_tasks LEFT JOIN coaches，WHERE deleted_at IS NULL，ORDER BY created_at DESC，OFFSET/LIMIT 分页；将结果映射到 TaskListItemResponse
- [x] T010 [US1] 在 `list_tasks` 中实现 COUNT 子查询获取 total，计算 total_pages，构造并返回 TaskListResponse；page_size 超过 200 时截断为 200
- [x] T011 [US1] 在 `list_tasks` 中添加参数校验错误处理：page < 1 或 page_size < 1 时返回 400 with detail 说明；添加结构化日志（任务总数、耗时）
- [x] T012 [US1] 在 tests/integration/test_task_list.py 中为 US1 编写集成测试：验证分页（page=1/2）、空结果、默认排序（创建时间倒序）

**检查点**: `GET /api/v1/tasks` 基础分页可用，可独立演示

---

## 阶段 4: 用户故事 2 - 查询单个任务的完整关联信息 (优先级: P2)

**目标**: 扩展 `GET /api/v1/tasks/{task_id}`，在现有响应中追加 `summary` 关联统计字段。

**独立测试**: `curl "http://localhost:8080/api/v1/tasks/{existing_task_id}"` 响应中出现 `summary` 字段，含 tech_point_count 等 6 项统计；传入不存在的 task_id 返回 404。

### 用户故事 2 的契约测试

- [x] T013 [P] [US2] 在 tests/contract/test_task_list_api.py 中为扩展的 `GET /tasks/{task_id}` 编写契约测试：验证响应包含 `summary` 字段，且 summary 包含 tech_point_count/has_transcript/semantic_segment_count/motion_analysis_count/deviation_count/advice_count
- [x] T014 [P] [US2] 在 tests/contract/test_task_list_api.py 中为 404 场景编写契约测试：不存在的 task_id 和软删除的 task_id 均返回 404

### 用户故事 2 的实现

- [x] T015 [US2] 在 src/api/routers/tasks.py 的现有 `GET /tasks/{task_id}` 处理函数中，新增聚合查询逻辑：对 expert_tech_points/audio_transcript/tech_semantic_segments/athlete_motion_analyses/deviation_reports/coaching_advice 分别执行 COUNT/EXISTS 查询（依赖 T002 TaskSummary schema）
- [x] T016 [US2] 将聚合查询结果填充到 TaskSummary 实例，赋值到 TaskStatusResponse.summary 字段后返回；对未完成/失败任务各统计字段返回 0/False
- [x] T017 [US2] 在 tests/integration/test_task_list.py 中为 US2 编写集成测试：验证 expert_video 任务 summary.tech_point_count 正确；验证 athlete_video 任务 summary.motion_analysis_count 正确；验证 404 场景

**检查点**: `GET /api/v1/tasks/{task_id}` 返回完整关联统计，US1 不受影响

---

## 阶段 5: 用户故事 3 - 按多维度筛选和排序任务 (优先级: P3)

**目标**: 在 `GET /api/v1/tasks` 基础上增加 5 个筛选参数（status/task_type/coach_id/created_after/created_before）和完整排序支持（含 NULLS LAST）。

**独立测试**: `curl "http://localhost:8080/api/v1/tasks?status=failed&task_type=expert_video&sort_by=completed_at&order=desc"` 只返回 failed 的 expert_video 任务，按 completed_at 倒序，NULL 排末尾。

### 用户故事 3 的实现

- [x] T018 [US3] 在 src/api/routers/tasks.py 的 `list_tasks` 中新增 query params：status: TaskStatus | None, task_type: TaskType | None, coach_id: UUID | None, created_after: datetime | None, created_before: datetime | None
- [x] T019 [US3] 在 `list_tasks` 的 SQLAlchemy 查询中追加条件过滤：status/task_type/coach_id 的 WHERE 条件，created_after/created_before 的时间范围过滤；多条件为 AND 关系
- [x] T020 [US3] 在 `list_tasks` 中实现 completed_at 排序的 NULLS LAST 处理（`.nullslast()`）；created_at 排序无需特殊处理
- [x] T021 [US3] 在 `list_tasks` 中添加非法 status/task_type 枚举值的参数校验：返回 400，detail 说明合法枚举值列表
- [x] T022 [US3] 在 tests/integration/test_task_list.py 中为 US3 编写集成测试：验证按 status 筛选、按 coach_id 筛选、按 task_type+时间范围组合筛选、completed_at NULLS LAST 排序正确性

**检查点**: 所有筛选和排序功能可用，US1/US2 不受影响

---

## 阶段 6: 完善与横切关注点

**目的**: 端到端验收和文档确认

- [x] T023 [P] 按照 quickstart.md 中的 6 个 curl 示例逐一手工验收，确认响应结构符合 contracts/task_list_api.md 定义
- [x] T024 [P] 运行全部测试并确认通过：`pytest tests/contract/test_task_list_api.py tests/integration/test_task_list.py -v`
- [x] T025 验证 SC-001（10k 条数据列表 < 1s）和 SC-002（详情含关联统计 < 2s）：在开发环境用 `time curl` 或日志耗时确认

---

## 依赖关系与执行顺序

### 阶段依赖关系

- **阶段 1（设置）**: 无依赖，立即开始
- **阶段 2（基础）**: 依赖阶段 1；T002/T003 可并行，T004 依赖 T003，T005 依赖 T002
- **阶段 3（US1）**: 依赖阶段 2 全部完成；T006/T007 可并行，T008→T009→T010→T011 顺序执行
- **阶段 4（US2）**: 依赖阶段 2（T002 TaskSummary）；T013/T014 可并行；T015→T016 顺序执行
- **阶段 5（US3）**: 依赖阶段 3（list_tasks 端点已存在）；T018→T019→T020→T021 顺序执行
- **阶段 6（完善）**: 依赖阶段 3、4、5 全部完成

### 用户故事依赖关系

- **US1（P1）**: 阶段 2 完成后即可开始，无其他故事依赖
- **US2（P2）**: 仅依赖阶段 2（T002），可与 US1 并行开始
- **US3（P3）**: 依赖 US1（list_tasks 端点），需在 US1 完成后开始

### 并行机会

- T002 与 T003 可并行（不同 class 定义）
- T006 与 T007 可并行（同文件不同测试函数）
- T013 与 T014 可并行
- US1 的 T006/T007（测试）与 US2 的 T013/T014（测试）可并行编写

---

## 并行示例

```bash
# 阶段 2 内并行（Schema 定义）:
任务: "在 task.py 中新增 TaskSummary"
任务: "在 task.py 中新增 TaskListItemResponse"

# 基础就绪后并行（US1 和 US2 测试同步编写）:
任务: "为 GET /tasks 编写契约测试（T006/T007）"
任务: "为扩展 GET /tasks/{task_id} 编写契约测试（T013/T014）"
```

---

## 实施策略

### 仅 MVP（用户故事 1）

1. 完成阶段 1 + 阶段 2
2. 完成阶段 3（US1）
3. **停止验证**: `curl "http://localhost:8080/api/v1/tasks"` 返回分页任务列表
4. 可演示：全量列表查询基础能力

### 增量交付

1. 阶段 1+2 → Schema 就绪
2. 阶段 3（US1）→ 列表端点可用 → 演示/验收（MVP）
3. 阶段 4（US2）→ 详情关联统计可用 → 演示
4. 阶段 5（US3）→ 完整筛选排序 → 演示
5. 阶段 6 → 验收收尾

---

## 注意事项

- [P] 任务 = 不同文件或不同独立代码块，无执行依赖
- [Story] 标签映射到 spec.md 中的用户故事以实现可追溯性
- 每个用户故事可独立完成和测试，不破坏其他故事
- T002（TaskSummary）是 US2 的基础依赖，须在阶段 2 完成
- `video_storage_uri` 是 EncryptedString 类型，需确认 ORM 读取时自动解密后写入响应
- 所有查询必须过滤 `deleted_at IS NULL`（软删除）
