"""Feature-021 T068 — GET /api/v1/curation-stats 合约测试.

覆盖 contracts/curation_stats.md 全部 6 条用例：
1. ✅ group_by=coach 分页正常
2. ✅ group_by=tech_category 限定 coach_name
3. ✅ group_by=rubric_version 对比 v1 vs v2
4. ❌ group_by 缺失 ⇒ 422 VALIDATION_FAILED
5. ❌ page_size=200 ⇒ 422 VALIDATION_FAILED（Pydantic Query le=100 拦截）
   注：spec contracts 写的是 400 INVALID_PAGE_SIZE，但项目所有路由统一通过 Pydantic
   Query 校验，越界统一返 422 VALIDATION_FAILED（与 F-017 章程 v1.4.0 实践一致）。
6. ✅ 数据源为空 ⇒ 200 + data=[]、meta.total=0
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.services.curation.curation_service import CurationStatsItemDTO
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


_PATCH_AGG = "src.api.routers.curation_stats.aggregate_curation_stats"


@pytest.mark.contract
class TestGetCurationStatsContract:

    def test_c1_group_by_coach_paginated(self, client):
        items = [
            CurationStatsItemDTO(
                coach_name="张继科",
                tech_category=None,
                curation_rubric_version=None,
                video_count=45,
                avg_accepted_duration_ratio=0.72,
                avg_validity_score=0.78,
                low_quality_video_count=3,
                with_overrides_video_count=5,
                low_sample=False,
            ),
            CurationStatsItemDTO(
                coach_name="孙浩泓",
                tech_category=None,
                curation_rubric_version=None,
                video_count=120,
                avg_accepted_duration_ratio=0.61,
                avg_validity_score=0.69,
                low_quality_video_count=12,
                with_overrides_video_count=8,
                low_sample=False,
            ),
        ]
        with patch(
            _PATCH_AGG, new_callable=AsyncMock, return_value=(items, 12)
        ) as mock_agg:
            resp = client.get(
                "/api/v1/curation-stats?group_by=coach&page=1&page_size=20"
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        data = assert_success_envelope(body, expect_meta=True)
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["coach_name"] == "张继科"
        assert data[0]["tech_category"] is None
        assert data[0]["video_count"] == 45
        assert data[0]["with_overrides_video_count"] == 5
        assert data[0]["low_sample"] is False
        assert body["meta"]["page"] == 1
        assert body["meta"]["page_size"] == 20
        assert body["meta"]["total"] == 12
        # 参数透传
        kwargs = mock_agg.call_args.kwargs
        assert kwargs["group_by"] == "coach"
        assert kwargs["page"] == 1
        assert kwargs["page_size"] == 20

    def test_c2_group_by_tech_category_with_coach_filter(self, client):
        items = [
            CurationStatsItemDTO(
                coach_name=None,
                tech_category="forehand_topspin",
                curation_rubric_version=None,
                video_count=8,
                avg_accepted_duration_ratio=0.7,
                avg_validity_score=0.74,
                low_quality_video_count=1,
                with_overrides_video_count=0,
                low_sample=False,
            ),
        ]
        with patch(
            _PATCH_AGG, new_callable=AsyncMock, return_value=(items, 1)
        ) as mock_agg:
            resp = client.get(
                "/api/v1/curation-stats?group_by=tech_category&coach_name=张继科"
            )
        assert resp.status_code == 200, resp.text
        data = assert_success_envelope(resp.json(), expect_meta=True)
        assert data[0]["tech_category"] == "forehand_topspin"
        assert data[0]["coach_name"] is None
        kwargs = mock_agg.call_args.kwargs
        assert kwargs["group_by"] == "tech_category"
        assert kwargs["coach_name"] == "张继科"

    def test_c3_group_by_rubric_version(self, client):
        items = [
            CurationStatsItemDTO(
                coach_name=None,
                tech_category=None,
                curation_rubric_version="v1",
                video_count=200,
                avg_accepted_duration_ratio=0.65,
                avg_validity_score=0.71,
                low_quality_video_count=18,
                with_overrides_video_count=None,
                low_sample=False,
            ),
            CurationStatsItemDTO(
                coach_name=None,
                tech_category=None,
                curation_rubric_version="v2",
                video_count=200,
                avg_accepted_duration_ratio=0.74,
                avg_validity_score=0.79,
                low_quality_video_count=8,
                with_overrides_video_count=None,
                low_sample=False,
            ),
        ]
        with patch(
            _PATCH_AGG, new_callable=AsyncMock, return_value=(items, 2)
        ):
            resp = client.get("/api/v1/curation-stats?group_by=rubric_version")
        assert resp.status_code == 200, resp.text
        data = assert_success_envelope(resp.json(), expect_meta=True)
        assert {d["curation_rubric_version"] for d in data} == {"v1", "v2"}
        # rubric_version 维度无 with_overrides 字段语义（service 返 None）
        for d in data:
            assert d["with_overrides_video_count"] is None

    def test_c4_group_by_missing_returns_422(self, client):
        resp = client.get("/api/v1/curation-stats")
        assert resp.status_code == 422, resp.text
        assert_error_envelope(resp.json(), code="VALIDATION_FAILED")

    def test_c5_page_size_too_large_returns_422(self, client):
        # Pydantic Query le=100 拦截，统一返 422 VALIDATION_FAILED
        resp = client.get(
            "/api/v1/curation-stats?group_by=coach&page_size=200"
        )
        assert resp.status_code == 422, resp.text
        assert_error_envelope(resp.json(), code="VALIDATION_FAILED")

    def test_c6_empty_dataset_returns_empty_list(self, client):
        with patch(
            _PATCH_AGG, new_callable=AsyncMock, return_value=([], 0)
        ):
            resp = client.get("/api/v1/curation-stats?group_by=coach")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        data = assert_success_envelope(body, expect_meta=True)
        assert data == []
        assert body["meta"]["total"] == 0
