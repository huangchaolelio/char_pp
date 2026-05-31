# 错误码登记: Feature 023 技术分类重构

**关联**: [plan.md](../plan.md) / [data-model.md](../data-model.md)  
**章程依据**: 原则 IX «错误码集中化» —— `ErrorCode` 枚举 + `ERROR_STATUS_MAP` + `ERROR_DEFAULT_MESSAGE` 三表同步登记于 `src/api/errors.py`

---

## 新增错误码

| ErrorCode | HTTP | Default Message | 触发场景 | 调用方期望行为 |
|---|---|---|---|---|
| `ACTION_NOT_FOUND` | 404 | `动作不存在` | `GET /api/v1/standards?action=xxx` 或类似按 action 查询的接口，请求的 `action` 不在 `tech_actions` 字典 | 客户端校验请求参数；运营检查字典是否需要扩展 |
| `ACTION_DICTIONARY_VIOLATION` | 400 | `action 不在字典内` | `POST /api/v1/tasks` 或 athlete 提交体的 `action` 字段不在字典；LLM 兜底返回值落入此场景时由 service 层捕获后改为 `unclassified`，不外抛此码 | 客户端立即修正请求体；前端在提交前应根据 `GET /api/v1/admin/tech-actions` 做下拉校验 |
| `STANDARD_NOT_AVAILABLE_FOR_ACTION` | 503 | `该动作暂无 active 技术标准` | 诊断提交时 `tech_standards` 无 `(action, status='active')` 行 | 客户端展示运营页提示；运营到 KB 管理页发布该 action 的 active 标准 |

`details` 字段约定：

```json
// ACTION_NOT_FOUND
{ "action": "xxx" }

// ACTION_DICTIONARY_VIOLATION
{ "field": "action", "value": "xxx", "valid_count": 44 }

// STANDARD_NOT_AVAILABLE_FOR_ACTION
{ "action": "xxx" }
```

---

## 移除错误码（接口下线 — 物理删除路径）

| ErrorCode | 旧 HTTP | 移除原因 | 替代码 |
|---|---|---|---|
| `STANDARD_NOT_AVAILABLE` | 503 | 旧版按 `tech_category` 检索；分类体系重构后语义已不准确 | `STANDARD_NOT_AVAILABLE_FOR_ACTION` |

> 章程 v2.0.0 原则 IX 已明确「接口下线采用直接物理删除」。本错误码在 PR 中：
> - `src/api/errors.py::ErrorCode` 枚举删除该项
> - `ERROR_STATUS_MAP` / `ERROR_DEFAULT_MESSAGE` 删除该项
> - 旧合约测试 `tests/contract/test_athlete_inference.py::test_no_active_standard` 改名为 `test_no_active_standard_for_action`，断言新错误码

---

## 重命名错误码（语义保持）

| 旧 ErrorCode | 新 ErrorCode | HTTP | 重命名理由 |
|---|---|---|---|
| `NO_ACTIVE_KB_FOR_CATEGORY` | `NO_ACTIVE_KB_FOR_ACTION` | 400 | 单 active 约束作用域从 per-tech_category 变为 per-action |

> 重命名等价于「删除 + 新增」，遵循「已发布的错误码禁止改名或更换 HTTP 状态」的字面约束。但本 feature 是**章程级约束变化**（spec 业务阶段映射 + Clarifications Q2 已明确零兼容、全表 TRUNCATE），所有引用旧错误码的客户端契约同步在本 PR 中物理删除。

---

## 同步变更点（PR 必检）

| 文件 | 变更 |
|---|---|
| `src/api/errors.py::ErrorCode` | DELETE: `STANDARD_NOT_AVAILABLE`, `NO_ACTIVE_KB_FOR_CATEGORY`<br>ADD: `ACTION_NOT_FOUND`, `ACTION_DICTIONARY_VIOLATION`, `STANDARD_NOT_AVAILABLE_FOR_ACTION`, `NO_ACTIVE_KB_FOR_ACTION` |
| `src/api/errors.py::ERROR_STATUS_MAP` | 同步增删 4 项 |
| `src/api/errors.py::ERROR_DEFAULT_MESSAGE` | 同步增删 4 项 |
| `tests/unit/api/test_errors.py` | DELETE 旧码用例；ADD `test_action_not_found_maps_to_404` 等 4 项 |
| `tests/contract/test_action_dictionary_violation.py` | 🆕 |
| `docs/business-workflow.md § 7.4 错误码表` | 行级同步 |

---

## 合约测试矩阵（先于实现 RED）

```
tests/contract/test_action_dictionary_violation.py
  ✗ test_submit_task_with_invalid_action_returns_400
      POST /api/v1/tasks with task_kwargs.action="不存在的动作"
      EXPECT: 400 + error.code == "ACTION_DICTIONARY_VIOLATION"

  ✗ test_submit_task_without_action_returns_422
      POST /api/v1/tasks with task_kwargs missing action
      EXPECT: 422 + error.code == "VALIDATION_FAILED"

  ✗ test_diagnosis_no_active_standard_returns_503
      POST /api/v1/tasks type=athlete_diagnosis with action that has no active standard
      EXPECT: 503 + error.code == "STANDARD_NOT_AVAILABLE_FOR_ACTION"
      EXPECT: error.details.action == "<action>"

tests/contract/test_standards_action_query.py
  ✗ test_get_standards_with_unknown_action_returns_404
      GET /api/v1/standards?action=不存在
      EXPECT: 404 + error.code == "ACTION_NOT_FOUND"

  ✗ test_get_standards_with_legacy_tech_category_param_rejected
      GET /api/v1/standards?tech_category=forehand_topspin
      EXPECT: 422 (旧参数物理删除后被 FastAPI 默认拒绝) 或 400 + INVALID_QUERY_PARAM
```

> 所有合约测试在 `tasks.md` 中归到对应用户故事并标注「先于实现 RED」。
