# Feature-019 错误码登记

**日期**: 2026-04-30
**关联章程条款**: 章程原则 IX（API 接口规范统一）+ 原则 X（业务流程对齐 · § 7.4 错误码双向同步）

## 本 Feature 新增错误码

| Code | HTTP | 默认消息 | 触发场景 |
|------|------|---------|---------|
| `KB_CONFLICT_UNRESOLVED` | 409 | "知识库存在未解决的冲突点" | `approve` 时检测到该 `(tech_category, version)` 下仍有 `expert_tech_points.conflict_flag=true` 的记录 |
| `KB_EMPTY_POINTS` | 409 | "知识库为空，无法批准" | `approve` 时目标记录的 `point_count=0` |
| `NO_ACTIVE_KB_FOR_CATEGORY` | 409 | "该技术类别无已激活的知识库" | `POST /standards/build` 或 诊断读路径 请求一个类别但 `tech_knowledge_bases` 中该类别无 `status='active'` 行 |
| `STANDARD_ALREADY_UP_TO_DATE` | 409 | "标准已是最新，无需重建" | `POST /standards/build` 请求的类别当前 active KB 指纹与已存在的 active standard 指纹相同（幂等拒绝） |

## 本 Feature 复用现有错误码

| Code | HTTP | 复用场景 |
|------|------|---------|
| `VALIDATION_FAILED` | 422 | `POST /standards/build` 未传 `tech_category`；`approve` 请求 `approved_by` 缺失；`page_size` 非整数 |
| `INVALID_PAGE_SIZE` | 400 | `page_size > 100` 或 `< 1` |
| `INVALID_ENUM_VALUE` | 400 | `?status=` / `?tech_category=` 传入非法枚举值 |
| `KB_VERSION_NOT_FOUND` | 404 | `approve` / detail 找不到目标 `(tech_category, version)` |
| `KB_VERSION_NOT_DRAFT` | 409 | `approve` 时目标记录 `status != 'draft'` |
| `EXTRACTION_JOB_NOT_FOUND` | 404 | `GET /extraction-jobs/{id}` 找不到作业 |
| `INTERNAL_ERROR` | 500 | 未预期异常（含 `logging.exception`） |

## 登记位置

新增 code MUST 同步落到以下 3 处，作为单一事实来源（章程原则 IX）：

1. **`src/api/errors.py::ErrorCode`** — 枚举值定义
2. **`src/api/errors.py::ERROR_STATUS_MAP`** — HTTP 状态码映射
3. **`src/api/errors.py::ERROR_DEFAULT_MESSAGE`** — 默认消息映射
4. **本文件**（`contracts/error-codes.md`）— Feature 级登记
5. **`docs/business-workflow.md` § 7.4** — 全局错误码总表（由 refresh-docs skill 在实现阶段刷新）

## 兼容性声明

- 已发布的错误码 **不得改名** 或更换 HTTP 状态（章程原则 IX 强制）。
- 本 Feature 的 4 个新 code 均为首次发布，历史无冲突。
- 未来本 Feature 所在的业务范围若需要新 code，优先扩展 `KB_*` / `STANDARD_*` / `TIP_*` 前缀以保持命名一致。
