"""Unit tests for knowledge_base router — US1 endpoints (T028–T029)."""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.unit.conftest import KB_VERSION, TASK_ID, make_kb, make_tech_point


# ── GET /api/v1/knowledge-base/versions ──────────────────────────────────────

class TestListKbVersions:
    @pytest.mark.asyncio
    async def test_returns_empty_list(self, client, override_db):
        with patch(
            "src.api.routers.knowledge_base.knowledge_base_svc.list_versions",
            new=AsyncMock(return_value=[]),
        ):
            resp = await client.get("/api/v1/knowledge-base/versions")
        assert resp.status_code == 200
        body = resp.json()
        # Feature-017：信封格式，空列表 data=[]
        assert body["success"] is True
        assert body["data"] == []

    @pytest.mark.asyncio
    async def test_returns_versions_list(self, client, override_db):
        kb = make_kb(version="1.0.0", status="active")
        kb.approved_at = datetime(2026, 4, 17, 9, 0, 0)

        with patch(
            "src.api.routers.knowledge_base.knowledge_base_svc.list_versions",
            new=AsyncMock(return_value=[kb]),
        ):
            resp = await client.get("/api/v1/knowledge-base/versions")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        versions = body["data"]
        assert len(versions) == 1
        assert versions[0]["version"] == "1.0.0"
        assert versions[0]["status"] == "active"
        assert versions[0]["point_count"] == 2


# ── GET /api/v1/knowledge-base/{version} ─────────────────────────────────────

class TestGetKbVersion:
    @pytest.mark.asyncio
    async def test_version_not_found_returns_404(self, client, override_db):
        from src.services.knowledge_base_svc import VersionNotFoundError

        with patch(
            "src.api.routers.knowledge_base.knowledge_base_svc.get_version",
            new=AsyncMock(side_effect=VersionNotFoundError("9.9.9")),
        ):
            resp = await client.get("/api/v1/knowledge-base/9.9.9")

        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "KB_VERSION_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_returns_version_detail_with_points(self, client, override_db):
        kb = make_kb(version=KB_VERSION, status="draft")
        point = make_tech_point(version=KB_VERSION)

        with (
            patch(
                "src.api.routers.knowledge_base.knowledge_base_svc.get_version",
                new=AsyncMock(return_value=kb),
            ),
            patch(
                "src.api.routers.knowledge_base.knowledge_base_svc.get_tech_points",
                new=AsyncMock(return_value=[point]),
            ),
        ):
            resp = await client.get(f"/api/v1/knowledge-base/{KB_VERSION}")

        assert resp.status_code == 200
        envelope = resp.json()
        assert envelope["success"] is True
        body = envelope["data"]
        assert body["version"] == KB_VERSION
        assert body["status"] == "draft"
        assert len(body["tech_points"]) == 1
        assert body["tech_points"][0]["dimension"] == "elbow_angle"
        assert body["tech_points"][0]["extraction_confidence"] == 0.91


# ── POST /api/v1/knowledge-base/{version}/approve ────────────────────────────

class TestApproveKbVersion:
    @pytest.mark.asyncio
    async def test_version_not_found_returns_404(self, client, override_db):
        from src.services.knowledge_base_svc import VersionNotFoundError

        mock_begin = MagicMock()
        mock_begin.__aenter__ = AsyncMock(return_value=None)
        mock_begin.__aexit__ = AsyncMock(return_value=False)
        override_db.begin = MagicMock(return_value=mock_begin)

        with patch(
            "src.api.routers.knowledge_base.knowledge_base_svc.approve_version",
            new=AsyncMock(side_effect=VersionNotFoundError(KB_VERSION)),
        ):
            resp = await client.post(
                f"/api/v1/knowledge-base/{KB_VERSION}/approve",
                json={"approved_by": "张教练"},
            )

        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "KB_VERSION_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_non_draft_returns_400(self, client, override_db):
        from src.services.knowledge_base_svc import VersionNotDraftError

        mock_begin = MagicMock()
        mock_begin.__aenter__ = AsyncMock(return_value=None)
        mock_begin.__aexit__ = AsyncMock(return_value=False)
        override_db.begin = MagicMock(return_value=mock_begin)

        with patch(
            "src.api.routers.knowledge_base.knowledge_base_svc.approve_version",
            new=AsyncMock(
                side_effect=VersionNotDraftError(KB_VERSION, "active")
            ),
        ):
            resp = await client.post(
                f"/api/v1/knowledge-base/{KB_VERSION}/approve",
                json={"approved_by": "张教练"},
            )

        # Feature-017：状态校验类错误 400（非 409，章程 v1.4.0 对齐）
        assert resp.status_code == 400
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "KB_VERSION_NOT_DRAFT"

    @pytest.mark.asyncio
    async def test_approve_success(self, client, override_db):
        approved_at = datetime(2026, 4, 18, 10, 0, 0)
        kb = make_kb(version=KB_VERSION, status="active")
        kb.approved_by = "张教练"
        kb.approved_at = approved_at

        mock_begin = MagicMock()
        mock_begin.__aenter__ = AsyncMock(return_value=None)
        mock_begin.__aexit__ = AsyncMock(return_value=False)
        override_db.begin = MagicMock(return_value=mock_begin)

        with patch(
            "src.api.routers.knowledge_base.knowledge_base_svc.approve_version",
            new=AsyncMock(return_value=(kb, "1.0.0")),
        ):
            resp = await client.post(
                f"/api/v1/knowledge-base/{KB_VERSION}/approve",
                json={"approved_by": "张教练", "notes": "审核通过"},
            )

        assert resp.status_code == 200
        envelope = resp.json()
        assert envelope["success"] is True
        body = envelope["data"]
        assert body["version"] == KB_VERSION
        assert body["status"] == "active"
        assert body["approved_by"] == "张教练"
        assert body["previous_active_version"] == "1.0.0"
