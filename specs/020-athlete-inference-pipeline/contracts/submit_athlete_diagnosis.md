# 契约 · POST /api/v1/tasks/athlete-diagnosis

**Feature**: 020-athlete-inference-pipeline
**用户故事**: US3 — 运动员诊断任务端到端自动编排
**方法**: POST
**路径**: `/api/v1/tasks/athlete-diagnosis`（单条）；`/api/v1/tasks/athlete-diagnosis/batch`（批量）

## 请求（单条 · POST /tasks/athlete-diagnosis）

```json
{
  "athlete_video_classification_id": "5e4c...",
  "force": false
}
```

## 请求（批量 · POST /tasks/athlete-diagnosis/batch）

```json
{
  "items": [
    { "athlete_video_classification_id": "5e4c..." },
    { "athlete_video_classification_id": "6d7f..." }
  ]
}
```

`force` 含义：允许对"已有最近诊断报告"的素材重新诊断；默认 `false` 也会新建任务（与 Q3 决议一致：每次提交都新建报告），`force=true` 仅用于绕过未来可能新增的重复提交节流策略（本 feature 期暂不做节流，保留字段）。

## 成功响应 200 OK（单条）

```json
{
  "success": true,
  "data": {
    "task_id": "9ab0...",
    "athlete_video_classification_id": "5e4c...",
    "status": "pending",
    "tech_category": "forehand_attack",
    "estimated_completion_seconds": 60
  }
}
```

## 成功响应 200 OK（批量）

```json
{
  "success": true,
  "data": {
    "submitted": [
      { "athlete_video_classification_id": "5e4c...", "task_id": "9ab0...", "tech_category": "forehand_attack" },
      { "athlete_video_classification_id": "6d7f...", "task_id": "a1b2...", "tech_category": "backhand_push" }
    ],
    "rejected": [
      {
        "athlete_video_classification_id": "7fff...",
        "error": { "code": "ATHLETE_VIDEO_NOT_PREPROCESSED", "message": "运动员视频尚未完成预处理，不能直接诊断" }
      }
    ]
  }
}
```

**通道容量行为**：批量提交如果要插入 N 条任务但 `diagnosis` 通道剩余槽位为 M（M < N），路由层按 F-013 通道门控逻辑**整批原子拒绝** 503 `CHANNEL_QUEUE_FULL`，不混合部分成功。单条提交则按原子行为返回 503。

## 错误响应

| HTTP | ErrorCode | 触发场景 |
|------|-----------|---------|
| 404 | `ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND` | `athlete_video_classification_id` 不存在 |
| 409 | `ATHLETE_VIDEO_NOT_PREPROCESSED` | 素材存在但 `preprocessed != true`（US3 AC3 硬约束） |
| 409 | `STANDARD_NOT_AVAILABLE` | 对应 `tech_category` 无 active `tech_standards`（US3 AC2 硬约束，提交时已前置校验） |
| 422 | `VALIDATION_FAILED` | 请求体校验失败 |
| 422 | `ATHLETE_VIDEO_POSE_UNUSABLE` | 诊断执行时姿态提取全程失败（此为 task-level 错误，HTTP 层任务已被接受并返回 200；错误体现在 `GET /tasks/{task_id}` 的 `status='failed'` + `error_code`）|
| 503 | `CHANNEL_QUEUE_FULL` | `diagnosis` 通道满（默认容量=20） |

> **职责分工**：路由层只做 `ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND` / `ATHLETE_VIDEO_NOT_PREPROCESSED` / `STANDARD_NOT_AVAILABLE` / `CHANNEL_QUEUE_FULL` 四类同步校验；`ATHLETE_VIDEO_POSE_UNUSABLE` 是诊断执行阶段的异步错误，由 Celery task 写入任务状态。

## 合约测试 `tests/contract/test_submit_athlete_diagnosis.py`

必须覆盖：
- [ ] 正常单条：200 + `SuccessEnvelope` + `tech_category` 为素材 `tech_category`（不接受 override）
- [ ] 素材 ID 不存在 → 404 `ATHLETE_VIDEO_CLASSIFICATION_NOT_FOUND`
- [ ] 素材未预处理 → 409 `ATHLETE_VIDEO_NOT_PREPROCESSED` + `details.athlete_video_classification_id`
- [ ] 素材 `tech_category` 无 active `tech_standards` → 409 `STANDARD_NOT_AVAILABLE` + `details.tech_category`
- [ ] 提交后生成的 `analysis_tasks` 行：`task_type='athlete_diagnosis'`、`business_phase='INFERENCE'`、`business_step='diagnose_athlete'`
- [ ] 批量 3 条，1 条无预处理 → `rejected[0]` 含 `ATHLETE_VIDEO_NOT_PREPROCESSED`；其余 2 条正常入队
- [ ] 批量 5 条但通道剩余 3 槽 → 503 `CHANNEL_QUEUE_FULL` 整批原子拒绝
- [ ] 重复提交同一 ID 两次：生成两条独立 `analysis_tasks` + 最终两个独立 `diagnosis_reports`（Q3 决议）
- [ ] 诊断完成后 `diagnosis_reports` 行包含：`cos_object_key / preprocessing_job_id / standard_version / source='athlete_pipeline'`（集成测试范畴，本合约测试只断言 schema）
