# 快速入门: 全量任务查询接口

**功能**: 012-task-query-all
**日期**: 2026-04-23

## 前置条件

- API 服务运行在 `http://localhost:8080`（或 8002）
- 系统中存在至少一条 analysis_tasks 记录

---

## 1. 获取全量任务列表（默认分页）

```bash
curl -s "http://localhost:8080/api/v1/tasks" | python3 -m json.tool
```

预期响应结构：
```json
{
  "items": [...],
  "total": 100,
  "page": 1,
  "page_size": 20,
  "total_pages": 5
}
```

---

## 2. 按状态筛选（只看失败任务）

```bash
curl -s "http://localhost:8080/api/v1/tasks?status=failed&page=1&page_size=50"
```

---

## 3. 按任务类型 + 时间范围筛选

```bash
curl -s "http://localhost:8080/api/v1/tasks?task_type=expert_video&created_after=2026-04-01T00:00:00Z&created_before=2026-04-23T23:59:59Z"
```

---

## 4. 按完成时间倒序（NULL 排末尾）

```bash
curl -s "http://localhost:8080/api/v1/tasks?sort_by=completed_at&order=desc&page_size=10"
```

---

## 5. 查看单任务完整关联统计（扩展详情）

```bash
TASK_ID="550e8400-e29b-41d4-a716-446655440000"
curl -s "http://localhost:8080/api/v1/tasks/${TASK_ID}" | python3 -m json.tool
```

响应中新增 `summary` 字段：
```json
{
  "task_id": "...",
  "status": "success",
  "summary": {
    "tech_point_count": 42,
    "has_transcript": true,
    "semantic_segment_count": 8,
    "motion_analysis_count": 0,
    "deviation_count": 0,
    "advice_count": 0
  }
}
```

---

## 6. 参数校验错误示例

```bash
# 非法 status 值 → 400
curl -s "http://localhost:8080/api/v1/tasks?status=invalid_status"
# 响应: {"detail": "Invalid status value: 'invalid_status'. Valid values: ..."}

# page 超出截断 → 200，page_size 自动截为 200
curl -s "http://localhost:8080/api/v1/tasks?page_size=999"
# 响应中 page_size 字段值为 200
```

---

## 运行测试

```bash
cd /data/charhuang/char_ai_coding/charhuang_pp_cn
source .venv/bin/activate
pytest tests/contract/test_task_list_api.py -v
pytest tests/integration/test_task_list.py -v
```
