# Feature-019 Retirement Ledger — 下线接口台账

> 章程原则 IX 强制：已下线接口必须保留哨兵路由（抛 `AppException(ENDPOINT_RETIRED, ...)`），禁止物理删除；
> 下线条目只可追加，不可删除；已发布的错误码禁止改名或更换 HTTP 状态（只允许新增）。
>
> 双份台账——本文件（文档侧） + `src/api/routers/_retired.py::RETIREMENT_LEDGER`（代码侧）同步维护。

---

## 条目 1 — `GET /api/v1/knowledge-base/{version}`

| 属性 | 值 |
|------|------|
| **方法** | `GET` |
| **路径** | `/api/v1/knowledge-base/{version}` |
| **状态** | 已下线（Retired） |
| **下线版本** | Feature-019 |
| **下线提交** | `c5d450c` (master)|
| **迁移指引** | 使用 `GET /api/v1/knowledge-base/versions/{tech_category}/{version}` — Feature-019 将 KB 主键提升为 `(tech_category, version)` 复合键，详情路径必须同时指定两者 |
| **哨兵位置** | [`src/api/routers/knowledge_base.py::_retired_detail_single_key`](../../../src/api/routers/knowledge_base.py) |
| **错误码** | `ENDPOINT_RETIRED`（HTTP 404）|
| **details 字段** | `{"successor": "/api/v1/knowledge-base/versions/{tech_category}/{version}", "migration_note": "Feature-019 详情路径需同时指定 tech_category 与 version"}` |

---

## 条目 2 — `POST /api/v1/knowledge-base/{version}/approve`

| 属性 | 值 |
|------|------|
| **方法** | `POST` |
| **路径** | `/api/v1/knowledge-base/{version}/approve` |
| **状态** | 已下线（Retired） |
| **下线版本** | Feature-019 |
| **下线提交** | `c5d450c` (master) |
| **迁移指引** | 使用 `POST /api/v1/knowledge-base/versions/{tech_category}/{version}/approve` — 审批作用域从"全局单 active"变更为"每 tech_category 单 active"，必须指定 tech_category 才能避免跨类别副作用（Feature-019 US1 核心行为） |
| **哨兵位置** | [`src/api/routers/knowledge_base.py::_retired_approve_single_key`](../../../src/api/routers/knowledge_base.py) |
| **错误码** | `ENDPOINT_RETIRED`（HTTP 404）|
| **details 字段** | `{"successor": "/api/v1/knowledge-base/versions/{tech_category}/{version}/approve", "migration_note": "Feature-019 将 KB 主键提升为 (tech_category, version) 复合键；请使用新路径"}` |

---

## 条目 3 — `POST /api/v1/standards/build` without `tech_category` (批量模式)

| 属性 | 值 |
|------|------|
| **方法** | `POST` |
| **路径** | `/api/v1/standards/build` |
| **状态** | 参数契约变更（`tech_category` 从可选变为必填）|
| **下线版本** | Feature-019 |
| **下线提交** | `c5d450c` (master) |
| **迁移指引** | 必须显式传 `{"tech_category": "<21 类之一>"}`；不再支持"缺省即批量 build 全部"语义（FR-015） |
| **哨兵位置** | Pydantic 校验层（`BuildRequest.tech_category: str`，缺失自动 422 `VALIDATION_FAILED`）|
| **错误码** | `VALIDATION_FAILED`（HTTP 422；参数层校验）|

> 注：此条不产生 `ENDPOINT_RETIRED` 哨兵——路径/方法不变，只是请求体字段约束变更，按章程属于"请求体 Schema 兼容性调整"而非"接口下线"。合约层面旧客户端的空请求直接由 Pydantic 返回 422，消息明确告知需补 `tech_category`。

---

## 更新规则

1. **只追加不删除**：任何既存条目一旦登记即为永久档，即使哨兵代码被重构也不得从本文件移除
2. **错误码稳定性**：已登记的 `successor` 路径、`migration_note`、错误码 HTTP 状态不得修改
3. **新下线条目加在文末**，编号连续（条目 4、5、…）
4. **对应代码侧**：同步更新 `src/api/routers/_retired.py::RETIREMENT_LEDGER`（若该 ledger 文件不存在则以本文档为唯一事实来源）
5. **pre-push hook** `spec_compliance.py` 会扫描本文件确保哨兵路由在 `*.py` 中可溯源

---

## 章程交叉索引

- 章程 v1.4.0 原则 IX：错误信封 + ENDPOINT_RETIRED 哨兵
- Feature-017 retirement-ledger.md（全局基线）：[specs/017-api-standardization/contracts/retirement-ledger.md](../../017-api-standardization/contracts/retirement-ledger.md)
- Feature-019 spec.md FR-025/FR-026：per-category 改造的向前兼容策略
