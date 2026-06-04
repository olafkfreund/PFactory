"""Plan MCP tools — let any host hand a plan to PFactory (#2, Phase F).

Six tools (`mcp__pfactory__plan_*`) that wrap the framework-agnostic callables in
:mod:`plan.agent_api` (which drive the shared ``plan.service.SERVICE``):

  - plan_ingest   — ingest inline text or a file path → new session
  - plan_process  — run the pipeline (enrich · feasibility · review)
  - plan_status   — status + board column + gate result
  - plan_get      — fuller view (review findings + cost/effort estimates)
  - plan_list     — list sessions
  - plan_approve  — record human approval

Registered ONLY from the standalone MCP server, so Claude Code / Antigravity /
Codex can call them over MCP. No GitHub/AIFactory side-effects — emission stays
behind the human-approved path.
"""

from __future__ import annotations

import json
from typing import Any

try:
    from claude_agent_sdk import tool

    SDK_TOOLS_AVAILABLE = True
except ImportError:
    SDK_TOOLS_AVAILABLE = False
    tool = None  # type: ignore[assignment]


def _ok(data: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(data, indent=2, default=str)}]}


def _err(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": f"ERROR: {message}"}], "is_error": True}


def create_plan_tools() -> list:
    """Create the PFactory plan tools (empty list if the SDK isn't installed)."""
    if not SDK_TOOLS_AVAILABLE:
        return []

    from plan import agent_api

    tools: list = []

    @tool(
        "plan_ingest",
        "Hand a plan to PFactory. Provide inline `text` (markdown / Gherkin / "
        "EARS acceptance criteria) OR a file `path` (pdf/docx/md). Optional "
        "`category`/`template` record the intake choice. Returns the session_id "
        "and board_state. Call plan_process next.",
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Inline plan text"},
                "path": {"type": "string", "description": "Path to a plan document"},
                "title": {"type": "string"},
                "category": {"type": "string", "description": "Intake category (see plan_categories)"},
                "template": {"type": "string", "description": "Selected template (its policy is enforced)"},
            },
        },
    )
    async def plan_ingest(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return _ok(agent_api.plan_ingest(
                text=args.get("text"), path=args.get("path"),
                title=args.get("title"), category=args.get("category", ""),
                template=args.get("template", ""),
            ))
        except Exception as exc:  # noqa: BLE001
            return _err(str(exc))

    tools.append(plan_ingest)

    @tool(
        "plan_process",
        "Run PFactory's pipeline for a session: enrich (cloud + provider MCPs) → "
        "feasibility (cost/time/access) → decompose → review gates. Returns the "
        "review summary with cited findings + cost/effort estimates.",
        {"type": "object", "properties": {"session_id": {"type": "string"}},
         "required": ["session_id"]},
    )
    async def plan_process(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return _ok(agent_api.plan_process(args["session_id"]))
        except Exception as exc:  # noqa: BLE001
            return _err(str(exc))

    tools.append(plan_process)

    @tool(
        "plan_status",
        "Lightweight status for a session: status, board column "
        "(backlog/in_progress/ai_review/human_review/done), and gate result.",
        {"type": "object", "properties": {"session_id": {"type": "string"}},
         "required": ["session_id"]},
    )
    async def plan_status(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return _ok(agent_api.plan_status(args["session_id"]))
        except Exception as exc:  # noqa: BLE001
            return _err(str(exc))

    tools.append(plan_status)

    @tool(
        "plan_get",
        "Fuller view of a session: summary + review lenses/findings (with "
        "citations) + cost/effort estimates.",
        {"type": "object", "properties": {"session_id": {"type": "string"}},
         "required": ["session_id"]},
    )
    async def plan_get(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return _ok(agent_api.plan_get(args["session_id"]))
        except Exception as exc:  # noqa: BLE001
            return _err(str(exc))

    tools.append(plan_get)

    @tool("plan_list", "List all PFactory plan sessions (summaries).",
          {"type": "object", "properties": {}})
    async def plan_list(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return _ok(agent_api.plan_list())
        except Exception as exc:  # noqa: BLE001
            return _err(str(exc))

    tools.append(plan_list)

    @tool(
        "plan_categories",
        "List the intake categories (product/software/feature/hosting/"
        "infrastructure/testing/cicd/…) and the templates in each. Call this to "
        "discover what to pass as `category`/`template` on plan_ingest. Selecting "
        "a template enforces its policy (required tags/regions/IAM) at review.",
        {"type": "object", "properties": {}},
    )
    async def plan_categories(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return _ok(agent_api.plan_categories())
        except Exception as exc:  # noqa: BLE001
            return _err(str(exc))

    tools.append(plan_categories)

    @tool(
        "plan_approve",
        "Record human approval for a session (requires the AI gates to have "
        "passed). Pass the approver's identity. Unlocks emission.",
        {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "approver": {"type": "string"},
                "feedback": {"type": "string"},
            },
            "required": ["session_id", "approver"],
        },
    )
    async def plan_approve(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return _ok(agent_api.plan_approve(
                args["session_id"], approver=args["approver"],
                feedback=args.get("feedback"),
            ))
        except Exception as exc:  # noqa: BLE001
            return _err(str(exc))

    tools.append(plan_approve)

    return tools
