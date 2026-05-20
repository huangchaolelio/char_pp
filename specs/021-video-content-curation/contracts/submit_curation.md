# Contract — `POST /api/v1/tasks/curation`

提交"视频内容清洗"任务（单条 + 批量）。Feature-021。

---

## 请求

### 单条提交

```http
POST /api/v1/tasks/curation HTTP/1.1
Content-Type: application/json

{
  "coach_video_classification_id": 1234,
  "curation_rubric_version": "v1",
  "force": false
}
```

| 字段 | 类型 | 必填 | 说明 |
|-----|-----|-----|------|
| `coach_video_classification_id` | int | 是 | 教练侧素材 ID（隐含 `cos_object_key` + `preprocessing_job_id`）|
| `curation_rubric_version` | string | 否 | 默认取 `src/config/curation_rubric/` 下当前最高版本号；显式声明则严格用该版本 |
| `force` | bool | 否 | 默认 false。若该视频已有 `status=success` 作业且版本号一致则幂等返回；若 `force=true` 一律新建 |

### 批量提交

```http
POST /api/v1/tasks/curation HTTP/1.1
Content-Type: application/json

{
  "items": [
    {"coach_video_classification_id": 1234},
    {"coach_video_classification_id": 1235},
    {"coach_video_classification_id": 1236}
  ],
  "curation_rubric_version": "v1",
  "force": false
}
```

| 字段 | 类型 | 必填 | 说明 |
|-----|-----|-----|------|
| `items` | array | 是 | 长度 ≥ 1 且 ≤ `BATCH_MAX_SIZE`（默认 100）|
| 其余字段同单条 |

---

## 响应

### 成功

**单条**：

```json
{
  "success": true,
  "data": {
    "job_id": 9001,
    "task_id": 99001,
    "cos_object_key": "charhuang/tt_video/.../coach_a/forehand_topspin.mp4",
    "curation_rubric_version": "v1",
    "status": "pending",
    "queued": true,
    "idempotent_short_circuit": false
  }
}
```

`idempotent_short_circuit=true` 表示命中既有 `status=success` 作业（force=false 时），未实际入队；返回的 `job_id` 是历史作业。

**批量**：

```json
{
  "success": true,
  "data": {
    "submitted": [
      {"coach_video_classification_id": 1234, "job_id": 9001, "task_id": 99001, "queued": true,  "idempotent_short_circuit": false},
      {"coach_video_classification_id": 1235, "job_id": 9000, "task_id": null,  "queued": false, "idempotent_short_circuit": true},
      {"coach_video_classification_id": 1236, "job_id": null, "task_id": null,  "queued": false, "rejected": true, "error": {"code":"PREPROCESSING_NOT_AVAILABLE", "message":"..."}}
    ],
    "queued_count": 1,
    "skipped_count": 1,
    "rejected_count": 1
  }
}
```

批量提交单条失败**不**回滚整批；逐条状态在 `submitted[]` 中报告。

### 错误

| HTTP | code | 触发场景 |
|------|------|---------|
| 422 | `VALIDATION_FAILED` | 请求 schema 不通过（缺字段 / items 超 100 / force 非 bool 等）|
| 404 | `RESOURCE_NOT_FOUND` 或既有 classification 专属 code | `coach_video_classification_id` 不存在 |
| 409 | `PREPROCESSING_NOT_AVAILABLE` | 该视频未完成预处理（既有错误码，非本 feature 新增）|
| 404 | `RUBRIC_VERSION_NOT_FOUND` | `curation_rubric_version` 文件不存在 |
| 422 | `RUBRIC_INVALID` | 规范文件 schema 校验失败 |
| 409 | `CURATION_RUBRIC_MISMATCH` | 既有 success 作业的 rubric 版本与请求不一致且 `force=false` |
| 503 | `CHANNEL_OVERFLOW` | `default` 队列容量满（既有错误码）|

---

## 行为契约

1. **任务类型**：内部新建 `analysis_tasks` 行 `task_type=video_curation`、`business_phase=TRAINING`、`business_step=curate_segments`；`_phase_step_hook` 自动派生
2. **队列**：路由到 `default`（concurrency=1）
3. **幂等**：单条 `force=false` + 同 rubric 版本已 success ⇒ 直接返回历史 `job_id`，不入队
4. **前置校验**（service 层在排队前同步执行）：
   - `coach_video_classification.tech_category != 'unclassified'`（清洗对未分类视频无意义）
   - `coach_video_classification.preprocessed=true` 且 `preprocessing_job_id` 关联的 `video_preprocessing_jobs.status='success'`
   - rubric 文件可加载 + schema 校验通过
5. **副作用**：
   - 入队前：`INSERT video_curation_jobs (status='pending')` + `INSERT analysis_tasks`
   - Worker 执行后：参见 `data-model.md § 4`

---

## 合约测试用例（`tests/contract/test_submit_curation.py`）

1. ✅ 单条提交，预处理已完成 ⇒ 200，`queued=true`
2. ✅ 单条提交，相同 rubric 已 success ⇒ 200，`idempotent_short_circuit=true`
3. ✅ 单条提交，`force=true` ⇒ 200，新建 job
4. ❌ classification_id 不存在 ⇒ 404
5. ❌ 预处理未完成 ⇒ 409 `PREPROCESSING_NOT_AVAILABLE`
6. ❌ rubric 版本不存在 ⇒ 404 `RUBRIC_VERSION_NOT_FOUND`
7. ❌ rubric schema 错误 ⇒ 422 `RUBRIC_INVALID`
8. ❌ rubric 版本与既有 job 不一致且 `force=false` ⇒ 409 `CURATION_RUBRIC_MISMATCH`
9. ✅ 批量 3 条混合（1 success / 1 短路 / 1 拒绝）⇒ 200，逐条状态正确
10. ❌ 批量超 100 ⇒ 422
