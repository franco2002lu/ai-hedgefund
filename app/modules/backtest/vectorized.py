"""VectorizedScreeningEngine — fast screening-only backtester."""

from __future__ import annotations

from datetime import date, timedelta

from app.modules.backtest.models import ScreeningSnapshot


class VectorizedScreeningEngine:
    """Runs screening over a date range without full simulation."""

    async def run(
        self,
        branch_name: str,
        start_date: date,
        end_date: date,
        step_days: int = 5,
        screener=None,
        time_provider=None,
        store=None,
        compute_forward_returns: bool = False,
    ) -> list[ScreeningSnapshot]:
        if start_date > end_date:
            return []

        snapshots: list[ScreeningSnapshot] = []
        current_date = start_date

        while current_date <= end_date:
            # Skip weekends
            if current_date.weekday() >= 5:
                current_date += timedelta(days=1)
                continue

            # Advance time
            time_provider.advance_to(current_date)

            # Run screening
            passed = await screener.screen(branch_name)
            passed_symbols = [s.symbol for s in passed]

            # Build per-filter results
            filter_results: dict[str, list[str]] = {}
            if hasattr(screener, "filters") and screener.filters:
                for f in screener.filters:
                    filter_results[str(f.name)] = list(passed_symbols)

            # Compute forward returns if requested
            forward_returns: dict[str, dict[str, float]] | None = None
            if compute_forward_returns and store:
                forward_returns = {}
                for symbol in passed_symbols:
                    current_close = store.get_close(symbol, current_date)
                    if current_close is None:
                        continue
                    returns: dict[str, float] = {}
                    for n in [5, 10, 21]:
                        future_date = current_date + timedelta(days=n)
                        future_close = store.get_close(symbol, future_date)
                        if future_close is not None:
                            returns[str(n)] = (future_close / current_close) - 1
                    forward_returns[symbol] = returns

            snapshots.append(
                ScreeningSnapshot(
                    date=current_date,
                    passed_symbols=passed_symbols,
                    filter_results=filter_results,
                    forward_returns=forward_returns,
                )
            )

            # Advance by step_days
            days_advanced = 0
            while days_advanced < step_days:
                current_date += timedelta(days=1)
                if current_date.weekday() < 5:
                    days_advanced += 1

        return snapshots
