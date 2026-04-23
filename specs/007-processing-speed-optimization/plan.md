# 实施计划: 优化视频提取知识库的处理耗时

**分支**: `007-processing-speed-optimization` | **日期**: 2026-04-21 | **规范**: [spec.md](./spec.md)
**输入**: 来自 `/specs/007-processing-speed-optimization/spec.md` 的功能规范

## 摘要

对专家视频知识库提取的处理流水线实施两项性能优化：

1. **并行预分割**：`_pre_split_video()` 从串行 `for` 循环改为 `ProcessPoolExecutor(max_workers=4)` 并行执行 ffmpeg 子进程；任一片段失败立即取消其余，整体任务标记 `failed`
2. **ffmpeg 快速编码**：分段命令从 `-c:v libx264 -preset medium -crf 23` 改为 `-c:v libx264 -preset ultrafast -crf 23`，编码速度提升 4–6x；检测到输入已是目标分辨率时进一步降级为 `-c copy`
3. **耗时可观察性**：`analysis_tasks` 表新增 `timing_stats JSONB` 字段，同时写入 worker 日志，记录 pre_split / pose_estimation / kb_extraction 各阶段耗时

目标：5 分钟视频 ≤ 100s（当前 216s），10 分钟视频 ≤ 200s（当前 310s），预分割阶段降低 ≥ 60%。

## 技术背景

**语言/版本**: Python 3.11（`/opt/conda/envs/coaching/bin/python3.11`）
**主要依赖**: FastAPI 0.111+, SQLAlchemy 2.0+ (asyncio), Alembic 1.13+, Pydantic v2, Celery, `concurrent.futures`（标准库）
**存储**: PostgreSQL（asyncpg 驱动），新增 `JSONB` 字段
**测试**: pytest 8.2+ / pytest-asyncio 0.23+
**目标平台**: Linux 服务器（Tesla T4 GPU + 多核 CPU）
**项目类型**: Web 服务（REST API）+ Celery Worker
**性能目标**: 5min 视频 ≤ 100s，10min 视频 ≤ 200s，预分割 -60%
**约束条件**: max_workers=4（GPU 同机运行，保留资源余量）；不引入新外部依赖；向后兼容
**规模/范围**: 单 worker，内部并行化；不涉及 Celery 多 worker 扩展

## 章程检查

*门控: 必须在阶段 0 研究前通过。阶段 1 设计后重新检查。*

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 规范包含用户故事和可衡量成功标准 | ✅ 通过 | spec.md 含 3 个用户故事、7 条 SC（SC-001~SC-007）含量化指标 |
| 无前端实现任务混入 | ✅ 通过 | 全部为 worker + DB 层任务，无任何前端代码 |
| 量化精准度指标（原则 VIII） | ✅ 通过 | 本功能为性能优化（非 AI 模型引入），SC-005 定义结果一致性指标（≤5% 偏差）；无精准度回退风险 |
| 测试优先（原则 II） | ✅ 计划 | 单元测试先写（mock subprocess），集成测试验证 timing_stats 写入，均在实现任务前创建 |
| 复杂性违规 | ✅ 无违规 | ProcessPoolExecutor 为标准库，无额外抽象层；ffmpeg 参数变更为一行改动 |
| AI 模型治理（原则 VI）| N/A | 本功能不引入新 AI 模型，不修改姿态估计逻辑 |
| 用户数据隐私（原则 VII）| ✅ 通过 | timing_stats 为系统内部性能数据，无敏感用户信息 |
| Python 环境隔离 | ✅ 遵守 | 使用 coaching conda 环境，仅 Python 标准库（`concurrent.futures`），无新依赖 |

**阶段 1 设计后重检**: 见本文档末尾

## 项目结构

### 文档（此功能）

```
specs/007-processing-speed-optimization/
├── plan.md                     # 此文件
├── research.md                 # 阶段 0 输出
├── data-model.md               # 阶段 1 输出
├── contracts/
│   └── tasks-api-changes.md   # 阶段 1 输出
└── tasks.md                    # 阶段 2 输出（/speckit.tasks 命令创建）
```

### 源代码（新增/修改文件）

```
src/
├── workers/
│   └── expert_video_task.py          # 修改：_pre_split_video() 并行化 + ffmpeg 参数
├── models/
│   └── analysis_task.py              # 修改：新增 timing_stats JSONB 字段
├── api/
│   └── schemas/
│       └── (task schema 文件)         # 修改：TaskStatusResponse 新增 timing_stats 字段
└── db/
    └── migrations/versions/
        └── 0008_add_timing_stats.py  # 新增：Alembic 迁移

tests/
├── unit/
│   ├── test_pre_split_parallel.py   # 新增：并行分割单元测试
│   └── test_ffmpeg_command.py       # 新增：ffmpeg 参数验证
└── integration/
    └── test_timing_stats_persisted.py  # 新增：timing_stats 写入验证
```

**结构决策**: 沿用现有单一项目结构（选项 1），在 `src/workers/` 内修改，无新目录。

## 复杂度跟踪

> 无违规，无需填写。

## 阶段 1 设计后重检

*于实现完成后补充*

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 设计与规范一致 | ⏳ 待验证 | — |
| 向后兼容性 | ⏳ 待验证 | — |
| 测试优先（原则 II）| ⏳ 待验证 | — |
| 复杂度无增长 | ⏳ 待验证 | — |
| 迁移可逆 | ⏳ 待验证 | — |
| 全量回归 | ⏳ 待验证 | — |
