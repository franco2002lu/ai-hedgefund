"""Seed N-PORT snapshot CSVs for backtest universe loading.

Fetches quarterly N-PORT filings from SEC EDGAR for VOOG (growth) and VOOV (value),
resolves CUSIPs to tickers via OpenFIGI, and writes one CSV per filing to:

    data/universes/nport_snapshots/{etf}_{report_date}.csv

Each CSV has columns: symbol, name, weight (sorted by weight descending).

Usage:
    python scripts/seed_nport_snapshots.py                          # default 5-year lookback
    python scripts/seed_nport_snapshots.py --start 2022-01-01       # custom start
    python scripts/seed_nport_snapshots.py --etfs VOOG              # single ETF
"""

import argparse
import asyncio
import csv
import logging
import os
from datetime import date, timedelta
from pathlib import Path

from app.modules.data_platform.adapters.nport_client import NPortClient, SERIES_MAP, VANGUARD_CIK

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / "data" / "universes" / "nport_snapshots"


async def seed_etf(client: NPortClient, etf_symbol: str, start: date, end: date) -> int:
    """Fetch all N-PORT filings for an ETF and write snapshot CSVs.

    Returns the number of snapshot files written.
    """
    series_id = SERIES_MAP.get(etf_symbol.upper())
    if series_id is None:
        logger.error("Unknown ETF symbol: %s (known: %s)", etf_symbol, list(SERIES_MAP.keys()))
        return 0

    logger.info("Fetching filing index for %s ...", etf_symbol)
    filings = await client.fetch_filing_index(VANGUARD_CIK)
    range_filings = [f for f in filings if start <= f.filing_date <= end]
    logger.info("Found %d N-PORT filings in [%s, %s]", len(range_filings), start, end)

    written = 0
    for filing in range_filings:
        # Check if snapshot already exists
        out_name = f"{etf_symbol.lower()}_{filing.report_date}.csv"
        out_path = SNAPSHOT_DIR / out_name
        if out_path.exists():
            logger.info("  Skipping %s (already exists)", out_name)
            written += 1
            continue

        logger.info("  Fetching holdings for %s (filed %s, report %s) ...",
                     etf_symbol, filing.filing_date, filing.report_date)
        holdings = await client.fetch_holdings(VANGUARD_CIK, filing, series_id)

        if not holdings:
            logger.warning("  No holdings for filing %s — skipping", filing.accession_number)
            continue

        # Write CSV
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["symbol", "name", "weight"])
            for h in holdings:
                writer.writerow([h.ticker, h.name, f"{h.pct_val:.4f}"])

        logger.info("  Wrote %s (%d holdings)", out_name, len(holdings))
        written += 1

    return written


async def main():
    parser = argparse.ArgumentParser(description="Seed N-PORT snapshot CSVs")
    parser.add_argument("--start", type=date.fromisoformat,
                        default=date.today() - timedelta(days=5 * 365),
                        help="Start date (default: 5 years ago)")
    parser.add_argument("--end", type=date.fromisoformat,
                        default=date.today(),
                        help="End date (default: today)")
    parser.add_argument("--etfs", nargs="*", default=["VOOG", "VOOV"],
                        help="ETF symbols to fetch (default: VOOG VOOV)")
    args = parser.parse_args()

    os.makedirs(SNAPSHOT_DIR, exist_ok=True)

    client = NPortClient()
    total = 0
    for etf in args.etfs:
        count = await seed_etf(client, etf.upper(), args.start, args.end)
        total += count
        logger.info("%s: %d snapshots written", etf, count)

    logger.info("Done. %d total snapshot files in %s", total, SNAPSHOT_DIR)


if __name__ == "__main__":
    asyncio.run(main())
