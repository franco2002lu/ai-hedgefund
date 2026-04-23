"""Unit tests for the PipelineRunModel ORM class."""

from app.db.models import PipelineRunModel


def test_pipeline_run_model_has_expected_columns():
    cols = {c.name for c in PipelineRunModel.__table__.columns}
    expected = {
        "run_id",
        "branch_id",
        "run_date",
        "attempt",
        "status",
        "started_at",
        "completed_at",
        "summary_json",
        "error_msg",
    }
    assert expected.issubset(cols)


def test_pipeline_run_model_primary_key_is_run_id():
    pk_cols = [c.name for c in PipelineRunModel.__table__.primary_key.columns]
    assert pk_cols == ["run_id"]


def test_pipeline_run_model_has_branch_date_index():
    indexes = {ix.name for ix in PipelineRunModel.__table__.indexes}
    assert "idx_pipeline_runs_branch_date" in indexes
