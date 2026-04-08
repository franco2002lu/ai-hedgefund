# LLM-Mode Backtest Attribution — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable reproducible LLM-mode backtests. After this phase, running `python -m scripts.run_backtest <dates> --top-n 50 --llm --save` twice with identical arguments produces bit-identical numbers because every LLM call hits a persistent SQLite cache.

**Architecture:** Add a SQLite-backed `LLMResponseCache` keyed on `hash(system_prompt + user_prompt + model + temperature)`. Wrap `AnthropicAnalystClient.invoke()` to check the cache before the API call. Plumb an optional `skills_dir` parameter through `compose_system_prompt` and the three analyst constructors so alternate skill bundles can be loaded without changing the live `_SKILLS_DIR` constant. Add a `BacktestRun` pydantic model + JSON-file result store with prompt fingerprinting. Extend the `run_backtest` CLI with `--llm`, `--save`, `--skills-bundle`, `--no-llm-cache`, `--temperature`, and `--max-llm-calls-per-rebalance` flags.

**Tech Stack:** Python 3.12, SQLite (stdlib `sqlite3`), Pydantic 2, pytest, asyncio, existing Anthropic SDK wrapper.

**Spec:** See `plans/architecture/LLM-BACKTEST-ATTRIBUTION.md` §6 for the full Phase 1 component specification.

**Out of scope for this phase:** Phase 2 comparison primitives (`compare_runs`, drill-down), Phase 3 variance probing and experiment harness. Those get their own implementation plans after this phase ships and produces real saved runs.

---

## File Structure Overview

### New files

| Path | Responsibility |
|---|---|
| `app/modules/backtest/llm_response_cache.py` | SQLite-backed persistent cache for LLM responses. Single `LLMResponseCache` class with `get/put/stats/close`, plus instance-level `hits`/`misses` counters. |
| `app/modules/backtest/result_store.py` | Pydantic models (`StockSignalRecord`, `BacktestRun`), prompt fingerprinting (`hash_skill_bundle`), and file-based save/load/list helpers. |
| `scripts/bundle_skills.py` | CLI script to snapshot the live `app/.../skills/` directory as a named bundle under `data/skill_bundles/<name>/`. |
| `tests/unit/backtest/test_llm_response_cache.py` | Unit tests for `LLMResponseCache`. |
| `tests/unit/backtest/test_result_store.py` | Unit tests for the result store module. |
| `tests/unit/backtest/test_bundle_skills.py` | Unit tests for the `bundle_skills` script. |
| `tests/integration/backtest/test_llm_reproducibility.py` | Cost-gated E2E test: two identical `--llm --save` runs produce bit-identical outputs. |

### Modified files

| Path | Change |
|---|---|
| `app/modules/equities/agents/skills/loader.py` | Add `skills_dir: Path \| None = None` parameter to `compose_system_prompt`. |
| `app/modules/equities/agents/fundamentals_analyst.py` | Add `skills_dir` constructor arg, store it, pass to `compose_system_prompt`. |
| `app/modules/equities/agents/news_analyst.py` | Same as above. |
| `app/modules/equities/agents/technical_analyst.py` | Same as above. |
| `app/modules/equities/agents/llm_client.py` | Add `response_cache` constructor arg; check cache inside `invoke()` before API call. |
| `app/modules/backtest/config.py` | Add `skills_bundle`, `use_llm_response_cache`, `llm_response_cache_path` fields. |
| `app/modules/backtest/models.py` | Add `signals: list[StockSignalRecord]` and `llm_cache_hits/misses` to `BacktestResult`. |
| `app/modules/backtest/context.py` | Resolve `skills_bundle` to path; construct `LLMResponseCache`; pass both into analyst/client constructors. |
| `app/modules/backtest/engine.py` | At end-of-run, populate `BacktestResult.signals` from `ctx.llm_cache` and cache stats. |
| `scripts/run_backtest.py` | Add new CLI flags; wire up `--save` path that builds and persists a `BacktestRun`. |
| `tests/unit/equities/test_skill_loader.py` | Add tests for `skills_dir` parameter. |
| `tests/unit/equities/test_llm_client.py` | Add tests for `response_cache` wrapping. |
| `tests/unit/equities/test_fundamentals_analyst.py` | Add test for `skills_dir` constructor arg. |
| `tests/unit/equities/test_news_analyst.py` | Same. |
| `tests/unit/equities/test_technical_analyst.py` | Same. |
| `tests/unit/backtest/test_context.py` | Add tests for bundle resolution and cache instantiation. |
| `plans/architecture/LLM-BACKTEST-ATTRIBUTION.md` | Update Phase 1 section with any implementation deltas. |

---

## Task 1: Create `LLMResponseCache` module

**Files:**
- Create: `app/modules/backtest/llm_response_cache.py`
- Test: `tests/unit/backtest/test_llm_response_cache.py`

### Step 1.1: Write the failing test for cache construction

- [ ] **Create `tests/unit/backtest/test_llm_response_cache.py`** with:

```python
"""Unit tests for LLMResponseCache — the persistent SQLite cache for LLM responses."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.backtest.llm_response_cache import LLMResponseCache


class TestLLMResponseCacheInit:
    def test_creates_db_file_and_schema_on_first_use(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test_cache.db"
        assert not db_path.exists()

        cache = LLMResponseCache(db_path)
        try:
            assert db_path.exists()
            # Empty cache has 0 entries, 0 hits, 0 misses
            stats = cache.stats()
            assert stats["entry_count"] == 0
            assert stats["hits"] == 0
            assert stats["misses"] == 0
        finally:
            cache.close()

    def test_reopens_existing_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test_cache.db"
        cache1 = LLMResponseCache(db_path)
        cache1.put("sys", "usr", "claude-sonnet-4-6", 0.0, {"bullish_score": 7})
        cache1.close()

        cache2 = LLMResponseCache(db_path)
        try:
            result = cache2.get("sys", "usr", "claude-sonnet-4-6", 0.0)
            assert result == {"bullish_score": 7}
        finally:
            cache2.close()
```

### Step 1.2: Run tests to verify failure

- [ ] Run: `pytest tests/unit/backtest/test_llm_response_cache.py -v`
- Expected: `ModuleNotFoundError: No module named 'app.modules.backtest.llm_response_cache'`

### Step 1.3: Create the `LLMResponseCache` class

- [ ] **Create `app/modules/backtest/llm_response_cache.py`** with:

```python
"""Persistent SQLite cache for Anthropic API responses.

Keyed on hash(system_prompt + user_prompt + model + temperature). Used by
backtests to make LLM-mode runs reproducible — identical inputs always
return the cached response without hitting the API.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_responses (
    cache_key TEXT PRIMARY KEY,
    system_prompt TEXT NOT NULL,
    user_prompt TEXT NOT NULL,
    model TEXT NOT NULL,
    temperature REAL NOT NULL,
    response_json TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    hit_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_created_at ON llm_responses(created_at);
"""


def _compute_cache_key(system_prompt: str, user_prompt: str, model: str, temperature: float) -> str:
    """Deterministic hash of the inputs. Identical inputs always produce the same key."""
    hasher = hashlib.sha256()
    hasher.update(system_prompt.encode("utf-8"))
    hasher.update(b"\x00")
    hasher.update(user_prompt.encode("utf-8"))
    hasher.update(b"\x00")
    hasher.update(model.encode("utf-8"))
    hasher.update(b"\x00")
    hasher.update(f"{temperature:.6f}".encode("utf-8"))
    return hasher.hexdigest()


class LLMResponseCache:
    """SQLite-backed persistent cache for LLM responses.

    Thread-safe for concurrent reads; single-writer for puts (SQLite handles
    locking internally). Use one instance per backtest run.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self.hits = 0
        self.misses = 0

    def get(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
    ) -> dict | None:
        """Return cached response or None on miss. Increments hit/miss counters."""
        cache_key = _compute_cache_key(system_prompt, user_prompt, model, temperature)
        row = self._conn.execute(
            "SELECT response_json FROM llm_responses WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if row is None:
            self.misses += 1
            return None
        self._conn.execute(
            "UPDATE llm_responses SET hit_count = hit_count + 1 WHERE cache_key = ?",
            (cache_key,),
        )
        self._conn.commit()
        self.hits += 1
        return json.loads(row[0])

    def put(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
        response: dict,
    ) -> None:
        """Store a response. Upserts on the cache key (idempotent)."""
        cache_key = _compute_cache_key(system_prompt, user_prompt, model, temperature)
        self._conn.execute(
            """
            INSERT INTO llm_responses
                (cache_key, system_prompt, user_prompt, model, temperature, response_json, created_at, hit_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(cache_key) DO UPDATE SET
                response_json = excluded.response_json
            """,
            (
                cache_key,
                system_prompt,
                user_prompt,
                model,
                temperature,
                json.dumps(response),
                datetime.utcnow().isoformat(),
            ),
        )
        self._conn.commit()

    def stats(self) -> dict:
        """Return cache statistics for inspection / diagnostics."""
        row = self._conn.execute("SELECT COUNT(*) FROM llm_responses").fetchone()
        entry_count = row[0] if row else 0
        db_size_bytes = self._db_path.stat().st_size if self._db_path.exists() else 0
        return {
            "entry_count": entry_count,
            "hits": self.hits,
            "misses": self.misses,
            "db_size_bytes": db_size_bytes,
        }

    def close(self) -> None:
        self._conn.close()
```

### Step 1.4: Run tests to verify they pass

- [ ] Run: `pytest tests/unit/backtest/test_llm_response_cache.py::TestLLMResponseCacheInit -v`
- Expected: 2 passed.

### Step 1.5: Add put/get round-trip and missing-key tests

- [ ] **Append to `tests/unit/backtest/test_llm_response_cache.py`**:

```python
class TestLLMResponseCachePutGet:
    def test_put_then_get_round_trip(self, tmp_path: Path) -> None:
        cache = LLMResponseCache(tmp_path / "cache.db")
        try:
            response = {"bullish_score": 8, "confidence": 7, "summary": "strong"}
            cache.put("sys-A", "usr-A", "claude-sonnet-4-6", 0.0, response)
            result = cache.get("sys-A", "usr-A", "claude-sonnet-4-6", 0.0)
            assert result == response
        finally:
            cache.close()

    def test_missing_key_returns_none_and_increments_miss(self, tmp_path: Path) -> None:
        cache = LLMResponseCache(tmp_path / "cache.db")
        try:
            result = cache.get("no", "such", "claude-sonnet-4-6", 0.0)
            assert result is None
            assert cache.misses == 1
            assert cache.hits == 0
        finally:
            cache.close()

    def test_hit_increments_hit_counter(self, tmp_path: Path) -> None:
        cache = LLMResponseCache(tmp_path / "cache.db")
        try:
            cache.put("sys", "usr", "claude-sonnet-4-6", 0.0, {"x": 1})
            cache.get("sys", "usr", "claude-sonnet-4-6", 0.0)
            cache.get("sys", "usr", "claude-sonnet-4-6", 0.0)
            assert cache.hits == 2
            assert cache.misses == 0
        finally:
            cache.close()

    def test_different_temperature_is_different_key(self, tmp_path: Path) -> None:
        cache = LLMResponseCache(tmp_path / "cache.db")
        try:
            cache.put("sys", "usr", "claude-sonnet-4-6", 0.0, {"at_zero": True})
            cache.put("sys", "usr", "claude-sonnet-4-6", 0.5, {"at_zero": False})
            a = cache.get("sys", "usr", "claude-sonnet-4-6", 0.0)
            b = cache.get("sys", "usr", "claude-sonnet-4-6", 0.5)
            assert a == {"at_zero": True}
            assert b == {"at_zero": False}
        finally:
            cache.close()

    def test_put_is_idempotent_on_duplicate_key(self, tmp_path: Path) -> None:
        cache = LLMResponseCache(tmp_path / "cache.db")
        try:
            cache.put("sys", "usr", "claude-sonnet-4-6", 0.0, {"v": 1})
            cache.put("sys", "usr", "claude-sonnet-4-6", 0.0, {"v": 2})
            result = cache.get("sys", "usr", "claude-sonnet-4-6", 0.0)
            assert result == {"v": 2}
            assert cache.stats()["entry_count"] == 1
        finally:
            cache.close()
```

### Step 1.6: Run tests to verify they pass

- [ ] Run: `pytest tests/unit/backtest/test_llm_response_cache.py -v`
- Expected: 7 passed (2 from Init + 5 from PutGet).

### Step 1.7: Run linter

- [ ] Run: `ruff check app/modules/backtest/llm_response_cache.py tests/unit/backtest/test_llm_response_cache.py`
- Expected: `All checks passed!`

### Step 1.8: Commit

- [ ] Run:

```bash
git add app/modules/backtest/llm_response_cache.py tests/unit/backtest/test_llm_response_cache.py
git commit -m "Add LLMResponseCache for reproducible LLM-mode backtests

$(cat <<'EOF'
SQLite-backed persistent cache keyed on hash(system_prompt + user_prompt +
model + temperature). Used by Phase 1 of the LLM-mode backtest attribution
work (see plans/architecture/LLM-BACKTEST-ATTRIBUTION.md §6.1).

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add `skills_dir` parameter to `compose_system_prompt`

**Files:**
- Modify: `app/modules/equities/agents/skills/loader.py`
- Modify: `tests/unit/equities/test_skill_loader.py`

### Step 2.1: Write the failing test

- [ ] **Append to `tests/unit/equities/test_skill_loader.py`** (after the existing classes):

```python
# ---------------------------------------------------------------------------
# skills_dir parameter tests
# ---------------------------------------------------------------------------


class TestSkillsDirParameter:
    """compose_system_prompt must accept an optional skills_dir override so
    backtests can load alternate skill bundles without mutating the module-level
    _SKILLS_DIR constant."""

    def test_default_none_uses_package_skills(self):
        """Passing skills_dir=None behaves identically to omitting it."""
        loader._load_output_format.cache_clear()
        compose_system_prompt.cache_clear()
        prompt_default = compose_system_prompt("fundamentals", "growth")
        prompt_explicit_none = compose_system_prompt("fundamentals", "growth", None, None)
        assert prompt_default == prompt_explicit_none

    def test_alternate_skills_dir_loads_different_content(self, tmp_path):
        """Pointing skills_dir at a directory with different files produces
        a different composed prompt."""
        loader._load_output_format.cache_clear()
        compose_system_prompt.cache_clear()

        # Build a minimal alternate bundle
        (tmp_path / "base").mkdir()
        (tmp_path / "base" / "fundamentals.md").write_text(
            "# Alternate Fundamentals Skill\n\nAlternate instructions."
        )
        (tmp_path / "output_format.md").write_text(
            '## Output Format\n\nReturn JSON with "bullish_score".'
        )

        prompt = compose_system_prompt("fundamentals", "", None, tmp_path)
        assert "Alternate Fundamentals Skill" in prompt
        assert "Alternate instructions" in prompt
        # Output format layer still appended
        assert "bullish_score" in prompt

    def test_different_skills_dirs_cached_separately(self, tmp_path):
        """The lru_cache key must include skills_dir so two bundles don't collide."""
        loader._load_output_format.cache_clear()
        compose_system_prompt.cache_clear()

        # Build two minimal alternate bundles
        dir_a = tmp_path / "bundle_a"
        dir_b = tmp_path / "bundle_b"
        for d, label in ((dir_a, "A"), (dir_b, "B")):
            (d / "base").mkdir(parents=True)
            (d / "base" / "fundamentals.md").write_text(f"# Bundle {label}")
            (d / "output_format.md").write_text("## Output Format\n\nReturn JSON with bullish_score.")

        prompt_a = compose_system_prompt("fundamentals", "", None, dir_a)
        prompt_b = compose_system_prompt("fundamentals", "", None, dir_b)
        assert "Bundle A" in prompt_a
        assert "Bundle B" in prompt_b
        assert prompt_a != prompt_b
```

### Step 2.2: Run tests to verify failure

- [ ] Run: `pytest tests/unit/equities/test_skill_loader.py::TestSkillsDirParameter -v`
- Expected: `TypeError: compose_system_prompt() takes from 1 to 3 positional arguments but 4 were given`

### Step 2.3: Modify `loader.py` to accept `skills_dir`

- [ ] **Edit `app/modules/equities/agents/skills/loader.py`** — replace `compose_system_prompt` and `_load_output_format` with:

```python
@lru_cache(maxsize=1)
def _load_output_format(skills_dir_str: str = "") -> str:
    """Load the shared output format layer.

    Takes a string path argument (not Path) so lru_cache can hash it. Empty
    string means "use the package default _SKILLS_DIR".
    """
    root = Path(skills_dir_str) if skills_dir_str else _SKILLS_DIR
    path = root / "output_format.md"
    content = _read_skill(path)
    if content is None:
        raise MissingSkillError(
            f"Required output format skill not found at {path}"
        )
    return content


@lru_cache(maxsize=64)
def compose_system_prompt(
    analyst_type: str,
    branch_name: str = "",
    sector: str | None = None,
    skills_dir: Path | None = None,
) -> str:
    """Compose system prompt by layering: base + branch + sector + output format.

    Args:
        analyst_type: "fundamentals" | "technical" | "news"
        branch_name: "growth" | "value" | "test_growth" | "" etc.
        sector: GICS sector from UniverseStock (optional, for future sector overlays)
        skills_dir: Override the package-default skills directory. When None,
            uses the live app/modules/equities/agents/skills/ files. When set,
            all layer files are read from this alternate directory — used by
            backtests to load alternate skill bundles.

    Returns:
        Composed system prompt string with all applicable layers joined by --- separators.
    """
    root = skills_dir if skills_dir is not None else _SKILLS_DIR
    layers: list[str] = []

    # 1. Base skill (required)
    base = _read_skill(root / "base" / f"{analyst_type}.md")
    if base:
        layers.append(base)
    else:
        logger.warning("Missing base skill for analyst_type=%s", analyst_type)

    # 2. Branch overlay (optional)
    if branch_name:
        branch_key = _normalize_branch(branch_name)
        branch_skill = _read_skill(
            root / "branches" / branch_key / f"{analyst_type}.md"
        )
        if branch_skill:
            layers.append(branch_skill)

    # 3. Sector overlay (optional, Phase 2)
    if sector:
        sector_key = _normalize_sector(sector)
        sector_skill = _read_skill(root / "sectors" / f"{sector_key}.md")
        if sector_skill:
            layers.append(sector_skill)

    # 4. Output format (always last, required)
    layers.append(_load_output_format(str(skills_dir) if skills_dir is not None else ""))

    return _SEPARATOR.join(layers)
```

### Step 2.4: Update the existing `test_missing_output_format_raises_missing_skill_error` test

The existing test monkeypatches `loader._SKILLS_DIR` and calls `_load_output_format()` with no args. Since `_load_output_format` now takes a string argument, the existing test still works (calling with no args gives `""`), but the cache key has changed. Update the test to pass an explicit empty string:

- [ ] **Edit `tests/unit/equities/test_skill_loader.py`** — replace the body of `test_missing_output_format_raises_missing_skill_error` with:

```python
    def test_missing_output_format_raises_missing_skill_error(self, monkeypatch, tmp_path):
        """If output_format.md is missing, _load_output_format raises MissingSkillError."""
        loader._load_output_format.cache_clear()
        monkeypatch.setattr(loader, "_SKILLS_DIR", tmp_path)
        with pytest.raises(MissingSkillError, match="output format"):
            loader._load_output_format("")
        # Restore the real cache for downstream tests
        loader._load_output_format.cache_clear()
```

Also update `test_load_output_format_returns_non_empty_content` to match the new signature:

```python
    def test_load_output_format_returns_non_empty_content(self):
        content = loader._load_output_format("")
        assert content
        assert "bullish_score" in content
        assert "confidence" in content
        assert "summary" in content
```

And `test_output_format_contains_pre_response_checklist`:

```python
    def test_output_format_contains_pre_response_checklist(self):
        content = loader._load_output_format("")
        assert "Before You Respond" in content
```

### Step 2.5: Run all skill loader tests

- [ ] Run: `pytest tests/unit/equities/test_skill_loader.py -v`
- Expected: all tests pass (existing 27 + new 3 from TestSkillsDirParameter = 30).

### Step 2.6: Run linter

- [ ] Run: `ruff check app/modules/equities/agents/skills/loader.py tests/unit/equities/test_skill_loader.py`
- Expected: `All checks passed!`

### Step 2.7: Commit

- [ ] Run:

```bash
git add app/modules/equities/agents/skills/loader.py tests/unit/equities/test_skill_loader.py
git commit -m "Add skills_dir parameter to compose_system_prompt

$(cat <<'EOF'
Supports loading alternate skill bundles (e.g., data/skill_bundles/<name>/)
without mutating the module-level _SKILLS_DIR. Required for Phase 1 of the
LLM-mode backtest attribution work — see plans/architecture/LLM-BACKTEST-ATTRIBUTION.md §6.3.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Add `skills_dir` to analyst constructors

**Files:**
- Modify: `app/modules/equities/agents/fundamentals_analyst.py`
- Modify: `app/modules/equities/agents/news_analyst.py`
- Modify: `app/modules/equities/agents/technical_analyst.py`
- Modify: `tests/unit/equities/test_fundamentals_analyst.py`
- Modify: `tests/unit/equities/test_news_analyst.py`
- Modify: `tests/unit/equities/test_technical_analyst.py`

### Step 3.1: Write the failing test for FundamentalsAnalyst

- [ ] **Append to `tests/unit/equities/test_fundamentals_analyst.py`**:

```python
# ---------------------------------------------------------------------------
# skills_dir parameter tests
# ---------------------------------------------------------------------------


class TestFundamentalsAnalystSkillsDir:
    def test_stores_skills_dir_on_init(self, tmp_path):
        from pathlib import Path
        from app.modules.equities.agents.fundamentals_analyst import FundamentalsAnalyst
        from app.modules.equities.config import AnalystLLMConfig

        analyst = FundamentalsAnalyst(
            config=AnalystLLMConfig(),
            skills_dir=tmp_path,
        )
        assert analyst.skills_dir == tmp_path

    def test_default_skills_dir_is_none(self):
        from app.modules.equities.agents.fundamentals_analyst import FundamentalsAnalyst
        from app.modules.equities.config import AnalystLLMConfig

        analyst = FundamentalsAnalyst(config=AnalystLLMConfig())
        assert analyst.skills_dir is None
```

### Step 3.2: Run the failing test

- [ ] Run: `pytest tests/unit/equities/test_fundamentals_analyst.py::TestFundamentalsAnalystSkillsDir -v`
- Expected: `TypeError: __init__() got an unexpected keyword argument 'skills_dir'`

### Step 3.3: Modify `FundamentalsAnalyst.__init__`

- [ ] **Edit `app/modules/equities/agents/fundamentals_analyst.py`** — update `__init__` and `analyze`:

```python
    def __init__(
        self,
        config: AnalystLLMConfig,
        data_service=None,
        sec_edgar=None,
        llm_client=None,
        time_provider: TimeProvider | None = None,
        branch_name: str = "",
        skills_dir: Path | None = None,
    ) -> None:
        self.config = config
        self.data_service = data_service
        self.sec_edgar = sec_edgar
        self.llm_client = llm_client
        self._explicit_time_provider = time_provider is not None
        self.time_provider = time_provider or LiveTimeProvider()
        self.branch_name = branch_name
        self.skills_dir = skills_dir
```

And update the `analyze` method's `compose_system_prompt` call:

```python
        system_prompt = compose_system_prompt(
            self.ANALYST_TYPE,
            self.branch_name,
            stock.sector,
            self.skills_dir,
        )
```

Add the `Path` import at the top:

```python
from pathlib import Path
```

### Step 3.4: Run the test to verify it passes

- [ ] Run: `pytest tests/unit/equities/test_fundamentals_analyst.py::TestFundamentalsAnalystSkillsDir -v`
- Expected: 2 passed.

### Step 3.5: Repeat for NewsAnalyst

- [ ] **Append to `tests/unit/equities/test_news_analyst.py`**:

```python
class TestNewsAnalystSkillsDir:
    def test_stores_skills_dir_on_init(self, tmp_path):
        from app.modules.equities.agents.news_analyst import NewsAnalyst
        from app.modules.equities.config import AnalystLLMConfig

        analyst = NewsAnalyst(
            config=AnalystLLMConfig(),
            skills_dir=tmp_path,
        )
        assert analyst.skills_dir == tmp_path

    def test_default_skills_dir_is_none(self):
        from app.modules.equities.agents.news_analyst import NewsAnalyst
        from app.modules.equities.config import AnalystLLMConfig

        analyst = NewsAnalyst(config=AnalystLLMConfig())
        assert analyst.skills_dir is None
```

- [ ] **Edit `app/modules/equities/agents/news_analyst.py`** — add `skills_dir: Path | None = None` to `__init__`, store as `self.skills_dir`, and pass as 4th positional to `compose_system_prompt` in `analyze`. Add `from pathlib import Path` at top.

- [ ] Run: `pytest tests/unit/equities/test_news_analyst.py::TestNewsAnalystSkillsDir -v`
- Expected: 2 passed.

### Step 3.6: Repeat for TechnicalAnalyst

- [ ] **Append to `tests/unit/equities/test_technical_analyst.py`**:

```python
class TestTechnicalAnalystSkillsDir:
    def test_stores_skills_dir_on_init(self, tmp_path):
        from app.modules.equities.agents.technical_analyst import TechnicalAnalyst
        from app.modules.equities.config import AnalystLLMConfig

        analyst = TechnicalAnalyst(
            config=AnalystLLMConfig(),
            skills_dir=tmp_path,
        )
        assert analyst.skills_dir == tmp_path

    def test_default_skills_dir_is_none(self):
        from app.modules.equities.agents.technical_analyst import TechnicalAnalyst
        from app.modules.equities.config import AnalystLLMConfig

        analyst = TechnicalAnalyst(config=AnalystLLMConfig())
        assert analyst.skills_dir is None
```

- [ ] **Edit `app/modules/equities/agents/technical_analyst.py`** — same pattern as fundamentals_analyst and news_analyst. Add `skills_dir: Path | None = None` to `__init__`, store as `self.skills_dir`, pass as 4th positional to `compose_system_prompt` in `analyze`. Add `from pathlib import Path` at top.

- [ ] Run: `pytest tests/unit/equities/test_technical_analyst.py::TestTechnicalAnalystSkillsDir -v`
- Expected: 2 passed.

### Step 3.7: Run all analyst tests to ensure no regression

- [ ] Run: `pytest tests/unit/equities/test_fundamentals_analyst.py tests/unit/equities/test_news_analyst.py tests/unit/equities/test_technical_analyst.py -v`
- Expected: all existing tests still pass plus 6 new tests.

### Step 3.8: Run linter

- [ ] Run: `ruff check app/modules/equities/agents/fundamentals_analyst.py app/modules/equities/agents/news_analyst.py app/modules/equities/agents/technical_analyst.py tests/unit/equities/test_fundamentals_analyst.py tests/unit/equities/test_news_analyst.py tests/unit/equities/test_technical_analyst.py`
- Expected: `All checks passed!`

### Step 3.9: Commit

- [ ] Run:

```bash
git add app/modules/equities/agents/fundamentals_analyst.py app/modules/equities/agents/news_analyst.py app/modules/equities/agents/technical_analyst.py tests/unit/equities/test_fundamentals_analyst.py tests/unit/equities/test_news_analyst.py tests/unit/equities/test_technical_analyst.py
git commit -m "Add skills_dir constructor arg to LLM analyst classes

$(cat <<'EOF'
Each of the three analyst classes (fundamentals, news, technical) now
accepts an optional skills_dir parameter and passes it to compose_system_prompt.
Enables loading alternate skill bundles per backtest run. See
plans/architecture/LLM-BACKTEST-ATTRIBUTION.md §6.4.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Wire `response_cache` into `AnthropicAnalystClient`

**Files:**
- Modify: `app/modules/equities/agents/llm_client.py`
- Modify: `tests/unit/equities/test_llm_client.py`

### Step 4.1: Write the failing test

- [ ] **Append to `tests/unit/equities/test_llm_client.py`**:

```python
# ---------------------------------------------------------------------------
# response_cache integration tests
# ---------------------------------------------------------------------------


class TestAnthropicAnalystClientResponseCache:
    @pytest.mark.asyncio
    async def test_cache_hit_skips_api_call(self, tmp_path):
        from unittest.mock import AsyncMock

        from app.modules.backtest.llm_response_cache import LLMResponseCache
        from app.modules.equities.agents.llm_client import AnthropicAnalystClient

        cache = LLMResponseCache(tmp_path / "cache.db")
        try:
            cache.put(
                system_prompt="sys",
                user_prompt="usr",
                model="claude-sonnet-4-6",
                temperature=0.0,
                response={"bullish_score": 9, "confidence": 8, "summary": "cached"},
            )
            client = AnthropicAnalystClient(
                model="claude-sonnet-4-6",
                temperature=0.0,
                response_cache=cache,
            )
            # Swap in a mock that would FAIL the test if called
            client._client = AsyncMock()
            client._client.messages.create.side_effect = AssertionError(
                "should not hit API on cache hit"
            )

            result = await client.invoke("usr", system_prompt="sys")
            assert result == {"bullish_score": 9, "confidence": 8, "summary": "cached"}
            assert cache.hits == 1
            assert cache.misses == 0
        finally:
            cache.close()

    @pytest.mark.asyncio
    async def test_cache_miss_calls_api_and_stores_result(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock

        from app.modules.backtest.llm_response_cache import LLMResponseCache
        from app.modules.equities.agents.llm_client import AnthropicAnalystClient

        cache = LLMResponseCache(tmp_path / "cache.db")
        try:
            client = AnthropicAnalystClient(
                model="claude-sonnet-4-6",
                temperature=0.0,
                response_cache=cache,
            )
            # Mock the API response
            fake_response = MagicMock()
            fake_response.content = [MagicMock(text='{"bullish_score": 6, "confidence": 5, "summary": "mock"}')]
            client._client = AsyncMock()
            client._client.messages.create = AsyncMock(return_value=fake_response)

            result = await client.invoke("usr", system_prompt="sys")
            assert result == {"bullish_score": 6, "confidence": 5, "summary": "mock"}
            assert cache.misses == 1
            # Second call should hit cache, not API
            client._client.messages.create.reset_mock()
            client._client.messages.create.side_effect = AssertionError("should be cached now")
            result2 = await client.invoke("usr", system_prompt="sys")
            assert result2 == result
            assert cache.hits == 1
        finally:
            cache.close()

    @pytest.mark.asyncio
    async def test_no_cache_when_response_cache_is_none(self):
        from unittest.mock import AsyncMock, MagicMock

        from app.modules.equities.agents.llm_client import AnthropicAnalystClient

        client = AnthropicAnalystClient(
            model="claude-sonnet-4-6",
            temperature=0.0,
            response_cache=None,
        )
        fake_response = MagicMock()
        fake_response.content = [MagicMock(text='{"bullish_score": 5, "confidence": 5, "summary": "no cache"}')]
        client._client = AsyncMock()
        client._client.messages.create = AsyncMock(return_value=fake_response)

        result = await client.invoke("usr", system_prompt="sys")
        assert result["bullish_score"] == 5
        # Second call should hit API again (no cache)
        result2 = await client.invoke("usr", system_prompt="sys")
        assert result2["bullish_score"] == 5
        assert client._client.messages.create.call_count == 2
```

### Step 4.2: Run the failing test

- [ ] Run: `pytest tests/unit/equities/test_llm_client.py::TestAnthropicAnalystClientResponseCache -v`
- Expected: `TypeError: __init__() got an unexpected keyword argument 'response_cache'`

### Step 4.3: Modify `AnthropicAnalystClient`

- [ ] **Edit `app/modules/equities/agents/llm_client.py`** — update `__init__` and `invoke`:

```python
    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        temperature: float = 0.3,
        response_cache: "LLMResponseCache | None" = None,
    ):
        # ... existing init body ...
        self.model = model
        self.temperature = temperature
        self._response_cache = response_cache
        # ... rest of existing init unchanged ...
```

And update the `invoke` method — add a cache check at the start and a cache write before returning:

```python
    async def invoke(self, prompt: str, *, system_prompt: str | None = None) -> dict:
        # ... existing docstring ...
        if self._client is None:
            raise RuntimeError("ANTHROPIC_API_KEY not set — cannot invoke LLM analyst")

        # Cache lookup: only when both a cache and a system_prompt are present
        if self._response_cache is not None and system_prompt is not None:
            cached = self._response_cache.get(
                system_prompt, prompt, self.model, self.temperature
            )
            if cached is not None:
                return cached

        # ... existing API call logic unchanged (system = [...], response = await self._client.messages.create(...), text parsing, JSON parsing) ...

        # After `parsed` is computed, store in cache
        if self._response_cache is not None and system_prompt is not None:
            self._response_cache.put(
                system_prompt, prompt, self.model, self.temperature, parsed
            )
        return parsed
```

Add the `TYPE_CHECKING` import at the top so the forward-ref type annotation works cleanly:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.backtest.llm_response_cache import LLMResponseCache
```

### Step 4.4: Run the tests to verify they pass

- [ ] Run: `pytest tests/unit/equities/test_llm_client.py::TestAnthropicAnalystClientResponseCache -v`
- Expected: 3 passed.

### Step 4.5: Run linter

- [ ] Run: `ruff check app/modules/equities/agents/llm_client.py tests/unit/equities/test_llm_client.py`
- Expected: `All checks passed!`

### Step 4.6: Commit

- [ ] Run:

```bash
git add app/modules/equities/agents/llm_client.py tests/unit/equities/test_llm_client.py
git commit -m "Wire persistent response cache into AnthropicAnalystClient

$(cat <<'EOF'
invoke() now checks the optional response_cache before hitting the API and
stores the result on a miss. Cache key is (system_prompt + user_prompt + model
+ temperature). See plans/architecture/LLM-BACKTEST-ATTRIBUTION.md §6.2.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Create `result_store.py` — hash + models

**Files:**
- Create: `app/modules/backtest/result_store.py`
- Create: `tests/unit/backtest/test_result_store.py`

### Step 5.1: Write the failing test for `hash_skill_bundle`

- [ ] **Create `tests/unit/backtest/test_result_store.py`** with:

```python
"""Unit tests for result_store — backtest run persistence and prompt fingerprinting."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from app.modules.backtest.result_store import hash_skill_bundle


class TestHashSkillBundle:
    def test_same_content_produces_same_hash(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        for d in (dir_a, dir_b):
            (d / "base").mkdir(parents=True)
            (d / "base" / "fundamentals.md").write_text("# Fundamentals")
            (d / "base" / "news.md").write_text("# News")
            (d / "output_format.md").write_text("## Output")
        assert hash_skill_bundle(dir_a) == hash_skill_bundle(dir_b)

    def test_different_content_produces_different_hash(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        for d in (dir_a, dir_b):
            (d / "base").mkdir(parents=True)
            (d / "output_format.md").write_text("## Output")
        (dir_a / "base" / "fundamentals.md").write_text("# Version A")
        (dir_b / "base" / "fundamentals.md").write_text("# Version B")
        assert hash_skill_bundle(dir_a) != hash_skill_bundle(dir_b)

    def test_ignores_pycache(self, tmp_path):
        """__pycache__ directories should be skipped — they're non-deterministic
        compiled artifacts that shouldn't affect the content hash."""
        dir_base = tmp_path / "bundle"
        (dir_base / "base").mkdir(parents=True)
        (dir_base / "base" / "fundamentals.md").write_text("# content")
        (dir_base / "output_format.md").write_text("## out")
        hash_before = hash_skill_bundle(dir_base)

        # Add a __pycache__ dir with files
        (dir_base / "__pycache__").mkdir()
        (dir_base / "__pycache__" / "something.pyc").write_bytes(b"compiled")
        hash_after = hash_skill_bundle(dir_base)

        assert hash_before == hash_after

    def test_hash_is_stable_hex_string(self, tmp_path):
        (tmp_path / "base").mkdir()
        (tmp_path / "base" / "f.md").write_text("x")
        (tmp_path / "output_format.md").write_text("y")
        h = hash_skill_bundle(tmp_path)
        assert isinstance(h, str)
        assert len(h) == 64  # full sha256 hex
        int(h, 16)  # raises if not valid hex
```

### Step 5.2: Run the failing test

- [ ] Run: `pytest tests/unit/backtest/test_result_store.py -v`
- Expected: `ModuleNotFoundError: No module named 'app.modules.backtest.result_store'`

### Step 5.3: Create `result_store.py` with `hash_skill_bundle`

- [ ] **Create `app/modules/backtest/result_store.py`** with:

```python
"""Backtest run persistence and prompt fingerprinting.

Saves each backtest run as a JSON file in data/backtest_runs/<id>.json. Provides
hash_skill_bundle to produce a deterministic fingerprint of a skill directory
(used to identify which prompt version produced a given run).
"""
from __future__ import annotations

import hashlib
import json
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
    """
    hasher = hashlib.sha256()
    root = Path(skills_dir).resolve()
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
```

### Step 5.4: Run the tests

- [ ] Run: `pytest tests/unit/backtest/test_result_store.py::TestHashSkillBundle -v`
- Expected: 4 passed.

### Step 5.5: Commit

- [ ] Run:

```bash
git add app/modules/backtest/result_store.py tests/unit/backtest/test_result_store.py
git commit -m "Add result_store module with hash_skill_bundle helper

$(cat <<'EOF'
Deterministic sha256 fingerprint of a skill directory's files, skipping
__pycache__. Used to tag backtest runs with the exact prompt version they
used. See plans/architecture/LLM-BACKTEST-ATTRIBUTION.md §6.7.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Add `StockSignalRecord` and `BacktestRun` models

**Files:**
- Modify: `app/modules/backtest/result_store.py`
- Modify: `tests/unit/backtest/test_result_store.py`

### Step 6.1: Write the failing test

- [ ] **Append to `tests/unit/backtest/test_result_store.py`**:

```python
from app.modules.backtest.result_store import BacktestRun, StockSignalRecord
from app.modules.backtest.config import BacktestConfig
from app.modules.backtest.models import PerformanceMetrics


class TestBacktestRunModel:
    def _make_metrics(self) -> PerformanceMetrics:
        return PerformanceMetrics(
            total_return=0.1, annualized_return=0.1, volatility=0.15, sharpe_ratio=0.67,
            sortino_ratio=1.0, calmar_ratio=0.5, max_drawdown=-0.05, max_drawdown_duration_days=10,
            value_at_risk_95=-0.02, conditional_var_95=-0.03, ulcer_index=0.02, total_trades=5,
            win_rate=0.6, profit_factor=1.5, avg_win=100.0, avg_loss=-50.0, turnover_rate=0.5,
            avg_position_count=10.0, max_position_count=12, avg_long_exposure=0.95,
        )

    def test_backtest_run_round_trips_through_json(self):
        run = BacktestRun(
            run_id="2026-04-07T12-00-00_abc123_medium",
            timestamp=datetime(2026, 4, 7, 12, 0, 0),
            git_sha="deadbeef",
            config=BacktestConfig(start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)),
            skill_bundle_name=None,
            skill_bundle_hash="a" * 64,
            metrics=self._make_metrics(),
            benchmarks=[],
            snapshots=[],
            trades=[],
            signals=[
                StockSignalRecord(
                    date=date(2025, 6, 15),
                    symbol="AAPL",
                    analyst_type="fundamentals",
                    bullish_score=7,
                    confidence=8,
                    summary="strong",
                )
            ],
            llm_cache_hits=42,
            llm_cache_misses=8,
        )
        json_str = run.model_dump_json()
        rehydrated = BacktestRun.model_validate_json(json_str)
        assert rehydrated.run_id == run.run_id
        assert rehydrated.skill_bundle_hash == "a" * 64
        assert len(rehydrated.signals) == 1
        assert rehydrated.signals[0].symbol == "AAPL"
        assert rehydrated.llm_cache_hits == 42
```

### Step 6.2: Run the failing test

- [ ] Run: `pytest tests/unit/backtest/test_result_store.py::TestBacktestRunModel -v`
- Expected: `ImportError: cannot import name 'BacktestRun'` or `StockSignalRecord`.

### Step 6.3: Add the models to `result_store.py`

- [ ] **Edit `app/modules/backtest/result_store.py`** — add the models after the existing `hash_skill_bundle` function:

```python
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
```

### Step 6.4: Run the test

- [ ] Run: `pytest tests/unit/backtest/test_result_store.py::TestBacktestRunModel -v`
- Expected: 1 passed.

### Step 6.5: Commit

- [ ] Run:

```bash
git add app/modules/backtest/result_store.py tests/unit/backtest/test_result_store.py
git commit -m "Add BacktestRun and StockSignalRecord models

$(cat <<'EOF'
Pydantic models for persisted backtest runs. BacktestRun captures config,
prompt fingerprint, metrics, and per-signal data for drill-down (Phase 2).
See plans/architecture/LLM-BACKTEST-ATTRIBUTION.md §6.7.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Add save_run / load_run / list_runs

**Files:**
- Modify: `app/modules/backtest/result_store.py`
- Modify: `tests/unit/backtest/test_result_store.py`

### Step 7.1: Write the failing test

- [ ] **Append to `tests/unit/backtest/test_result_store.py`**:

```python
from app.modules.backtest.result_store import list_runs, load_run, save_run


class TestSaveLoadListRuns:
    def _make_run(self, run_id: str) -> BacktestRun:
        return BacktestRun(
            run_id=run_id,
            timestamp=datetime(2026, 4, 7, 12, 0, 0),
            git_sha="deadbeef",
            config=BacktestConfig(start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)),
            skill_bundle_name=None,
            skill_bundle_hash="a" * 64,
            metrics=TestBacktestRunModel()._make_metrics(),
        )

    def test_save_creates_json_file_and_returns_path(self, tmp_path):
        run = self._make_run("2026-04-07_aaa_medium")
        path = save_run(run, runs_dir=tmp_path)
        assert path.exists()
        assert path.parent == tmp_path
        assert path.name == "2026-04-07_aaa_medium.json"

    def test_load_round_trips(self, tmp_path):
        run = self._make_run("2026-04-07_bbb_quick")
        save_run(run, runs_dir=tmp_path)
        loaded = load_run("2026-04-07_bbb_quick", runs_dir=tmp_path)
        assert loaded.run_id == "2026-04-07_bbb_quick"
        assert loaded.skill_bundle_hash == "a" * 64

    def test_load_missing_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="no_such_run"):
            load_run("no_such_run", runs_dir=tmp_path)

    def test_list_runs_returns_all_saved_runs_sorted_by_timestamp_desc(self, tmp_path):
        from datetime import timedelta

        base_time = datetime(2026, 4, 7, 12, 0, 0)
        for i in range(3):
            run = self._make_run(f"run_{i}")
            run.timestamp = base_time + timedelta(hours=i)
            save_run(run, runs_dir=tmp_path)

        entries = list_runs(runs_dir=tmp_path)
        assert len(entries) == 3
        # Most recent first
        assert entries[0]["run_id"] == "run_2"
        assert entries[2]["run_id"] == "run_0"

    def test_list_runs_empty_dir_returns_empty_list(self, tmp_path):
        entries = list_runs(runs_dir=tmp_path)
        assert entries == []
```

### Step 7.2: Run the failing test

- [ ] Run: `pytest tests/unit/backtest/test_result_store.py::TestSaveLoadListRuns -v`
- Expected: `ImportError: cannot import name 'save_run'` (and friends).

### Step 7.3: Add save/load/list functions to `result_store.py`

- [ ] **Append to `app/modules/backtest/result_store.py`**:

```python
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
```

### Step 7.4: Run the tests

- [ ] Run: `pytest tests/unit/backtest/test_result_store.py -v`
- Expected: all 10 tests pass (4 hash + 1 model + 5 save/load/list).

### Step 7.5: Run linter

- [ ] Run: `ruff check app/modules/backtest/result_store.py tests/unit/backtest/test_result_store.py`
- Expected: `All checks passed!`

### Step 7.6: Commit

- [ ] Run:

```bash
git add app/modules/backtest/result_store.py tests/unit/backtest/test_result_store.py
git commit -m "Add save_run / load_run / list_runs to result_store

$(cat <<'EOF'
JSON-file-per-run persistence under data/backtest_runs/. list_runs returns
summary dicts sorted by timestamp descending. See
plans/architecture/LLM-BACKTEST-ATTRIBUTION.md §6.7.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Create `scripts/bundle_skills.py`

**Files:**
- Create: `scripts/bundle_skills.py`
- Create: `tests/unit/backtest/test_bundle_skills.py`

### Step 8.1: Write the failing test

- [ ] **Create `tests/unit/backtest/test_bundle_skills.py`** with:

```python
"""Tests for the bundle_skills script — snapshots the live skills directory."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import bundle_skills


class TestBundleSkills:
    def test_copies_skill_files_to_named_bundle(self, tmp_path, monkeypatch):
        # Create a fake live skills directory
        source = tmp_path / "source_skills"
        (source / "base").mkdir(parents=True)
        (source / "base" / "fundamentals.md").write_text("# Fund")
        (source / "output_format.md").write_text("## Output")
        bundles_dir = tmp_path / "bundles"

        result_path = bundle_skills.create_bundle(
            name="baseline_v1",
            source_dir=source,
            bundles_dir=bundles_dir,
        )

        assert result_path == bundles_dir / "baseline_v1"
        assert (result_path / "base" / "fundamentals.md").read_text() == "# Fund"
        assert (result_path / "output_format.md").read_text() == "## Output"

    def test_refuses_overwrite_without_force(self, tmp_path):
        source = tmp_path / "source_skills"
        source.mkdir()
        (source / "x.md").write_text("x")
        bundles_dir = tmp_path / "bundles"

        bundle_skills.create_bundle("a", source, bundles_dir)
        with pytest.raises(FileExistsError, match="already exists"):
            bundle_skills.create_bundle("a", source, bundles_dir)

    def test_force_overwrites_existing_bundle(self, tmp_path):
        source = tmp_path / "source_skills"
        source.mkdir()
        (source / "x.md").write_text("first")
        bundles_dir = tmp_path / "bundles"

        bundle_skills.create_bundle("a", source, bundles_dir)
        (source / "x.md").write_text("second")
        bundle_skills.create_bundle("a", source, bundles_dir, force=True)

        assert (bundles_dir / "a" / "x.md").read_text() == "second"

    def test_writes_bundle_meta_json(self, tmp_path):
        source = tmp_path / "source_skills"
        source.mkdir()
        (source / "x.md").write_text("x")
        bundles_dir = tmp_path / "bundles"

        bundle_skills.create_bundle("a", source, bundles_dir)
        meta_path = bundles_dir / "a" / ".bundle_meta.json"
        assert meta_path.is_file()
        meta = json.loads(meta_path.read_text())
        assert "created_at" in meta
        assert "source_dir" in meta

    def test_excludes_pycache(self, tmp_path):
        source = tmp_path / "source_skills"
        (source / "base").mkdir(parents=True)
        (source / "base" / "fundamentals.md").write_text("# Fund")
        (source / "__pycache__").mkdir()
        (source / "__pycache__" / "x.pyc").write_bytes(b"compiled")
        bundles_dir = tmp_path / "bundles"

        bundle_skills.create_bundle("a", source, bundles_dir)
        assert not (bundles_dir / "a" / "__pycache__").exists()
```

### Step 8.2: Run the failing test

- [ ] Run: `pytest tests/unit/backtest/test_bundle_skills.py -v`
- Expected: `ModuleNotFoundError: No module named 'scripts.bundle_skills'`

### Step 8.3: Create the script

- [ ] **Create `scripts/bundle_skills.py`** with:

```python
"""Snapshot the live analyst skills directory as a named bundle.

Usage:
    python -m scripts.bundle_skills <name> [--force]

Copies app/modules/equities/agents/skills/ (excluding __pycache__) to
data/skill_bundles/<name>/. A .bundle_meta.json file is written alongside
with creation timestamp and source directory for provenance.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_DEFAULT_SOURCE = Path("app/modules/equities/agents/skills")
_DEFAULT_BUNDLES_DIR = Path("data/skill_bundles")
_EXCLUDED_DIRS = {"__pycache__"}


def _current_git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def create_bundle(
    name: str,
    source_dir: Path = _DEFAULT_SOURCE,
    bundles_dir: Path = _DEFAULT_BUNDLES_DIR,
    force: bool = False,
) -> Path:
    """Copy source_dir to bundles_dir/name/, skipping excluded directories.

    Returns the path of the created bundle. Raises FileExistsError if the
    bundle already exists and force is False.
    """
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Source skills directory not found: {source_dir}")

    target = bundles_dir / name
    if target.exists():
        if not force:
            raise FileExistsError(
                f"Bundle already exists at {target}. Pass force=True to overwrite."
            )
        shutil.rmtree(target)

    bundles_dir.mkdir(parents=True, exist_ok=True)

    def _ignore(dir_path: str, contents: list[str]) -> list[str]:
        return [c for c in contents if c in _EXCLUDED_DIRS]

    shutil.copytree(source_dir, target, ignore=_ignore)

    # Write metadata
    meta = {
        "name": name,
        "created_at": datetime.utcnow().isoformat(),
        "source_dir": str(source_dir.resolve()),
        "git_sha": _current_git_sha(),
    }
    (target / ".bundle_meta.json").write_text(json.dumps(meta, indent=2))

    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot live skills as a named bundle")
    parser.add_argument("name", help="Bundle name (e.g., baseline_v1)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing bundle")
    args = parser.parse_args()

    try:
        path = create_bundle(args.name, force=args.force)
    except FileExistsError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    print(f"Created bundle: {path}")


if __name__ == "__main__":
    main()
```

### Step 8.4: Run the tests

- [ ] Run: `pytest tests/unit/backtest/test_bundle_skills.py -v`
- Expected: 5 passed.

### Step 8.5: Run linter

- [ ] Run: `ruff check scripts/bundle_skills.py tests/unit/backtest/test_bundle_skills.py`
- Expected: `All checks passed!`

### Step 8.6: Commit

- [ ] Run:

```bash
git add scripts/bundle_skills.py tests/unit/backtest/test_bundle_skills.py
git commit -m "Add bundle_skills script to snapshot live skills as named bundles

$(cat <<'EOF'
Copies app/modules/equities/agents/skills/ to data/skill_bundles/<name>/
(excluding __pycache__). Writes .bundle_meta.json with timestamp and git SHA.
Enables parallel prompt-version comparison without git checkouts. See
plans/architecture/LLM-BACKTEST-ATTRIBUTION.md §6.8.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Extend `BacktestConfig` with new fields

**Files:**
- Modify: `app/modules/backtest/config.py`
- Modify: `tests/unit/backtest/test_config.py`

### Step 9.1: Write the failing test

- [ ] **Append to `tests/unit/backtest/test_config.py`**:

```python
class TestLLMModeConfigFields:
    def test_defaults(self):
        from datetime import date
        from pathlib import Path
        from app.modules.backtest.config import BacktestConfig

        cfg = BacktestConfig(start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))
        assert cfg.skills_bundle is None
        assert cfg.use_llm_response_cache is True
        assert cfg.llm_response_cache_path == Path("data/llm_response_cache.db")

    def test_override_skills_bundle(self):
        from datetime import date
        from app.modules.backtest.config import BacktestConfig

        cfg = BacktestConfig(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            skills_bundle="baseline_v1",
        )
        assert cfg.skills_bundle == "baseline_v1"

    def test_disable_response_cache(self):
        from datetime import date
        from app.modules.backtest.config import BacktestConfig

        cfg = BacktestConfig(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            use_llm_response_cache=False,
        )
        assert cfg.use_llm_response_cache is False
```

### Step 9.2: Run the failing test

- [ ] Run: `pytest tests/unit/backtest/test_config.py::TestLLMModeConfigFields -v`
- Expected: `AttributeError: 'BacktestConfig' object has no attribute 'skills_bundle'`

### Step 9.3: Add the fields

- [ ] **Edit `app/modules/backtest/config.py`** — add `from pathlib import Path` at the top and append these fields to the existing `BacktestConfig` class:

```python
    skills_bundle: str | None = None
    use_llm_response_cache: bool = True
    llm_response_cache_path: Path = Path("data/llm_response_cache.db")
```

### Step 9.4: Run the tests

- [ ] Run: `pytest tests/unit/backtest/test_config.py -v`
- Expected: all existing tests pass + 3 new tests pass.

### Step 9.5: Commit

- [ ] Run:

```bash
git add app/modules/backtest/config.py tests/unit/backtest/test_config.py
git commit -m "Add skills_bundle and LLM cache fields to BacktestConfig

$(cat <<'EOF'
skills_bundle: optional bundle name to load from data/skill_bundles/.
use_llm_response_cache / llm_response_cache_path: drive the persistent cache.
See plans/architecture/LLM-BACKTEST-ATTRIBUTION.md §6.5.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Extend `BacktestResult` with signals and cache stats

**Files:**
- Modify: `app/modules/backtest/models.py`
- Modify: `tests/unit/backtest/test_models.py`

### Step 10.1: Write the failing test

- [ ] **Append to `tests/unit/backtest/test_models.py`**:

```python
class TestBacktestResultLLMFields:
    def test_signals_defaults_to_empty_list(self):
        from app.modules.backtest.models import BacktestResult

        result = BacktestResult(
            backtest_id="test",
            status="completed",
            config={},
            metrics=None,
            snapshots=[],
            trades=[],
            rebalance_count=0,
            duration_seconds=1.0,
        )
        assert result.signals == []
        assert result.llm_cache_hits == 0
        assert result.llm_cache_misses == 0

    def test_populated_signals_round_trip(self):
        from datetime import date
        from app.modules.backtest.models import BacktestResult
        from app.modules.backtest.result_store import StockSignalRecord

        signal = StockSignalRecord(
            date=date(2025, 6, 15),
            symbol="AAPL",
            analyst_type="fundamentals",
            bullish_score=7,
            confidence=8,
            summary="good",
        )
        result = BacktestResult(
            backtest_id="test",
            status="completed",
            config={},
            metrics=None,
            snapshots=[],
            trades=[],
            rebalance_count=0,
            duration_seconds=1.0,
            signals=[signal],
            llm_cache_hits=5,
            llm_cache_misses=3,
        )
        assert len(result.signals) == 1
        assert result.signals[0].symbol == "AAPL"
        assert result.llm_cache_hits == 5
```

### Step 10.2: Run the failing test

- [ ] Run: `pytest tests/unit/backtest/test_models.py::TestBacktestResultLLMFields -v`
- Expected: `TypeError: ... unexpected keyword argument 'signals'` or `AttributeError`.

### Step 10.3: Add the fields to `BacktestResult`

- [ ] **Edit `app/modules/backtest/models.py`** — add these fields to the `BacktestResult` class:

```python
    signals: list["StockSignalRecord"] = []
    llm_cache_hits: int = 0
    llm_cache_misses: int = 0
```

And add a `TYPE_CHECKING` import with a forward reference so we don't create a circular import (result_store imports from models):

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.backtest.result_store import StockSignalRecord
```

At the bottom of `models.py`, after `BacktestResult` is defined, add:

```python
# Rebuild to resolve the forward reference once result_store is importable
def _rebuild_backtest_result() -> None:
    from app.modules.backtest.result_store import StockSignalRecord  # noqa: F401
    BacktestResult.model_rebuild()
```

And document that this must be called once at import time (we'll call it from `app/modules/backtest/__init__.py` to avoid circular imports).

- [ ] **Edit `app/modules/backtest/__init__.py`** — add at the bottom:

```python
from app.modules.backtest.models import _rebuild_backtest_result
_rebuild_backtest_result()
```

### Step 10.4: Run the tests

- [ ] Run: `pytest tests/unit/backtest/test_models.py -v`
- Expected: all existing + 2 new tests pass.

### Step 10.5: Run the broader backtest unit suite to catch any regression

- [ ] Run: `pytest tests/unit/backtest/ -q`
- Expected: all pass.

### Step 10.6: Commit

- [ ] Run:

```bash
git add app/modules/backtest/models.py app/modules/backtest/__init__.py tests/unit/backtest/test_models.py
git commit -m "Add signals and LLM cache stats fields to BacktestResult

$(cat <<'EOF'
BacktestResult now carries per-signal records and cache hit/miss counts so
the run_backtest CLI can persist them into BacktestRun via the result_store.
See plans/architecture/LLM-BACKTEST-ATTRIBUTION.md §6.7.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Extend `BacktestContext` for bundle resolution + cache instantiation

**Files:**
- Modify: `app/modules/backtest/context.py`
- Modify: `tests/unit/backtest/test_context.py`

### Step 11.1: Write the failing test

- [ ] **Append to `tests/unit/backtest/test_context.py`**:

```python
class TestBundleResolutionAndCache:
    def test_missing_skills_bundle_raises_value_error(self, tmp_path):
        """If config.skills_bundle is set but the directory doesn't exist, setup raises."""
        import asyncio
        from datetime import date
        from app.modules.backtest.config import BacktestConfig
        from app.modules.backtest.context import BacktestContext

        # Patch the bundles root to tmp_path so we test without touching real data/
        config = BacktestConfig(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            use_llm_agents=True,
            skills_bundle="nonexistent_bundle",
        )
        with pytest.raises(ValueError, match="Skill bundle not found"):
            asyncio.run(BacktestContext.resolve_skills_bundle(config, bundles_root=tmp_path))

    def test_none_skills_bundle_returns_none(self, tmp_path):
        import asyncio
        from datetime import date
        from app.modules.backtest.config import BacktestConfig
        from app.modules.backtest.context import BacktestContext

        config = BacktestConfig(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            use_llm_agents=True,
            skills_bundle=None,
        )
        path = asyncio.run(BacktestContext.resolve_skills_bundle(config, bundles_root=tmp_path))
        assert path is None

    def test_existing_skills_bundle_returns_path(self, tmp_path):
        import asyncio
        from datetime import date
        from app.modules.backtest.config import BacktestConfig
        from app.modules.backtest.context import BacktestContext

        bundle_dir = tmp_path / "my_bundle"
        (bundle_dir / "base").mkdir(parents=True)
        (bundle_dir / "base" / "fundamentals.md").write_text("# x")
        (bundle_dir / "output_format.md").write_text("## out")

        config = BacktestConfig(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            use_llm_agents=True,
            skills_bundle="my_bundle",
        )
        path = asyncio.run(BacktestContext.resolve_skills_bundle(config, bundles_root=tmp_path))
        assert path == bundle_dir
```

### Step 11.2: Run the failing test

- [ ] Run: `pytest tests/unit/backtest/test_context.py::TestBundleResolutionAndCache -v`
- Expected: `AttributeError: type object 'BacktestContext' has no attribute 'resolve_skills_bundle'`

### Step 11.3: Add `resolve_skills_bundle` to `BacktestContext`

- [ ] **Edit `app/modules/backtest/context.py`** — add the following static method to the `BacktestContext` class:

```python
    @staticmethod
    async def resolve_skills_bundle(
        config: BacktestConfig,
        bundles_root: Path = Path("data/skill_bundles"),
    ) -> Path | None:
        """Resolve config.skills_bundle to a concrete directory path.

        Returns None if skills_bundle is None or equal to "live".
        Raises ValueError if the bundle is set but the directory doesn't exist.
        """
        if config.skills_bundle is None or config.skills_bundle == "live":
            return None
        path = bundles_root / config.skills_bundle
        if not path.is_dir():
            raise ValueError(
                f"Skill bundle not found: {path}. "
                f"Run: python -m scripts.bundle_skills {config.skills_bundle}"
            )
        return path
```

Add `from pathlib import Path` at the top if not already imported.

### Step 11.4: Run the bundle resolution tests

- [ ] Run: `pytest tests/unit/backtest/test_context.py::TestBundleResolutionAndCache -v`
- Expected: 3 passed.

### Step 11.5: Wire `resolve_skills_bundle` and `LLMResponseCache` into the existing `use_llm_agents=True` branch

- [ ] **Edit `app/modules/backtest/context.py`** — in `BacktestContext.create()` (or wherever the `use_llm_agents=True` branch lives), modify the analyst instantiation section.

First, resolve the bundle and construct the cache **once** at the start of the branch:

```python
        if config.use_llm_agents:
            from app.modules.backtest.llm_response_cache import LLMResponseCache
            from app.modules.equities.agents.fundamentals_analyst import FundamentalsAnalyst
            from app.modules.equities.agents.llm_client import AnthropicAnalystClient
            from app.modules.equities.agents.news_analyst import NewsAnalyst
            from app.modules.equities.agents.technical_analyst import TechnicalAnalyst

            # Resolve the optional skill bundle (raises if misconfigured)
            skills_dir = await BacktestContext.resolve_skills_bundle(config)

            # Construct the response cache once if enabled; pass it to all three clients
            response_cache: LLMResponseCache | None = None
            if config.use_llm_response_cache:
                response_cache = LLMResponseCache(config.llm_response_cache_path)
                ctx.llm_response_cache = response_cache  # keep a reference for end-of-run stats
```

Then update each of the three `raw_*` analyst constructions to pass `response_cache` into the client and `skills_dir` into the analyst:

```python
            raw_news = NewsAnalyst(
                config=llm_cfg.news_analyst,
                data_service=data_service,
                llm_client=AnthropicAnalystClient(
                    model=llm_cfg.news_analyst.model,
                    temperature=llm_cfg.news_analyst.temperature,
                    response_cache=response_cache,
                ),
                skills_dir=skills_dir,
            )
            raw_fundamentals = FundamentalsAnalyst(
                config=llm_cfg.fundamentals_analyst,
                data_service=data_service,
                llm_client=AnthropicAnalystClient(
                    model=llm_cfg.fundamentals_analyst.model,
                    temperature=llm_cfg.fundamentals_analyst.temperature,
                    response_cache=response_cache,
                ),
                time_provider=tp,
                skills_dir=skills_dir,
            )
            raw_technical = TechnicalAnalyst(
                config=llm_cfg.technical_analyst,
                data_service=data_service,
                llm_client=AnthropicAnalystClient(
                    model=llm_cfg.technical_analyst.model,
                    temperature=llm_cfg.technical_analyst.temperature,
                    response_cache=response_cache,
                ),
                time_provider=tp,
                skills_dir=skills_dir,
            )
```

Also add `self.llm_response_cache: LLMResponseCache | None = None` to the `BacktestContext.__init__` alongside the existing `llm_cache`, `llm_counter`, `llm_wrappers` attributes, so the cache can be read at end-of-run.

### Step 11.6: Run the full context test file

- [ ] Run: `pytest tests/unit/backtest/test_context.py -v`
- Expected: all pass.

### Step 11.7: Run linter

- [ ] Run: `ruff check app/modules/backtest/context.py tests/unit/backtest/test_context.py`
- Expected: `All checks passed!`

### Step 11.8: Commit

- [ ] Run:

```bash
git add app/modules/backtest/context.py tests/unit/backtest/test_context.py
git commit -m "Resolve skill bundle and construct LLM cache in BacktestContext

$(cat <<'EOF'
BacktestContext now: (1) resolves config.skills_bundle to a concrete directory
(or None for live skills), raising ValueError if the bundle is missing; and
(2) constructs one LLMResponseCache per run and threads it into all three
AnthropicAnalystClient instances. See plans/architecture/LLM-BACKTEST-ATTRIBUTION.md §6.6.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Populate signals and cache stats in engine result

**Files:**
- Modify: `app/modules/backtest/engine.py`
- Modify: `tests/unit/backtest/test_engine.py`

### Step 12.1: Locate the end-of-run result construction

- [ ] Run: `grep -n "BacktestResult(" app/modules/backtest/engine.py`
- Note: record the line where `BacktestResult(...)` is constructed. It's the spot where `metrics`, `snapshots`, `trades`, `benchmarks` are passed in. This is where we'll add `signals`, `llm_cache_hits`, and `llm_cache_misses`.

### Step 12.2: Write the failing test

- [ ] **Append to `tests/unit/backtest/test_engine.py`**:

```python
class TestEngineLLMSignalCapture:
    @pytest.mark.asyncio
    async def test_signals_populated_from_ctx_llm_cache(self, monkeypatch):
        """When ctx.llm_cache has entries at end-of-run, they land in result.signals."""
        from datetime import date
        from unittest.mock import AsyncMock, MagicMock

        from app.modules.backtest.context import BacktestContext
        from app.modules.backtest.engine import BacktestEngine
        from app.modules.equities.models import StockSignal

        # Fake a minimal BacktestContext with a populated llm_cache
        fake_ctx = MagicMock(spec=BacktestContext)
        fake_ctx.llm_cache = {
            (date(2025, 6, 15), "AAPL", "fundamentals"): StockSignal(
                symbol="AAPL",
                analyst_type="fundamentals",
                bullish_score=7,
                confidence=8,
                summary="strong margin",
            ),
            (date(2025, 6, 15), "AAPL", "news"): StockSignal(
                symbol="AAPL",
                analyst_type="news",
                bullish_score=6,
                confidence=5,
                summary="mixed",
            ),
        }
        fake_cache = MagicMock()
        fake_cache.hits = 2
        fake_cache.misses = 0
        fake_ctx.llm_response_cache = fake_cache

        signals = BacktestEngine._collect_signals_from_context(fake_ctx)
        hits, misses = BacktestEngine._collect_cache_stats_from_context(fake_ctx)

        assert len(signals) == 2
        symbols = {s.symbol for s in signals}
        assert symbols == {"AAPL"}
        analyst_types = {s.analyst_type for s in signals}
        assert analyst_types == {"fundamentals", "news"}
        assert hits == 2
        assert misses == 0

    def test_no_llm_cache_returns_empty_signals(self):
        from app.modules.backtest.context import BacktestContext
        from app.modules.backtest.engine import BacktestEngine
        from unittest.mock import MagicMock

        fake_ctx = MagicMock(spec=BacktestContext)
        fake_ctx.llm_cache = {}
        fake_ctx.llm_response_cache = None

        signals = BacktestEngine._collect_signals_from_context(fake_ctx)
        hits, misses = BacktestEngine._collect_cache_stats_from_context(fake_ctx)

        assert signals == []
        assert hits == 0
        assert misses == 0
```

### Step 12.2: Run the failing test

- [ ] Run: `pytest tests/unit/backtest/test_engine.py::TestEngineLLMSignalCapture -v`
- Expected: `AttributeError: type object 'BacktestEngine' has no attribute '_collect_signals_from_context'`

### Step 12.3: Add the helper methods and wire them into the result

- [ ] **Edit `app/modules/backtest/engine.py`** — add two static helper methods to `BacktestEngine`:

```python
    @staticmethod
    def _collect_signals_from_context(ctx) -> list:
        """Convert ctx.llm_cache entries to StockSignalRecord instances.

        ctx.llm_cache is a dict[(date, symbol, analyst_type), StockSignal]
        populated by CachedAnalystWrapper during the run. Returns an empty
        list if the cache is empty or missing.
        """
        from app.modules.backtest.result_store import StockSignalRecord

        llm_cache = getattr(ctx, "llm_cache", None) or {}
        records: list[StockSignalRecord] = []
        for (sig_date, symbol, analyst_type), signal in llm_cache.items():
            records.append(
                StockSignalRecord(
                    date=sig_date,
                    symbol=symbol,
                    analyst_type=analyst_type,
                    bullish_score=signal.bullish_score,
                    confidence=signal.confidence,
                    summary=signal.summary,
                )
            )
        return records

    @staticmethod
    def _collect_cache_stats_from_context(ctx) -> tuple[int, int]:
        """Return (hits, misses) from ctx.llm_response_cache, or (0, 0) if absent."""
        cache = getattr(ctx, "llm_response_cache", None)
        if cache is None:
            return (0, 0)
        return (cache.hits, cache.misses)
```

Then find the `BacktestResult(...)` construction (from Step 12.1) and add the three new fields to it:

```python
        return BacktestResult(
            backtest_id=...,
            status=...,
            config=...,
            metrics=...,
            snapshots=...,
            trades=...,
            benchmarks=...,
            rebalance_count=...,
            duration_seconds=...,
            signals=BacktestEngine._collect_signals_from_context(ctx),
            llm_cache_hits=BacktestEngine._collect_cache_stats_from_context(ctx)[0],
            llm_cache_misses=BacktestEngine._collect_cache_stats_from_context(ctx)[1],
        )
```

### Step 12.4: Run the tests

- [ ] Run: `pytest tests/unit/backtest/test_engine.py::TestEngineLLMSignalCapture -v`
- Expected: 2 passed.

### Step 12.5: Run the broader engine suite

- [ ] Run: `pytest tests/unit/backtest/ -q`
- Expected: all pass.

### Step 12.6: Commit

- [ ] Run:

```bash
git add app/modules/backtest/engine.py tests/unit/backtest/test_engine.py
git commit -m "Populate BacktestResult.signals and cache stats at end-of-run

$(cat <<'EOF'
Engine extracts signals from ctx.llm_cache (populated by CachedAnalystWrapper)
and cache hit/miss counts from ctx.llm_response_cache. These become available
in BacktestResult for downstream save_run() in scripts/run_backtest.py. See
plans/architecture/LLM-BACKTEST-ATTRIBUTION.md §6.7.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Extend `scripts/run_backtest.py` with new CLI flags

**Files:**
- Modify: `scripts/run_backtest.py`
- Manual smoke test (no unit tests — CLI integration)

### Step 13.1: Add the new flags to the argument parser

- [ ] **Edit `scripts/run_backtest.py`** — after the existing `--value-top-n` argument definition, add:

```python
    parser.add_argument("--llm", action="store_true",
                        help="Enable LLM-mode analysts (default: quantitative)")
    parser.add_argument("--temperature", type=float, default=None,
                        help="Override analyst LLM temperature")
    parser.add_argument("--max-llm-calls-per-rebalance", type=int, default=None,
                        help="Override the per-rebalance LLM call cap")
    parser.add_argument("--no-llm-cache", action="store_true",
                        help="Disable the persistent LLM response cache")
    parser.add_argument("--skills-bundle", type=str, default=None,
                        help="Load skills from data/skill_bundles/<name> instead of the live directory")
    parser.add_argument("--save", action="store_true",
                        help="Persist the result to data/backtest_runs/")
```

### Step 13.2: Pass the flags through to `run_one`

- [ ] **Edit `scripts/run_backtest.py`** — update the `run_one` function signature and `BacktestConfig` construction:

```python
async def run_one(
    branch: str,
    start: date,
    end: date,
    capital: float,
    top_n: int | None = None,
    use_llm: bool = False,
    temperature: float | None = None,
    max_llm_calls_per_rebalance: int | None = None,
    use_llm_cache: bool = True,
    skills_bundle: str | None = None,
    save: bool = False,
):
    analyst_mode = "LLM" if use_llm else "Quantitative"
    universe_desc = f"top-{top_n}" if top_n is not None else "full"

    print(f"\n{'='*60}")
    print(f"  BACKTEST: {branch.upper()} BRANCH ({universe_desc} universe)")
    print(f"  Period: {start} to {end} | Weekly Rebalance")
    print(f"  Capital: ${capital:,.0f} | {analyst_mode} Analysts")
    if skills_bundle:
        print(f"  Skills bundle: {skills_bundle}")
    print(f"{'='*60}")

    # Build the LLMBacktestConfig if a cap override was provided
    from app.modules.backtest.config import LLMBacktestConfig
    llm_cfg = LLMBacktestConfig(
        cache_signals=True,
        max_llm_calls_per_rebalance=max_llm_calls_per_rebalance if max_llm_calls_per_rebalance is not None else 60,
    )

    config = BacktestConfig(
        start_date=start,
        end_date=end,
        initial_capital=capital,
        rebalance_frequency=RebalanceFrequency.WEEKLY,
        branch_name=branch,
        use_llm_agents=use_llm,
        llm_config=llm_cfg,
        benchmark_symbols=["SPY", "VOOG" if "growth" in branch else "VOOV"],
        top_n=top_n,
        skills_bundle=skills_bundle,
        use_llm_response_cache=use_llm_cache,
    )

    # If --temperature is set and LLM mode is on, override the per-analyst temperatures
    if use_llm and temperature is not None:
        from app.modules.equities.config import EquitiesConfig
        from copy import deepcopy
        override = deepcopy(config.equities_config_override) if config.equities_config_override else None
        if override is None:
            from app.config import get_settings
            # Start from the live equities config
            from app.modules.equities.config import get_equities_config
            override = deepcopy(get_equities_config())
        override.agents.news_analyst.temperature = temperature
        override.agents.fundamentals_analyst.temperature = temperature
        override.agents.technical_analyst.temperature = temperature
        config = config.model_copy(update={"equities_config_override": override})
```

(The temperature override path depends on the exact shape of `EquitiesConfig`; the implementer should verify the attribute path by reading `app/modules/equities/config.py` before editing.)

### Step 13.3: Add the `--save` code path

- [ ] **Edit `scripts/run_backtest.py`** — after the existing trade-printing loop at the end of `run_one`, add:

```python
    if save:
        from datetime import datetime
        import subprocess
        from pathlib import Path
        from app.modules.backtest.result_store import (
            BacktestRun,
            hash_skill_bundle,
            save_run,
        )

        # Determine which skill directory to fingerprint
        if skills_bundle:
            skills_dir = Path("data/skill_bundles") / skills_bundle
        else:
            skills_dir = Path("app/modules/equities/agents/skills")
        bundle_hash = hash_skill_bundle(skills_dir)
        short_hash = bundle_hash[:12]

        # Current git SHA
        try:
            git_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
        except Exception:
            git_sha = "unknown"

        timestamp = datetime.utcnow()
        run_id = f"{timestamp.strftime('%Y-%m-%dT%H-%M-%S')}_{short_hash}_{branch}"

        run = BacktestRun(
            run_id=run_id,
            timestamp=timestamp,
            git_sha=git_sha,
            config=config,
            skill_bundle_name=skills_bundle,
            skill_bundle_hash=bundle_hash,
            metrics=result.metrics,
            benchmarks=result.benchmarks,
            snapshots=result.snapshots,
            trades=result.trades,
            signals=result.signals,
            llm_cache_hits=result.llm_cache_hits,
            llm_cache_misses=result.llm_cache_misses,
        )
        save_path = save_run(run)
        print(f"\nSaved run: {run_id}")
        print(f"Path:      {save_path}")
```

### Step 13.4: Update `main()` to pass the flags through

- [ ] **Edit `scripts/run_backtest.py`** — in `main()`, update the loop that calls `run_one`:

```python
    for branch in args.branches:
        top_n = per_branch_top_n.get(branch) if per_branch_top_n.get(branch) is not None else args.top_n
        results[branch] = await run_one(
            branch,
            start,
            end,
            args.capital,
            top_n=top_n,
            use_llm=args.llm,
            temperature=args.temperature,
            max_llm_calls_per_rebalance=args.max_llm_calls_per_rebalance,
            use_llm_cache=not args.no_llm_cache,
            skills_bundle=args.skills_bundle,
            save=args.save,
        )
```

### Step 13.5: Manual smoke test — quantitative mode still works

- [ ] Run: `python -m scripts.run_backtest 2025-01-01 2025-06-30 growth --top-n 10`
- Expected: existing behavior preserved — runs a quantitative backtest and prints the summary as before.

### Step 13.6: Manual smoke test — save without LLM works

- [ ] Run: `python -m scripts.run_backtest 2025-01-01 2025-06-30 growth --top-n 10 --save`
- Expected: runs quantitatively AND saves a JSON file. Check: `ls data/backtest_runs/`
- Verify the saved file has empty `signals` (quantitative mode doesn't use the LLM cache): `python -c "import json; d = json.load(open('data/backtest_runs/<run_id>.json')); print(len(d['signals']), d['llm_cache_hits'])"` → expect `0 0`.

### Step 13.7: Run linter

- [ ] Run: `ruff check scripts/run_backtest.py`
- Expected: `All checks passed!`

### Step 13.8: Commit

- [ ] Run:

```bash
git add scripts/run_backtest.py
git commit -m "Add --llm, --save, --skills-bundle flags to run_backtest CLI

$(cat <<'EOF'
New flags:
  --llm                         Enable LLM-mode analysts
  --temperature FLOAT           Override analyst temperature
  --max-llm-calls-per-rebalance Override the per-rebalance cap
  --no-llm-cache                Disable persistent response cache
  --skills-bundle NAME          Load skills from data/skill_bundles/<name>
  --save                        Persist result to data/backtest_runs/

With --save, the script fingerprints the active skill directory, captures the
git SHA, and writes a full BacktestRun JSON file. See
plans/architecture/LLM-BACKTEST-ATTRIBUTION.md §6.9.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: Reproducibility integration test (cost-gated)

**Files:**
- Create: `tests/integration/backtest/test_llm_reproducibility.py`

### Step 14.1: Create the gated integration test

- [ ] **Create `tests/integration/backtest/test_llm_reproducibility.py`** with:

```python
"""End-to-end reproducibility test for LLM-mode backtests.

Cost-gated: requires ANTHROPIC_API_KEY. Expected cost: ~$0.50 per run.

Verifies that running the same LLM-mode backtest twice with the same arguments
produces bit-identical outputs because the persistent response cache covers
every LLM call on the second run.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from app.modules.backtest.config import BacktestConfig, LLMBacktestConfig
from app.modules.backtest.engine import BacktestEngine


pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="Requires ANTHROPIC_API_KEY for live LLM calls",
)
@pytest.mark.asyncio
async def test_llm_backtest_is_reproducible_via_cache(tmp_path: Path) -> None:
    """Two identical --llm runs must produce bit-identical metrics on the second run."""
    cache_path = tmp_path / "llm_response_cache.db"

    config = BacktestConfig(
        start_date=date(2025, 6, 1),
        end_date=date(2025, 6, 30),  # 1-month window keeps the call count small
        initial_capital=10_000.0,
        branch_name="growth",
        use_llm_agents=True,
        llm_config=LLMBacktestConfig(cache_signals=True, max_llm_calls_per_rebalance=60),
        top_n=3,  # 3 stocks × ~4 weekly rebalances × 3 analysts ≈ 36 calls
        use_llm_response_cache=True,
        llm_response_cache_path=cache_path,
    )

    engine = BacktestEngine()

    # First run — populates the cache
    result_1 = await engine.run(config)
    assert result_1.metrics is not None, f"First run failed: {result_1.error_message}"
    assert result_1.llm_cache_misses > 0, "First run should miss the cache at least once"

    # Second run — should hit 100%
    result_2 = await engine.run(config)
    assert result_2.metrics is not None, f"Second run failed: {result_2.error_message}"
    assert result_2.llm_cache_misses == 0, (
        f"Second run had {result_2.llm_cache_misses} misses; cache should cover every call"
    )
    assert result_2.llm_cache_hits > 0

    # Core metrics must be bit-identical
    m1, m2 = result_1.metrics, result_2.metrics
    assert m1.total_return == m2.total_return
    assert m1.sharpe_ratio == m2.sharpe_ratio
    assert m1.max_drawdown == m2.max_drawdown
    assert len(result_1.trades) == len(result_2.trades)
    assert len(result_1.signals) == len(result_2.signals)
```

### Step 14.2: Run the integration test (requires ANTHROPIC_API_KEY)

- [ ] Run: `pytest tests/integration/backtest/test_llm_reproducibility.py -v -m integration`
- Expected (with API key set): 1 passed, ~60 second duration, ~$0.50 API spend.
- Expected (without API key): 1 skipped.

### Step 14.3: Run the full unit suite to make sure nothing regressed

- [ ] Run: `pytest tests/unit/ -q`
- Expected: all pass.

### Step 14.4: Commit

- [ ] Run:

```bash
git add tests/integration/backtest/test_llm_reproducibility.py
git commit -m "Add cost-gated reproducibility integration test for LLM-mode backtests

$(cat <<'EOF'
Runs the same 1-month top-3 backtest twice and asserts the second run hits
the cache 100% and produces bit-identical metrics. Gated on ANTHROPIC_API_KEY
and marked with @pytest.mark.integration. ~\$0.50 per run. See
plans/architecture/LLM-BACKTEST-ATTRIBUTION.md §12.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: Update architecture docs

**Files:**
- Modify: `plans/architecture/LLM-BACKTEST-ATTRIBUTION.md`
- Modify: `plans/architecture/ANALYST-SKILLS-SYSTEM.md` (if implementation diverged from spec)
- Modify: `CLAUDE.md` (add LLM-mode command example)

### Step 15.1: Sanity-check the implementation against the spec

- [ ] Re-read `plans/architecture/LLM-BACKTEST-ATTRIBUTION.md` §6 (Phase 1) and verify every component matches what was built. Note any drifts.

### Step 15.2: Add a "Phase 1 Implementation Notes" section to the spec

- [ ] **Edit `plans/architecture/LLM-BACKTEST-ATTRIBUTION.md`** — after §6.9 (before `## 7. Phase 2`), append:

```markdown
### 6.10. Phase 1 Implementation Notes

Phase 1 was implemented in commits on 2026-04-07 per the implementation plan at `plans/implementation/2026-04-07-llm-backtest-attribution-phase1.md`. Deltas from the original spec:

- **`_load_output_format` now takes a string-path argument** (not `Path`) so `lru_cache` can hash it. Empty string means "use the package default". This is an internal detail; callers of `compose_system_prompt` pass `Path | None` as specified.
- **`resolve_skills_bundle` accepts `"live"` as a synonym for `None`.** This gives the CLI a way to explicitly say "use the current skills directory" without defaulting.
- **`LLMResponseCache.hits`/`misses` are instance counters, not persistent columns.** They track the current process's cache activity and are read at end-of-run to populate `BacktestResult.llm_cache_hits`/`llm_cache_misses`. Per-row `hit_count` remains in the SQL schema for future stats queries.
- **`BacktestEngine._collect_signals_from_context` and `_collect_cache_stats_from_context`** are new static helpers that translate runtime context state into the result schema. Kept static to make them individually unit-testable.
```

### Step 15.3: Add LLM-mode usage to `CLAUDE.md`

- [ ] **Edit `CLAUDE.md`** — in the `Commands` section under `# Backtesting`, add after the existing backtest examples:

```bash
# LLM-mode backtesting (Phase 1 — reproducible via persistent cache)
python -m scripts.bundle_skills baseline_v1                      # snapshot current skills
python -m scripts.run_backtest 2025-01-01 2025-06-30 growth --top-n 20 --llm --save
python -m scripts.run_backtest 2025-01-01 2025-06-30 growth --top-n 20 --llm --skills-bundle baseline_v1 --save
# Second run with same args is bit-identical (100% cache hit). Disable cache with --no-llm-cache.
```

### Step 15.4: Commit

- [ ] Run:

```bash
git add plans/architecture/LLM-BACKTEST-ATTRIBUTION.md CLAUDE.md
git commit -m "Document Phase 1 implementation of LLM-mode backtest attribution

$(cat <<'EOF'
Added §6.10 Implementation Notes to the spec listing the small deltas from
the original design. Added LLM-mode usage examples to CLAUDE.md.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

Before handing off, verify:

**Spec coverage (§6 Phase 1):**
- §6.1 `LLMResponseCache` → Task 1 ✓
- §6.2 `llm_client.py` cache wrapping → Task 4 ✓
- §6.3 `loader.py` skills_dir parameter → Task 2 ✓
- §6.4 Analyst constructor changes → Task 3 ✓
- §6.5 `BacktestConfig` new fields → Task 9 ✓
- §6.6 `BacktestContext` bundle resolution + cache instantiation → Task 11 ✓
- §6.7 `result_store.py` models + functions → Tasks 5, 6, 7 ✓
- §6.8 `scripts/bundle_skills.py` → Task 8 ✓
- §6.9 `scripts/run_backtest.py` new flags → Task 13 ✓
- Phase 1 deliverable (reproducible run) → Task 14 (integration test) ✓

Not in spec but necessary for Phase 1:
- `BacktestResult` schema extension → Task 10 (required for signals to flow through the engine's return type)
- `BacktestEngine` signal extraction → Task 12 (required for the signals to get from `ctx.llm_cache` into the result)

These two tasks are **implementation details** that fall out of the Phase 1 goal but weren't explicitly called out in the spec. Added to the plan because without them, the signals data wouldn't actually reach `save_run`.

**Placeholder scan:**
- No "TBD", "TODO", "implement later", "similar to Task N", "add appropriate error handling"
- All test code is complete and runnable
- All implementation code is complete

**Type consistency:**
- `LLMResponseCache.get(system_prompt, user_prompt, model, temperature)` consistent across Tasks 1, 4
- `compose_system_prompt(analyst_type, branch_name, sector, skills_dir)` consistent across Tasks 2, 3
- `BacktestRun` field names consistent across Tasks 6, 7, 13
- `hash_skill_bundle(skills_dir: Path) -> str` consistent across Tasks 5, 13
- `BacktestConfig.skills_bundle: str | None` consistent across Tasks 9, 11, 13

**Commit message style:** Matches existing repo convention (imperative, no conventional commits prefix).

**Ordering:** Tasks are linearized by dependency; each task is self-contained and testable on completion.

---

## Out-of-Scope Reminders (Phase 2 + Phase 3)

These are **explicitly not part of this plan**. They will get their own implementation plans after Phase 1 ships and produces real saved runs:

- `scripts/compare_runs.py` (Phase 2)
- `scripts/inspect_run.py` (Phase 2)
- `app/modules/backtest/comparison.py` (Phase 2)
- `app/modules/backtest/noise_floor_store.py` (Phase 3)
- `app/modules/backtest/statistics.py` (Phase 3)
- `app/modules/backtest/experiment.py` (Phase 3)
- `scripts/probe_noise.py` (Phase 3)
- `scripts/run_experiment.py` (Phase 3)
- Tier presets (`BacktestTier`, `TIER_PRESETS`) in `config.py` (Phase 3)

Per the project memory at `memory/llm_backtest_attribution_phase_ordering.md`: Phase 2 and Phase 3 implementation plans should not be written until Phase 1 has produced at least one saved `BacktestRun` with non-empty signals, so the actual data shapes can inform the next phase's design.
