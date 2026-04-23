"""Unit tests for DiagnosisReport and DiagnosisDimensionResult ORM models.

These tests do NOT hit a real database — they only inspect model metadata
and verify that instantiation works correctly in memory.
"""

from __future__ import annotations

import os
import uuid

import pytest

# Provide required env vars before any src imports
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("COS_SECRET_ID", "test-id")
os.environ.setdefault("COS_SECRET_KEY", "test-key")
os.environ.setdefault("COS_REGION", "ap-guangzhou")
os.environ.setdefault("COS_BUCKET", "test-bucket")

import src.models  # noqa: F401 — triggers mapper registration

from src.models.diagnosis_report import (
    DeviationDirection,
    DeviationLevel,
    DiagnosisDimensionResult,
    DiagnosisReport,
)


# ---------------------------------------------------------------------------
# Table names
# ---------------------------------------------------------------------------

class TestTableNames:
    def test_diagnosis_report_tablename(self):
        assert DiagnosisReport.__tablename__ == "diagnosis_reports"

    def test_diagnosis_dimension_result_tablename(self):
        assert DiagnosisDimensionResult.__tablename__ == "diagnosis_dimension_results"


# ---------------------------------------------------------------------------
# Enum values
# ---------------------------------------------------------------------------

class TestDeviationLevelEnum:
    def test_deviation_level_values(self):
        assert DeviationLevel.ok.value == "ok"
        assert DeviationLevel.slight.value == "slight"
        assert DeviationLevel.significant.value == "significant"
        # Ensure no extra or missing members
        assert set(e.value for e in DeviationLevel) == {"ok", "slight", "significant"}


class TestDeviationDirectionEnum:
    def test_deviation_direction_values(self):
        assert DeviationDirection.above.value == "above"
        assert DeviationDirection.below.value == "below"
        assert DeviationDirection.none.value == "none"
        assert set(e.value for e in DeviationDirection) == {"above", "below", "none"}


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------

class TestRelationships:
    def test_diagnosis_report_has_dimensions_relationship(self):
        assert hasattr(DiagnosisReport, "dimensions")

    def test_dimension_result_has_report_relationship(self):
        assert hasattr(DiagnosisDimensionResult, "report")


# ---------------------------------------------------------------------------
# Instantiation (no DB)
# ---------------------------------------------------------------------------

class TestDiagnosisReportInstantiation:
    def test_diagnosis_report_instantiation(self):
        report = DiagnosisReport(
            tech_category="forehand_topspin",
            standard_id=1,
            standard_version=1,
            video_path="/tmp/test.mp4",
            overall_score=85.0,
        )
        assert report.tech_category == "forehand_topspin"
        assert report.standard_id == 1
        assert report.standard_version == 1
        assert report.video_path == "/tmp/test.mp4"
        assert report.overall_score == pytest.approx(85.0)

    def test_strengths_summary_nullable(self):
        """strengths_summary is optional — instantiating without it must not raise."""
        report = DiagnosisReport(
            tech_category="forehand_topspin",
            standard_id=1,
            standard_version=1,
            video_path="/tmp/test.mp4",
            overall_score=85.0,
        )
        # strengths_summary should be None (nullable)
        assert report.strengths_summary is None

        # Also verify the mapped column is declared nullable
        col = DiagnosisReport.__table__.c["strengths_summary"]
        assert col.nullable is True


class TestDiagnosisDimensionResultInstantiation:
    def test_dimension_result_instantiation(self):
        rid = uuid.uuid4()
        result = DiagnosisDimensionResult(
            report_id=rid,
            dimension="elbow_angle",
            measured_value=90.0,
            ideal_value=95.0,
            standard_min=85.0,
            standard_max=105.0,
            score=100.0,
            deviation_level="ok",
        )
        assert result.report_id == rid
        assert result.dimension == "elbow_angle"
        assert result.measured_value == pytest.approx(90.0)
        assert result.ideal_value == pytest.approx(95.0)
        assert result.standard_min == pytest.approx(85.0)
        assert result.standard_max == pytest.approx(105.0)
        assert result.score == pytest.approx(100.0)
        assert result.deviation_level == "ok"

    def test_improvement_advice_nullable(self):
        """improvement_advice is optional — instantiating without it must not raise."""
        result = DiagnosisDimensionResult(
            report_id=uuid.uuid4(),
            dimension="elbow_angle",
            measured_value=90.0,
            ideal_value=95.0,
            standard_min=85.0,
            standard_max=105.0,
            score=100.0,
            deviation_level="ok",
        )
        assert result.improvement_advice is None

        # Also verify the mapped column is declared nullable
        col = DiagnosisDimensionResult.__table__.c["improvement_advice"]
        assert col.nullable is True
