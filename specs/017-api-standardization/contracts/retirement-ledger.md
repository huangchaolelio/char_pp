# 已下线接口台账（RetirementLedger）

**版本**: v1.0（Feature-017 首发）
**权威来源**: `src/api/routers/_retired.py::RETIREMENT_LEDGER`
**下线日**: Feature-017 合入日（Big Bang 策略，无废弃期）
**下线方式**: 保留哨兵路由（方法+路径不变），处理器统一抛 `AppException(ErrorCode.ENDPOINT_RETIRED, details=...)`，返回 HTTP 404 + 统一错误信封

## 下线接口清单

| # | 旧方法 | 旧路径 | 替代路径（successor） | 语义差异说明 |
|---|---|---|---|---|
| 1 | POST | `/api/v1/tasks/expert-video` | `/api/v1/tasks/classification` + `/api/v1/tasks/kb-extraction` | **由单步调用改为两步独立提交**：先用 classification 完成视频分类，再用 kb-extraction 提取知识库。调用方需自行串联。 |
| 2 | POST | `/api/v1/tasks/athlete-video` | `/api/v1/tasks/diagnosis` | 路径改名，请求/响应字段保持不变；新接口走专属 diagnosis 队列（Feature-013 后的架构）。 |
| 3 | GET | `/api/v1/videos/classifications` | `/api/v1/classifications` | 资源前缀变更；查询参数 `tech_category`、`kb_extracted` 等保持不变。 |
| 4 | POST | `/api/v1/videos/classifications/refresh` | `/api/v1/classifications/scan` | 接口改名 `refresh` → `scan`，行为一致（Celery 异步全量扫描 COS）；返回 `task_id` 供轮询。 |
| 5 | PATCH | `/api/v1/videos/classifications/{cos_object_key}` | `/api/v1/classifications/{id}` | **路径参数由 COS 对象键改为分类记录 ID**；调用方需先通过 GET `/classifications?...` 获取记录 ID。 |
| 6 | POST | `/api/v1/videos/classifications/batch-submit` | `/api/v1/tasks/kb-extraction/batch` | 批量提交迁移到任务通道的批量入口；请求体字段命名可能不同，需按新接口 schema 重写。 |
| 7 | POST | `/api/v1/diagnosis` | `/api/v1/tasks/diagnosis` | **同步 60s 响应改为异步提交**：新接口返回 `task_id`，调用方必须轮询 `GET /api/v1/tasks/{task_id}` 拉取最终结果；时序语义差异由调用方承担改造。 |

**合计**: 7 条端点（spec.md FR-009 宏观列 6 条；`videos/classifications*` 通配符展开为独立 3 条）。

## 响应示例

调用任一已下线接口（如 `POST /api/v1/tasks/expert-video`）时：

```http
HTTP/1.1 404 Not Found
Content-Type: application/json

{
  "success": false,
  "error": {
    "code": "ENDPOINT_RETIRED",
    "message": "该接口已下线，请调用替代接口",
    "details": {
      "successor": [
        "/api/v1/tasks/classification",
        "/api/v1/tasks/kb-extraction"
      ],
      "migration_note": "原一次调用改为两次独立提交：先分类再提取 KB"
    }
  }
}
```

## 与 FastAPI 未匹配路由的区别

| 场景 | HTTP | 响应体 |
|---|---|---|
| 调用已下线接口（本台账中） | 404 | `{"success":false,"error":{"code":"ENDPOINT_RETIRED","details":{"successor":...}}}` |
| 调用完全不存在的路径（例如拼错） | 404 | `{"success":false,"error":{"code":"NOT_FOUND","message":"未找到对应路由","details":null}}` |

两者 HTTP 状态相同，靠 `error.code` 区分——这是澄清决策 Q3 的核心依据。

## 变更规则

1. **本台账禁止删除已下线的条目**（审计需要），只允许追加
2. 未来新增下线接口时，同步更新：
   - 本文件新增一行
   - `src/api/routers/_retired.py::RETIREMENT_LEDGER` 追加元组元素
   - `tests/contract/test_retirement_contract.py` 新增对应的 404 断言
3. **禁止复用已下线路径**：任何新接口不得与本台账中的路径冲突（哪怕 Method 不同）；若需要，先在台账中标注 "reused by Feature-NNN"

## 调用方迁移指南

前端/SDK 在收到 `error.code == "ENDPOINT_RETIRED"` 时，应：

1. 读取 `error.details.successor`（字符串或字符串数组）
2. 如果是字符串数组（如上例的分类→提取），意味着需要**按顺序**调用多个新接口
3. 参考 `error.details.migration_note` 理解语义差异（如同步→异步）
4. 在调用方代码中**硬性删除**对旧路径的调用（合入日起不允许再出现）
