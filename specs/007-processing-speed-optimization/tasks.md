# 任务: 优化视频提取知识库的处理耗时

**输入**: 来自 `/specs/007-processing-speed-optimization/` 的设计文档
**前置条件**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅

## 格式: `[ID] [P?] [Story] 描述`
- **[P]**: 可以并行运行（不同文件，无依赖关系）
- **[Story]**: 此任务属于哪个用户故事（US1, US2, US3）
- 描述中包含确切的文件路径

---

## 阶段 1: 设置

**目的**: 确认环境与基准，无代码变更

- [ ] T001 确认 coaching conda 环境可用：`/opt/conda/envs/coaching/bin/python3.11 -c "import concurrent.futures; print('OK')"`
- [ ] T002 记录优化前基准：对现有任务（孙浩泓 task_id / 高云娇 task_id）的 `started_at`/`completed_at` 差值作为对比基线（写入 `specs/007-processing-speed-optimization/research.md` 末尾的"基准数据"节）

---

## 阶段 2: 基础（阻塞性前置条件）

**目的**: 数据库迁移 + ORM 变更，所有用户故事依赖

**⚠️ 关键**: 在此阶段完成之前，无法开始任何用户故事工作

- [ ] T003 新建 Alembic 迁移 `src/db/migrations/versions/0008_add_timing_stats.py`：`ALTER TABLE analysis_tasks ADD COLUMN timing_stats JSONB`（可空，downgrade 为 `DROP COLUMN`）
- [ ] T004 在 `src/models/analysis_task.py` 中新增 ORM 字段：`timing_stats: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)`（在 Feature-006 `coach_id` 字段之后）
- [ ] T005 运行 `alembic upgrade head` 验证迁移成功，再运行 `alembic downgrade -1` + `alembic upgrade head` 验证往返可逆

**检查点**: `timing_stats` 字段在 DB 和 ORM 中就绪

---

## 阶段 3: 用户故事 1 — 更快获得知识库结果（优先级: P1）🎯 MVP

**目标**: 将 `_pre_split_video()` 改为 4 路并行 + ffmpeg `-preset ultrafast`，使 5min 视频 ≤ 100s

**独立测试**: 提交孙浩泓正手视频任务，等待完成，比对耗时与基线（216s → ≤100s）

### 用户故事 1 的测试（TDD：先写测试确认失败，再实现）⚠️

- [ ] T006 [P] [US1] 在 `tests/unit/test_pre_split_parallel.py` 中编写并行分割单元测试：mock `subprocess.run`，验证 ① `ProcessPoolExecutor` 被调用、② max_workers=4 上限、③ 任一片段失败时其余被取消并返回全空列表
- [ ] T007 [P] [US1] 在 `tests/unit/test_ffmpeg_command.py` 中编写 ffmpeg 命令参数测试：验证命令包含 `-preset ultrafast` 且不含 `-preset medium`；验证 `-c copy` 回退路径

### 用户故事 1 的实现

- [ ] T008 [US1] 在 `src/workers/expert_video_task.py` 中修改 `_pre_split_video()` 函数（第 378–414 行）：将串行 `for` 循环替换为 `ProcessPoolExecutor(max_workers=4)` 并行执行各片段的 ffmpeg 子进程；任一 Future 失败立即取消其余，返回全 `None` 列表（调用方现有的失败检测逻辑不变）
- [ ] T009 [US1] 在同文件中将 ffmpeg 分段命令参数 `-c:v libx264 -crf 23`（无 preset 或 preset medium）改为 `-c:v libx264 -preset ultrafast -crf 23`；在同一 `_pre_split_video()` 中添加分辨率检测逻辑：若输入视频已是 1280x720，则使用 `-c copy -an` 跳过重编码
- [ ] T010 [US1] 运行 `tests/unit/test_pre_split_parallel.py` 和 `tests/unit/test_ffmpeg_command.py`，确认全部通过

**检查点**: 单元测试 100% 通过，`_pre_split_video()` 已并行化

---

## 阶段 4: 用户故事 2 — 资源利用率提升（优先级: P2）

**目标**: 在阶段 3 并行化基础上，验证多任务批量场景下两任务总耗时 ≤ 单任务 1.5x

**独立测试**: 同时提交两个视频任务（孙浩泓 + 高云娇），记录两个任务的 started_at/completed_at 时间戳，确认两者预分割阶段时间戳交叉（并行推进）且总耗时 ≤ 单任务的 1.5 倍

### 用户故事 2 的测试

- [ ] T011 [P] [US2] 在 `tests/integration/test_parallel_tasks.py` 中编写集成测试：mock 两个任务的预分割调用，验证两个调用的 ffmpeg subprocess 在时间上有重叠（时间戳交叉）

### 用户故事 2 的实现

- [ ] T012 [US2] 检查 `src/workers/expert_video_task.py` 中 `ProcessPoolExecutor` 的 `max_workers` 参数：确认使用 `min(4, total_segments)` 而非硬编码 4，避免片段数 < 4 时浪费进程资源
- [ ] T013 [US2] 在 Celery worker 配置（`src/workers/` 或 `celery_app.py`）中检查是否需要调整 `CELERYD_CONCURRENCY` 或 `prefetch_multiplier`，确保两个任务可同时被不同 worker 拾取（如当前为单 worker 单并发，记录限制说明到 `specs/007-processing-speed-optimization/research.md`）

**检查点**: 批量场景下并发行为符合预期

---

## 阶段 5: 用户故事 3 — 可观察性（优先级: P1）

**目标**: 每个完成的任务写入 `timing_stats` 到 DB 和 worker 日志，满足 SC-007

**独立测试**: 完成一个任务后，查询 `analysis_tasks.timing_stats`，确认包含 `pre_split_s`、`pose_estimation_s`、`kb_extraction_s`、`total_s` 四个键且值为正数

### 用户故事 3 的测试

- [ ] T014 [P] [US3] 在 `tests/integration/test_timing_stats_persisted.py` 中编写集成测试：mock worker 处理完成流程，验证 `analysis_tasks.timing_stats` 被写入 DB 且包含四个必需键

### 用户故事 3 的实现

- [ ] T015 [P] [US3] 在 `src/workers/expert_video_task.py` 主处理函数（`process_expert_video_task` 或等效入口）中添加阶段计时逻辑：在 `pre_split`、`pose_estimation`、`kb_extraction` 各阶段前后记录 `time.perf_counter()`，汇总为 `timing_stats` 字典
- [ ] T016 [US3] 在同文件中将 `timing_stats` 写入两处：① `logger.info("[timing] phase=%s duration=%.1fs", ...)` 每阶段一条；② 在任务完成时通过 SQLAlchemy 更新 `AnalysisTask.timing_stats`（async update）
- [ ] T017 [P] [US3] 找到任务响应 schema 文件（`src/api/schemas/` 中的任务 schema），新增 `timing_stats: Optional[dict] = None` 字段到 `TaskStatusResponse`（或等效 Pydantic 模型）
- [ ] T018 [US3] 运行 `tests/integration/test_timing_stats_persisted.py`，确认通过

**检查点**: `timing_stats` 在 DB 和日志中均可观测

---

## 阶段 6: 收尾与验证

**目的**: 全量回归 + 端到端验证 + plan.md 重检

- [ ] T019 运行全量测试套件：`/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/ -v`，确认 0 failed（≥ 230 passed）
- [ ] T020 [P] 端到端验证：提交孙浩泓正手视频任务，等待完成，记录耗时并与基线对比，确认 ≤ 100s（SC-001）
- [ ] T021 [P] 端到端验证：查询完成任务的 `timing_stats` 字段，确认四个键均存在且值合理（SC-007）
- [ ] T022 更新 `specs/007-processing-speed-optimization/plan.md` 末尾"阶段 1 设计后重检"表格：填入实际测试结果和回归测试通过数
- [ ] T023 [P] 更新 `specs/speckit.constitution.md` 的 Feature 历史表：将 Feature-007 状态从"进行中"改为"完成"，填写完成日期

---

## 依赖关系与执行顺序

### 阶段依赖关系

- **阶段 1（设置）**: 无依赖，可立即开始
- **阶段 2（基础）**: 依赖阶段 1 完成 — 阻塞所有用户故事
- **阶段 3（US1 P1）**: 依赖阶段 2 完成 — MVP 核心
- **阶段 4（US2 P2）**: 依赖阶段 3 完成（并行化已实现）
- **阶段 5（US3 P1）**: 依赖阶段 2 完成（timing_stats 字段就绪），可与阶段 3 并行
- **阶段 6（收尾）**: 依赖阶段 3 + 4 + 5 全部完成

### 用户故事依赖关系

```
阶段2(基础) ──→ 阶段3(US1: 并行预分割) ──→ 阶段4(US2: 批量验证)
              ↘→ 阶段5(US3: 可观察性)
                                              ↘→ 阶段6(收尾)
```

- **US1 与 US3** 可在基础完成后并行开始（文件不同：worker 逻辑 vs schema/timing 写入）
- **US2** 依赖 US1（需要并行化先就绪）

### 并行机会

- T006、T007（US1 测试）可并行写
- T014、T015、T017（US3 测试+实现）可并行
- T020、T021（端到端验证）可并行

---

## 并行示例

```bash
# 阶段 3 测试可同时开始:
任务 T006: "在 tests/unit/test_pre_split_parallel.py 中编写并行分割单元测试"
任务 T007: "在 tests/unit/test_ffmpeg_command.py 中编写 ffmpeg 参数测试"

# 阶段 2 完成后，阶段 3 和阶段 5 可并行:
任务 T006-T010 (US1: 并行预分割)
任务 T014-T018 (US3: 可观察性)
```

---

## 实施策略

### MVP（仅用户故事 1 — 最大收益）

1. 完成阶段 1: 环境确认
2. 完成阶段 2: DB 迁移 + ORM
3. 完成阶段 3: 并行预分割 + ultrafast 编码
4. **停止并验证**: 端到端测量耗时，对比基线
5. 预期收益已实现 ≥ 50%

### 增量交付

1. 阶段 1+2 → 基础就绪
2. 阶段 3 → **MVP！** 性能提升可观测
3. 阶段 5 → 可观察性，量化优化效果
4. 阶段 4 → 批量场景验证
5. 阶段 6 → 全量验收

---

## 注意事项

- `_pre_split_video()` 的调用方（主流程）已有 `None` 检测逻辑，并行失败后整体任务标记 `failed` 的行为由调用方现有代码处理，无需修改调用方
- `concurrent.futures.ProcessPoolExecutor` 在 Celery worker 中使用时需注意：worker 进程本身已是 fork，嵌套 fork 在某些平台有限制；Linux 上安全，但需确认 `celery --pool=prefork` 与 ProcessPoolExecutor 兼容性
- ffmpeg `-c copy` 仅在不需要缩放时可用；当前 `_validate_video_quality` 不保证输入精确为 1280x720，建议默认使用 `-preset ultrafast` 而非 `-c copy`，更安全
- `timing_stats` 字段写入使用 async SQLAlchemy session，需在现有 async context 中执行（与其他字段更新合并为单次 `await db.commit()`）
