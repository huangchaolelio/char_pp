# API 规范化开发指南

> Feature-017 合入后的 API 开发统一规范（对齐章程 v2.0.0 原则 IX）
>
> 权威参考：
> - `specs/017-api-standardization/contracts/response-envelope.schema.json`
> - `specs/017-api-standardization/contracts/error-codes.md`

本文档面向新成员，帮助你在 5 分钟内掌握向本仓库新增/变更 API 的约束与模板。

---

## 1. 路径命名

| 维度 | 规则 | 正例 | 反例 |
|---|---|---|---|
| 版本前缀 | 统一 `/api/v1/`（由 `main.py::include_router(prefix="/api/v1")` 单点拼接） | `/api/v1/tasks` | `/tasks` / `/v1/tasks` |
| 资源段 | kebab-case 复数名词 | `/teaching-tips` | `/teachingTips` / `/teaching_tip` |
| 动作段 | kebab-case 动词 | `/scan` / `/approve` / `/refresh` | `/scanVideos` |
| ID 段 | `{resource_id}`（下划线 + `_id` 后缀） | `/tasks/{task_id}` | `/tasks/{id}` / `/tasks/{taskId}` |
| 枚举路径参数 | snake_case 业务名词（如 `{task_type}`、`{version}`、`{tech_category}`） | `/task-channels/{task_type}` | `/task-channels/{type}` |

**自动校验**：运行 `python scripts/lint_api_naming.py`，0 违规即合规。

---

## 2. 响应信封

所有 `/api/v1/**` 接口响应体必须匹配下列两种信封之一（顶层 `success` 布尔位作为判别式）：

### 2.1 成功信封

```json
{
  "success": true,
  "data": <业务载荷>,
  "meta": { "page": 1, "page_size": 20, "total": 42 }
}
```

- `data`：单对象 / 列表 / `null`
- `meta`：列表/分页接口非空；非列表接口为 `null`

**构造方式**（路由层代码）：

```python
from src.api.schemas.envelope import SuccessEnvelope, ok, page as page_envelope

# 非分页
@router.get("/coaches/{coach_id}", response_model=SuccessEnvelope[CoachResponse])
async def get_coach(...) -> SuccessEnvelope[CoachResponse]:
    return ok(CoachResponse.model_validate(coach))

# 分页
@router.get("/tasks", response_model=SuccessEnvelope[list[TaskListItemResponse]])
async def list_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    ...
) -> SuccessEnvelope[list[TaskListItemResponse]]:
    ...
    return page_envelope(items, page=page, page_size=page_size, total=total)
```

**禁止**：手写 `return {"success": True, "data": ...}` 字典；必须通过 `ok()` / `page()` 构造器。

### 2.2 错误信封

```json
{
  "success": false,
  "error": {
    "code": "TASK_NOT_FOUND",
    "message": "任务不存在",
    "details": { "task_id": "abc" }
  }
}
```

**抛出方式**：

```python
from src.api.errors import AppException, ErrorCode

raise AppException(
    ErrorCode.TASK_NOT_FOUND,
    details={"task_id": str(task_id)},
)
```

由全局异常处理器自动转为 404 + 错误信封；不需要自己写 `JSONResponse`。

**禁止**：
- `raise HTTPException(...)` — 用 `AppException` 替代
- `return {"code": "XXX", "message": "..."}` — 用 `raise AppException` 替代
- 裸字符串错误码（如 `"code": "TASK_NOT_FOUND"` 字面量） — 用 `ErrorCode` 枚举

**自动校验**：`python scripts/lint_error_codes.py` 0 违规。

---

## 3. 分页参数

- **统一**：`page`（从 1 开始，默认 1）+ `page_size`（默认 20，最大 100）
- **约束**：Pydantic `Query(ge=1, le=100)` 硬约束，越界返回 422 + `VALIDATION_FAILED`
- **禁用**：`limit` / `offset` / `skip` / `take` / `pageNum` / `pageSize`

**标准模板**：

```python
from fastapi import Query

async def list_resources(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数，最大 100"),
    ...
):
    offset = (page - 1) * page_size
    stmt = select(Model).offset(offset).limit(page_size)
    ...
    return page_envelope(items, page=page, page_size=page_size, total=total)
```

---

## 4. 枚举参数归一化

所有枚举型查询参数/路径参数/请求体字段，服务端统一按小写下划线归一化（大写 → 小写、中划线 → 下划线、首尾空白 strip）。

**使用辅助函数**（`src/api/enums.py`）：

```python
from src.api.enums import parse_enum_param, validate_enum_choice

# 用于 str Enum 类（TaskStatus / TaskType 等）
status_enum = parse_enum_param(status, field="status", enum_cls=TaskStatus)

# 用于裸字符串白名单
scan_mode = validate_enum_choice(scan_mode, field="scan_mode", allowed=["full", "incremental"])
```

失败自动抛 `AppException(INVALID_ENUM_VALUE)`，`details` 含 `field` / `value` / `allowed` 三元组。

---

## 5. 新增错误码

1. 在 `src/api/errors.py::ErrorCode` 枚举中追加新值（按分类注释插入）
2. 在 `ERROR_STATUS_MAP` 中追加 HTTP 状态映射
3. 在 `ERROR_DEFAULT_MESSAGE` 中追加默认消息
4. 三元同步后，`tests/contract/test_error_codes_contract.py` 会自动参数化覆盖该新码

**已发布的错误码禁止改名或更换 HTTP 状态**，只允许新增。

---

## 6. 下线接口

接口下线采用**直接物理删除**策略（章程 v2.0.0 原则 IV + 原则 IX）。

**操作步骤**：

1. 删除路由代码：在 `src/api/routers/<module>.py` 直接移除对应端点的装饰器与函数
2. 删除合约文件：从 `specs/<feature>/contracts/` 下删除已无实际对应的契约条目
3. 删除合约测试：从 `tests/contract/` 下删除相关断言文件或用例
4. 迁移说明：在对应 Feature 的 changelog / `spec.md`「业务阶段映射」中一次性简述替代路径

客户端调用已下线路径将收到 FastAPI 默认 404 `NOT_FOUND`。不再保留哨兵路由或双份台账文件。

---

## 7. 新接口 TDD 流程

1. **契约先行**：在 `specs/<feature>/contracts/` 下写 API 契约（OpenAPI YAML 片段或 Markdown 表格）
2. **合约测试**：在 `tests/contract/` 下写断言（参考 `conftest.py::assert_success_envelope` / `assert_error_envelope`）
3. **Pydantic Schema**：在 `src/api/schemas/<module>.py` 定义请求/响应模型
4. **路由实现**：在 `src/api/routers/<module>.py` 添加端点
5. **业务实现**：若涉及新逻辑，写到 `src/services/<module>_svc.py`
6. **集成验证**：在 `tests/integration/` 下写端到端测试

---

## 8. Pre-merge 自检清单

提交 PR 前本地跑一遍：

```bash
# 1. 命名 linter
/opt/conda/envs/coaching/bin/python3.11 -c "
from src.api.main import app
import json
json.dump(app.openapi(), open('/tmp/openapi.json','w'), ensure_ascii=False)
"
/opt/conda/envs/coaching/bin/python3.11 scripts/lint_api_naming.py --file /tmp/openapi.json

# 2. 错误码 linter
/opt/conda/envs/coaching/bin/python3.11 scripts/lint_error_codes.py

# 3. 全量测试
/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/ -q

# 4. 信封契约断言（T072 扫描 OpenAPI）
/opt/conda/envs/coaching/bin/python3.11 -c "
import json
spec = json.load(open('/tmp/openapi.json'))
for p, methods in spec['paths'].items():
    if p in {'/health', '/', '/docs', '/redoc', '/openapi.json'}:
        continue
    for m, op in methods.items():
        if m in ('parameters','summary','description'): continue
        if not isinstance(op, dict): continue
        for code, r in op.get('responses', {}).items():
            if str(code) == '204':
                continue
            schema_str = json.dumps(r.get('content', {}).get('application/json', {}).get('schema', {}), ensure_ascii=False)
            is_envelope = 'Envelope' in schema_str
            is_error = str(code).startswith('4') or str(code).startswith('5')
            assert is_envelope or is_error, f'{m.upper()} {p} {code} 未用信封'
print('SC-009 OK')
"
```

四项全通过后方可发起 PR。

---

## 9. 常见问题（FAQ）

**Q1：列表接口返回聚合统计（如 `/standards`），data 结构不是纯 list，怎么办？**

A：使用 `SuccessEnvelope[CustomData]` 自定义 data 模型，把总数等放在 `data` 内部；这类接口 linter 自动识别为"非列表端点"，不强制 `page/page_size` 参数。但若希望保持一致性，也可追加分页参数并在应用层切片。

**Q2：为什么删除了 `total_pages` 字段？**

A：章程 v1.4.0 规定分页 meta 只保留 `page/page_size/total` 三项，`total_pages` 由前端按 `ceil(total/page_size)` 自算，避免后端多算字段且与客户端分页 UI 解耦。

**Q3：服务层抛 `ValueError` 还能继续用吗？**

A：允许继续用，但语义限定为"配置缺失 / 内部数据不一致 / 调用方契约违反"等**内部错误**。路由层必须 catch 后重抛对应的 `AppException`。业务校验类错误应直接抛 `AppException(INVALID_INPUT)`。

**Q4：我能在路由装饰器里写 `prefix="/tasks"` 吗？**

A：不能。11/12 个路由文件统一不带 prefix（`APIRouter(tags=[...])`），装饰器内写完整子路径（如 `@router.get("/tasks/{task_id}")`）。`standards.py` 已于 Feature-017 阶段 5 T053 从带 prefix 改为无 prefix，保持一致。

---

## 10. 参考资料

- 章程：`.specify/memory/constitution.md` 原则 IX
- 信封 JSON Schema：`specs/017-api-standardization/contracts/response-envelope.schema.json`
- 38 个错误码：`specs/017-api-standardization/contracts/error-codes.md`
- 开发快速入门：`specs/017-api-standardization/quickstart.md`
