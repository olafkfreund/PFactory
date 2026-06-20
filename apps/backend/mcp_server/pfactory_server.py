"""
Standalone stdio entrypoint for the pfactory MCP server.

Issue #10 — Epic #6.

When Claude Code opens a repo containing a project-scoped ``.mcp.json``
that references this module, it spawns us as a subprocess and speaks the
MCP JSON-RPC protocol over our stdin/stdout. We re-use the same tool
implementations the in-process Claude Agent SDK session uses (see
``agents.tools_pkg.registry.create_magestic_ai_mcp_server``) — only the
transport changes.

Spec resolution (per the architect-reviewer's design):

1. ``--spec-dir <path>`` CLI argument (highest precedence)
2. ``PFACTORY_SPEC_DIR`` env var (set by PFactory's CLI / web UI when a
   spec becomes active)
3. ``PFACTORY_PROJECT_DIR`` / ``CLAUDE_PROJECT_DIR`` env var
   (fallback — "no active spec" mode where tools return a guidance error
   instead of writing to whatever directory happens to be cwd)

The ``.pfactory/current-spec`` pointer-file convention floated by an
earlier worker was deliberately NOT adopted — that's a separate design
question (worktrees, concurrent builds) and gets its own follow-up issue.

Run directly for debugging:

    PFACTORY_SPEC_DIR=/path/to/spec \
      python -m apps.backend.mcp.pfactory_server

…then send MCP JSON-RPC frames on stdin.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Callable
from pathlib import Path

# ---------------------------------------------------------------------------
# Spec / project resolution
# ---------------------------------------------------------------------------


def _build_spec_dir_resolver(
    cli_spec_dir: Path | None,
) -> Callable[[], Path]:
    """Return a callable that re-evaluates the active spec dir on each call.

    Re-evaluating on each call (rather than capturing at startup) means
    PFactory's backend can change ``PFACTORY_SPEC_DIR`` mid-session and
    subsequent tool invocations pick up the new value automatically.
    """

    def resolve() -> Path:
        if cli_spec_dir is not None:
            return cli_spec_dir
        env_spec = os.environ.get("PFACTORY_SPEC_DIR")
        if env_spec:
            return Path(env_spec)
        # Degraded "no active spec" fallback — tools detect the missing
        # test_plan.json / memory/ subtree and emit clear guidance
        # errors. This keeps the server listable in `claude mcp list` even
        # when no build is active.
        return Path(
            os.environ.get("PFACTORY_PROJECT_DIR")
            or os.environ.get("CLAUDE_PROJECT_DIR")
            or os.getcwd()
        )

    return resolve


def _build_project_dir_resolver() -> Callable[[], Path]:
    """Return a callable that re-evaluates the project root on each call."""

    def resolve() -> Path:
        return Path(
            os.environ.get("PFACTORY_PROJECT_DIR")
            or os.environ.get("CLAUDE_PROJECT_DIR")
            or os.getcwd()
        )

    return resolve


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


async def _run(spec_dir_factory: Callable[[], Path]) -> None:
    """Build the SDK-backed MCP server and serve it over stdio."""

    # Imports deferred so a bare ``--help`` doesn't pay the SDK import cost
    # (and so the error path in __main__ can still produce a useful message
    # if the SDK isn't installed).
    # Late import: agents/tools_pkg pulls in the rest of PFactory's backend,
    # so we don't want to trip module-import errors on a help screen.
    from agents.tools_pkg.registry import (
        create_all_tools,
        create_magestic_ai_mcp_server,
    )
    from agents.tools_pkg.tools.task_control import create_task_control_tools
    from claude_agent_sdk import create_sdk_mcp_server
    from mcp.server.models import InitializationOptions
    from mcp.server.stdio import stdio_server

    project_dir_factory = _build_project_dir_resolver()

    # Build the tool list ourselves so the standalone server can register
    # task-control tools (Epic #50 M1) IN ADDITION to the spec-internal
    # tools the in-process agent gets. The in-process Claude Agent SDK
    # session deliberately does NOT get task-control tools — the agent
    # shouldn't drive itself recursively (start its own siblings, kill
    # itself, etc.). That's why this lives in pfactory_server.py and
    # NOT in registry.create_all_tools.
    spec_internal_tools = create_all_tools(
        spec_dir=spec_dir_factory, project_dir=project_dir_factory
    )
    if not spec_internal_tools:
        # Replicate the original create_magestic_ai_mcp_server failure path
        # so the error message stays identical for operators who hit it.
        _ = create_magestic_ai_mcp_server(
            spec_dir=spec_dir_factory, project_dir=project_dir_factory
        )
        sys.stderr.write(
            "pfactory MCP: claude-agent-sdk not installed in this venv.\n"
            "Run 'npm run install:backend' from the PFactory repo root.\n"
        )
        sys.exit(2)

    task_control_tools = create_task_control_tools()

    # Plan tools (#2, Phase F): let any host hand a plan to PFactory and track
    # it over MCP. Like task-control, these are registered ONLY on the standalone
    # server, not for the in-process agent.
    from agents.tools_pkg.tools.plan_control import create_plan_tools

    plan_tools = create_plan_tools()

    sdk_cfg = create_sdk_mcp_server(
        name="pfactory",
        version="1.0.0",
        tools=spec_internal_tools + task_control_tools + plan_tools,
    )

    server = sdk_cfg["instance"]  # mcp.server.lowlevel.server.Server

    # NotificationOptions actually lives in lowlevel.server (mcp.server.models
    # only exports InitializationOptions in the installed package version).
    from mcp.server.lowlevel.server import NotificationOptions

    init_options = InitializationOptions(
        server_name="pfactory",
        server_version="1.0.0",
        capabilities=server.get_capabilities(
            notification_options=NotificationOptions(),
            experimental_capabilities={},
        ),
    )

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_options)


def _ensure_backend_on_path() -> None:
    """Add apps/backend to sys.path so ``from agents.tools_pkg ...`` resolves.

    When invoked via ``python -m apps.backend.mcp.pfactory_server``, Python
    runs this from the repo root and the import path is fine. When invoked
    via the wrapper script with ``-m mcp.pfactory_server`` from inside
    ``apps/backend/``, we still need to make sure ``apps/backend`` is
    importable so ``agents.tools_pkg.registry`` resolves correctly.
    """
    backend = Path(__file__).resolve().parents[1]
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="pfactory-mcp-server",
        description=(
            "Standalone MCP server exposing PFactory's spec-management "
            "tools to Claude Code over stdio."
        ),
    )
    parser.add_argument(
        "--spec-dir",
        type=Path,
        default=None,
        help=(
            "Path to the active spec directory. Overrides "
            "PFACTORY_SPEC_DIR env var. When neither is set, tools run in "
            "'no active spec' mode and return guidance errors."
        ),
    )
    args = parser.parse_args()

    _ensure_backend_on_path()
    spec_resolver = _build_spec_dir_resolver(args.spec_dir)

    try:
        asyncio.run(_run(spec_resolver))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"pfactory MCP server failed: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
