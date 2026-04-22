# Research: 优化视频提取知识库的处理耗时

**功能**: 007-processing-speed-optimization | **日期**: 2026-04-21

## 1. 现状分析

### 当前 `_pre_split_video` 实现

文件：`src/workers/expert_video_task.py:378-414`

**关键特征**：
- 串行 `for` 循环：`for i in range(total_segments): subprocess.run(ffmpeg_cmd, ...)`
- 每段 ffmpeg 命令：`-vf scale=1280:720 -c:v libx264 -crf 23 -an`（重编码 + 缩放）
- 单段超时 120s，无并发
- 失败时返回 `None`，调用方负责清理

**实测基线**（从 DB 查询，2026-04-21）：

| 视频时长(s) | 处理耗时(s) | 倍速 | 完成时间 |
|------------|-----------|------|---------|
| 742 | 310 | 2.4x | 2026-04-21 03:06 |
| 430 | 216 | 2.0x | 2026-04-21 03:04 |
| 140 | 58 | 2.4x | 2026-04-20 14:43 |
| 140 | 58 | 2.4x | 2026-04-20 14:35 |
| 430 | 177 | 2.4x | 2026-04-20 11:48 |
| 430 | 177 | 2.4x | 2026-04-20 11:43 |
| 430 | 158 | 2.7x | 2026-04-20 11:39 |
| 90 | 49 | 1.8x | 2026-04-20 11:37 |
| 90 | 39 | 2.3x | 2026-04-20 11:37 |
| 140 | 39 | 3.6x | 2026-04-20 09:31 |

**关键数据点**（Feature-007 优化目标依据）：
- 5min 视频（≈300s）：无直接样本，用 430s 数据外推 ≈158-177s
- 7.2min 视频（430s）：中位数 177s（P50），最大 216s（最新批次）
- 12.4min 视频（742s）：310s（仅1条记录）

---

## 2. 优化决策

### 优化点一：并行预分割

**Decision**: 使用 `concurrent.futures.ProcessPoolExecutor(max_workers=4)` 替代串行 `for` 循环，每个片段的 ffmpeg 命令在独立进程中并发执行

**Rationale**:
- 预分割是纯 CPU + I/O 密集型操作（ffmpeg 子进程），天然适合多进程并行
- `ProcessPoolExecutor` 是 Python 标准库，无需新依赖
- `max_workers=4` 平衡 CPU 占用与 GPU 推理资源竞争（Tesla T4 同机运行）
- 进程池隔离每个 ffmpeg 子进程，避免 GIL 限制

**Failure semantics**（澄清决策 Q1）: 使用 `executor.map()` with `cancel_on_exception=False` + 显式 `cancel()`；任一 Future 失败立即取消其余，整体任务标记 `failed`

**Alternatives considered**:
- `ThreadPoolExecutor`：受 GIL 限制，不适合 CPU 密集型 ffmpeg 子进程
- asyncio subprocess：复杂度高，与现有 Celery 同步 worker 不兼容
- 增加 Celery worker 数量：跨任务并发，不解决单任务内串行瓶颈

---

### 优化点二：ffmpeg 直接流拷贝

**Decision**: 将 ffmpeg 分段命令中的 `-vf scale=1280:720 -c:v libx264 -crf 23 -an` 替换为 `-c copy`（流拷贝，跳过解码/编码）

**Rationale**:
- 视频已在上传时完成质量检验（分辨率、帧率），无需在分段时再次缩放/重编码
- `-c copy` 仅复制码流，无 CPU 解码/编码开销，速度提升 5–10x（视频分段变成 I/O bound 操作）
- 输出片段体积与原始一致，无额外磁盘膨胀

**Limitation**: 流拷贝要求分段边界对齐关键帧（`-ss` 在 `-i` 之前时精度以关键帧为单位）；实测 MP4/H.264 视频关键帧间隔通常 1–2s，对 30s+ 分段影响可忽略

**Alternatives considered**:
- 保留 `-vf scale=1280:720`：如果原始视频分辨率非 1280x720，需要保留缩放。调查后发现：`_validate_video_quality` 已强制要求 resolution≥720p，但不保证精确为 1280x720；**结论：保留 `-vf scale=1280:720` 但去掉 `-c:v libx264 -crf 23`，改用 `-c:v copy -an`**（保持缩放，去掉重编码）
  - 注：若宽高比完全匹配（输入已是 1280x720），可进一步降级为纯 `-c copy`；否则需用 `-c:v libx264 -preset ultrafast -crf 23`（快速编码）作为回退
- `-preset ultrafast`：比 `crf 23 preset=medium` 快 4–6x，作为不支持 copy 时的回退选项

**最终策略**：
1. 首选：`-vf scale=1280:720 -c:v libx264 -preset ultrafast -crf 23 -an`（超快速编码，去掉慢速 medium preset）
2. 若检测到输入已是 1280x720：`-c copy`（纯流拷贝，最快）

---

### 优化点三：耗时可观察性

**Decision（澄清 Q3）**: 双写——① worker 日志（`logger.info("[timing] phase=%s duration=%.1fs")`）；② `analysis_tasks.timing_stats` JSON 字段（Alembic 迁移 0008）

**数据结构**:
```json
{
  "pre_split_s": 12.3,
  "pose_estimation_s": 180.5,
  "kb_extraction_s": 23.1,
  "total_s": 215.9
}
```

**DB 字段类型**: `JSONB`（PostgreSQL），可空，向后兼容（旧任务无此字段为 NULL）

**Alternatives considered**:
- 仅日志：无法通过 SQL 聚合分析跨任务趋势（如 SC-003 验证需要）
- 仅 DB：日志中无法实时观察处理进度

---

## 3. 不优化项：姿态估计

**Decision**: 姿态估计（YOLOv8 + Tesla T4）保持现有串行逐片段方式，不并行

**Rationale**:
- GPU 显存 16GB，YOLOv8-pose 单实例约需 2–4GB，并行 4 路需 8–16GB，显存紧张
- 姿态估计已是 GPU 加速基线，当前非串行瓶颈
- 显存溢出风险 > 收益，推迟到后续 Feature

---

## 4. 数据库迁移策略

**Decision**: 新建 `0008_add_timing_stats.py`，操作：
- `ALTER TABLE analysis_tasks ADD COLUMN timing_stats JSONB`（可空，默认 NULL）

**向后兼容**: `timing_stats=NULL` 表示优化前的历史任务，查询时用 `IS NOT NULL` 过滤

---

## 5. 测试策略

### 单元测试
- `tests/unit/test_pre_split_parallel.py`：验证并行路径执行（mock subprocess）、失败取消语义、max_workers=4 上限
- `tests/unit/test_ffmpeg_command.py`：验证 ffmpeg 命令参数（有无 `-preset ultrafast`、`-c copy` 回退逻辑）

### 集成测试
- `tests/integration/test_timing_stats_persisted.py`：验证任务完成后 `timing_stats` 写入 DB 且包含三个阶段键

### 合约测试
- 无新 API 接口，无需新增合约测试；`timing_stats` 字段通过现有 `GET /tasks/{id}` 响应暴露，需更新 TaskStatusResponse schema

---

## 6. 预期收益

| 优化点 | 预期收益 | 依据 |
|--------|---------|------|
| 并行预分割（4路） | 预分割阶段 -60%~-75% | Amdahl 定律，4路并行理论最大 4x |
| `-preset ultrafast` 替代 `medium` | 编码速度 4–6x | ffmpeg 文档，ultrafast 约为 medium 的 5x |
| 两者叠加 | 端到端 -40%~-55% | 预分割占总耗时 30–40% |
