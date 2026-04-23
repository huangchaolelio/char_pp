"""Unit tests for migration 0011_diagnosis_report.

Uses sqlalchemy.inspect on a real PostgreSQL database (synchronous engine)
to verify the migration created the expected tables, columns, indexes,
unique constraints, and foreign keys.

DB URL: postgresql://postgres:password@localhost:5432/coaching_db
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text

DB_URL = "postgresql://postgres:password@localhost:5432/coaching_db"


@pytest.fixture(scope="module")
def inspector():
    engine = create_engine(DB_URL)
    insp = inspect(engine)
    yield insp
    engine.dispose()


# ---------------------------------------------------------------------------
# diagnosis_reports table
# ---------------------------------------------------------------------------

class TestDiagnosisReportsTableExists:
    def test_diagnosis_reports_table_exists(self, inspector):
        tables = inspector.get_table_names()
        assert "diagnosis_reports" in tables


class TestDiagnosisReportsColumns:
    def test_diagnosis_reports_columns(self, inspector):
        cols = {c["name"]: c for c in inspector.get_columns("diagnosis_reports")}

        # id — UUID
        assert "id" in cols
        assert "uuid" in str(cols["id"]["type"]).lower()

        # tech_category — VARCHAR(64)
        assert "tech_category" in cols
        assert "varchar" in str(cols["tech_category"]["type"]).lower()

        # standard_id — BIGINT, not nullable
        assert "standard_id" in cols
        assert "big" in str(cols["standard_id"]["type"]).lower() or \
               "int" in str(cols["standard_id"]["type"]).lower()

        # standard_version — INTEGER
        assert "standard_version" in cols
        assert "int" in str(cols["standard_version"]["type"]).lower()

        # video_path — TEXT
        assert "video_path" in cols
        assert "text" in str(cols["video_path"]["type"]).lower()

        # overall_score — FLOAT / DOUBLE_PRECISION
        assert "overall_score" in cols
        col_type = str(cols["overall_score"]["type"]).lower()
        assert "float" in col_type or "double" in col_type or "real" in col_type

        # strengths_summary — TEXT, nullable
        assert "strengths_summary" in cols
        assert "text" in str(cols["strengths_summary"]["type"]).lower()
        assert cols["strengths_summary"]["nullable"] is True

        # created_at — TIMESTAMPTZ
        assert "created_at" in cols
        col_type = str(cols["created_at"]["type"]).lower()
        assert "timestamp" in col_type


class TestDiagnosisReportsIndexes:
    def test_diagnosis_reports_indexes(self, inspector):
        index_names = {
            idx["name"]
            for idx in inspector.get_indexes("diagnosis_reports")
        }
        assert "idx_dr_tech_category" in index_names
        assert "idx_dr_created_at" in index_names


# ---------------------------------------------------------------------------
# diagnosis_dimension_results table
# ---------------------------------------------------------------------------

class TestDiagnosisDimensionResultsTableExists:
    def test_diagnosis_dimension_results_table_exists(self, inspector):
        tables = inspector.get_table_names()
        assert "diagnosis_dimension_results" in tables


class TestDiagnosisDimensionResultsColumns:
    def test_diagnosis_dimension_results_columns(self, inspector):
        cols = {c["name"]: c for c in inspector.get_columns("diagnosis_dimension_results")}

        # id — BIGINT (BIGSERIAL)
        assert "id" in cols
        assert "int" in str(cols["id"]["type"]).lower()

        # report_id — UUID, not nullable
        assert "report_id" in cols
        assert "uuid" in str(cols["report_id"]["type"]).lower()
        assert cols["report_id"]["nullable"] is False

        # dimension — VARCHAR(128)
        assert "dimension" in cols
        assert "varchar" in str(cols["dimension"]["type"]).lower()

        # score — FLOAT
        assert "score" in cols
        col_type = str(cols["score"]["type"]).lower()
        assert "float" in col_type or "double" in col_type or "real" in col_type

        # deviation_level — VARCHAR(20), not nullable
        assert "deviation_level" in cols
        assert "varchar" in str(cols["deviation_level"]["type"]).lower()
        assert cols["deviation_level"]["nullable"] is False

        # deviation_direction — VARCHAR(10), nullable
        assert "deviation_direction" in cols
        assert "varchar" in str(cols["deviation_direction"]["type"]).lower()
        assert cols["deviation_direction"]["nullable"] is True

        # improvement_advice — TEXT, nullable
        assert "improvement_advice" in cols
        assert "text" in str(cols["improvement_advice"]["type"]).lower()
        assert cols["improvement_advice"]["nullable"] is True


class TestDiagnosisDimensionResultsUniqueConstraint:
    def test_diagnosis_dimension_results_unique_constraint(self, inspector):
        unique_constraints = inspector.get_unique_constraints("diagnosis_dimension_results")
        constraint_names = {uc["name"] for uc in unique_constraints}
        assert "uq_ddr_report_dimension" in constraint_names


class TestDiagnosisDimensionResultsForeignKey:
    def test_diagnosis_dimension_results_fk_to_reports(self, inspector):
        fks = inspector.get_foreign_keys("diagnosis_dimension_results")
        # Find the FK that references diagnosis_reports
        report_fks = [
            fk for fk in fks
            if fk["referred_table"] == "diagnosis_reports"
        ]
        assert len(report_fks) >= 1, (
            "Expected a FK from diagnosis_dimension_results.report_id "
            "to diagnosis_reports.id"
        )
        fk = report_fks[0]
        assert "report_id" in fk["constrained_columns"]
        assert "id" in fk["referred_columns"]
