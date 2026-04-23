"""Regression: run_pipeline() accepts run_id without breaking existing behavior."""

import inspect

from app.modules.equities.service import EquitiesBranchService


def test_run_pipeline_accepts_run_id_kwarg():
    sig = inspect.signature(EquitiesBranchService.run_pipeline)
    assert "run_id" in sig.parameters
    assert sig.parameters["run_id"].default is None
