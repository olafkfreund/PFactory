"""
Subtask Management Tools
========================

Tools for managing subtask status in test_plan.json.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from claude_agent_sdk import tool

    SDK_TOOLS_AVAILABLE = True
except ImportError:
    SDK_TOOLS_AVAILABLE = False
    tool = None


def create_subtask_tools(
    spec_dir: Path | Callable[[], Path],
    project_dir: Path | Callable[[], Path],
) -> list:
    """
    Create subtask management tools.

    Accepts either a fixed Path (in-process callers — agent sessions own a
    specific spec for their lifetime) or a callable returning Path (standalone
    MCP server callers — the active spec is resolved per tool call via env).
    Issue #10.

    Args:
        spec_dir: Path or Callable[[], Path] to the spec directory
        project_dir: Path or Callable[[], Path] to the project root

    Returns:
        List of subtask tool functions
    """
    if not SDK_TOOLS_AVAILABLE:
        return []

    # Normalise Path -> lambda once at factory build time; tool handlers
    # invoke get_spec_dir() per call so the standalone server picks up
    # PFACTORY_SPEC_DIR changes between calls.
    get_spec_dir: Callable[[], Path] = spec_dir if callable(spec_dir) else (lambda p=spec_dir: p)

    tools = []

    # -------------------------------------------------------------------------
    # Tool: update_subtask_status
    # -------------------------------------------------------------------------
    @tool(
        "update_subtask_status",
        "Update the status of a subtask in test_plan.json. Use this when completing or starting a subtask.",
        {"subtask_id": str, "status": str, "notes": str},
    )
    async def update_subtask_status(args: dict[str, Any]) -> dict[str, Any]:
        """Update subtask status in the implementation plan."""
        subtask_id = args["subtask_id"]
        status = args["status"]
        notes = args.get("notes", "")

        valid_statuses = ["pending", "in_progress", "completed", "failed"]
        if status not in valid_statuses:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Error: Invalid status '{status}'. Must be one of: {valid_statuses}",
                    }
                ]
            }

        plan_file = get_spec_dir() / "test_plan.json"
        if not plan_file.exists():
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "Error: test_plan.json not found",
                    }
                ]
            }

        try:
            with open(plan_file) as f:
                plan = json.load(f)

            # Find and update the subtask
            subtask_found = False
            for phase in plan.get("phases", []):
                for subtask in phase.get("subtasks", []):
                    if subtask.get("id") == subtask_id:
                        subtask["status"] = status
                        if notes:
                            subtask["notes"] = notes
                        subtask["updated_at"] = datetime.now(UTC).isoformat()
                        subtask_found = True
                        break
                if subtask_found:
                    break

            if not subtask_found:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Error: Subtask '{subtask_id}' not found in implementation plan",
                        }
                    ]
                }

            # Update plan metadata
            plan["last_updated"] = datetime.now(UTC).isoformat()

            with open(plan_file, "w") as f:
                json.dump(plan, f, indent=2)

            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Successfully updated subtask '{subtask_id}' to status '{status}'",
                    }
                ]
            }

        except json.JSONDecodeError as e:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Error: Invalid JSON in test_plan.json: {e}",
                    }
                ]
            }
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Error updating subtask status: {e}"}]}

    tools.append(update_subtask_status)

    return tools
