#!/usr/bin/env bash
# Re-sync the Claude subscription token into PFactory every 30 minutes.
#   bash scripts/sync-claude-auth-loop.sh &
set -uo pipefail
cd "$(dirname "$0")/.."
while true; do
  python3 scripts/sync-claude-auth.py || true
  sleep 1800
done
