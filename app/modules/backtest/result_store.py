"""Backtest run persistence and prompt fingerprinting.

Saves each backtest run as a JSON file in data/backtest_runs/<id>.json. Provides
hash_skill_bundle to produce a deterministic fingerprint of a skill directory
(used to identify which prompt version produced a given run).
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel

from app.modules.backtest.config import BacktestConfig
from app.modules.backtest.models import (
    BacktestTrade,
    BenchmarkComparison,
    DailySnapshot,
    PerformanceMetrics,
)

_EXCLUDED_DIRS = {"__pycache__"}


def hash_skill_bundle(skills_dir: Path) -> str:
    """Compute a deterministic sha256 hash of all skill files in a directory.

    Walks the directory, sorts files by relative path, and hashes the concatenated
    content. Stable across machines and OS. Skips __pycache__ and other excluded
    directories so build artifacts don't perturb the hash.

    Returns a full 64-character sha256 hex digest. Callers can take a 12-char
    prefix for human-readable use.

    Raises:
        FileNotFoundError: if skills_dir doesn't exist or isn't a directory.
            Without this guard, a typo'd path would silently produce the empty
            sha256 (e3b0c44...) and tag a backtest run with a meaningless hash.
    """
    root = Path(skills_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Skill bundle directory not found: {skills_dir}")
    hasher = hashlib.sha256()
    # Collect files, excluding any path that contains an excluded directory
    files: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in _EXCLUDED_DIRS for part in p.relative_to(root).parts):
            continue
        files.append(p)
    # Sort by relative path for deterministic ordering
    for f in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
        rel = f.relative_to(root).as_posix()
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(f.read_bytes())
        hasher.update(b"\x00")
    return hasher.hexdigest()


class StockSignalRecord(BaseModel):
    """One analyst's signal for one stock on one rebalance day."""

    date: date
    symbol: str
    analyst_type: str
    bullish_score: int
    confidence: int
    summary: str


class BacktestRun(BaseModel):
    """A saved backtest run with full metadata for later comparison."""

    run_id: str
    timestamp: datetime
    git_sha: str
    config: BacktestConfig
    skill_bundle_name: str | None
    skill_bundle_hash: str  # full sha256 of all skill files concatenated
    metrics: PerformanceMetrics | None
    benchmarks: list[BenchmarkComparison] = []
    snapshots: list[DailySnapshot] = []
    trades: list[BacktestTrade] = []
    signals: list[StockSignalRecord] = []
    llm_cache_hits: int = 0
    llm_cache_misses: int = 0


def save_run(run: BacktestRun, runs_dir: Path = Path("data/backtest_runs")) -> Path:
    """Serialize a BacktestRun to JSON and write it to runs_dir/<run_id>.json.

    Creates runs_dir if it doesn't exist. Overwrites any existing file with
    the same run_id (callers should use unique IDs).
    """
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"{run.run_id}.json"
    path.write_text(run.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_run(run_id: str, runs_dir: Path = Path("data/backtest_runs")) -> BacktestRun:
    """Load a BacktestRun by run_id. Raises FileNotFoundError if missing."""
    path = runs_dir / f"{run_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Backtest run not found: {path}")
    return BacktestRun.model_validate_json(path.read_text(encoding="utf-8"))


def list_runs(runs_dir: Path = Path("data/backtest_runs")) -> list[dict]:
    """List all saved runs as summary dicts, sorted by timestamp descending.

    Each entry contains: run_id, timestamp, skill_bundle_hash (first 12 chars),
    config (summary: dates, top_n, branch), metrics (total_return, sharpe_ratio).
    """
    if not runs_dir.is_dir():
        return []
    entries: list[dict] = []
    for path in sorted(runs_dir.glob("*.json")):
        try:
            run = BacktestRun.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        entries.append({
            "run_id": run.run_id,
            "timestamp": run.timestamp.isoformat(),
            "skill_bundle_hash_short": run.skill_bundle_hash[:12],
            "start_date": run.config.start_date.isoformat(),
            "end_date": run.config.end_date.isoformat(),
            "top_n": run.config.top_n,
            "branch_name": run.config.branch_name,
            "total_return": run.metrics.total_return if run.metrics else None,
            "sharpe_ratio": run.metrics.sharpe_ratio if run.metrics else None,
        })
    entries.sort(key=lambda e: e["timestamp"], reverse=True)
    return entries
