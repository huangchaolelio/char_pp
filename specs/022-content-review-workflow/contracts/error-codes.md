# Feature-022 错误码登记

**关联**: [plan.md](../plan.md) | [contracts/content-reviews.yaml](./content-reviews.yaml)
**章程依据**: 原则 IX「错误码集中化」— 所有新增错误码必须同时在 `src/api/errors.py` 三张映射表（`ErrorCode` 枚举 / `ERROR_STATUS_MAP` / `ERROR_DEFAULT_MESSAGE`）登记，并在本文件留痕。

---

## 1. 新增错误码清单

| ErrorCode | HTTP 状态 | 默认消息（开发者可读） | 触发场景 | 关联 FR |
|-----------|----------|---------------------|---------|--------|
| `CONTENT_NOT_REVIEWED` | 409 Conflict | 视频尚未通过内容审核，请先在审核工作台提交决策 | KB 抽取入口校验：来源条目 `review_state=pending_review` | FR-008 / FR-009 |
| `CONTENT_REVIEW_REJECTED` | 409 Conflict | 视频审核已被拒绝，无法进入训练阶段 | KB 抽取入口校验：来源条目 `review_state=rejected` | FR-009 / FR-010a |
| `CONTENT_REVIEW_STALE` | 409 Conflict | 视频已重新清洗，原审核结论已失效，请重新审核 | KB 抽取入口校验：来源条目 `review_state=stale` | FR-009 / FR-011 / FR-011a |
| `REVIEW_VERSION_CONFLICT` | 409 Conflict | 审核条目已被他人更新，请刷新后重试 | 决策提交时 `expected_review_version` 与服务端值不一致（乐观锁） | FR-007a 隐含约束 |
| `REVIEW_NOT_PENDING` | 409 Conflict | 该条目当前不在待审核状态，无法决策 | 决策提交时 `review_state` 不在 `{pending_review, stale}` | FR-006 / FR-011 |
| `INVALID_REVIEWER_IDENTITY` | 400 Bad Request | 请求头与请求体中的审核员标识不一致 | `X-Reviewer-Id` header 与请求体 `reviewer_id` 不等 | R5（鉴权研究决策）|
| `REJECTED_REQUIRES_REASON` | 400 Bad Request | 拒绝决策必须提供 reason_code | `decision=rejected` 但缺少 `reason_code` | FR-010 |
| `REVIEW_GATE_INVALID_STATE` | 400 Bad Request | 审核门开关参数非法 | `PATCH /admin/review-gate` 请求体校验失败 | FR-014 |

---

## 2. `src/api/errors.py` 同步要求

每个新增 code 必须同步更新 3 处：

### 2.1 `ErrorCode` 枚举

```python
class ErrorCode(str, Enum):
    # ... 既有 ...
    # ── Feature-022: 内容审核门 ────────────────────────────
    CONTENT_NOT_REVIEWED = "CONTENT_NOT_REVIEWED"
    CONTENT_REVIEW_REJECTED = "CONTENT_REVIEW_REJECTED"
    CONTENT_REVIEW_STALE = "CONTENT_REVIEW_STALE"
    REVIEW_VERSION_CONFLICT = "REVIEW_VERSION_CONFLICT"
    REVIEW_NOT_PENDING = "REVIEW_NOT_PENDING"
    INVALID_REVIEWER_IDENTITY = "INVALID_REVIEWER_IDENTITY"
    REJECTED_REQUIRES_REASON = "REJECTED_REQUIRES_REASON"
    REVIEW_GATE_INVALID_STATE = "REVIEW_GATE_INVALID_STATE"
```

### 2.2 `ERROR_STATUS_MAP`

```python
ERROR_STATUS_MAP = {
    # ... 既有 ...
    ErrorCode.CONTENT_NOT_REVIEWED: HTTPStatus.CONFLICT,           # 409
    ErrorCode.CONTENT_REVIEW_REJECTED: HTTPStatus.CONFLICT,        # 409
    ErrorCode.CONTENT_REVIEW_STALE: HTTPStatus.CONFLICT,           # 409
    ErrorCode.REVIEW_VERSION_CONFLICT: HTTPStatus.CONFLICT,        # 409
    ErrorCode.REVIEW_NOT_PENDING: HTTPStatus.CONFLICT,             # 409
    ErrorCode.INVALID_REVIEWER_IDENTITY: HTTPStatus.BAD_REQUEST,   # 400
    ErrorCode.REJECTED_REQUIRES_REASON: HTTPStatus.BAD_REQUEST,    # 400
    ErrorCode.REVIEW_GATE_INVALID_STATE: HTTPStatus.BAD_REQUEST,   # 400
}
```

### 2.3 `ERROR_DEFAULT_MESSAGE`

```python
ERROR_DEFAULT_MESSAGE = {
    # ... 既有 ...
    ErrorCode.CONTENT_NOT_REVIEWED: "视频尚未通过内容审核，请先在审核工作台提交决策",
    ErrorCode.CONTENT_REVIEW_REJECTED: "视频审核已被拒绝，无法进入训练阶段",
    ErrorCode.CONTENT_REVIEW_STALE: "视频已重新清洗，原审核结论已失效，请重新审核",
    ErrorCode.REVIEW_VERSION_CONFLICT: "审核条目已被他人更新，请刷新后重试",
    ErrorCode.REVIEW_NOT_PENDING: "该条目当前不在待审核状态，无法决策",
    ErrorCode.INVALID_REVIEWER_IDENTITY: "请求头与请求体中的审核员标识不一致",
    ErrorCode.REJECTED_REQUIRES_REASON: "拒绝决策必须提供 reason_code",
    ErrorCode.REVIEW_GATE_INVALID_STATE: "审核门开关参数非法",
}
```

---

## 3. 错误响应示例（统一信封）

### 3.1 `CONTENT_NOT_REVIEWED`（409）

```json
{
  "success": false,
  "error": {
    "code": "CONTENT_NOT_REVIEWED",
    "message": "视频尚未通过内容审核，请先在审核工作台提交决策",
    "details": {
      "cos_object_key": "charhuang/tt_video/.../001.mp4",
      "cvclf_id": "11111111-2222-3333-4444-555555555555",
      "current_review_state": "pending_review"
    }
  }
}
```

### 3.2 `REVIEW_VERSION_CONFLICT`（409）

```json
{
  "success": false,
  "error": {
    "code": "REVIEW_VERSION_CONFLICT",
    "message": "审核条目已被他人更新，请刷新后重试",
    "details": {
      "cvclf_id": "11111111-2222-3333-4444-555555555555",
      "expected_version": 0,
      "current_version": 1
    }
  }
}
```

### 3.3 `INVALID_REVIEWER_IDENTITY`（400）

```json
{
  "success": false,
  "error": {
    "code": "INVALID_REVIEWER_IDENTITY",
    "message": "请求头与请求体中的审核员标识不一致",
    "details": {
      "header_value": "ops-zhangwei",
      "body_value": "ops-lihua"
    }
  }
}
```

---

## 4. 不变性约束（章程 IX）

- 已发布的错误码**禁止改名或更换 HTTP 状态**，只允许新增
- `details` 是"可选结构化上下文"，不进入合约稳定性承诺；客户端不应硬编码 `details` 字段
- `message` 仅作为开发者可读消息；面向终端用户的文案应在前端基于 `code` 构造

---

## 5. 合约测试覆盖要求

`tests/contract/test_022_content_reviews_contract.py` MUST 覆盖：

- [ ] 每个新错误码至少 1 条触发用例（共 8 条）
- [ ] 错误信封形态：`success=false`、含 `error.code` / `error.message`、不含 `data` / `meta`
- [ ] HTTP 状态码与 `ERROR_STATUS_MAP` 一致
- [ ] 默认消息与 `ERROR_DEFAULT_MESSAGE` 一致（除非显式覆盖）

合约测试 MUST 在路由实现之前创建，且在实现开始前处于失败状态（章程原则 II）。
