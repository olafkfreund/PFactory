"""
Session Memory Tools
====================

Tools for recording and retrieving session memory, including discoveries,
gotchas, and patterns.
"""

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from memory import scratchpad, trust
from memory.novelty import drop_repeats
from memory.retention import (
    CAPS,
    confirm,
    escape_headings,
    gotchas_context,
    session_type,
)

try:
    from claude_agent_sdk import tool

    SDK_TOOLS_AVAILABLE = True
except ImportError:
    SDK_TOOLS_AVAILABLE = False
    tool = None

# The gotchas window shown by get_session_context.
GOTCHA_CONTEXT_CHARS = 1000


def _dead_end_parts(memory_dir: Path) -> list[str]:
    dead_ends = scratchpad.dead_end_context(memory_dir)
    if not dead_ends:
        return []
    return ["\n## Dead ends (already tried, do not retry)", dead_ends]


def create_memory_tools(
    spec_dir: Path | Callable[[], Path],
    project_dir: Path | Callable[[], Path],
    memory_session_type: str | None = None,
) -> list:
    """
    Create session memory tools.

    Accepts either a fixed Path or a callable returning Path (Issue #10).

    Args:
        spec_dir: Path or Callable[[], Path] to the spec directory
        project_dir: Path or Callable[[], Path] to the project root

    Returns:
        List of memory tool functions
    """
    if not SDK_TOOLS_AVAILABLE:
        return []

    get_spec_dir: Callable[[], Path] = spec_dir if callable(spec_dir) else (lambda p=spec_dir: p)

    tools = []

    # -------------------------------------------------------------------------
    # Tool: record_discovery
    # -------------------------------------------------------------------------
    @tool(
        "record_discovery",
        "Record a codebase discovery to session memory. Use this when you learn something important about the codebase.",
        {"file_path": str, "description": str, "category": str},
    )
    async def record_discovery(args: dict[str, Any]) -> dict[str, Any]:
        """Record a discovery to the codebase map."""
        file_path = args["file_path"]
        description = args["description"]
        category = args.get("category", "general")

        memory_dir = get_spec_dir() / "memory"
        memory_dir.mkdir(exist_ok=True)

        codebase_map_file = memory_dir / "codebase_map.json"

        try:
            # Load existing map or create new
            if codebase_map_file.exists():
                with open(codebase_map_file) as f:
                    codebase_map = json.load(f)
            else:
                codebase_map = {
                    "discovered_files": {},
                    "last_updated": None,
                }

            # Add or update the discovery
            codebase_map["discovered_files"][file_path] = {
                "description": description,
                "category": category,
                "discovered_at": datetime.now(UTC).isoformat(),
            }
            codebase_map["last_updated"] = datetime.now(UTC).isoformat()

            with open(codebase_map_file, "w") as f:
                json.dump(codebase_map, f, indent=2)

            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Recorded discovery for '{file_path}': {description}",
                    }
                ]
            }

        except Exception as e:
            return {"content": [{"type": "text", "text": f"Error recording discovery: {e}"}]}

    tools.append(record_discovery)

    # -------------------------------------------------------------------------
    # Tool: record_gotcha
    # -------------------------------------------------------------------------
    gotchas_written: dict[Path, int] = {}  # per spec: the server resolves it per call

    @tool(
        "record_gotcha",
        "Record a gotcha or pitfall to avoid. Use this when you encounter something that future sessions should know.",
        {"gotcha": str, "context": str},
    )
    async def record_gotcha(args: dict[str, Any]) -> dict[str, Any]:
        """Record a gotcha to session memory."""
        gotcha = args["gotcha"]
        context = args.get("context", "")

        memory_dir = get_spec_dir() / "memory"
        memory_dir.mkdir(exist_ok=True)

        gotchas_file = memory_dir / "gotchas.md"

        try:
            timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")

            body = gotcha
            if context:
                body += f"\n\n_Context: {context}_"

            # Escape header-looking lines (injective: backslashes shift by one)
            # so the split below cannot cut inside an entry.
            body = escape_headings(body)

            seen: set[str] = set()
            if gotchas_file.exists():
                tail = gotchas_file.read_text()[-GOTCHA_CONTEXT_CHARS:]
                seen = {
                    trust.strip_marker(segment.strip())
                    for segment in re.split(
                        r"(?m)^## \[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\]\r?\n", tail
                    )[1:]
                }
            rejected: list[str] = []
            if not drop_repeats([body], seen, "gotchas.md", rejected):
                confirm(memory_dir, [*rejected, *(k + trust.UNVERIFIED for k in rejected)])
                return {"content": [{"type": "text", "text": f"Gotcha already recorded: {gotcha}"}]}

            if gotchas_written.get(memory_dir, 0) >= CAPS[session_type(memory_session_type)]:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": "Gotcha cap reached; new gotcha not recorded.",
                        }
                    ]
                }

            if trust.tainted():
                body += trust.UNVERIFIED
            entry = f"\n## [{timestamp}]\n{body}\n"

            with gotchas_file.open("a") as f:
                if not gotchas_file.exists() or gotchas_file.stat().st_size == 0:
                    f.write("# Gotchas & Pitfalls\n\nThings to watch out for in this codebase.\n")
                f.write(entry)

            gotchas_written[memory_dir] = gotchas_written.get(memory_dir, 0) + 1
            confirm(memory_dir, [body.strip()])

            return {"content": [{"type": "text", "text": f"Recorded gotcha: {gotcha}"}]}

        except Exception as e:
            return {"content": [{"type": "text", "text": f"Error recording gotcha: {e}"}]}

    tools.append(record_gotcha)

    # -------------------------------------------------------------------------
    # record_dead_end tool
    # -------------------------------------------------------------------------
    dead_ends_written: dict[Path, int] = {}  # new dead ends per spec (cap)

    @tool(
        "record_dead_end",
        "Record an approach you tried and abandoned because it does not work, "
        "so later sessions do not retry it.",
        {"approach": str, "reason": str},
    )
    async def record_dead_end(args: dict[str, Any]) -> dict[str, Any]:
        """Record an abandoned approach (counters only, no LLM)."""
        approach = args["approach"]
        reason = args.get("reason", "")
        memory_dir = get_spec_dir() / "memory"

        try:
            memory_dir.mkdir(exist_ok=True)
            known = scratchpad.fingerprint(approach, "dead_end") in scratchpad.read(memory_dir).get(
                "dead_end", {}
            )
            if (
                not known
                and dead_ends_written.get(memory_dir, 0) >= CAPS[session_type(memory_session_type)]
            ):
                text = "Dead end cap reached; new dead end not recorded."
            elif scratchpad.record_dead_end(
                memory_dir, approach, reason, untrusted=trust.tainted()
            ):
                if not known:
                    dead_ends_written[memory_dir] = dead_ends_written.get(memory_dir, 0) + 1
                text = f"Recorded dead end: {approach}"
            else:
                text = "Dead end not recorded."
            return {"content": [{"type": "text", "text": text}]}
        except (OSError, KeyError, TypeError, ValueError) as e:
            return {"content": [{"type": "text", "text": f"Error recording dead end: {e}"}]}

    tools.append(record_dead_end)

    # -------------------------------------------------------------------------
    # Tool: get_session_context
    # -------------------------------------------------------------------------
    @tool(
        "get_session_context",
        "Get context from previous sessions including discoveries, gotchas, and patterns.",
        {},
    )
    async def get_session_context(args: dict[str, Any]) -> dict[str, Any]:
        """Get accumulated session context."""
        memory_dir = get_spec_dir() / "memory"

        if not memory_dir.exists():
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "No session memory found. This appears to be the first session.",
                    }
                ]
            }

        result_parts = []

        # Load codebase map
        codebase_map_file = memory_dir / "codebase_map.json"
        if codebase_map_file.exists():
            try:
                with open(codebase_map_file) as f:
                    codebase_map = json.load(f)

                discoveries = codebase_map.get("discovered_files", {})
                if discoveries:
                    result_parts.append("## Codebase Discoveries")
                    for path, info in list(discoveries.items())[:20]:  # Limit to 20
                        desc = info.get("description", "No description")
                        result_parts.append(f"- `{path}`: {desc}")
            except Exception:
                pass

        # Load gotchas
        gotchas_file = memory_dir / "gotchas.md"
        if gotchas_file.exists():
            try:
                content = gotchas_file.read_text()
                if content.strip():
                    result_parts.append("\n## Gotchas")
                    result_parts.append(gotchas_context(memory_dir, content, GOTCHA_CONTEXT_CHARS))
            except Exception:
                pass

        # Load dead ends (approaches already tried and abandoned)
        result_parts.extend(_dead_end_parts(memory_dir))

        # Load patterns
        patterns_file = memory_dir / "patterns.md"
        if patterns_file.exists():
            try:
                content = patterns_file.read_text()
                if content.strip():
                    result_parts.append("\n## Patterns")
                    result_parts.append(content[-1000:] if len(content) > 1000 else content)
            except Exception:
                pass

        if not result_parts:
            return {"content": [{"type": "text", "text": "No session context available yet."}]}

        return {"content": [{"type": "text", "text": "\n".join(result_parts)}]}

    tools.append(get_session_context)

    # -------------------------------------------------------------------------
    # Tool: test_memory_integration
    # -------------------------------------------------------------------------
    @tool(
        "test_memory_integration",
        "Test Graphiti memory integration with a simple save/retrieve cycle",
        {},
    )
    async def test_memory_integration(args: dict[str, Any]) -> dict[str, Any]:
        """
        Test Graphiti memory system by saving and retrieving test data.

        This performs a basic health check:
        1. Checks if Graphiti is enabled
        2. Saves a test episode to the graph
        3. Verifies the connection is working

        Returns a status message indicating whether the memory system is functioning.
        """
        try:
            # Check if Graphiti integration is available
            try:
                from integrations.graphiti.memory import (
                    is_graphiti_enabled,
                    test_graphiti_connection,
                )
            except ImportError:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": "❌ Graphiti integration not available. Install required packages.",
                        }
                    ]
                }

            # Check if Graphiti is enabled
            if not is_graphiti_enabled():
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": "❌ Graphiti is not enabled. Set GRAPHITI_ENABLED=true in environment.",
                        }
                    ]
                }

            # Test connection
            success, message = await test_graphiti_connection()

            if success:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"✅ Graphiti memory integration is working!\n\n{message}",
                        }
                    ]
                }
            else:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"❌ Graphiti memory test failed:\n\n{message}",
                        }
                    ]
                }

        except Exception as e:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"❌ Error testing Graphiti memory: {e!s}",
                    }
                ]
            }

    tools.append(test_memory_integration)

    return tools
