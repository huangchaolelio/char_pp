"""Feature-017 — SuccessEnvelope / ErrorEnvelope / PaginationMeta 单元测试.

断言的核心不变量：
- 成功信封序列化为 ``{success: true, data, meta}``，且 ``success`` 永远为 ``True``
- 错误信封序列化为 ``{success: false, error: {code, message, details}}``，不含 ``data``/``meta``
- ``PaginationMeta`` 越界（``page_size > 100`` / ``page < 1``）抛 ``ValidationError``
- ``ok()`` 与 ``page()`` 构造器产出的 JSON 结构与 OpenAPI 契约
  (``contracts/response-envelope.schema.json``) 一致
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.api.schemas.envelope import (
    ErrorBody,
    ErrorEnvelope,
    PaginationMeta,
    RetiredErrorDetails,
    SuccessEnvelope,
    UpstreamErrorDetails,
    ValidationErrorDetails,
    ok,
    page,
)


# ── SuccessEnvelope 序列化 ─────────────────────────────────────────────────
class TestSuccessEnvelope:
    def test_dict_payload_serializes_with_success_true(self) -> None:
        env = SuccessEnvelope[dict](success=True, data={"id": 1, "name": "x"})
        dumped = env.model_dump(mode="json")
        assert dumped == {
            "success": True,
            "data": {"id": 1, "name": "x"},
            "meta": None,
        }

    def test_list_payload_with_meta(self) -> None:
        env = SuccessEnvelope[list[dict]](
            success=True,
            data=[{"id": 1}, {"id": 2}],
            meta=PaginationMeta(page=1, page_size=20, total=42),
        )
        dumped = env.model_dump(mode="json")
        assert dumped["success"] is True
        assert dumped["data"] == [{"id": 1}, {"id": 2}]
        assert dumped["meta"] == {"page": 1, "page_size": 20, "total": 42}

    def test_data_can_be_none(self) -> None:
        env = SuccessEnvelope[dict | None](success=True, data=None)
        dumped = env.model_dump(mode="json")
        assert dumped == {"success": True, "data": None, "meta": None}

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            SuccessEnvelope[dict](success=True, data={}, error={"code": "X", "message": "y"})  # type: ignore[call-arg]


# ── ErrorEnvelope 序列化 ───────────────────────────────────────────────────
class TestErrorEnvelope:
    def test_error_envelope_has_success_false_and_no_data_meta(self) -> None:
        env = ErrorEnvelope(
            success=False,
            error=ErrorBody(code="TASK_NOT_FOUND", message="任务不存在", details={"task_id": "abc"}),
        )
        dumped = env.model_dump(mode="json")
        assert dumped["success"] is False
        assert dumped["error"] == {
            "code": "TASK_NOT_FOUND",
            "message": "任务不存在",
            "details": {"task_id": "abc"},
        }
        assert "data" not in dumped
        assert "meta" not in dumped

    def test_details_can_be_null(self) -> None:
        env = ErrorEnvelope(
            success=False,
            error=ErrorBody(code="NOT_FOUND", message="未找到", details=None),
        )
        dumped = env.model_dump(mode="json")
        assert dumped["error"]["details"] is None

    def test_extra_fields_forbidden_on_error(self) -> None:
        with pytest.raises(ValidationError):
            ErrorEnvelope(
                success=False,
                error=ErrorBody(code="X", message="y"),
                data={"should": "not allow"},  # type: ignore[call-arg]
            )


# ── PaginationMeta 边界 ────────────────────────────────────────────────────
class TestPaginationMeta:
    def test_valid_values(self) -> None:
        meta = PaginationMeta(page=1, page_size=20, total=0)
        assert meta.page == 1 and meta.page_size == 20 and meta.total == 0

    def test_page_size_over_max_raises(self) -> None:
        with pytest.raises(ValidationError):
            PaginationMeta(page=1, page_size=101, total=0)

    def test_page_size_below_min_raises(self) -> None:
        with pytest.raises(ValidationError):
            PaginationMeta(page=1, page_size=0, total=0)

    def test_page_below_min_raises(self) -> None:
        with pytest.raises(ValidationError):
            PaginationMeta(page=0, page_size=20, total=0)

    def test_negative_total_raises(self) -> None:
        with pytest.raises(ValidationError):
            PaginationMeta(page=1, page_size=20, total=-1)


# ── 构造器 ok() / page() ───────────────────────────────────────────────────
class TestConstructors:
    def test_ok_without_meta(self) -> None:
        env = ok({"id": 42})
        assert env.success is True
        assert env.data == {"id": 42}
        assert env.meta is None

    def test_ok_with_meta_not_typical_but_allowed(self) -> None:
        # 虽然非分页接口不应传 meta，但类型上允许（分页场景通常走 page()）
        env = ok({"id": 1}, PaginationMeta(page=1, page_size=20, total=1))
        assert env.meta is not None and env.meta.page_size == 20

    def test_page_builds_meta_correctly(self) -> None:
        env = page([1, 2, 3], page=2, page_size=10, total=23)
        assert env.success is True
        assert env.data == [1, 2, 3]
        assert env.meta is not None
        assert env.meta.page == 2
        assert env.meta.page_size == 10
        assert env.meta.total == 23

    def test_page_empty_list(self) -> None:
        env = page([], page=1, page_size=20, total=0)
        assert env.data == []
        assert env.meta is not None and env.meta.total == 0

    def test_page_propagates_meta_validation(self) -> None:
        # 构造器不额外校验，但 PaginationMeta 会兜底
        with pytest.raises(ValidationError):
            page([], page=1, page_size=999, total=0)


# ── 专用 Details 子类 ──────────────────────────────────────────────────────
class TestDetailsSubclasses:
    def test_retired_details_successor_str(self) -> None:
        d = RetiredErrorDetails(successor="/api/v1/tasks/diagnosis", migration_note="同步→异步")
        assert d.model_dump() == {
            "successor": "/api/v1/tasks/diagnosis",
            "migration_note": "同步→异步",
        }

    def test_retired_details_successor_list(self) -> None:
        d = RetiredErrorDetails(
            successor=["/api/v1/tasks/classification", "/api/v1/tasks/kb-extraction"],
            migration_note=None,
        )
        assert isinstance(d.successor, list) and len(d.successor) == 2

    def test_retired_details_requires_successor(self) -> None:
        with pytest.raises(ValidationError):
            RetiredErrorDetails()  # type: ignore[call-arg]

    def test_validation_details_all_optional(self) -> None:
        d = ValidationErrorDetails()
        assert d.model_dump(exclude_none=True) == {}

    def test_validation_details_with_allowed(self) -> None:
        d = ValidationErrorDetails(field="tech_category", value="invalid", allowed=["forehand_push_long", "serve"])
        assert d.allowed == ["forehand_push_long", "serve"]

    def test_upstream_details_requires_upstream(self) -> None:
        with pytest.raises(ValidationError):
            UpstreamErrorDetails()  # type: ignore[call-arg]

    def test_upstream_details_minimal(self) -> None:
        d = UpstreamErrorDetails(upstream="venus-proxy")
        assert d.upstream == "venus-proxy"
        assert d.upstream_code is None
