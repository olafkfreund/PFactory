"""
Tool Models and Constants
==========================

Defines tool name constants and configuration for pfactory MCP tools.

This module is the single source of truth for all tool definitions used by
the Claude Agent SDK client. Tool lists are organized by category:

- Base tools: Core file operations (Read, Write, Edit, etc.)
- Web tools: Documentation and research (WebFetch, WebSearch)
- MCP tools: External integrations (Context7, Graphiti, etc.)
- PFactory tools: Custom build management tools
"""

import os

# =============================================================================
# Base Tools (Built-in Claude Code tools)
# =============================================================================

# Core file operation tools
BASE_READ_TOOLS = ["Read", "Glob", "Grep"]
BASE_WRITE_TOOLS = ["Write", "Edit", "Bash"]

# Web tools for documentation lookup and research
# Always available to all agents for accessing external information
WEB_TOOLS = ["WebFetch", "WebSearch"]

# =============================================================================
# PFactory MCP Tools (Custom build management)
# =============================================================================

# PFactory MCP tool names (prefixed with mcp__pfactory__)
TOOL_UPDATE_SUBTASK_STATUS = "mcp__pfactory__update_subtask_status"
TOOL_GET_BUILD_PROGRESS = "mcp__pfactory__get_build_progress"
TOOL_RECORD_DISCOVERY = "mcp__pfactory__record_discovery"
TOOL_RECORD_GOTCHA = "mcp__pfactory__record_gotcha"
TOOL_GET_SESSION_CONTEXT = "mcp__pfactory__get_session_context"
TOOL_UPDATE_QA_STATUS = "mcp__pfactory__update_qa_status"
TOOL_TEST_MEMORY_INTEGRATION = "mcp__pfactory__test_memory_integration"

# All pfactory MCP tools (for permissions)
AI_FACTORY_TOOLS = [
    TOOL_UPDATE_SUBTASK_STATUS,
    TOOL_GET_BUILD_PROGRESS,
    TOOL_RECORD_DISCOVERY,
    TOOL_RECORD_GOTCHA,
    TOOL_GET_SESSION_CONTEXT,
    TOOL_UPDATE_QA_STATUS,
    TOOL_TEST_MEMORY_INTEGRATION,
]

# =============================================================================
# External MCP Tools
# =============================================================================

# Context7 MCP tools for documentation lookup (always enabled)
CONTEXT7_TOOLS = [
    "mcp__context7__resolve-library-id",
    "mcp__context7__get-library-docs",
]

# Graphiti MCP tools for knowledge graph memory (when GRAPHITI_MCP_URL is set)
# See: https://github.com/getzep/graphiti
GRAPHITI_MCP_TOOLS = [
    "mcp__graphiti-memory__search_nodes",  # Search entity summaries
    "mcp__graphiti-memory__search_facts",  # Search relationships between entities
    "mcp__graphiti-memory__add_episode",  # Add data to knowledge graph
    "mcp__graphiti-memory__get_episodes",  # Retrieve recent episodes
    "mcp__graphiti-memory__get_entity_edge",  # Get specific entity/relationship
]

# =============================================================================
# Browser Automation MCP Tools (QA agents only)
# =============================================================================

# Playwright MCP tools for web browser automation
# Used for web frontend validation (non-Electron web apps)
# Uses @playwright/mcp with headless Chromium for reliable Linux support.
# NOTE: Screenshots must be compressed (1280x720, quality 60, JPEG) to stay under
# Claude SDK's 1MB JSON message buffer limit. See GitHub issue #74.
PLAYWRIGHT_TOOLS = [
    "mcp__playwright__browser_navigate",
    "mcp__playwright__browser_take_screenshot",
    "mcp__playwright__browser_click",
    "mcp__playwright__browser_fill_form",
    "mcp__playwright__browser_select_option",
    "mcp__playwright__browser_hover",
    "mcp__playwright__browser_evaluate",
    "mcp__playwright__browser_snapshot",
    "mcp__playwright__browser_console_messages",
    "mcp__playwright__browser_press_key",
    "mcp__playwright__browser_wait_for",
    "mcp__playwright__browser_navigate_back",
    "mcp__playwright__browser_close",
]

# =============================================================================
# Agent Configuration Registry
# =============================================================================
# Single source of truth for phase → tools → MCP servers mapping.
# This enables phase-aware tool control and context window optimization.

AGENT_CONFIGS = {
    # ═══════════════════════════════════════════════════════════════════════
    # SPEC CREATION PHASES (Minimal tools, fast startup)
    # ═══════════════════════════════════════════════════════════════════════
    "spec_gatherer": {
        "tools": BASE_READ_TOOLS + WEB_TOOLS,
        "mcp_servers": [],  # No MCP needed - just reads project
        "pfactory_tools": [],
        "thinking_default": "medium",
    },
    "spec_researcher": {
        "tools": BASE_READ_TOOLS + WEB_TOOLS,
        "mcp_servers": ["context7"],  # Needs docs lookup
        "pfactory_tools": [],
        "thinking_default": "medium",
    },
    "spec_writer": {
        "tools": BASE_READ_TOOLS + BASE_WRITE_TOOLS,
        "mcp_servers": [],  # Just writes spec.md
        "pfactory_tools": [],
        "thinking_default": "high",
    },
    "spec_critic": {
        "tools": BASE_READ_TOOLS,
        "mcp_servers": [],  # Self-critique, no external tools
        "pfactory_tools": [],
        "thinking_default": "high",
    },
    "spec_discovery": {
        "tools": BASE_READ_TOOLS + WEB_TOOLS,
        "mcp_servers": [],
        "pfactory_tools": [],
        "thinking_default": "medium",
    },
    "spec_context": {
        "tools": BASE_READ_TOOLS,
        "mcp_servers": [],
        "pfactory_tools": [],
        "thinking_default": "medium",
    },
    "spec_validation": {
        "tools": BASE_READ_TOOLS,
        "mcp_servers": [],
        "pfactory_tools": [],
        "thinking_default": "high",
    },
    "spec_compaction": {
        "tools": BASE_READ_TOOLS + BASE_WRITE_TOOLS,
        "mcp_servers": [],
        "pfactory_tools": [],
        "thinking_default": "medium",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # BUILD PHASES (Full tools + Graphiti memory)
    # ═══════════════════════════════════════════════════════════════════════
    "planner": {
        "tools": BASE_READ_TOOLS + BASE_WRITE_TOOLS + WEB_TOOLS,
        "mcp_servers": ["context7", "graphiti", "pfactory"],
        "pfactory_tools": [
            TOOL_GET_BUILD_PROGRESS,
            TOOL_GET_SESSION_CONTEXT,
            TOOL_RECORD_DISCOVERY,
        ],
        "thinking_default": "high",
    },
    "coder": {
        "tools": BASE_READ_TOOLS + BASE_WRITE_TOOLS + WEB_TOOLS,
        "mcp_servers": ["context7", "graphiti", "pfactory"],
        "pfactory_tools": [
            TOOL_UPDATE_SUBTASK_STATUS,
            TOOL_GET_BUILD_PROGRESS,
            TOOL_RECORD_DISCOVERY,
            TOOL_RECORD_GOTCHA,
            TOOL_GET_SESSION_CONTEXT,
            TOOL_TEST_MEMORY_INTEGRATION,
        ],
        "thinking_default": "none",  # Coding doesn't use extended thinking
    },
    # ═══════════════════════════════════════════════════════════════════════
    # QA PHASES (Read + test + browser + Graphiti memory)
    # ═══════════════════════════════════════════════════════════════════════
    "qa_reviewer": {
        # Read + Write/Edit (for QA reports and plan updates) + Bash (for tests)
        # Note: Reviewer writes to spec directory only (qa_report.md, test_plan.json)
        "tools": BASE_READ_TOOLS + BASE_WRITE_TOOLS + WEB_TOOLS,
        "mcp_servers": ["context7", "graphiti", "pfactory", "browser"],
        "pfactory_tools": [
            TOOL_GET_BUILD_PROGRESS,
            TOOL_UPDATE_QA_STATUS,
            TOOL_GET_SESSION_CONTEXT,
            TOOL_TEST_MEMORY_INTEGRATION,
        ],
        "thinking_default": "high",
    },
    "qa_fixer": {
        "tools": BASE_READ_TOOLS + BASE_WRITE_TOOLS + WEB_TOOLS,
        "mcp_servers": ["context7", "graphiti", "pfactory", "browser"],
        "pfactory_tools": [
            TOOL_UPDATE_SUBTASK_STATUS,
            TOOL_GET_BUILD_PROGRESS,
            TOOL_UPDATE_QA_STATUS,
            TOOL_RECORD_GOTCHA,
            TOOL_TEST_MEMORY_INTEGRATION,
        ],
        "thinking_default": "medium",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # UTILITY PHASES (Minimal, no MCP)
    # ═══════════════════════════════════════════════════════════════════════
    "insights": {
        "tools": BASE_READ_TOOLS + WEB_TOOLS,
        "mcp_servers": [],
        "pfactory_tools": [],
        "thinking_default": "medium",
    },
    "merge_resolver": {
        "tools": [],  # Text-only analysis
        "mcp_servers": [],
        "pfactory_tools": [],
        "thinking_default": "low",
    },
    "commit_message": {
        "tools": [],
        "mcp_servers": [],
        "pfactory_tools": [],
        "thinking_default": "low",
    },
    "pr_reviewer": {
        "tools": BASE_READ_TOOLS + WEB_TOOLS,  # Read-only
        "mcp_servers": ["context7"],
        "pfactory_tools": [],
        "thinking_default": "high",
    },
    "pr_orchestrator_parallel": {
        "tools": BASE_READ_TOOLS + WEB_TOOLS,  # Read-only for parallel PR orchestrator
        "mcp_servers": ["context7"],
        "pfactory_tools": [],
        "thinking_default": "high",
    },
    "pr_followup_parallel": {
        "tools": BASE_READ_TOOLS
        + WEB_TOOLS,  # Read-only for parallel followup reviewer
        "mcp_servers": ["context7"],
        "pfactory_tools": [],
        "thinking_default": "high",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # ANALYSIS PHASES
    # ═══════════════════════════════════════════════════════════════════════
    "analysis": {
        "tools": BASE_READ_TOOLS + WEB_TOOLS,
        "mcp_servers": ["context7"],
        "pfactory_tools": [],
        "thinking_default": "medium",
    },
    "batch_analysis": {
        "tools": BASE_READ_TOOLS + WEB_TOOLS,
        "mcp_servers": [],
        "pfactory_tools": [],
        "thinking_default": "low",
    },
    "batch_validation": {
        "tools": BASE_READ_TOOLS,
        "mcp_servers": [],
        "pfactory_tools": [],
        "thinking_default": "low",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # ROADMAP & IDEATION
    # ═══════════════════════════════════════════════════════════════════════
    "roadmap_discovery": {
        "tools": BASE_READ_TOOLS + WEB_TOOLS,
        "mcp_servers": ["context7"],
        "pfactory_tools": [],
        "thinking_default": "high",
    },
    "competitor_analysis": {
        "tools": BASE_READ_TOOLS + WEB_TOOLS,
        "mcp_servers": ["context7"],  # WebSearch for competitor research
        "pfactory_tools": [],
        "thinking_default": "high",
    },
    "ideation": {
        "tools": BASE_READ_TOOLS + WEB_TOOLS,
        "mcp_servers": [],
        "pfactory_tools": [],
        "thinking_default": "high",
    },
}


# =============================================================================
# Agent Config Helper Functions
# =============================================================================


def get_agent_config(agent_type: str) -> dict:
    """
    Get full configuration for an agent type.

    Args:
        agent_type: The agent type identifier (e.g., 'coder', 'planner', 'qa_reviewer')

    Returns:
        Configuration dict containing tools, mcp_servers, pfactory_tools, thinking_default

    Raises:
        ValueError: If agent_type is not found in AGENT_CONFIGS (strict mode)
    """
    if agent_type not in AGENT_CONFIGS:
        raise ValueError(
            f"Unknown agent type: '{agent_type}'. "
            f"Valid types: {sorted(AGENT_CONFIGS.keys())}"
        )
    return AGENT_CONFIGS[agent_type]


def _map_mcp_server_name(
    name: str, custom_server_ids: list[str] | None = None
) -> str | None:
    """
    Map user-friendly MCP server names to internal identifiers.
    Also accepts custom server IDs directly.

    Args:
        name: User-provided MCP server name
        custom_server_ids: List of custom server IDs to accept as-is

    Returns:
        Internal server identifier or None if not recognized
    """
    if not name:
        return None
    mappings = {
        "context7": "context7",
        "graphiti-memory": "graphiti",
        "graphiti": "graphiti",
        "playwright": "playwright",
        "puppeteer": "playwright",  # backward compat: puppeteer maps to playwright
        "pfactory": "pfactory",
    }
    # Check if it's a known mapping
    mapped = mappings.get(name.lower().strip())
    if mapped:
        return mapped
    # Catalog servers (github, kubernetes, aws, azure, ...): accept by id verbatim.
    # Imported lazily so the mapping module stays cheap to load.
    try:
        from agents.tools_pkg.mcp_catalog import is_catalog_server

        if is_catalog_server(name.lower().strip()):
            return name.lower().strip()
    except ImportError:
        pass
    # Check if it's a custom server ID (accept as-is)
    if custom_server_ids and name in custom_server_ids:
        return name
    return None


def get_required_mcp_servers(
    agent_type: str,
    project_capabilities: dict | None = None,
    mcp_config: dict | None = None,
    infra_markers: dict | None = None,
) -> list[str]:
    """
    Get MCP servers required for this agent type.

    Handles dynamic server selection:
    - "browser" → playwright (if is_web_frontend)
    - "graphiti" → only if GRAPHITI_MCP_URL is set
    - Catalog servers (github/kubernetes/aws/azure) auto-enable when their
      ``marker_capability_keys`` match ``infra_markers`` AND credentials probe
      succeeds. See ``agents.tools_pkg.mcp_catalog``.
    - Respects per-project MCP config overrides from .pfactory/.env
    - Applies per-agent ADD/REMOVE overrides from AGENT_MCP_<agent>_ADD/REMOVE
      (these run LAST so operators can always force-enable or force-disable)

    Args:
        agent_type: The agent type identifier
        project_capabilities: Dict from detect_project_capabilities() or None
        mcp_config: Per-project MCP server toggles from .pfactory/.env
                   Keys: CONTEXT7_ENABLED,
                         PLAYWRIGHT_MCP_ENABLED, AGENT_MCP_<agent>_ADD/REMOVE
        infra_markers: Dict from detect_infra_markers() — has_kubernetes, has_aws, etc.
                       When None, catalog auto-enable is skipped entirely (legacy callers
                       keep current behavior).

    Returns:
        List of MCP server names to start
    """
    config = get_agent_config(agent_type)
    servers = list(config.get("mcp_servers", []))

    # Load per-project config (or use defaults)
    if mcp_config is None:
        mcp_config = {}

    # Filter context7 if explicitly disabled by project config
    if "context7" in servers:
        context7_enabled = mcp_config.get("CONTEXT7_ENABLED", "true")
        if str(context7_enabled).lower() == "false":
            servers = [s for s in servers if s != "context7"]

    # Handle dynamic "browser" → playwright based on project type and config
    if "browser" in servers:
        servers = [s for s in servers if s != "browser"]
        if project_capabilities:
            is_web_frontend = project_capabilities.get("is_web_frontend", False)

            # Check per-project override (default false)
            # Accept both PLAYWRIGHT_MCP_ENABLED and legacy PUPPETEER_MCP_ENABLED
            playwright_enabled = mcp_config.get(
                "PLAYWRIGHT_MCP_ENABLED",
                mcp_config.get("PUPPETEER_MCP_ENABLED", "false"),
            )

            # Playwright: enabled by project config for web frontends
            if is_web_frontend and str(playwright_enabled).lower() == "true":
                servers.append("playwright")

    # Filter graphiti if not enabled
    if "graphiti" in servers:
        if not os.environ.get("GRAPHITI_MCP_URL"):
            servers = [s for s in servers if s != "graphiti"]

    # ========== Catalog auto-enable (github/kubernetes/aws/azure/...) ==========
    # Walks ``mcp_catalog.CATALOG`` and appends any entry where the agent is in
    # ``default_for_agents`` AND the project markers match AND credentials are
    # available. Lazily imported so module load stays cheap; ImportError just
    # skips the framework (legacy behaviour).
    if infra_markers is not None:
        try:
            from agents.tools_pkg.mcp_catalog import CATALOG
            from core.mcp_credentials import get_credential_status

            for entry in CATALOG:
                if agent_type not in entry.default_for_agents:
                    continue
                if entry.id in servers:
                    continue  # already in base list from AGENT_CONFIGS
                # Empty marker list = always-on (e.g. github — every repo qualifies)
                if entry.marker_capability_keys and not any(
                    infra_markers.get(key) for key in entry.marker_capability_keys
                ):
                    continue
                if entry.credential_provider:
                    creds = get_credential_status(entry.credential_provider)
                    if not creds.available:
                        continue
                servers.append(entry.id)
        except ImportError:
            # Framework not installed (or running in a stripped-down context) —
            # silently skip catalog auto-enable rather than break the existing path.
            pass

    # ========== Apply per-agent MCP overrides ==========
    # Format: AGENT_MCP_<agent_type>_ADD=server1,server2
    #         AGENT_MCP_<agent_type>_REMOVE=server1,server2
    add_key = f"AGENT_MCP_{agent_type}_ADD"
    remove_key = f"AGENT_MCP_{agent_type}_REMOVE"

    # Extract custom server IDs for mapping (allows custom servers to be recognized)
    custom_servers = mcp_config.get("CUSTOM_MCP_SERVERS", [])
    custom_server_ids = [s.get("id") for s in custom_servers if s.get("id")]

    # Process additions
    if add_key in mcp_config:
        additions = [
            s.strip() for s in str(mcp_config[add_key]).split(",") if s.strip()
        ]
        for server in additions:
            mapped = _map_mcp_server_name(server, custom_server_ids)
            if mapped and mapped not in servers:
                servers.append(mapped)

    # Process removals (but never remove pfactory)
    if remove_key in mcp_config:
        removals = [
            s.strip() for s in str(mcp_config[remove_key]).split(",") if s.strip()
        ]
        for server in removals:
            mapped = _map_mcp_server_name(server, custom_server_ids)
            if mapped and mapped != "pfactory":  # pfactory cannot be removed
                servers = [s for s in servers if s != mapped]

    return servers


def get_default_thinking_level(agent_type: str) -> str:
    """
    Get default thinking level string for agent type.

    This returns the thinking level name (e.g., 'medium', 'high'), not the token budget.
    To convert to tokens, use phase_config.get_thinking_budget(level).

    Args:
        agent_type: The agent type identifier

    Returns:
        Thinking level string (none, low, medium, high, max)
    """
    config = get_agent_config(agent_type)
    return config.get("thinking_default", "medium")
