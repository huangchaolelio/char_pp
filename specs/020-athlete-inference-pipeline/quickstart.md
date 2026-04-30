# Quickstart · 运动员推理流水线（Feature-020）

**目标**：从零体验一次完整的"运动员视频上传 → 扫描 → 预处理 → 诊断 → 报告反查"链路，以 curl 动作为准，零前端依赖。

## 前置准备

1. **虚拟环境**：所有命令使用 `/opt/conda/envs/coaching/bin/python3.11`
2. **迁移就位**：
   ```bash
   /opt/conda/envs/coaching/bin/alembic upgrade head
   # 确认 head 为 0018_athlete_inference_pipeline
   ```
3. **环境变量** `.env` 新增：
   ```bash
   COS_VIDEO_ALL_ATHLETE=charhuang/tt_video/athletes/
   ```
4. **目录映射** `config/athlete_directory_map.json`：
   ```json
   {
     "张三": "张三",
     "李四": "李四"
   }
   ```
5. **服务**：API + `default` / `preprocessing` / `diagnosis` 三队列 worker + Celery Beat 均已启动（命令见项目规则 7）

## 步骤 1 · 上传运动员视频到 COS

把 2 条运动员测试视频按如下结构上传：

```
charhuang/tt_video/athletes/
└── 张三/
    ├── 正手攻球01.mp4
    └── 反手拨球02.mp4
```

## 步骤 2 · 触发素材扫描

```bash
curl -X POST http://127.0.0.1:8080/api/v1/athlete-classifications/scan \
  -H 'Content-Type: application/json' \
  -d '{"scan_mode":"full"}'
```

响应：
```json
{"success":true,"data":{"task_id":"<scan_task_id>","status":"pending"}}
```

轮询进度：
```bash
curl http://127.0.0.1:8080/api/v1/athlete-classifications/scan/<scan_task_id>
```

等到 `status=success` 为止。

### 幂等验证（SC-006 / spec US1-AC2）

再跑一次**增量扫描**，确认已扫描视频不重复入库：

```bash
curl -X POST http://127.0.0.1:8080/api/v1/athlete-classifications/scan \
  -H 'Content-Type: application/json' \
  -d '{"scan_mode":"incremental"}'
```

轮询直到 `status=success`，断言 `inserted=0 / updated=0 / skipped>=2`（即全部视作已入库）。

## 步骤 3 · 查看素材清单

```bash
curl 'http://127.0.0.1:8080/api/v1/athlete-classifications?page_size=20'
```

拿一条 `id`（记作 `<clf_id>`）进入下一步。

## 步骤 4 · 批量触发预处理

```bash
curl -X POST http://127.0.0.1:8080/api/v1/tasks/athlete-preprocessing/batch \
  -H 'Content-Type: application/json' \
  -d '{
    "items": [
      {"athlete_video_classification_id":"<clf_id_1>"},
      {"athlete_video_classification_id":"<clf_id_2>"}
    ]
  }'
```

等 `analysis_tasks.status=success`（或查 `GET /api/v1/video-preprocessing/{job_id}`）。

## 步骤 5 · 批量触发诊断

```bash
curl -X POST http://127.0.0.1:8080/api/v1/tasks/athlete-diagnosis/batch \
  -H 'Content-Type: application/json' \
  -d '{
    "items": [
      {"athlete_video_classification_id":"<clf_id_1>"},
      {"athlete_video_classification_id":"<clf_id_2>"}
    ]
  }'
```

拿到每条的 `task_id`，轮询：
```bash
curl http://127.0.0.1:8080/api/v1/tasks/<task_id>
```

完成后 `status='success'`、结果含 `overall_score` / `dimensions[]` / `improvement_advice`。

## 步骤 6 · 报告反查 → 验证三要素锚点

**按运动员查报告清单（SC-005）**：
```bash
curl 'http://127.0.0.1:8080/api/v1/diagnosis-reports?athlete_name=张三&page_size=10'
```

**按素材 key 反查所有版本**：
```bash
curl 'http://127.0.0.1:8080/api/v1/diagnosis-reports?cos_object_key=charhuang/tt_video/athletes/张三/正手攻球01.mp4'
```

两次请求结果应都含：`cos_object_key` / `preprocessing_job_id` / `standard_id + standard_version` 三要素锚点，SC-005 通过。

## 步骤 7 · 任务监控两侧互斥验证（SC-004）

```bash
# INFERENCE 侧：只应看到 scan_athlete_videos / preprocess_athlete_video / diagnose_athlete 三类任务
curl 'http://127.0.0.1:8080/api/v1/tasks?business_phase=INFERENCE&page_size=50'

# TRAINING 侧：不应看到本 feature 产生的任何任务
curl 'http://127.0.0.1:8080/api/v1/tasks?business_phase=TRAINING&page_size=50'
```

二次结果互斥、总数之和等于所有任务数 → SC-004 通过。

## 步骤 8 · 数据边界验证（SC-006）

连接 PostgreSQL：
```sql
-- 教练侧表应无任何本次扫描插入的行
SELECT COUNT(*) FROM coach_video_classifications
  WHERE cos_object_key LIKE 'charhuang/tt_video/athletes/%';  -- 预期 = 0

-- 运动员侧表应含全部本次扫描行
SELECT COUNT(*) FROM athlete_video_classifications
  WHERE cos_object_key LIKE 'charhuang/tt_video/athletes/%';  -- 预期 = 2
```

## 失败场景模拟

| 场景 | 操作 | 预期响应 |
|------|------|---------|
| 诊断未预处理素材 | 跳过步骤 4，直接执行步骤 5 | 409 `ATHLETE_VIDEO_NOT_PREPROCESSED` |
| 目标类别无 active 标准 | 临时把 `tech_standards` 的 active 行 archive，再诊断 | 409 `STANDARD_NOT_AVAILABLE` |
| COS 根路径错误 | `.env` 改为不存在的 `COS_VIDEO_ALL_ATHLETE`，重启 API 后扫描 | 扫描 task 最终 `status=failed`，`error_detail` 以 `ATHLETE_ROOT_UNREADABLE:` 起始 |
| 请求体多余字段 | `curl -d '{"scan_mode":"full","extra":"x"}'` | 422 `VALIDATION_FAILED` |
| 分页越界 | `?page_size=101` | 422 `VALIDATION_FAILED` |

## 清理

```bash
# 运动员侧清理（不触教练侧）
psql -c "DELETE FROM diagnosis_reports WHERE source='athlete_pipeline';"
psql -c "DELETE FROM athlete_video_classifications;"
psql -c "DELETE FROM athletes WHERE created_via='athlete_scan';"

# 预处理中间产物由 cleanup_intermediate_artifacts beat 任务 24h 内自动回收
```
