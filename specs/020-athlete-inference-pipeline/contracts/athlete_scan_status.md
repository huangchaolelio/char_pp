# 契约 · GET /api/v1/athlete-classifications/scan/{task_id}

**Feature**: 020-athlete-inference-pipeline
**用户故事**: US1 — 扫描进度查询
**方法**: GET
**路径**: `/api/v1/athlete-classifications/scan/{task_id}`

## 请求

路径参数：
- `task_id` (UUID) — `POST /athlete-classifications/scan` 返回的扫描任务 ID

## 成功响应 200 OK

```json
{
  "success": true,
  "data": {
    "task_id": "7e5e3f7a-...",
    "status": "success",
    "scanned": 128,
    "inserted": 128,
    "updated": 0,
    "skipped": 0,
    "errors": 0,
    "elapsed_s": 37.2,
    "error_detail": null
  }
}
```

**状态取值**：`pending` | `running` | `success` | `failed`

**路由实现语义**（对齐 T025 实现细节）：
- 路由必须**先查 `analysis_tasks` 表**确认 `task_id` 存在且 `task_type='athlete_video_classification'`；否则 404 `TASK_NOT_FOUND`
- 存在记录后，从 `analysis_tasks.status / progress / error` 字段读取权威状态（扫描进度摘要由 scanner 写入 `progress` JSON）
- Celery `AsyncResult(task_id)` 仅作为辅助补充信号，**不作为"存在性"判定**（Celery 对未知 task_id 默认返回 `PENDING`，不符合本契约 404 语义）

**字段语义**：
- 运行中 (`running`)：`scanned / inserted / updated / skipped / errors` 为当时进度快照，`elapsed_s` 为至今耗时，`error_detail=null`
- 完成 (`success`)：所有计数冻结，`elapsed_s` 为总耗时
- 失败 (`failed`)：`error_detail` 含结构化错误（如 `"ATHLETE_ROOT_UNREADABLE: ..."`）

## 错误响应

| HTTP | ErrorCode | 触发场景 |
|------|-----------|---------|
| 404 | `TASK_NOT_FOUND` | `task_id` 不存在（或非本 feature 的扫描任务） |
| 422 | `VALIDATION_FAILED` | `task_id` 非 UUID 格式 |

## 合约测试 `tests/contract/test_athlete_scan_status.py`

必须覆盖：
- [ ] 不存在的 UUID → 404 + `TASK_NOT_FOUND`（错误信封）
- [ ] 非 UUID 字符串 → 422 + `VALIDATION_FAILED`
- [ ] 状态流转断言：刚提交 → `pending` → `running` → `success`（通过 mock Celery `AsyncResult` 模拟）
- [ ] 失败任务的 `error_detail` 以错误码前缀开头（例如以 `"ATHLETE_ROOT_UNREADABLE:"` 起始）
