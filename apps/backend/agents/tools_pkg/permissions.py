"""
Agent Tool Permissions
======================

Manages which tools are allowed for each agent type to prevent context
pollution and accidental misuse.

Supports dynamic tool filtering based on project capabilities to optimize
context window usage. For example, Playwright tools are only included for
web projects that need browser automation.

This module now uses AGENT_CONFIGS from models.py as the single source of truth
for tool permissions. The get_allowed_tools() function remains the primary API
for backwards compatibility.
"""

from .models import (
    AGENT_CONFIGS,
    CONTEXT7_TOOLS,
    GRAPHITI_MCP_TOOLS,
    PLAYWRIGHT_TOOLS,
    get_agent_config,
    get_required_mcp_servers,
)
from .registry import is_tools_available


def get_allowed_tools(
    agent_type: str,
    project_capabilities: dict | None = None,
    mcp_config: dict | None = None,
) -> list[str]:
    """
    Get the list of allowed tools for a specific agent type.

    This ensures each agent only sees tools relevant to their role,
    preventing context pollution and accidental misuse.

    Uses AGENT_CONFIGS as the single source of truth for tool permissions.
    Dynamic MCP tools are added based on project capabilities and required servers.

    Args:
        agent_type: Agent type identifier (e.g., 'coder', 'planner', 'qa_reviewer')
        project_capabilities: Optional dict from detect_project_capabilities()
                            containing flags like is_web_frontend, is_nextjs, etc.
        mcp_config: Per-project MCP server toggles from .pfactory/.env

    Returns:
        List of allowed tool names

    Raises:
        ValueError: If agent_type is not found in AGENT_CONFIGS
    """
    # Get agent configuration (raises ValueError if unknown type)
    config = get_agent_config(agent_type)

    # Start with base tools from config
    tools = list(config.get("tools", []))

    # Get required MCP servers for this agent
    required_servers = get_required_mcp_servers(
        agent_type,
        project_capabilities,
        mcp_config,
    )

    # Add pfactory tools ONLY if the MCP server is available
    # This prevents allowing tools that won't work because the server isn't running
    if "pfactory" in required_servers and is_tools_available():
        tools.extend(config.get("pfactory_tools", []))

    # Add MCP tool names based on required servers
    tools.extend(_get_mcp_tools_for_servers(required_servers))

    return tools


def _get_mcp_tools_for_servers(servers: list[str]) -> list[str]:
    """
    Get the list of MCP tools for a list of required servers.

    Maps server names to their corresponding tool lists.

    Args:
        servers: List of MCP server names (e.g., ['context7', 'playwright'])

    Returns:
        List of MCP tool names for all specified servers
    """
    tools = []

    for server in servers:
        if server == "context7":
            tools.extend(CONTEXT7_TOOLS)
        elif server == "graphiti":
            tools.extend(GRAPHITI_MCP_TOOLS)
        elif server == "playwright":
            tools.extend(PLAYWRIGHT_TOOLS)
        # pfactory tools are already added via config["pfactory_tools"]

    return tools


def get_all_agent_types() -> list[str]:
    """
    Get all registered agent types.

    Returns:
        Sorted list of all agent type identifiers
    """
    return sorted(AGENT_CONFIGS.keys())
