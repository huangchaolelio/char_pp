# Contract — `GET /api/v1/curation-stats`（P3）

按教练 / `tech_category` / `curation_rubric_version` 维度聚合视频有效率。Feature-021 P3 用户故事 5。

---

## 请求

```http
GET /api/v1/curation-stats?group_by=coach&page=1&page_size=20 HTTP/1.1
GET /api/v1/curation-stats?group_by=tech_category&rubric_version=v1
GET /api/v1/curation-stats?group_by=rubric_version&coach_name=张继科
```

| Query 参数 | 类型 | 必填 | 说明 |
|-----------|-----|-----|------|
| `group_by` | string | 是 | `coach` / `tech_category` / `rubric_version` 三选一 |
| `coach_name` | string | 否 | 限定教练（仅 group_by=tech_category 或 rubric_version 时有意义）|
| `tech_category` | string | 否 | 限定类别（仅 group_by=coach 或 rubric_version 时有意义）|
| `rubric_version` | string | 否 | 限定规范版本（仅 group_by=coach 或 tech_category 时有意义）|
| `page` | int | 否 | 默认 1 |
| `page_size` | int | 否 | 默认 20，最大 100；越界 422 `INVALID_PAGE_SIZE` |

---

## 响应

### 成功（`group_by=coach`）

```json
{
  "success": true,
  "data": [
    {
      "coach_name": "张继科",
      "video_count": 45,
      "avg_accepted_duration_ratio": 0.72,
      "avg_validity_score": 0.78,
      "low_quality_video_count": 3,
      "with_overrides_video_count": 5
    },
    {
      "coach_name": "孙浩泓",
      "video_count": 120,
      "avg_accepted_duration_ratio": 0.61,
      "avg_validity_score": 0.69,
      "low_quality_video_count": 12,
      "with_overrides_video_count": 8
    }
  ],
  "meta": {
    "page": 1,
    "page_size": 20,
    "total": 12
  }
}
```

### 成功（`group_by=rubric_version`，用于版本对比 P3）

```json
{
  "success": true,
  "data": [
    {
      "curation_rubric_version": "v1",
      "video_count": 200,
      "avg_accepted_duration_ratio": 0.65,
      "avg_validity_score": 0.71,
      "low_quality_video_count": 18
    },
    {
      "curation_rubric_version": "v2",
      "video_count": 200,
      "avg_accepted_duration_ratio": 0.74,
      "avg_validity_score": 0.79,
      "low_quality_video_count": 8
    }
  ],
  "meta": {"page": 1, "page_size": 20, "total": 2}
}
```

### 错误

| HTTP | code | 触发场景 |
|------|------|---------|
| 422 | `VALIDATION_FAILED` | `group_by` 非枚举 / 缺失 |
| 400 | `INVALID_PAGE_SIZE` | `page_size > 100` 或 `< 1` |

---

## 行为契约

1. **只读**：本接口不写任何表
2. **聚合源**：基于 `video_curation_jobs` + `coach_video_classifications` + `coaches` 三表 JOIN，仅统计 `status='success'` 的作业
3. **筛选互斥**：`group_by` 与同名筛选参数互斥（如 `group_by=coach` + `coach_name=...` 等价于"按指定教练单条返回"）
4. **样本量保护**：`video_count < 5` 的分组项可在响应中标记 `low_sample=true`（避免拉偏聚合可信度）— 由 service 层在数据后处理时附加

---

## 合约测试用例（`tests/contract/test_curation_stats.py`）

1. ✅ `group_by=coach`，分页正常
2. ✅ `group_by=tech_category`，限定 coach_name
3. ✅ `group_by=rubric_version`，对比 v1 vs v2
4. ❌ `group_by` 缺失 ⇒ 422
5. ❌ `page_size=200` ⇒ 400 INVALID_PAGE_SIZE
6. ✅ 数据源为空 ⇒ 200 + `data=[]`、`meta.total=0`
