# 快速入门: 技术标准构建与查询

**功能**: 010-build-technique-standard
**前提**: 已有 ExpertTechPoint 数据（Feature-001/002 已跑通）

---

## 1. 触发单项技术标准构建

```bash
curl -X POST http://localhost:8000/api/v1/standards/build \
  -H "Content-Type: application/json" \
  -d '{"tech_category": "forehand_topspin"}'
```

预期响应（同步返回，结果直接附在响应体中）：
```json
{
  "task_id": "uuid-xxx",
  "mode": "single",
  "tech_category": "forehand_topspin",
  "result": {"result": "success", "version": 1, "dimension_count": 5, "coach_count": 3}
}
```

> **注**: 采用同步实现，无需轮询。`task_id` 仅作幂等性标识，无对应查询端点。

---

## 2. 查询技术标准

```bash
curl http://localhost:8000/api/v1/standards/forehand_topspin
```

预期响应包含各维度的 `ideal`（中位数）、`min`（P25）、`max`（P75）参数。

---

## 3. 全量批量构建（首次部署）

```bash
curl -X POST http://localhost:8000/api/v1/standards/build \
  -H "Content-Type: application/json" \
  -d '{}'
```

查询汇总结果中 `summary.success_count` 确认构建完成数量。

---

## 4. 查询所有标准摘要

```bash
curl "http://localhost:8000/api/v1/standards?source_quality=multi_source"
```
