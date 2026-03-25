"""XBRL concept → metric key mapping and TTM computation for SEC EDGAR data."""

from __future__ import annotations

from datetime import date

# ---------------------------------------------------------------------------
# XBRL concept mapping: our metric key → list of XBRL concept names (priority order)
# Each entry is (taxonomy, concept_name).
# ---------------------------------------------------------------------------

XBRL_CONCEPTS: dict[str, list[tuple[str, str]]] = {
    # Income statement (flow items — need TTM aggregation)
    "revenue": [
        ("us-gaap", "Revenues"),
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "SalesRevenueNet"),
        ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax"),
    ],
    "net_income": [
        ("us-gaap", "NetIncomeLoss"),
        ("us-gaap", "ProfitLoss"),
    ],
    "gross_profit": [
        ("us-gaap", "GrossProfit"),
    ],
    "operating_income": [
        ("us-gaap", "OperatingIncomeLoss"),
    ],
    # Balance sheet (stock items — use latest value)
    "total_assets": [
        ("us-gaap", "Assets"),
    ],
    "total_liabilities": [
        ("us-gaap", "Liabilities"),
    ],
    "stockholders_equity": [
        ("us-gaap", "StockholdersEquity"),
        ("us-gaap", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    ],
    "long_term_debt": [
        ("us-gaap", "LongTermDebt"),
        ("us-gaap", "LongTermDebtAndCapitalLeaseObligations"),
        ("us-gaap", "LongTermDebtNoncurrent"),
    ],
    "current_assets": [
        ("us-gaap", "AssetsCurrent"),
    ],
    "current_liabilities": [
        ("us-gaap", "LiabilitiesCurrent"),
    ],
    # Cash flow (flow items — need TTM aggregation)
    "operating_cash_flow": [
        ("us-gaap", "NetCashProvidedByOperatingActivities"),
    ],
    "capex": [
        ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),
    ],
    # Per-share items
    "eps_diluted": [
        ("us-gaap", "EarningsPerShareDiluted"),
        ("us-gaap", "EarningsPerShareBasic"),
    ],
    "shares_outstanding": [
        ("dei", "EntityCommonStockSharesOutstanding"),
        ("us-gaap", "CommonStockSharesOutstanding"),
        ("us-gaap", "WeightedAverageNumberOfSharesOutstandingDiluted"),
    ],
    "dividends_per_share": [
        ("us-gaap", "CommonStockDividendsPerShareDeclared"),
    ],
}

# Which metrics are flow items (need TTM = sum of 4 quarters)
# vs stock items (use latest snapshot)
FLOW_ITEMS = {
    "revenue", "net_income", "gross_profit", "operating_income",
    "operating_cash_flow", "capex", "eps_diluted", "dividends_per_share",
}

STOCK_ITEMS = {
    "total_assets", "total_liabilities", "stockholders_equity",
    "long_term_debt", "current_assets", "current_liabilities",
    "shares_outstanding",
}


def get_all_xbrl_concept_names() -> dict[str, list[str]]:
    """Return {taxonomy: [concept_names]} for use with parse_xbrl_filings()."""
    result: dict[str, list[str]] = {}
    for concepts in XBRL_CONCEPTS.values():
        for taxonomy, concept_name in concepts:
            result.setdefault(taxonomy, [])
            if concept_name not in result[taxonomy]:
                result[taxonomy].append(concept_name)
    return result


# Reverse mapping: XBRL concept name → our metric key
_CONCEPT_TO_METRIC: dict[str, str] = {}
for _metric_key, _concepts in XBRL_CONCEPTS.items():
    for _taxonomy, _concept_name in _concepts:
        if _concept_name not in _CONCEPT_TO_METRIC:
            _CONCEPT_TO_METRIC[_concept_name] = _metric_key


def concept_to_metric_key(concept_name: str) -> str | None:
    """Map an XBRL concept name back to our metric key."""
    return _CONCEPT_TO_METRIC.get(concept_name)


class QuarterlySnapshot:
    """A single quarterly filing's data for one company."""

    __slots__ = ("period_end", "filed_date", "form", "fp", "fy", "values")

    def __init__(
        self,
        period_end: date,
        filed_date: date,
        form: str,
        fp: str,
        fy: int | None,
    ) -> None:
        self.period_end = period_end
        self.filed_date = filed_date
        self.form = form
        self.fp = fp
        self.fy = fy
        self.values: dict[str, float] = {}  # metric_key → value

    def __repr__(self) -> str:
        return f"QuarterlySnapshot({self.period_end}, filed={self.filed_date}, {self.form} {self.fp})"


def build_quarterly_snapshots(
    filing_records: list[dict],
) -> list[QuarterlySnapshot]:
    """Group parsed XBRL filing records into QuarterlySnapshot objects.

    Args:
        filing_records: Output from parse_xbrl_filings().

    Returns:
        List of QuarterlySnapshot sorted by filed_date ascending.
    """
    # Group by (period_end, form, filed_date) to create snapshots
    # Key: (period_end, filed_date, form)
    snapshot_map: dict[tuple[date, date, str], QuarterlySnapshot] = {}

    for record in filing_records:
        metric_key = concept_to_metric_key(record["concept"])
        if metric_key is None:
            continue

        key = (record["end"], record["filed"], record["form"])
        if key not in snapshot_map:
            snapshot_map[key] = QuarterlySnapshot(
                period_end=record["end"],
                filed_date=record["filed"],
                form=record["form"],
                fp=record["fp"],
                fy=record["fy"],
            )

        snap = snapshot_map[key]
        # Only set if not already set (first concept in priority list wins)
        if metric_key not in snap.values:
            snap.values[metric_key] = record["value"]

    # Sort by filed_date ascending
    snapshots = sorted(snapshot_map.values(), key=lambda s: (s.filed_date, s.period_end))

    # Deduplicate: keep the most data-rich snapshot per (period_end, form)
    seen: dict[tuple[date, str], QuarterlySnapshot] = {}
    for snap in snapshots:
        dedup_key = (snap.period_end, snap.form)
        existing = seen.get(dedup_key)
        if existing is None or len(snap.values) > len(existing.values):
            seen[dedup_key] = snap

    return sorted(seen.values(), key=lambda s: s.filed_date)


def compute_ttm(
    snapshots: list[QuarterlySnapshot],
    metric_key: str,
    as_of: date,
) -> float | None:
    """Compute trailing twelve months value for a flow metric.

    Sums the 4 most recent quarterly values where filed_date <= as_of.
    For 10-K filings that already represent annual data, uses the value directly.

    Args:
        snapshots: Sorted list of QuarterlySnapshot for one company.
        metric_key: The flow metric to compute (e.g. "revenue").
        as_of: Point-in-time date.

    Returns:
        TTM value, or None if insufficient data.
    """
    # Find quarterly filings available as-of the given date
    available = [
        s for s in snapshots
        if s.filed_date <= as_of and metric_key in s.values
    ]

    if not available:
        return None

    latest = available[-1]

    # If the latest filing is a 10-K (annual), use it directly as TTM
    if latest.form == "10-K":
        return latest.values[metric_key]

    # Otherwise, sum the 4 most recent quarterly 10-Q/10-K values
    # Filter to only quarterly-period filings (10-Q has fp in Q1/Q2/Q3,
    # 10-K has fp=FY but represents Q4 implicitly)
    quarterly_values: list[float] = []
    seen_periods: set[date] = set()

    for snap in reversed(available):
        if snap.period_end in seen_periods:
            continue
        if metric_key not in snap.values:
            continue
        # For 10-K, this is the annual value — return directly
        if snap.form == "10-K":
            return snap.values[metric_key]
        quarterly_values.append(snap.values[metric_key])
        seen_periods.add(snap.period_end)
        if len(quarterly_values) >= 4:
            break

    if len(quarterly_values) >= 4:
        return sum(quarterly_values)

    return None


def get_latest_stock_value(
    snapshots: list[QuarterlySnapshot],
    metric_key: str,
    as_of: date,
) -> float | None:
    """Get the latest stock (point-in-time) value for a balance sheet metric.

    Args:
        snapshots: Sorted list of QuarterlySnapshot for one company.
        metric_key: The stock metric (e.g. "total_assets").
        as_of: Point-in-time date.

    Returns:
        Latest value, or None if unavailable.
    """
    for snap in reversed(snapshots):
        if snap.filed_date <= as_of and metric_key in snap.values:
            return snap.values[metric_key]
    return None
