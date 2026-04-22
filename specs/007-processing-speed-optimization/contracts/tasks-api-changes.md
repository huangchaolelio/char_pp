# API 契约变更: 优化视频提取知识库的处理耗时

**功能**: 007-processing-speed-optimization | **日期**: 2026-04-21

## 变更范围

本功能不新增任何 API 端点，不修改任何现有端点的请求结构。**唯一变更**：`GET /api/v1/tasks/{task_id}` 响应体新增可选字段 `timing_stats`。

---

## 变更端点：GET /api/v1/tasks/{task_id}

### 响应体变更（向后兼容）

在现有 `TaskStatusResponse` schema 中新增字段：

```
timing_stats?: TimingStats | null
```

**语义**：
- `null`（默认）：优化前创建的历史任务，或任务尚未完成
- 对象：任务已完成，包含各阶段耗时

### TimingStats 对象结构

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `pre_split_s` | number (float) | 是 | 视频预分割阶段耗时（秒） |
| `pose_estimation_s` | number (float) | 是 | 姿态估计总耗时（秒） |
| `kb_extraction_s` | number (float) | 是 | 知识库提炼总耗时（秒） |
| `total_s` | number (float) | 是 | 端到端总耗时（秒） |

### 响应示例

**有 timing_stats（优化后完成的任务）**:

```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "task_type": "expert_video",
  "status": "success",
  "total_segments": 8,
  "processed_segments": 8,
  "progress_pct": 100.0,
  "coach_id": "0ef6ae5f-...",
  "timing_stats": {
    "pre_split_s": 12.3,
    "pose_estimation_s": 180.5,
    "kb_extraction_s": 23.1,
    "total_s": 215.9
  },
  "created_at": "2026-04-21T10:00:00Z",
  "completed_at": "2026-04-21T10:03:36Z"
}
```

**无 timing_stats（历史任务或未完成）**:

```json
{
  "id": "...",
  "status": "success",
  "timing_stats": null
}
```

---

## 向后兼容性声明

- 新字段 `timing_stats` 为可选，默认 `null`
- 现有客户端忽略未知字段不受影响
- 无破坏性变更（无字段删除、无类型变更、无必填字段新增）
- 所有现有合约测试继续通过（`timing_stats` 未在旧测试中断言）
