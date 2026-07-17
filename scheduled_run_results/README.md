# Scheduled Run Results

Archive of weekly cron job outputs. One file per run for retrospective review and
week-over-week comparison.

## Naming convention

```
YYYY-MM-DD-<branch>.md          # one branch per file
YYYY-MM-DD.md                   # both branches combined
```

Examples:
- `2026-05-11-growth.md`
- `2026-05-11-value.md`
- `2026-05-11.md`

Use the `run_date` from the `pipeline_runs` row (America/New_York), not the UTC
timestamp of the cron firing.

## What to include

For each run, capture at minimum:

1. **Job Summary** (markdown from the GitHub Actions run page) — paste verbatim
2. **`pipeline_runs` row** — `run_id`, status, duration, `summary_json`
3. **Notable observations** — anything unusual: 0-order weeks, ❌ failures,
   rate-limiting warnings, or unexpected NAV moves

## Optional sections

- Trade list snapshot (`SELECT ... FROM trades WHERE executed_at > <run_start>`)
- Top-5 holdings change vs. last week
- Composite-score outliers (anything `score >= 8` or `<= 2`)

## How to find a run

```bash
# GitHub Actions UI
#   Actions tab → Weekly Rebalance → click the dated run → "Summary" section
```

```sql
-- Neon SQL Editor
SELECT run_id, status, started_at, completed_at,
       summary_json, error_msg
FROM pipeline_runs
ORDER BY started_at DESC
LIMIT 10;
```
