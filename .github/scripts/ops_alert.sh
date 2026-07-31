#!/usr/bin/env bash
# Open (or comment on) a GitHub issue labeled ops-alert.
# Usage: ops_alert.sh <subject> <run-url>
# Dedup: one open issue per "[ops-alert] <subject> — <UTC date>" title;
# repeats become comments on it.
set -euo pipefail

SUBJECT="$1"
RUN_URL="$2"
TITLE="[ops-alert] ${SUBJECT} — $(date -u +%F)"

gh label create ops-alert --force \
  --description "Automated operational alert" --color D93F0B

EXISTING=$(gh issue list --state open --label ops-alert --json number,title \
  --jq ".[] | select(.title == \"${TITLE}\") | .number" | head -1)

if [ -n "${EXISTING}" ]; then
  gh issue comment "${EXISTING}" --body "Recurred: ${RUN_URL}"
else
  gh issue create --title "${TITLE}" --label ops-alert --body "Run: ${RUN_URL}"
fi
