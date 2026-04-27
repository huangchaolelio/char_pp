# 快速开始: 按新规范实现一个资源接口

**面向对象**: 本项目新成员 / Feature-018+ 负责人
**目标**: 在 **10 分钟内**独立完成一个"创建资源 + 返回统一信封"的示例路由，并让契约测试开箱通过（对应 SC-008）。

## 前置条件

- 本 Feature 已合入（Phase 0.5 章程 v1.4.0 已修订、`src/api/schemas/envelope.py`、`src/api/errors.py`、`_retired.py` 均已存在）
- Python 虚拟环境：`/opt/conda/envs/coaching/bin/python3.11`（项目规则强制）
- 已运行 `alembic upgrade head`、API 可通过 `uvicorn src.api.main:app` 启动

## 示例场景

我们以"假设新增一个 `notes` 资源（教练笔记）"为例，演示新规范下的完整开发路径。**实际实现时该资源可能并不存在，本文档只作为范例参考。**

## Step 1：编写合约（TDD 第一步，原则 II 强制）

创建 `tests/contract/test_notes_contract.py`：

```python
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_note_success_envelope(async_client: AsyncClient):
    """创建笔记应返回统一成功信封。"""
    resp = await async_client.post(
        "/api/v1/notes",
        json={"title": "握拍笔记", "content": "..."},
    )
    assert resp.status_code == 201
    body = resp.json()
    # 顶层必须含 success=true
    assert body["success"] is True
    # 成功响应不得出现 error
    assert "error" not in body
    # data 包含业务字段
    assert "id" in body["data"]
    assert body["data"]["title"] == "握拍笔记"
    # 非列表接口 meta 为 None 或缺省
    assert body.get("meta") is None


@pytest.mark.asyncio
async def test_list_notes_pagination_meta(async_client: AsyncClient):
    """列表接口必须返回 meta 三件套。"""
    resp = await async_client.get("/api/v1/notes?page=1&page_size=20")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)
    assert body["meta"]["page"] == 1
    assert body["meta"]["page_size"] == 20
    assert "total" in body["meta"]


@pytest.mark.asyncio
async def test_get_note_not_found_error_envelope(async_client: AsyncClient):
    """404 场景的错误信封。"""
    resp = await async_client.get("/api/v1/notes/not-exist-id")
    assert resp.status_code == 404
    body = resp.json()
    assert body["success"] is False
    assert "data" not in body and "meta" not in body
    assert body["error"]["code"] == "NOTE_NOT_FOUND"
    assert body["error"]["message"]  # 非空


@pytest.mark.asyncio
async def test_invalid_page_size_returns_400(async_client: AsyncClient):
    resp = await async_client.get("/api/v1/notes?page_size=500")
    assert resp.status_code == 400
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_PAGE_SIZE"
    assert body["error"]["details"]["value"] == 500
    assert body["error"]["details"]["allowed"] == {"max": 100, "min": 1}
```

运行并**确认失败**（原则 II 强制的"Red"）：
```bash
/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/contract/test_notes_contract.py -v
# 预期: 4 failed, 0 passed（路由尚未实现）
```

## Step 2：添加错误码

编辑 `src/api/errors.py`：
```python
class ErrorCode(str, Enum):
    # ... existing codes ...
    NOTE_NOT_FOUND = "NOTE_NOT_FOUND"


ERROR_STATUS_MAP[ErrorCode.NOTE_NOT_FOUND] = HTTPStatus.NOT_FOUND
ERROR_DEFAULT_MESSAGE[ErrorCode.NOTE_NOT_FOUND] = "笔记不存在"
```

同步更新 `specs/018-<next-feature>/contracts/error-codes.md`（如果本资源是新 Feature 的一部分）。

## Step 3：定义业务 Schema 与 Service

`src/api/schemas/note.py`（**只定义 data 内层结构，不包信封**）：
```python
from pydantic import BaseModel, ConfigDict


class NoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    content: str


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    content: str
    created_at: str
```

`src/services/note_service.py`（业务逻辑层）：
```python
from src.api.errors import AppException, ErrorCode


class NoteService:
    async def get_note(self, note_id: str) -> NoteOut:
        row = await self._session.get(Note, note_id)
        if row is None:
            raise AppException(ErrorCode.NOTE_NOT_FOUND, details={"note_id": note_id})
        return NoteOut.model_validate(row)
```

**关键**: 服务层只抛 `AppException`，**不抛 `HTTPException`、不直接返回错误字典**。

## Step 4：注册路由

`src/api/routers/notes.py`：
```python
from fastapi import APIRouter, Depends, Query
from src.api.schemas.envelope import SuccessEnvelope, ok, page
from src.api.schemas.note import NoteCreate, NoteOut

router = APIRouter(prefix="/notes", tags=["notes"])  # 注意：前缀仅到资源名；/api/v1 由 main.py 拼接


@router.post("", status_code=201, response_model=SuccessEnvelope[NoteOut])
async def create_note(
    payload: NoteCreate,
    service: NoteService = Depends(get_note_service),
):
    created = await service.create(payload)
    return ok(created)


@router.get("", response_model=SuccessEnvelope[list[NoteOut]])
async def list_notes(
    page_num: int = Query(1, alias="page", ge=1),
    page_size: int = Query(20, ge=1, le=100),
    service: NoteService = Depends(get_note_service),
):
    items, total = await service.list(page=page_num, page_size=page_size)
    return page(items, page=page_num, page_size=page_size, total=total)


@router.get("/{note_id}", response_model=SuccessEnvelope[NoteOut])
async def get_note(note_id: str, service: NoteService = Depends(get_note_service)):
    return ok(await service.get_note(note_id))
```

在 `src/api/main.py`：
```python
from src.api.routers import notes

app.include_router(notes.router, prefix="/api/v1")  # /api/v1 + /notes = /api/v1/notes
```

## Step 5：运行测试，确认 Green

```bash
/opt/conda/envs/coaching/bin/python3.11 -m pytest tests/contract/test_notes_contract.py -v
# 预期: 4 passed
```

## Step 6：重启服务并手工验证

```bash
pkill -f "uvicorn src.api.main"
setsid /opt/conda/envs/coaching/bin/uvicorn src.api.main:app --host 0.0.0.0 --port 8080 \
    >> /tmp/uvicorn.log 2>&1 &

# 创建
curl -X POST http://localhost:8080/api/v1/notes \
  -H 'Content-Type: application/json' \
  -d '{"title":"握拍","content":"..."}'
# => {"success":true,"data":{"id":"...","title":"握拍",...},"meta":null}

# 列表
curl http://localhost:8080/api/v1/notes?page=1&page_size=20
# => {"success":true,"data":[...],"meta":{"page":1,"page_size":20,"total":N}}

# 404
curl http://localhost:8080/api/v1/notes/bad-id
# => {"success":false,"error":{"code":"NOTE_NOT_FOUND","message":"笔记不存在","details":{"note_id":"bad-id"}}}
```

## 常见陷阱

| 陷阱 | 正确做法 |
|---|---|
| 在路由里 `return {"success": True, "data": ...}` | 改用 `ok(data)` 或 `page(items, ...)`；原因：FastAPI 需要 `response_model` 的泛型解析才能生成正确 OpenAPI |
| 抛 `HTTPException` | 统一抛 `AppException(ErrorCode.XXX)`；`HTTPException` 只被全局 handler 当作 `INTERNAL_ERROR` 兜底 |
| 在路由文件装饰器里写完整 `/api/v1/...` | `APIRouter(prefix="/<resource>")` + `app.include_router(r, prefix="/api/v1")` 两段式，禁止重复 |
| 新错误码只加到枚举，不更新 `ERROR_STATUS_MAP` | CI 有检查：所有枚举值必须在 STATUS_MAP 中出现，否则阻断合入 |
| 列表接口返回 `ok(items)` 而不是 `page(...)` | 契约测试会在 `assert body["meta"]["total"]` 处失败 |
| 未在 `error.details` 放结构化字段 | 前端无法定位错误上下文；至少应带关键 ID（如 `note_id`） |

## 相关文档

- 响应信封 JSON Schema: `contracts/response-envelope.schema.json`
- 错误码完整清单: `contracts/error-codes.md`
- 已下线接口台账: `contracts/retirement-ledger.md`
- 数据模型细节: `data-model.md`
