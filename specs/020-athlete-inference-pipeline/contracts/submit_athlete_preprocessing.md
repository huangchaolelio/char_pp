# 契约 · POST /api/v1/tasks/athlete-preprocessing（单） + POST /api/v1/tasks/athlete-preprocessing/batch（批）

**Feature**: 020-athlete-inference-pipeline
**用户故事**: US2 — 运动员视频标准化预处理
**方法**: POST
**路径**：
- 单条：`/api/v1/tasks/athlete-preprocessing`
- 批量：`/api/v1/tasks/athlete-preprocessing/batch`

> **契约规范（对齐 F-013 `/tasks/classification/batch` 既有设计）**：单条与批量通过路径显式区分，请求体格式互不兼容；路由层不做"自动嵌波"推断。

## 请求（单条，`POST /athlete-preprocessing`）

```json
{
  "athlete_video_classification_id": "5e4c...",
  "force": false
}
```

## 请求（批量，`POST /athlete-preprocessing/batch`）

```json
{
  "items": [
    { "athlete_video_classification_id": "5e4c...", "force": false },
    { "athlete_video_classification_id": "6d7f...", "force": false }
  ]
}
```

**`force` 语义**：沿用 F-016 — `true` 时先 supersede 已 `success` 的 preprocessing job 再新建。

## 成功响应 200 OK（单条）

```json
{
  "success": true,
  "data": {
    "job_id": "9ff0...",
    "athlete_video_classification_id": "5e4c...",
    "cos_object_key": "charhuang/tt_video/athletes/张三/正手攻球01.mp4",
    "status": "running",
    "reused": false,
    "segment_count": null,
    "has_audio": false,
    "started_at": "2026-04-30T20:15:00+08:00",
    "completed_at": null
  }
}
```

## 成功响应 200 OK（批量）

```json
{
  "success": true,
  "data": {
    "submitted": [
      { "athlete_video_classification_id": "5e4c...", "job_id": "9ff0...", "reused": false },
      { "athlete_video_classification_id": "6d7f...", "job_id": "8ae1...", "reused": true }
    ],
    "rejected": []
  }
}
```

## 错误响应

| HTTP | ErrorCode | 触发场景 |
|------|-----------|---------|
| 404 | `ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND` | `athlete_video_classification_id` 不存在；需新增 code（从属于本 feature 资源专属 404） |
| 422 | `VALIDATION_FAILED` | 请求体校验失败 |
| 503 | `CHANNEL_QUEUE_FULL` | `preprocessing` 队列满（现有 `task_channel_configs` 容量=20） |
| 503 | `CHANNEL_DISABLED` | 运营熔断 `preprocessing` 通道 |

## 合约测试 `tests/contract/test_submit_athlete_preprocessing.py`

必须覆盖：
- [ ] 单条成功（`POST /athlete-preprocessing`）：返回 `status='running'` + `reused=false` + `job_id` UUID 格式
- [ ] 单条重复提交同一 ID（未 `force`） → `reused=true` + 返回原 `job_id`（幂等）
- [ ] `force=true` 对已 `success` 的 job → supersede + 新 `job_id`
- [ ] 批量（`POST /athlete-preprocessing/batch`） 3 条，其中 1 条 ID 不存在 → `rejected` 数组含该 ID + `ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND`；其余 2 条正常提交
- [ ] 批量请求体 `items=[]` → 422 `VALIDATION_FAILED`（`min_length=1`）
- [ ] 通道满 → 503 `CHANNEL_QUEUE_FULL`（整批原子拒绝）
- [ ] 提交成功后，创建的 `analysis_tasks` 行 `task_type='athlete_video_preprocessing'`、`business_phase='INFERENCE'`、`business_step='preprocess_athlete_video'`
- [ ] **路径隔离断言**：`POST /athlete-preprocessing` 的请求体传 `items=[...]` 单体格式以外的代口形态→ 422 `VALIDATION_FAILED`；`POST /athlete-preprocessing/batch` 请求体传单条形态 → 422 `VALIDATION_FAILED`

---

> **额外错误码登记**：`ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND` (404) 需添加到 `error-codes.md` 与 `src/api/errors.py`——更新 `contracts/error-codes.md` 在阶段 2 任务中单独列一条。
