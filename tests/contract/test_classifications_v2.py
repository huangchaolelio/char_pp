"""Feature-023 — Contract test for GET /api/v1/classifications response schema.

T023 (RED → GREEN once T030/T031 lands):
  - test_response_includes_four_level_fields
  - test_response_excludes_tech_category_field
  - test_response_uses_action_field

合约依据: specs/023-tech-classification-rebuild/contracts/classifications-v2.openapi.yaml
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


pytestmark = pytest.mark.contract


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_response_includes_four_level_fields(client: TestClient) -> None:
    """GET /api/v1/classifications 响应 data[*] 必须包含四级字段（静态 schema 校验）."""
    schema = app.openapi()
    item_schema = schema["components"]["schemas"]["ClassificationItem"]
    props = item_schema["properties"]
    for field in ("category_l1", "category_l2", "category_l3", "action"):
        assert field in props, f"ClassificationItem.{field} 缺失"


def test_response_excludes_tech_category_field(client: TestClient) -> None:
    """ClassificationItem 不得再含 tech_category 字段（章程 v2.0.0 物理删除）."""
    schema = app.openapi()
    item_schema = schema["components"]["schemas"]["ClassificationItem"]
    props = item_schema["properties"]
    assert "tech_category" not in props, "ClassificationItem.tech_category 仍存在（应物理删除）"


def test_query_param_action_accepted(client: TestClient) -> None:
    """GET /api/v1/classifications 必须支持 ?action= 查询参数（静态 OpenAPI 校验）."""
    spec = app.openapi()
    list_endpoint = spec["paths"].get("/api/v1/classifications", {}).get("get", {})
    params = list_endpoint.get("parameters", [])
    param_names = {p.get("name") for p in params}
    assert "action" in param_names, "GET /api/v1/classifications 必须接受 ?action= 查询参数"


def test_summary_response_uses_action_field(client: TestClient) -> None:
    """GET /api/v1/classifications/summary 的 tech_breakdown 应以 action 为聚合维度."""
    schema = app.openapi()
    breakdown_schema = schema["components"]["schemas"]["TechBreakdownItem"]
    props = breakdown_schema["properties"]
    assert "action" in props
    # label 与 tech_category 字段已在 Feature-023 移除
    assert "tech_category" not in props
    assert "label" not in props
