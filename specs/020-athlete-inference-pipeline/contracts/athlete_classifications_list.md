# 契约 · GET /api/v1/athlete-classifications

**Feature**: 020-athlete-inference-pipeline
**用户故事**: US1（列表）+ US5（按运动员/类别筛选）
**方法**: GET
**路径**: `/api/v1/athlete-classifications`

## 查询参数

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `page` | int ≥ 1 | 否 | 1 | 页码 |
| `page_size` | int ∈ [1, 100] | 否 | 20 | 每页数量；越界 422 `INVALID_PAGE_SIZE`（Pydantic `le=100`，章程硬约束） |
| `athlete_id` | UUID | 否 | — | 按运动员过滤 |
| `athlete_name` | str | 否 | — | 按运动员姓名精确匹配（辅助筛选） |
| `tech_category` | 枚举 21 值 | 否 | — | 按技术类别过滤；非法值 400 `INVALID_ENUM_VALUE` |
| `preprocessed` | bool | 否 | — | 是否已完成预处理 |
| `has_diagnosis` | bool | 否 | — | `last_diagnosis_report_id IS NOT NULL`（便于查"从未诊断过"的素材） |
| `sort_by` | enum `created_at` / `updated_at` | 否 | `created_at` | — |
| `order` | `asc` / `desc` | 否 | `desc` | — |

## 成功响应 200 OK

```json
{
  "success": true,
  "data": [
    {
      "id": "5e4c...",
      "cos_object_key": "charhuang/tt_video/athletes/张三/正手攻球01.mp4",
      "athlete_name": "张三",
      "name_source": "map",
      "tech_category": "forehand_attack",
      "classification_source": "rule",
      "classification_confidence": 1.0,
      "preprocessed": true,
      "preprocessing_job_id": "9ff0...",
      "last_diagnosis_report_id": null,
      "created_at": "2026-04-30T20:10:00+08:00",
      "updated_at": "2026-04-30T20:12:05+08:00"
    }
  ],
  "meta": { "page": 1, "page_size": 20, "total": 128 }
}
```

## 错误响应

| HTTP | ErrorCode | 触发场景 |
|------|-----------|---------|
| 400 | `INVALID_ENUM_VALUE` | `tech_category` / `sort_by` / `order` 非法 |
| 422 | `VALIDATION_FAILED` | `page_size > 100`、`page < 1`、`athlete_id` 非 UUID |

## 合约测试 `tests/contract/test_athlete_classifications_list.py`

必须覆盖：
- [ ] 默认分页 `page=1, page_size=20` + `meta.total` 真实计数
- [ ] `page_size=101` → 422 `VALIDATION_FAILED`
- [ ] `page_size=0` → 422 `VALIDATION_FAILED`
- [ ] `tech_category='forehand_attack'` 正确过滤
- [ ] `tech_category='invalid'` → 400 `INVALID_ENUM_VALUE` + `details.allowed` 含 21 类
- [ ] `has_diagnosis=true` 过滤出 `last_diagnosis_report_id IS NOT NULL` 的行
- [ ] **跨污染隔离**：当库里同时有教练侧 `coach_video_classifications` 行时，本接口一条也不返回教练侧数据（SC-006）
- [ ] 排序 `sort_by=updated_at, order=asc`：升序成立
