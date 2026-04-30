# 契约 · GET /api/v1/diagnosis-reports

**Feature**: 020-athlete-inference-pipeline
**用户故事**: US5（P3）— 运动员素材/报告的可追溯与清单化查询
**方法**: GET
**路径**: `/api/v1/diagnosis-reports`（新资源路由）

> **设计说明**：本路径为**新增**；现有 `GET /api/v1/tasks/{task_id}` 只能按任务 ID 反查单份报告，无法按"运动员"或"技术类别"聚合。本接口承载 US5 的聚合查询能力。

## 查询参数

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `page` | int ≥ 1 | 否 | 1 | — |
| `page_size` | int ∈ [1, 100] | 否 | 20 | 越界 422 `VALIDATION_FAILED` |
| `athlete_id` | UUID | 否 | — | 按运动员 |
| `athlete_name` | str | 否 | — | 按运动员姓名精确匹配 |
| `tech_category` | 枚举 21 值 | 否 | — | — |
| `cos_object_key` | str | 否 | — | 按素材 key 反查所有版本 |
| `preprocessing_job_id` | UUID | 否 | — | 按预处理 job 反查 |
| `source` | `legacy` / `athlete_pipeline` | 否 | — | 区分报告来源；默认返回两者 |
| `created_after` / `created_before` | ISO 8601 | 否 | — | 时间窗过滤 |
| `sort_by` | `created_at` / `overall_score` | 否 | `created_at` | — |
| `order` | `asc` / `desc` | 否 | `desc` | — |

**`athlete_id` 与 `athlete_name` 联合使用时**：以 `athlete_id` 为主（精确 UUID 匹配，忽略 name）。

## 成功响应 200 OK

```json
{
  "success": true,
  "data": [
    {
      "id": "c7a5...",
      "tech_category": "forehand_attack",
      "overall_score": 82.5,
      "standard_id": 17,
      "standard_version": 3,
      "cos_object_key": "charhuang/tt_video/athletes/张三/正手攻球01.mp4",
      "preprocessing_job_id": "9ff0...",
      "source": "athlete_pipeline",
      "created_at": "2026-04-30T20:18:00+08:00"
    }
  ],
  "meta": { "page": 1, "page_size": 20, "total": 7 }
}
```

**注意**：列表响应**不含** `dimensions[]` 详情，保持 payload 精简；详情走现有 `GET /api/v1/tasks/{task_id}`（任务级视图）或**可选**新增 `GET /api/v1/diagnosis-reports/{id}`（阶段 2 任务清单中标记为 P3 可延后）。

## 错误响应

| HTTP | ErrorCode | 触发场景 |
|------|-----------|---------|
| 400 | `INVALID_ENUM_VALUE` | `tech_category` / `source` / `sort_by` / `order` 非法 |
| 422 | `VALIDATION_FAILED` | 分页参数越界、UUID 格式错、时间格式错 |

## 合约测试 `tests/contract/test_athlete_reports_list.py`

必须覆盖：
- [ ] 默认分页 + 按 `created_at` 倒序
- [ ] 按 `athlete_id` 过滤：只返回该运动员的报告
- [ ] 按 `cos_object_key` 过滤：返回该素材的所有历史版本（Q3 决议：每次诊断新建报告）
- [ ] 按 `preprocessing_job_id` 过滤：返回该 job 下的所有诊断报告
- [ ] `source='athlete_pipeline'` 过滤：不返回 F-011/F-013 旧行（SC-006 侧边验证）
- [ ] `source='invalid'` → 400 `INVALID_ENUM_VALUE`
- [ ] `page_size=200` → 422 `VALIDATION_FAILED`
- [ ] 同一运动员 2 次诊断同一素材 → 返回 2 条，时间倒序
