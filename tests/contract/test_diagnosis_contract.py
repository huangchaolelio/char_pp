"""Contract tests for POST /api/v1/diagnosis.

Tests verify response structure matches the API contract defined in plan.md.
Uses TestClient with mocked DiagnosisService to avoid real DB/video processing.

T012 — contract test coverage:
  - 200 response structure: all required fields present and typed correctly
  - 422 response: invalid tech_category
  - 404 response structure: error/detail fields when no standard
  - 400 response structure: extraction_failed
T028 — confirm all FR-006 fields present
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.services.diagnosis_service import (
    DiagnosisReportData,
    DimensionResultData,
    ExtractionFailedError,
    StandardNotFoundError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_report_data(
    overall_score: float = 85.0,
    tech_category: str = "forehand_topspin",
) -> DiagnosisReportData:
    return DiagnosisReportData(
        report_id=uuid.uuid4(),
        tech_category=tech_category,
        standard_id=1,
        standard_version=2,
        overall_score=overall_score,
        strengths=["elbow_angle"],
        dimensions=[
            DimensionResultData(
                dimension="elbow_angle",
                measured_value=92.3,
                ideal_value=95.0,
                standard_min=85.0,
                standard_max=105.0,
                unit="°",
                score=100.0,
                deviation_level="ok",
                deviation_direction="none",
                improvement_advice=None,
            ),
            DimensionResultData(
                dimension="swing_trajectory",
                measured_value=0.45,
                ideal_value=0.65,
                standard_min=0.55,
                standard_max=0.80,
                unit="ratio",
                score=42.0,
                deviation_level="significant",
                deviation_direction="below",
                improvement_advice="增大引拍幅度，确保挥拍轨迹覆盖足够弧线距离。",
            ),
        ],
        created_at=datetime.now(timezone.utc),
    )


def _mock_diagnose(return_value=None, side_effect=None):
    """Context manager that patches DiagnosisService.diagnose."""
    mock = AsyncMock()
    if side_effect:
        mock.side_effect = side_effect
    else:
        mock.return_value = return_value
    return patch(
        "src.api.routers.diagnosis.DiagnosisService.diagnose",
        mock,
    )


# ---------------------------------------------------------------------------
# T012 — 200 response structure
# ---------------------------------------------------------------------------

class TestDiagnosisResponseStructure:
    def test_200_has_report_id_uuid(self):
        with _mock_diagnose(return_value=_make_report_data()):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/diagnosis",
                    json={"tech_category": "forehand_topspin", "video_path": "test.mp4"},
                )
        assert resp.status_code == 200
        data = resp.json()
        # report_id should be a valid UUID string
        uuid.UUID(data["report_id"])

    def test_200_has_tech_category(self):
        with _mock_diagnose(return_value=_make_report_data()):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/diagnosis",
                    json={"tech_category": "forehand_topspin", "video_path": "test.mp4"},
                )
        assert resp.json()["tech_category"] == "forehand_topspin"

    def test_200_has_standard_fields(self):
        with _mock_diagnose(return_value=_make_report_data()):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/diagnosis",
                    json={"tech_category": "forehand_topspin", "video_path": "test.mp4"},
                )
        data = resp.json()
        assert "standard_id" in data
        assert "standard_version" in data
        assert isinstance(data["standard_id"], int)
        assert isinstance(data["standard_version"], int)

    def test_200_overall_score_float_in_range(self):
        with _mock_diagnose(return_value=_make_report_data(overall_score=85.0)):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/diagnosis",
                    json={"tech_category": "forehand_topspin", "video_path": "test.mp4"},
                )
        data = resp.json()
        assert isinstance(data["overall_score"], float)
        assert 0.0 <= data["overall_score"] <= 100.0

    def test_200_has_strengths_list(self):
        """FR-006: report must include strengths (ok dimensions)"""
        with _mock_diagnose(return_value=_make_report_data()):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/diagnosis",
                    json={"tech_category": "forehand_topspin", "video_path": "test.mp4"},
                )
        data = resp.json()
        assert "strengths" in data
        assert isinstance(data["strengths"], list)

    def test_200_dimensions_list_nonempty(self):
        with _mock_diagnose(return_value=_make_report_data()):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/diagnosis",
                    json={"tech_category": "forehand_topspin", "video_path": "test.mp4"},
                )
        data = resp.json()
        assert "dimensions" in data
        assert len(data["dimensions"]) > 0

    def test_200_dimension_item_has_all_fields(self):
        """T028/FR-006: each dimension must have all required fields"""
        with _mock_diagnose(return_value=_make_report_data()):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/diagnosis",
                    json={"tech_category": "forehand_topspin", "video_path": "test.mp4"},
                )
        dim = resp.json()["dimensions"][0]
        required_fields = [
            "dimension", "measured_value", "ideal_value",
            "standard_min", "standard_max", "unit",
            "score", "deviation_level", "deviation_direction", "improvement_advice",
        ]
        for field in required_fields:
            assert field in dim, f"Missing field: {field}"

    def test_200_dimension_ok_has_null_advice(self):
        """FR-006: ok dimension → improvement_advice is null"""
        with _mock_diagnose(return_value=_make_report_data()):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/diagnosis",
                    json={"tech_category": "forehand_topspin", "video_path": "test.mp4"},
                )
        dims = resp.json()["dimensions"]
        ok_dims = [d for d in dims if d["deviation_level"] == "ok"]
        for d in ok_dims:
            assert d["improvement_advice"] is None

    def test_200_dimension_deviant_has_advice(self):
        """FR-006: deviant dimension → improvement_advice is non-null string"""
        with _mock_diagnose(return_value=_make_report_data()):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/diagnosis",
                    json={"tech_category": "forehand_topspin", "video_path": "test.mp4"},
                )
        dims = resp.json()["dimensions"]
        dev_dims = [d for d in dims if d["deviation_level"] != "ok"]
        for d in dev_dims:
            assert d["improvement_advice"] is not None
            assert isinstance(d["improvement_advice"], str)

    def test_200_has_created_at(self):
        with _mock_diagnose(return_value=_make_report_data()):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/diagnosis",
                    json={"tech_category": "forehand_topspin", "video_path": "test.mp4"},
                )
        assert "created_at" in resp.json()


# ---------------------------------------------------------------------------
# T012 — 422 response: invalid tech_category
# ---------------------------------------------------------------------------

class TestInvalidTechCategory:
    def test_422_for_invalid_tech_category(self):
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/diagnosis",
                json={"tech_category": "invalid_move", "video_path": "test.mp4"},
            )
        assert resp.status_code == 422

    def test_422_for_empty_tech_category(self):
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/diagnosis",
                json={"tech_category": "", "video_path": "test.mp4"},
            )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# T012 — 404 response structure: no active standard
# ---------------------------------------------------------------------------

class TestNoStandardResponse:
    def test_404_when_no_standard(self):
        with _mock_diagnose(side_effect=StandardNotFoundError("forehand_topspin")):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/diagnosis",
                    json={"tech_category": "forehand_topspin", "video_path": "test.mp4"},
                )
        assert resp.status_code == 404

    def test_404_has_error_field(self):
        with _mock_diagnose(side_effect=StandardNotFoundError("forehand_topspin")):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/diagnosis",
                    json={"tech_category": "forehand_topspin", "video_path": "test.mp4"},
                )
        inner = resp.json()["detail"]
        assert "error" in inner
        assert inner["error"] == "standard_not_found"

    def test_404_has_detail_field(self):
        with _mock_diagnose(side_effect=StandardNotFoundError("forehand_topspin")):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/diagnosis",
                    json={"tech_category": "forehand_topspin", "video_path": "test.mp4"},
                )
        inner = resp.json()["detail"]
        assert "detail" in inner


# ---------------------------------------------------------------------------
# T012 — 400 response: extraction_failed
# ---------------------------------------------------------------------------

class TestExtractionFailedResponse:
    def test_400_when_extraction_fails(self):
        with _mock_diagnose(side_effect=ExtractionFailedError()):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/diagnosis",
                    json={"tech_category": "forehand_topspin", "video_path": "test.mp4"},
                )
        assert resp.status_code == 400

    def test_400_has_extraction_failed_error(self):
        with _mock_diagnose(side_effect=ExtractionFailedError()):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/diagnosis",
                    json={"tech_category": "forehand_topspin", "video_path": "test.mp4"},
                )
        inner = resp.json()["detail"]
        assert inner["error"] == "extraction_failed"
