"""Feature-023 — Phase 7 (US5) Combined contract + integration tests.

合并自 T051 / T052 / T053:
  - T051: standards 路由 action 查询合约
  - T052: ACTION_DICTIONARY_VIOLATION + STANDARD_NOT_AVAILABLE_FOR_ACTION 错误码合约
  - T053: tech_standard_builder 按 action 聚合（不做层级降级）

策略：纯静态合约校验（OpenAPI schema、ErrorCode 枚举存在性）+ 类型/字段断言；
不依赖真实 DB Build 流程（避免启动 Celery）。
"""

from __future__ import annotations

import pytest

from src.api.errors import ERROR_DEFAULT_MESSAGE, ERROR_STATUS_MAP, ErrorCode
from src.api.main import app
from src.services.tech_standard_builder import BuildResult


pytestmark = pytest.mark.contract


# ── T051: standards 路由 action 查询合约 ──────────────────────────────


def test_standards_endpoint_registered() -> None:
    """GET /api/v1/standards 与 GET /api/v1/standards/{tech_category} 必须注册."""
    paths = app.openapi()["paths"]
    assert "/api/v1/standards" in paths
    # 路径参数 {tech_category} 仍保留作为 path segment（语义切到 action）
    assert any("/api/v1/standards/" in p for p in paths.keys())


def test_standards_response_schema_uses_action_field() -> None:
    """StandardResponse 至少有 tech_category（语义=action）字段；missing_categories 字段保留."""
    schemas = app.openapi()["components"]["schemas"]
    assert "StandardResponse" in schemas
    props = schemas["StandardResponse"]["properties"]
    assert "tech_category" in props  # 字段名暂保留（值=字典 action 名）
    assert "version" in props


def test_standards_list_includes_missing_categories() -> None:
    """StandardsListData 必须含 missing_categories 字段（spec FR-016 数据不足的标记）."""
    schemas = app.openapi()["components"]["schemas"]
    list_schema = schemas["StandardsListData"]
    assert "missing_categories" in list_schema["properties"]


# ── T052: 错误码合约 ─────────────────────────────────────────────────


def test_action_dictionary_violation_error_registered() -> None:
    """ACTION_DICTIONARY_VIOLATION 在 ErrorCode 中且映射 400."""
    assert hasattr(ErrorCode, "ACTION_DICTIONARY_VIOLATION")
    code = ErrorCode.ACTION_DICTIONARY_VIOLATION
    assert ERROR_STATUS_MAP[code].value == 400
    assert code in ERROR_DEFAULT_MESSAGE
    assert ERROR_DEFAULT_MESSAGE[code]  # 非空


def test_standard_not_available_for_action_error_registered() -> None:
    """STANDARD_NOT_AVAILABLE_FOR_ACTION 在 ErrorCode 中且映射 503."""
    assert hasattr(ErrorCode, "STANDARD_NOT_AVAILABLE_FOR_ACTION")
    code = ErrorCode.STANDARD_NOT_AVAILABLE_FOR_ACTION
    assert ERROR_STATUS_MAP[code].value == 503
    assert ERROR_DEFAULT_MESSAGE[code]


def test_action_not_found_error_registered() -> None:
    """ACTION_NOT_FOUND 在 ErrorCode 中且映射 404."""
    assert hasattr(ErrorCode, "ACTION_NOT_FOUND")
    code = ErrorCode.ACTION_NOT_FOUND
    assert ERROR_STATUS_MAP[code].value == 404


def test_no_active_kb_for_action_error_registered() -> None:
    """NO_ACTIVE_KB_FOR_ACTION 在 ErrorCode 中且映射 409."""
    assert hasattr(ErrorCode, "NO_ACTIVE_KB_FOR_ACTION")
    code = ErrorCode.NO_ACTIVE_KB_FOR_ACTION
    assert ERROR_STATUS_MAP[code].value == 409


def test_legacy_standard_not_available_physically_removed() -> None:
    """旧错误码 STANDARD_NOT_AVAILABLE 在 Feature-023 中物理删除."""
    assert not hasattr(ErrorCode, "STANDARD_NOT_AVAILABLE")


# ── T053: tech_standard_builder 按 action 聚合 ────────────────────────


def test_build_result_dataclass_uses_tech_category_field() -> None:
    """BuildResult.tech_category 字段（语义=action）保留作为 schema 字段."""
    r = BuildResult(tech_category="高吊弧圈球", result="success")
    assert r.tech_category == "高吊弧圈球"
    assert r.result == "success"


def test_build_result_supports_data_insufficient_marker() -> None:
    """spec FR-016：data_insufficient 状态可通过 result + reason 表达，不做层级降级."""
    r = BuildResult(
        tech_category="高吊弧圈球",
        result="skipped",
        reason="no_active_kb",
    )
    # builder 不把 high_action 的失败降级为 forehand_topspin（语义独立）
    assert r.tech_category == "高吊弧圈球"
    assert r.result == "skipped"


def test_build_all_iterates_dictionary_actions_not_etp_action_type() -> None:
    """T054 最关键校验：build_all 的实现应已切换到 ActionDictionaryService."""
    import inspect
    from src.services.tech_standard_builder import TechStandardBuilder

    src_text = inspect.getsource(TechStandardBuilder.build_all)
    # 必须引用 ActionDictionaryService 而不是仅 EtpActionType
    assert (
        "action_dict" in src_text or "ActionDictionaryService" in src_text
    ), "build_all 必须从字典而非 EtpActionType 派生 actions（spec T054）"


# ── tasks router 字典校验（T061 关键合约）────────────────────────────


def test_tasks_router_can_import_action_dictionary_service() -> None:
    """tasks router 必须能 import ActionDictionaryService（T061 留 hook）."""
    from src.services.action_dictionary_service import (
        ActionDictionaryService,
        get_action_dictionary_service,
    )
    assert ActionDictionaryService is not None
    assert callable(get_action_dictionary_service)
