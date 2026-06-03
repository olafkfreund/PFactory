"""
Tool Registry
=============

Central registry for creating and managing pfactory MCP tools.
"""

from collections.abc import Callable
from pathlib import Path

try:
    from claude_agent_sdk import create_sdk_mcp_server

    SDK_TOOLS_AVAILABLE = True
except ImportError:
    SDK_TOOLS_AVAILABLE = False
    create_sdk_mcp_server = None

from .tools import (
    create_memory_tools,
    create_progress_tools,
    create_qa_tools,
    create_subtask_tools,
)

# Either a fixed Path (in-process agent sessions) or a callable returning Path
# (standalone MCP server — re-reads PFACTORY_SPEC_DIR / --spec-dir on each
# tool call). The tool factories accept both shapes (Issue #10).
PathOrFactory = Path | Callable[[], Path]


def create_all_tools(spec_dir: PathOrFactory, project_dir: PathOrFactory) -> list:
    """
    Create all custom tools with the given spec and project directories.

    Args:
        spec_dir: Path or Callable[[], Path] to the spec directory
        project_dir: Path or Callable[[], Path] to the project root

    Returns:
        List of all tool functions
    """
    if not SDK_TOOLS_AVAILABLE:
        return []

    all_tools = []

    # Create tools by category. Each factory accepts Path | Callable[[], Path]
    # — passing through verbatim keeps Issue #10's standalone-server path open
    # without touching the in-process call site in core/client.py.
    all_tools.extend(create_subtask_tools(spec_dir, project_dir))
    all_tools.extend(create_progress_tools(spec_dir, project_dir))
    all_tools.extend(create_memory_tools(spec_dir, project_dir))
    all_tools.extend(create_qa_tools(spec_dir, project_dir))

    return all_tools


def create_magestic_ai_mcp_server(
    spec_dir: PathOrFactory,
    project_dir: PathOrFactory,
):
    """
    Create an MCP server with pfactory custom tools.

    Args:
        spec_dir: Path or Callable[[], Path] to the spec directory
        project_dir: Path or Callable[[], Path] to the project root

    Returns:
        MCP server instance, or None if SDK tools not available
    """
    if not SDK_TOOLS_AVAILABLE:
        return None

    tools = create_all_tools(spec_dir, project_dir)

    # In-process server name "pfactory" matches AI_FACTORY_TOOLS constants
    # in models.py (mcp__pfactory__update_subtask_status etc.) that the
    # Claude Agent SDK uses to permission these tools. The standalone MCP
    # server in apps/backend/mcp_server/pfactory_server.py registers under
    # the same name via .mcp.json — single source of truth post-rebrand.
    return create_sdk_mcp_server(name="pfactory", version="1.0.0", tools=tools)


def is_tools_available() -> bool:
    """Check if SDK tools functionality is available."""
    return SDK_TOOLS_AVAILABLE
