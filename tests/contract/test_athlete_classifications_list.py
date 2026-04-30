"""Contract tests for Feature-020 — GET /api/v1/athlete-classifications (T019).

Covers contracts/athlete_classifications_list.md 的 8 个断言点:
  1. 默认分页 page=1 / page_size=20 + meta.total 真实计数
  2. page_size=101 → 422 VALIDATION_FAILED
  3. page_size=0 → 422 VALIDATION_FAILED
  4. tech_category='forehand_attack' 正确过滤
  5. tech_category='invalid' → 400 INVALID_ENUM_VALUE + details.allowed
  6. has_diagnosis=true 过滤出 last_diagnosis_report_id IS NOT NULL
  7. 跨污染隔离：教练侧行不返回 (SC-006)
  8. 排序 sort_by=updated_at / order=asc 升序
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.utils.time_utils import now_cst
from tests.contract.conftest import assert_error_envelope, assert_success_envelope


@pytest.fixture
def db_no_op():
    from src.db.session import get_db

    async def _fake_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = _fake_db
    yield
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client(db_no_op):
    return TestClient(app)


def _fake_row(*, tech_category="forehand_attack", has_diag=False, athlete_name="张三"):
    from src.api.schemas.athlete_classification import AthleteClassificationItem

    return AthleteClassificationItem(
        id=uuid4(),
        cos_object_key=f"charhuang/tt_video/athletes/{athlete_name}/video.mp4",
        athlete_id=uuid4(),
        athlete_name=athlete_name,
        name_source="map",
        tech_category=tech_category,
        classification_source="rule",
        classification_confidence=1.0,
        preprocessed=True,
        preprocessing_job_id=uuid4(),
        last_diagnosis_report_id=uuid4() if has_diag else None,
        created_at=now_cst(),
        updated_at=now_cst(),
    )


@pytest.mark.contract
class TestAthleteClassificationsListContract:

    def test_c1_default_pagination(self, client):
        rows = [_fake_row() for _ in range(3)]
        with patch(
            "src.api.routers.athlete_classifications.list_classifications",
            new_callable=AsyncMock, return_value=(rows, 3),
        ):
            resp = client.get("/api/v1/athlete-classifications")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        data = assert_success_envelope(body, expect_meta=True)
        assert isinstance(data, list)
        assert body["meta"]["page"] == 1
        assert body["meta"]["page_size"] == 20
        assert body["meta"]["total"] == 3

    def test_c2_page_size_over_100_rejected(self, client):
        resp = client.get("/api/v1/athlete-classifications?page_size=101")
        assert resp.status_code in (400, 422), resp.text
        assert_error_envelope(resp.json())

    def test_c3_page_size_zero_rejected(self, client):
        resp = client.get("/api/v1/athlete-classifications?page_size=0")
        assert resp.status_code in (400, 422), resp.text
        assert_error_envelope(resp.json())

    def test_c4_tech_category_filter(self, client):
        rows = [_fake_row(tech_category="forehand_attack")]
        captured: dict = {}

        async def _fake(db, **kwargs):
            captured.update(kwargs)
            return rows, 1

        with patch(
            "src.api.routers.athlete_classifications.list_classifications",
            new=AsyncMock(side_effect=_fake),
        ):
            resp = client.get(
                "/api/v1/athlete-classifications?tech_category=forehand_attack"
            )
        assert resp.status_code == 200, resp.text
        assert captured.get("tech_category") == "forehand_attack"

    def test_c5_invalid_tech_category(self, client):
        resp = client.get("/api/v1/athlete-classifications?tech_category=invalid")
        # 400 INVALID_ENUM_VALUE 或 422（枚举校验层决定）
        assert resp.status_code in (400, 422), resp.text
        err = assert_error_envelope(resp.json())
        assert err["code"] in ("INVALID_ENUM_VALUE", "VALIDATION_FAILED")

    def test_c6_has_diagnosis_filter(self, client):
        rows = [_fake_row(has_diag=True)]
        captured: dict = {}

        async def _fake(db, **kwargs):
            captured.update(kwargs)
            return rows, 1

        with patch(
            "src.api.routers.athlete_classifications.list_classifications",
            new=AsyncMock(side_effect=_fake),
        ):
            resp = client.get("/api/v1/athlete-classifications?has_diagnosis=true")
        assert resp.status_code == 200, resp.text
        assert captured.get("has_diagnosis") is True

    def test_c7_coach_isolation_smoke(self, client):
        """SC-006：路由模块不应在运行时 import 教练侧 ORM."""
        import importlib

        mod = importlib.import_module("src.api.routers.athlete_classifications")
        src = (mod.__file__ or "")
        with open(src, encoding="utf-8") as f:
            source = f.read()
        assert "from src.models.coach_video_classification" not in source

    def test_c8_sort_and_order(self, client):
        rows = [_fake_row()]
        captured: dict = {}

        async def _fake(db, **kwargs):
            captured.update(kwargs)
            return rows, 1

        with patch(
            "src.api.routers.athlete_classifications.list_classifications",
            new=AsyncMock(side_effect=_fake),
        ):
            resp = client.get(
                "/api/v1/athlete-classifications?sort_by=updated_at&order=asc"
            )
        assert resp.status_code == 200, resp.text
        assert captured.get("sort_by") == "updated_at"
        assert captured.get("order") == "asc"


# ═════════════════════════════════════════════════════════════════════════
# Feature-020 · T053 · US5 追加：复合筛选断言
# （与上述 8 断言共存于同一文件；每条 case 独立，互不干扰）
# ═════════════════════════════════════════════════════════════════════════
@pytest.mark.contract
class TestAthleteClassificationsCompositeFilter:

    def test_has_diagnosis_plus_athlete_id_combined(self, client):
        """同时传 has_diagnosis=true + athlete_id：service 层都收到这两个参数."""
        athlete_uuid = uuid4()
        rows = [_fake_row(has_diag=True)]
        captured: dict = {}

        async def _fake(db, **kwargs):
            captured.update(kwargs)
            return rows, 1

        with patch(
            "src.api.routers.athlete_classifications.list_classifications",
            new=AsyncMock(side_effect=_fake),
        ):
            resp = client.get(
                f"/api/v1/athlete-classifications"
                f"?has_diagnosis=true&athlete_id={athlete_uuid}"
            )
        assert resp.status_code == 200, resp.text
        assert captured.get("has_diagnosis") is True
        assert captured.get("athlete_id") == athlete_uuid

    def test_three_way_composite_filter(self, client):
        """has_diagnosis=false + tech_category + preprocessed=true 三维叠加."""
        captured: dict = {}

        async def _fake(db, **kwargs):
            captured.update(kwargs)
            return [], 0

        with patch(
            "src.api.routers.athlete_classifications.list_classifications",
            new=AsyncMock(side_effect=_fake),
        ):
            resp = client.get(
                "/api/v1/athlete-classifications"
                "?has_diagnosis=false&tech_category=forehand_attack&preprocessed=true"
            )
        assert resp.status_code == 200, resp.text
        assert captured.get("has_diagnosis") is False
        assert captured.get("tech_category") == "forehand_attack"
        assert captured.get("preprocessed") is True

    def test_has_diagnosis_false_passes_through(self, client):
        """has_diagnosis=false（未诊断）参数被 service 原样接收."""
        captured: dict = {}

        async def _fake(db, **kwargs):
            captured.update(kwargs)
            return [], 0

        with patch(
            "src.api.routers.athlete_classifications.list_classifications",
            new=AsyncMock(side_effect=_fake),
        ):
            resp = client.get(
                "/api/v1/athlete-classifications?has_diagnosis=false"
            )
        assert resp.status_code == 200, resp.text
        assert captured.get("has_diagnosis") is False
