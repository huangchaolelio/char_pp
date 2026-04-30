# 契约 · POST /api/v1/athlete-classifications/scan

**Feature**: 020-athlete-inference-pipeline
**用户故事**: US1 — 运动员视频素材归集与自动分类
**方法**: POST
**路径**: `/api/v1/athlete-classifications/scan`
**幂等性**: 可重复提交（每次产生新 `task_id` → 新 `analysis_tasks` 行）

## 请求

```json
{
  "scan_mode": "full"  // or "incremental"
}
```

- `scan_mode`: `"full"` | `"incremental"`（默认 `"full"`），非法值 → 400 `INVALID_ENUM_VALUE`，`details.allowed = ["full", "incremental"]`

## 成功响应 202 Accepted

```json
{
  "success": true,
  "data": {
    "task_id": "7e5e3f7a-...",
    "status": "pending"
  }
}
```

**副作用**：
1. 创建 `analysis_tasks` 行：`task_type='athlete_video_classification'`、`business_phase='INFERENCE'`、`business_step='scan_athlete_videos'`、`submitted_via='batch_scan'`、`status='pending'`
2. 异步入队 `scan_athlete_videos` Celery task 到 `default` 队列
3. 进度可通过 `GET /api/v1/athlete-classifications/scan/{task_id}` 查询（另一份契约）

## 错误响应

| HTTP | ErrorCode | 触发场景 |
|------|-----------|---------|
| 400 | `INVALID_ENUM_VALUE` | `scan_mode` 非法 |
| 422 | `VALIDATION_FAILED` | 请求体 schema 校验失败（如多余字段，`extra='forbid'`） |
| 500 | `ATHLETE_DIRECTORY_MAP_MISSING` | `config/athlete_directory_map.json` 缺失（路由层预检或 scanner 启动失败） |
| 502 | `ATHLETE_ROOT_UNREADABLE` | COS 根路径凭证/网络问题（**异步**错误落到 task 结果，HTTP 层成功返回 `task_id` 后再失败） |
| 503 | `CHANNEL_QUEUE_FULL` | `default` 队列饱和（罕见；扫描类任务独占 default） |

## 合约测试 `tests/contract/test_athlete_scan.py`

必须覆盖：
- [ ] 正常 202 + `SuccessEnvelope` + `task_id` UUID 格式
- [ ] `scan_mode='invalid'` → 400 + `INVALID_ENUM_VALUE` + `details.allowed`
- [ ] 缺 `scan_mode` → 使用默认值 `"full"` 成功
- [ ] 请求体多余字段 → 422 `VALIDATION_FAILED`（`extra='forbid'` 必须生效）
- [ ] 创建出的 `analysis_tasks` 行 `business_phase='INFERENCE'` / `business_step='scan_athlete_videos'`（通过任务监控接口查回验证）
- [ ] 同时存在教练侧扫描任务，本扫描不污染 `coach_video_classifications`（SC-006 辅助验证）
