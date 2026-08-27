#!/usr/bin/env bash
#
# Spawn the PFactory MCP server for Claude Code via stdio.
# Referenced from the project-scoped .mcp.json at the repo root (Issue #10).
#
# Resolves the repo root via $CLAUDE_PROJECT_DIR (set by Claude Code) and
# falls back to the script's parent directory. The venv at
# apps/backend/.venv must exist — created by `npm run install:backend`.

set -euo pipefail

# This script lives in THIS repo's scripts/, so its own location is the only
# reliable answer to "which repo am I in". CLAUDE_PROJECT_DIR is the SESSION's
# project directory, which in a multi-repo setup -- the Factory hub driving
# four sibling repos -- is a DIFFERENT repo. Trusting it made the server hunt
# for its venv inside the hub and exit, which Claude Code surfaces only as
# CONNECTION_CLOSED, with no hint that the path was wrong.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Honour an explicit override only if it actually looks like this repo, so a
# stale or unrelated value cannot silently redirect the server.
if [ ! -x "$ROOT/apps/backend/.venv/bin/python" ]; then
  for candidate in "${PFACTORY_PROJECT_DIR:-}" "${CLAUDE_PROJECT_DIR:-}"; do
    if [ -n "$candidate" ] && [ -x "$candidate/apps/backend/.venv/bin/python" ]; then
      ROOT="$candidate"
      break
    fi
  done
fi

PYTHON="$ROOT/apps/backend/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  cat >&2 <<EOF
pfactory MCP server cannot start: Python venv missing at
    $PYTHON

From the PFactory repo root run:
    npm run install:backend

That builds apps/backend/.venv with claude-agent-sdk and the MCP runtime.
EOF
  exit 1
fi

cd "$ROOT/apps/backend"
exec "$PYTHON" -m mcp_server.pfactory_server "$@"
