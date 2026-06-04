"""
Claude SDK Client Configuration
===============================

Functions for creating and configuring the Claude Agent SDK client.

All AI interactions should use `create_client()` to ensure consistent OAuth authentication
and proper tool/MCP configuration. For simple message calls without full agent sessions,
use `create_simple_client()` from `core.simple_client`.

The client factory now uses AGENT_CONFIGS from agents/tools_pkg/models.py as the
single source of truth for phase-aware tool and MCP server configuration.
"""

import copy
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# =============================================================================
# SDK Message Parser Patch
# =============================================================================
# The Claude Agent SDK's message_parser raises MessageParseError for unknown
# message types (e.g., "rate_limit_event"). Since parse_message runs inside an
# async generator, the exception kills the entire agent session stream.
# Patch to log a warning and return a SystemMessage instead of crashing.
# This is needed until the SDK natively handles all CLI message types.


def _patch_sdk_message_parser() -> None:
    """Patch the SDK's parse_message to handle unknown message types gracefully.

    The Claude CLI may emit message types that the installed SDK version doesn't
    recognize (e.g., rate_limit_event, usage_event). Without this patch, any
    unrecognized type raises MessageParseError inside the SDK's async generator,
    which terminates the entire response stream and kills the agent session.

    The patch converts unknown types into SystemMessage objects with a
    'unknown_<type>' subtype, which all message consumers silently skip.
    """
    try:
        import claude_agent_sdk._internal.message_parser as _parser
        from claude_agent_sdk._errors import MessageParseError
        from claude_agent_sdk.types import SystemMessage

        _original_parse = _parser.parse_message

        def _patched_parse(data):
            try:
                return _original_parse(data)
            except MessageParseError as e:
                msg = str(e)
                if "Unknown message type" in msg:
                    msg_type = (
                        data.get("type", "unknown")
                        if isinstance(data, dict)
                        else "unknown"
                    )
                    # Rate limit events deserve a visible warning; others just debug-level
                    if "rate_limit" in msg_type:
                        retry_after = (
                            data.get("retry_after")
                            or data.get("data", {}).get("retry_after")
                            if isinstance(data, dict)
                            else None
                        )
                        retry_info = (
                            f" (retry_after={retry_after}s)" if retry_after else ""
                        )
                        logger.warning(
                            f"Rate limit event received from CLI{retry_info} — "
                            f"the SDK will handle backoff automatically"
                        )
                    else:
                        logger.debug(
                            f"SDK received unhandled message type '{msg_type}', skipping"
                        )
                    return SystemMessage(
                        subtype=f"unknown_{msg_type}",
                        data=data if isinstance(data, dict) else {},
                    )
                raise

        _parser.parse_message = _patched_parse
    except Exception as e:
        logger.warning(f"Failed to patch SDK message parser: {e}")


_patch_sdk_message_parser()

# =============================================================================
# Project Index Cache
# =============================================================================
# Caches project index and capabilities to avoid reloading on every create_client() call.
# This significantly reduces the time to create new agent sessions.

_PROJECT_INDEX_CACHE: dict[str, tuple[dict[str, Any], dict[str, bool], float]] = {}
_CACHE_TTL_SECONDS = 300  # 5 minute TTL
_CACHE_LOCK = threading.Lock()  # Protects _PROJECT_INDEX_CACHE access


def _get_cached_project_data(
    project_dir: Path,
) -> tuple[dict[str, Any], dict[str, bool]]:
    """
    Get project index and capabilities with caching.

    Args:
        project_dir: Path to the project directory

    Returns:
        Tuple of (project_index, project_capabilities)
    """

    key = str(project_dir.resolve())
    now = time.time()
    debug = os.environ.get("DEBUG", "").lower() in ("true", "1")

    # Check cache with lock
    with _CACHE_LOCK:
        if key in _PROJECT_INDEX_CACHE:
            cached_index, cached_capabilities, cached_time = _PROJECT_INDEX_CACHE[key]
            cache_age = now - cached_time
            if cache_age < _CACHE_TTL_SECONDS:
                if debug:
                    print(
                        f"[ClientCache] Cache HIT for project index (age: {cache_age:.1f}s / TTL: {_CACHE_TTL_SECONDS}s)"
                    )
                logger.debug(f"Using cached project index for {project_dir}")
                # Return deep copies to prevent callers from corrupting the cache
                return copy.deepcopy(cached_index), copy.deepcopy(cached_capabilities)
            elif debug:
                print(
                    f"[ClientCache] Cache EXPIRED for project index (age: {cache_age:.1f}s > TTL: {_CACHE_TTL_SECONDS}s)"
                )

    # Cache miss or expired - load fresh data (outside lock to avoid blocking)
    load_start = time.time()
    logger.debug(f"Loading project index for {project_dir}")
    project_index = load_project_index(project_dir)
    project_capabilities = detect_project_capabilities(project_index)

    if debug:
        load_duration = (time.time() - load_start) * 1000
        print(
            f"[ClientCache] Cache MISS - loaded project index in {load_duration:.1f}ms"
        )

    # Store in cache with lock - use double-checked locking pattern
    # Re-check if another thread populated the cache while we were loading
    with _CACHE_LOCK:
        if key in _PROJECT_INDEX_CACHE:
            cached_index, cached_capabilities, cached_time = _PROJECT_INDEX_CACHE[key]
            cache_age = time.time() - cached_time
            if cache_age < _CACHE_TTL_SECONDS:
                # Another thread already cached valid data while we were loading
                if debug:
                    print(
                        "[ClientCache] Cache was populated by another thread, using cached data"
                    )
                # Return deep copies to prevent callers from corrupting the cache
                return copy.deepcopy(cached_index), copy.deepcopy(cached_capabilities)
        # Either no cache entry or it's expired - store our fresh data
        _PROJECT_INDEX_CACHE[key] = (project_index, project_capabilities, time.time())

    # Return the freshly loaded data (no need to copy since it's not from cache)
    return project_index, project_capabilities


def invalidate_project_cache(project_dir: Path | None = None) -> None:
    """
    Invalidate the project index cache.

    Args:
        project_dir: Specific project to invalidate, or None to clear all
    """
    with _CACHE_LOCK:
        if project_dir is None:
            _PROJECT_INDEX_CACHE.clear()
            logger.debug("Cleared all project index cache entries")
        else:
            key = str(project_dir.resolve())
            if key in _PROJECT_INDEX_CACHE:
                del _PROJECT_INDEX_CACHE[key]
                logger.debug(f"Invalidated project index cache for {project_dir}")


from agents.tools_pkg import (
    AI_FACTORY_TOOLS,
    CONTEXT7_TOOLS,
    GRAPHITI_MCP_TOOLS,
    PLAYWRIGHT_TOOLS,
    create_magestic_ai_mcp_server,
    get_allowed_tools,
    get_required_mcp_servers,
    is_tools_available,
)
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import HookMatcher
from core.auth import get_sdk_env_vars, require_auth_token
from prompts_pkg.project_context import (
    detect_infra_markers,
    detect_project_capabilities,
    load_project_index,
)
from security import bash_security_hook


def _validate_custom_mcp_server(server: dict) -> bool:
    """
    Validate a custom MCP server configuration for security.

    Ensures only expected fields with valid types are present.
    Rejects configurations that could lead to command injection.

    Args:
        server: Dict representing a custom MCP server configuration

    Returns:
        True if valid, False otherwise
    """
    if not isinstance(server, dict):
        return False

    # Required fields
    required_fields = {"id", "name", "type"}
    if not all(field in server for field in required_fields):
        logger.warning(
            f"Custom MCP server missing required fields: {required_fields - server.keys()}"
        )
        return False

    # Validate field types
    if not isinstance(server.get("id"), str) or not server["id"]:
        return False
    if not isinstance(server.get("name"), str) or not server["name"]:
        return False
    # FIX: Changed from ('command', 'url') to ('command', 'http') to match actual usage
    if server.get("type") not in ("command", "http"):
        logger.warning(f"Invalid MCP server type: {server.get('type')}")
        return False

    # Allowlist of safe executable commands for MCP servers
    # Only allow known package managers and interpreters - NO shell commands
    SAFE_COMMANDS = {
        "npx",
        "npm",
        "node",
        "python",
        "python3",
        "uv",
        "uvx",
    }

    # Blocklist of dangerous shell commands that should never be allowed
    DANGEROUS_COMMANDS = {
        "bash",
        "sh",
        "cmd",
        "powershell",
        "pwsh",  # PowerShell Core
        "/bin/bash",
        "/bin/sh",
        "/bin/zsh",
        "/usr/bin/bash",
        "/usr/bin/sh",
        "zsh",
        "fish",
    }

    # Dangerous interpreter flags that allow arbitrary code execution
    # Covers Python (-e, -c, -m, -p), Node.js (--eval, --print, loaders), and general
    DANGEROUS_FLAGS = {
        "--eval",
        "-e",
        "-c",
        "--exec",
        "-m",  # Python module execution
        "-p",  # Python eval+print
        "--print",  # Node.js print
        "--input-type=module",  # Node.js ES module mode
        "--experimental-loader",  # Node.js custom loaders
        "--require",  # Node.js require injection
        "-r",  # Node.js require shorthand
    }

    # Type-specific validation
    if server["type"] == "command":
        if not isinstance(server.get("command"), str) or not server["command"]:
            logger.warning("Command-type MCP server missing 'command' field")
            return False

        # SECURITY FIX: Validate command is in safe list and not in dangerous list
        command = server.get("command", "")

        # Reject paths - commands must be bare names only (no / or \)
        # This prevents path traversal like '/custom/malicious' or './evil'
        if "/" in command or "\\" in command:
            logger.warning(
                f"Rejected command with path in MCP server: {command}. "
                f"Commands must be bare names without path separators."
            )
            return False

        if command in DANGEROUS_COMMANDS:
            logger.warning(
                f"Rejected dangerous command in MCP server: {command}. "
                f"Shell commands are not allowed for security reasons."
            )
            return False

        if command not in SAFE_COMMANDS:
            logger.warning(
                f"Rejected unknown command in MCP server: {command}. "
                f"Only allowed commands: {', '.join(sorted(SAFE_COMMANDS))}"
            )
            return False

        # Validate args is a list of strings if present
        if "args" in server:
            if not isinstance(server["args"], list):
                return False
            if not all(isinstance(arg, str) for arg in server["args"]):
                return False
            # Check for dangerous interpreter flags that allow code execution
            for arg in server["args"]:
                if arg in DANGEROUS_FLAGS:
                    logger.warning(
                        f"Rejected dangerous flag '{arg}' in MCP server args. "
                        f"Interpreter code execution flags are not allowed."
                    )
                    return False
    elif server["type"] == "http":
        if not isinstance(server.get("url"), str) or not server["url"]:
            logger.warning("HTTP-type MCP server missing 'url' field")
            return False
        # Validate headers is a dict of strings if present
        if "headers" in server:
            if not isinstance(server["headers"], dict):
                return False
            if not all(
                isinstance(k, str) and isinstance(v, str)
                for k, v in server["headers"].items()
            ):
                return False

    # Optional description must be string if present
    if "description" in server and not isinstance(server.get("description"), str):
        return False

    # Reject any unexpected fields that could be exploited
    allowed_fields = {
        "id",
        "name",
        "type",
        "command",
        "args",
        "url",
        "headers",
        "description",
    }
    unexpected_fields = set(server.keys()) - allowed_fields
    if unexpected_fields:
        logger.warning(f"Custom MCP server has unexpected fields: {unexpected_fields}")
        return False

    return True


def load_project_mcp_config(project_dir: Path) -> dict:
    """
    Load MCP configuration from project's .pfactory/.env file.

    Returns a dict of MCP-related env vars:
    - CONTEXT7_ENABLED (default: true)
    - PLAYWRIGHT_MCP_ENABLED (default: false) [also accepts legacy PUPPETEER_MCP_ENABLED]
    - AGENT_MCP_<agent>_ADD (per-agent MCP additions)
    - AGENT_MCP_<agent>_REMOVE (per-agent MCP removals)
    - CUSTOM_MCP_SERVERS (JSON array of custom server configs)

    Args:
        project_dir: Path to the project directory

    Returns:
        Dict of MCP configuration values (string values, except CUSTOM_MCP_SERVERS which is parsed JSON)
    """
    env_path = project_dir / ".pfactory" / ".env"
    if not env_path.exists():
        return {}

    config = {}
    mcp_keys = {
        "CONTEXT7_ENABLED",
        "PLAYWRIGHT_MCP_ENABLED",
        "PUPPETEER_MCP_ENABLED",  # backward compat: mapped to PLAYWRIGHT_MCP_ENABLED
    }

    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip("\"'")
                    # Include global MCP toggles
                    if key in mcp_keys:
                        config[key] = value
                    # Include per-agent MCP overrides (AGENT_MCP_<agent>_ADD/REMOVE)
                    elif key.startswith("AGENT_MCP_"):
                        config[key] = value
                    # Include custom MCP servers (parse JSON with schema validation)
                    elif key == "CUSTOM_MCP_SERVERS":
                        try:
                            parsed = json.loads(value)
                            if not isinstance(parsed, list):
                                logger.warning(
                                    "CUSTOM_MCP_SERVERS must be a JSON array"
                                )
                                config["CUSTOM_MCP_SERVERS"] = []
                            else:
                                # Validate each server and filter out invalid ones
                                valid_servers = []
                                for i, server in enumerate(parsed):
                                    if _validate_custom_mcp_server(server):
                                        valid_servers.append(server)
                                    else:
                                        logger.warning(
                                            f"Skipping invalid custom MCP server at index {i}"
                                        )
                                config["CUSTOM_MCP_SERVERS"] = valid_servers
                        except json.JSONDecodeError:
                            logger.warning(
                                f"Failed to parse CUSTOM_MCP_SERVERS JSON: {value}"
                            )
                            config["CUSTOM_MCP_SERVERS"] = []
    except Exception as e:
        logger.debug(f"Failed to load project MCP config from {env_path}: {e}")

    return config


def is_graphiti_mcp_enabled() -> bool:
    """
    Check if Graphiti MCP server integration is enabled.

    Requires GRAPHITI_MCP_URL to be set (e.g., http://localhost:3114/mcp/)
    This is separate from GRAPHITI_ENABLED which controls the Python library integration.
    """
    return bool(os.environ.get("GRAPHITI_MCP_URL"))


def get_graphiti_mcp_url() -> str:
    """Get the Graphiti MCP server URL."""
    return os.environ.get("GRAPHITI_MCP_URL", "http://localhost:3114/mcp/")


def should_use_claude_md() -> bool:
    """Check if CLAUDE.md instructions should be included in system prompt."""
    return os.environ.get("USE_CLAUDE_MD", "").lower() == "true"


def load_claude_md(project_dir: Path) -> str | None:
    """
    Load CLAUDE.md content from project root if it exists.

    Args:
        project_dir: Root directory of the project

    Returns:
        Content of CLAUDE.md if found, None otherwise
    """
    claude_md_path = project_dir / "CLAUDE.md"
    if claude_md_path.exists():
        try:
            return claude_md_path.read_text(encoding="utf-8")
        except Exception:
            return None
    return None


def create_client(
    project_dir: Path,
    spec_dir: Path,
    model: str,
    agent_type: str = "coder",
    max_thinking_tokens: int | None = None,
    output_format: dict | None = None,
    agents: dict | None = None,
    betas: list[str] | None = None,
    effort_level: str | None = None,
    fast_mode: bool = False,
    thinking_level: str | None = None,
    remote_control_session: str | None = None,
) -> ClaudeSDKClient:
    """
    Create a Claude Agent SDK client with multi-layered security.

    Uses AGENT_CONFIGS for phase-aware tool and MCP server configuration.
    Only starts MCP servers that the agent actually needs, reducing context
    window bloat and startup latency.

    Args:
        project_dir: Root directory for the project (working directory)
        spec_dir: Directory containing the spec (for settings file)
        model: Claude model to use
        agent_type: Agent type identifier from AGENT_CONFIGS
                   (e.g., 'coder', 'planner', 'qa_reviewer', 'spec_gatherer')
        max_thinking_tokens: Token budget for extended thinking (None = disabled)
                            - max: 65536 (maximum reasoning, Opus only)
                            - high: 16384 (deep thinking for QA review)
                            - medium: 4096 (moderate analysis)
                            - low: 1024 (light thinking)
                            - None: disabled (coding)
        output_format: Optional structured output format for validated JSON responses.
                      Use {"type": "json_schema", "schema": Model.model_json_schema()}
                      See: https://platform.claude.com/docs/en/agent-sdk/structured-outputs
        agents: Optional dict of subagent definitions for SDK parallel execution.
               Format: {"agent-name": {"description": "...", "prompt": "...",
                        "tools": [...], "model": "inherit"}}
               See: https://platform.claude.com/docs/en/agent-sdk/subagents
        betas: Optional list of SDK beta header strings (e.g., ["context-1m-2025-08-07"]
               for 1M context window). Use get_phase_model_betas() to compute from config.
        effort_level: Optional effort level for adaptive thinking models (e.g., "low",
                     "medium", "high"). When set, injected as CLAUDE_CODE_EFFORT_LEVEL
                     env var for the SDK subprocess. Only meaningful for models that
                     support adaptive thinking (e.g., Opus 4.6).
        fast_mode: Enable Fast Mode for faster Opus 4.6 output. When True, enables
                  the "user" setting source so the CLI reads fastMode from
                  ~/.claude/settings.json.
        thinking_level: Optional thinking level ("none", "low", "medium", "high").
                       When provided, drives the SDK-native `thinking` parameter
                       via phase_config.thinking_config_for(). On Opus 4.7 this
                       enables {"type": "adaptive"}; on other models it maps
                       to {"type": "enabled", "budget_tokens": N}.
                       Falls back to max_thinking_tokens path when None.

    Returns:
        Configured ClaudeSDKClient

    Raises:
        ValueError: If agent_type is not found in AGENT_CONFIGS

    Security layers (defense in depth):
    1. Sandbox - OS-level bash command isolation prevents filesystem escape
    2. Permissions - File operations restricted to project_dir only
    3. Security hooks - Bash commands validated against an allowlist
       (see security.py for ALLOWED_COMMANDS)
    4. Tool filtering - Each agent type only sees relevant tools (prevents misuse)
    """
    oauth_token = require_auth_token()
    # Ensure SDK can access it via its expected env var
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token

    # Collect env vars to pass to SDK (ANTHROPIC_BASE_URL, etc.)
    sdk_env = get_sdk_env_vars()

    # Inject effort level for adaptive thinking models (e.g., Opus 4.6)
    if effort_level:
        sdk_env["CLAUDE_CODE_EFFORT_LEVEL"] = effort_level

    # Credential broker (epic #62): merge any task-scoped cloud credentials into
    # the agent's environment. Off by default and fully fault-tolerant — only
    # acts when egress is explicitly enabled for the task (see #8).
    try:
        from pfactory_secrets.broker import inject_task_credentials

        inject_task_credentials(sdk_env, project_dir, spec_dir)
    except Exception:  # noqa: BLE001 - never let credential wiring break the agent
        pass

    # Fast mode requires the CLI to read "fastMode" from user settings.
    # The SDK default (setting_sources=None) passes --setting-sources "" which
    # blocks ALL filesystem settings. We must explicitly enable "user" source
    # so the CLI reads ~/.claude/settings.json where fastMode: true lives.
    if fast_mode:
        try:
            from core.fast_mode import ensure_fast_mode_in_user_settings
            ensure_fast_mode_in_user_settings()
        except ImportError:
            logger.warning("Fast mode requested but core.fast_mode module not available")
        logger.info("[Fast Mode] ACTIVE — will enable user setting source for fastMode")
        print(
            "[Fast Mode] ACTIVE — enabling user settings source for CLI to read fastMode"
        )
    else:
        logger.info("[Fast Mode] inactive — not requested for this client")

    # Check if custom pfactory tools are available
    pfactory_tools_enabled = is_tools_available()

    # Load project capabilities for dynamic MCP tool selection
    # This enables context-aware tool injection based on project type
    # Uses caching to avoid reloading on every create_client() call
    project_index, project_capabilities = _get_cached_project_data(project_dir)

    # Filesystem-only scan for infra markers (k8s/, terraform/, *.bicep, ...).
    # Cheap enough that we recompute every spawn rather than cache —
    # operators often add infra directories mid-task and expect immediate effect.
    infra_markers = detect_infra_markers(project_dir)

    # Load per-project MCP configuration from .pfactory/.env
    mcp_config = load_project_mcp_config(project_dir)

    # Get allowed tools using phase-aware configuration
    # This respects AGENT_CONFIGS and only includes tools the agent needs
    # Also respects per-project MCP configuration
    allowed_tools_list = get_allowed_tools(
        agent_type,
        project_capabilities,
        mcp_config,
    )

    # Get required MCP servers for this agent type
    # This is the key optimization - only start servers the agent needs
    # Now also respects per-project MCP configuration AND the catalog of
    # default infra servers (github/k8s/aws/azure) which auto-enable when
    # markers + credentials line up.
    required_servers = get_required_mcp_servers(
        agent_type,
        project_capabilities,
        mcp_config,
        infra_markers,
    )

    # Check if Graphiti MCP is enabled (already filtered by get_required_mcp_servers)
    graphiti_mcp_enabled = "graphiti" in required_servers

    # Determine browser tools for permissions (already in allowed_tools_list)
    browser_tools_permissions = []
    if "playwright" in required_servers:
        browser_tools_permissions = PLAYWRIGHT_TOOLS

    # Create comprehensive security settings
    # Note: Using both relative paths ("./**") and absolute paths to handle
    # cases where Claude uses absolute paths for file operations
    project_path_str = str(project_dir.resolve())
    spec_path_str = str(spec_dir.resolve())

    # Detect if we're running in a worktree and get the original project directory
    # Worktrees are located in either:
    # - .pfactory/worktrees/tasks/{spec-name}/ (new location)
    # - .worktrees/{spec-name}/ (legacy location)
    # When running in a worktree, we need to allow access to both the worktree
    # and the original project's .pfactory/ directory for spec files
    original_project_permissions = []
    resolved_project_path = project_dir.resolve()

    worktree_markers = [
        "/.pfactory/worktrees/tasks/",  # Spec/task worktrees
        "/.pfactory/github/pr/worktrees/",  # PR review worktrees
        "/.worktrees/",  # Legacy worktree location
    ]
    project_path_posix = str(resolved_project_path).replace("\\", "/")

    for marker in worktree_markers:
        if marker in project_path_posix:
            original_project_str = project_path_posix.rsplit(marker, 1)[0]
            original_project_dir = Path(original_project_str)

            permission_ops = ["Read", "Write", "Edit", "Glob", "Grep"]
            dirs_to_permit = [
                original_project_dir / ".pfactory",
                original_project_dir / ".worktrees",  # Legacy support
            ]

            for dir_path in dirs_to_permit:
                if dir_path.exists():
                    path_str = str(dir_path.resolve())
                    original_project_permissions.extend(
                        [f"{op}({path_str}/**)" for op in permission_ops]
                    )
            break

    security_settings = {
        "sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True},
        "enabledPlugins": {},  # Explicitly disable ALL plugins to prevent hook errors
        "permissions": {
            "defaultMode": "bypassPermissions",  # Bypass all permission prompts for headless operation
            "allow": [
                # Allow all file operations within the project directory
                # Include both relative (./**) and absolute paths for compatibility
                "Read(./**)",
                "Write(./**)",
                "Edit(./**)",
                "Glob(./**)",
                "Grep(./**)",
                # Also allow absolute paths (Claude sometimes uses full paths)
                f"Read({project_path_str}/**)",
                f"Write({project_path_str}/**)",
                f"Edit({project_path_str}/**)",
                f"Glob({project_path_str}/**)",
                f"Grep({project_path_str}/**)",
                # Allow spec directory explicitly (needed when spec is in worktree)
                f"Read({spec_path_str}/**)",
                f"Write({spec_path_str}/**)",
                f"Edit({spec_path_str}/**)",
                # Allow original project's .pfactory/ and .worktrees/ directories
                # when running in a worktree (fixes permission errors)
                *original_project_permissions,
                # Bash permission granted here, but actual commands are validated
                # by the bash_security_hook (see security.py for allowed commands)
                "Bash(*)",
                # Allow web tools for documentation and research
                "WebFetch(*)",
                "WebSearch(*)",
                # Allow MCP tools based on required servers
                # Format: tool_name(*) allows all arguments
                *(
                    [f"{tool}(*)" for tool in CONTEXT7_TOOLS]
                    if "context7" in required_servers
                    else []
                ),
                *(
                    [f"{tool}(*)" for tool in GRAPHITI_MCP_TOOLS]
                    if graphiti_mcp_enabled
                    else []
                ),
                *[f"{tool}(*)" for tool in browser_tools_permissions],
                # Magestic AI MCP tools for build management
                *(
                    [f"{tool}(*)" for tool in AI_FACTORY_TOOLS]
                    if "pfactory" in required_servers
                    else []
                ),
            ],
        },
    }

    # Write settings to a file in the project directory
    # Use headless settings to avoid hook errors in subprocess
    settings_file = Path.home() / ".claude" / "settings-headless.json"
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    with open(settings_file, "w") as f:
        json.dump(security_settings, f, indent=2)

    print(f"Security settings: {settings_file}")
    print("   - Sandbox enabled (OS-level bash isolation)")
    print(f"   - Filesystem restricted to: {project_dir.resolve()}")
    if original_project_permissions:
        print("   - Worktree permissions: granted for original project directories")
    print("   - Bash commands restricted to allowlist")
    if max_thinking_tokens:
        thinking_info = f"{max_thinking_tokens:,} tokens"
        if effort_level:
            thinking_info += f" + effort={effort_level}"
        if fast_mode:
            thinking_info += " + fast mode"
        print(f"   - Extended thinking: {thinking_info}")
    else:
        print("   - Extended thinking: disabled")

    # Build list of MCP servers for display based on required_servers
    mcp_servers_list = []
    if "context7" in required_servers:
        mcp_servers_list.append("context7 (documentation)")
    if "playwright" in required_servers:
        mcp_servers_list.append("playwright (browser automation)")
    if graphiti_mcp_enabled:
        mcp_servers_list.append("graphiti-memory (knowledge graph)")
    if "pfactory" in required_servers and pfactory_tools_enabled:
        mcp_servers_list.append(f"pfactory ({agent_type} tools)")
    if mcp_servers_list:
        print(f"   - MCP servers: {', '.join(mcp_servers_list)}")
    else:
        print("   - MCP servers: none (minimal configuration)")

    # Show detected project capabilities for QA agents
    if agent_type in ("qa_reviewer", "qa_fixer") and any(project_capabilities.values()):
        caps = [
            k.replace("is_", "").replace("has_", "")
            for k, v in project_capabilities.items()
            if v
        ]
        print(f"   - Project capabilities: {', '.join(caps)}")
    print()

    # Configure MCP servers - ONLY start servers that are required
    # This is the key optimization to reduce context bloat and startup latency
    mcp_servers = {}

    if "context7" in required_servers:
        mcp_servers["context7"] = {
            "command": "npx",
            "args": ["-y", "@upstash/context7-mcp"],
        }

    if "playwright" in required_servers:
        # Playwright for web frontends (headless Chromium)
        mcp_servers["playwright"] = {
            "command": "npx",
            "args": [
                "@playwright/mcp@latest",
                "--headless",
                "--browser", "chromium",
                "--viewport-size", "1280x720",
            ],
        }

    # Graphiti MCP server for knowledge graph memory
    if graphiti_mcp_enabled:
        mcp_servers["graphiti-memory"] = {
            "type": "http",
            "url": get_graphiti_mcp_url(),
        }

    # Add custom pfactory MCP server if required and available
    if "pfactory" in required_servers and pfactory_tools_enabled:
        magestic_ai_mcp_server = create_magestic_ai_mcp_server(spec_dir, project_dir)
        if magestic_ai_mcp_server:
            mcp_servers["pfactory"] = magestic_ai_mcp_server

    # ========== Catalog-driven servers (github, kubernetes, aws, azure, ...) =====
    # ``get_required_mcp_servers`` already filtered the catalog by agent +
    # markers + credentials. Here we just materialize the launcher config.
    # ImportError is tolerated so a broken framework module doesn't take down
    # the whole agent boot path; failures show up as missing tools, not a
    # subprocess crash.
    try:
        from agents.tools_pkg.mcp_catalog import get_catalog_entry
        from core.mcp_credentials import get_credential_status

        for server_id in required_servers:
            if server_id in mcp_servers:
                continue  # already configured above
            entry = get_catalog_entry(server_id)
            if entry is None:
                continue
            creds = (
                get_credential_status(entry.credential_provider)
                if entry.credential_provider
                else None
            )
            mcp_servers[entry.id] = entry.build_server_config(creds, read_only=True)
    except ImportError as exc:
        print(f"   - MCP catalog unavailable ({exc}); catalog servers skipped")

    # Add custom MCP servers from project config
    custom_servers = mcp_config.get("CUSTOM_MCP_SERVERS", [])
    for custom in custom_servers:
        server_id = custom.get("id")
        if not server_id:
            continue
        # Only include if agent has it in their effective server list
        if server_id not in required_servers:
            continue
        server_type = custom.get("type", "command")
        if server_type == "command":
            mcp_servers[server_id] = {
                "command": custom.get("command", "npx"),
                "args": custom.get("args", []),
            }
        elif server_type == "http":
            server_config = {
                "type": "http",
                "url": custom.get("url", ""),
            }
            if custom.get("headers"):
                server_config["headers"] = custom["headers"]
            mcp_servers[server_id] = server_config

    # Build system prompt
    # Static content (CLAUDE.md) is placed before the dynamic base instructions so
    # the Anthropic API's automatic prompt caching can reuse the prefix hash across
    # sessions.  build_cached_system_str keeps the static portion byte-identical;
    # any change there would invalidate the cached prefix.
    # For direct Anthropic API callers (not SDK sessions) use
    # core.cache.build_cached_system_blocks instead — it attaches an explicit
    # cache_control marker so the 5-min (or 1-h) KV cache is guaranteed.
    from core.cache import build_cached_system_str

    _base_instructions = (
        f"You are an expert full-stack developer building production-quality software. "
        f"Your working directory is: {project_dir.resolve()}\n"
        f"Your filesystem access is RESTRICTED to this directory only. "
        f"Use relative paths (starting with ./) for all file operations. "
        f"Never use absolute paths or try to access files outside your working directory.\n\n"
        f"You follow existing code patterns, write clean maintainable code, and verify "
        f"your work through thorough testing. You communicate progress through Git commits "
        f"and build-progress.txt updates."
    )

    # Include CLAUDE.md if enabled and present
    _claude_md_content: str | None = None
    if should_use_claude_md():
        _claude_md_content = load_claude_md(project_dir)
        if _claude_md_content:
            print("   - CLAUDE.md: included in system prompt")
        else:
            print("   - CLAUDE.md: not found in project root")
    else:
        print("   - CLAUDE.md: disabled by project settings")
    print()

    # Collapse into a single string for ClaudeAgentOptions.system_prompt.
    # Static prefix (CLAUDE.md) comes first so the server-side cache prefix hash
    # covers the largest stable portion of the prompt.
    base_prompt = build_cached_system_str(
        base_instructions=_base_instructions,
        claude_md_content=_claude_md_content,
        model=model,
        project_dir=str(project_dir.resolve()),
    )

    # Build options dict, conditionally including output_format
    options_kwargs: dict[str, Any] = {
        "model": model,
        "system_prompt": base_prompt,
        "allowed_tools": allowed_tools_list,
        "mcp_servers": mcp_servers,
        "hooks": {
            "PreToolUse": [
                HookMatcher(matcher="Bash", hooks=[bash_security_hook]),
            ],
        },
        "max_turns": 1000,
        "cwd": str(project_dir.resolve()),
        "settings": str(settings_file.resolve()),
        "env": sdk_env,  # Pass ANTHROPIC_BASE_URL etc. to subprocess
        "permission_mode": "bypassPermissions",  # Bypass all permission prompts for headless execution
        "max_buffer_size": 10
        * 1024
        * 1024,  # 10MB buffer (default: 1MB) - fixes large tool results
        # Enable file checkpointing to track file read/write state across tool calls
        # This prevents "File has not been read yet" errors in recovery sessions
        "enable_file_checkpointing": True,
    }

    # Fast mode: enable user setting source so CLI reads fastMode from
    # ~/.claude/settings.json. Without this, the SDK's default --setting-sources ""
    # blocks all filesystem settings and the CLI never sees fastMode: true.
    if fast_mode:
        options_kwargs["setting_sources"] = ["user"]

    # Add structured output format if specified
    # See: https://platform.claude.com/docs/en/agent-sdk/structured-outputs
    if output_format:
        options_kwargs["output_format"] = output_format

    # Add subagent definitions if specified
    # See: https://platform.claude.com/docs/en/agent-sdk/subagents
    if agents:
        options_kwargs["agents"] = agents

    # Issue #7 — Choose between SDK-native `thinking` config and legacy
    # `max_thinking_tokens` path. The helper returns None when there's no
    # reason to use the new shape, in which case we preserve the legacy
    # behaviour exactly so the 11+ call sites that don't pass `thinking_level`
    # see no change.
    from phase_config import interleaved_thinking_betas_for, thinking_config_for

    _level = thinking_level or (
        "high" if (max_thinking_tokens or 0) >= 16384
        else "medium" if max_thinking_tokens
        else "none"
    )
    _thinking_param = thinking_config_for(
        model_id=model,
        thinking_level=_level,
        explicit_budget=max_thinking_tokens,
    )
    if _thinking_param is not None:
        options_kwargs["thinking"] = _thinking_param  # type: ignore[typeddict-item]
    else:
        options_kwargs["max_thinking_tokens"] = max_thinking_tokens

    # Add beta headers — merge caller-provided (e.g. context-1m) with the
    # interleaved-thinking beta when the (model, agent_type) pair qualifies.
    # TODO: drop the `type: ignore` once the SDK's SdkBeta Literal includes
    # interleaved-thinking-2025-05-14 (currently only context-1m-2025-08-07).
    _all_betas: list[str] = list(betas or []) + interleaved_thinking_betas_for(
        model_id=model,
        agent_type=agent_type,
    )
    if _all_betas:
        options_kwargs["betas"] = _all_betas  # type: ignore[arg-type]

    # Remote Control session naming (#149). The Claude Agent SDK forwards
    # ``extra_args`` to the underlying ``claude`` CLI, so passing
    # ``{"remote-control": "PFactory: <spec-id>"}`` makes the spawned
    # claude register a Remote Control session under that name. The
    # session shows up in the user's claude.ai/code session list and the
    # mobile app, so they can drive the same conversation from anywhere.
    #
    # Caller is responsible for env scrubbing (CLAUDE_CODE_OAUTH_TOKEN,
    # ANTHROPIC_AUTH_TOKEN) so the subprocess falls back to the
    # full-scope ~/.claude/.credentials.json. See agent_service.py for
    # that scrubbing — it must happen at subprocess spawn time, not here.
    if remote_control_session:
        existing_extra = dict(options_kwargs.get("extra_args") or {})
        existing_extra["remote-control"] = remote_control_session
        options_kwargs["extra_args"] = existing_extra  # type: ignore[typeddict-item]

    return ClaudeSDKClient(options=ClaudeAgentOptions(**options_kwargs))
