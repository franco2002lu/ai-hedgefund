"""tests/unit/db/test_attribution_model.py"""

from app.db.models import AttributionReportModel


def test_attribution_report_model_columns():
    cols = {c.name for c in AttributionReportModel.__table__.columns}
    assert cols == {
        "id",
        "branch_id",
        "branch_name",
        "decision_date",
        "as_of_date",
        "basket_return_conviction",
        "basket_return_equal",
        "benchmark_return",
        "benchmark_symbol",
        "spy_return",
        "analyst_ics",
        "n_holdings",
        "n_holdings_priced",
        "created_at",
    }
    assert AttributionReportModel.__tablename__ == "attribution_reports"
