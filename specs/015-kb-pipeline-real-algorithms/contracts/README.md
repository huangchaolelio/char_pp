# Contracts — Feature-015

本 Feature **不引入新的 API 或 schema 契约**。所有对外接口沿用：

- **`POST /api/v1/tasks/kb-extraction`** — Feature-013 定义，Feature-014 扩展（内部创建 ExtractionJob）。Feature-015 不改端点签名，只让内部 step executor 产出真实数据。
- **`GET /api/v1/extraction-jobs`** / **`GET /api/v1/extraction-jobs/{id}`** / **`POST /api/v1/extraction-jobs/{id}/rerun`** — Feature-014 定义。响应结构不变，仅 `steps[*].output_summary` 的值从 scaffold 变为真实算法产出。

## 内部契约

本 Feature 涉及的契约为 **跨 executor 的 artifact 文件格式** + **kb_items dict 结构**，全部已在 [../data-model.md](../data-model.md) 中记录，遵循 Q4 决策（无 schema 版本、容错解析）。

## 错误码约定

见 [../data-model.md § 错误码约定（FR-016）](../data-model.md#错误码约定fr-016)。
