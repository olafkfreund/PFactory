#!/usr/bin/env bash
#
# Spawn the PFactory MCP server for Claude Code via stdio.
# Referenced from the project-scoped .mcp.json at the repo root (Issue #10).
#
# Resolves the repo root via $CLAUDE_PROJECT_DIR (set by Claude Code) and
# falls back to the script's parent directory. The venv at
# apps/backend/.venv must exist — created by `npm run install:backend`.

set -euo pipefail

ROOT="${CLAUDE_PROJECT_DIR:-}"
if [ -z "$ROOT" ]; then
  # Resolve symlinks then walk up from scripts/ to repo root
  ROOT="$(cd "$(dirname "$0")/.." && pwd)"
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
