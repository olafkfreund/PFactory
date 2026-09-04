"""Plan MCP tools — let any host hand a plan to PFactory (#2, Phase F).

Eight tools (`mcp__pfactory__plan_*`) that call the running PFactory server over
the scope-gated ``/api/mcp-stdio/*`` proxy (#694):

  - plan_ingest   — ingest inline text or a file path → new session
  - plan_process  — run the pipeline (enrich · feasibility · review)
  - plan_status   — status + board column + gate result
  - plan_get      — fuller view (review findings + cost/effort estimates)
  - plan_list     — list sessions
  - plan_approve  — record human approval

Registered ONLY from the standalone MCP server, so Claude Code / Antigravity /
Codex can call them over MCP. No GitHub/AIFactory side-effects — emission stays
behind the human-approved path.

WHY HTTP AND NOT ``plan.agent_api`` (#694). These tools used to call
``agent_api``, which drives an in-process ``plan.service.SERVICE``. The MCP
server runs as a **subprocess on the caller's machine**, so that store was a
second, private PlanService: a plan handed to PFactory over MCP never reached
the configured server. It did not appear in the portal, did not appear in
``GET /api/plan/sessions``, and died with the subprocess — while the tool
returned a complete, plausible payload with a session_id and a full review, so
nothing signalled that the work had landed nowhere. ``PFACTORY_API_URL`` was
configured and simply never read.

Going through ``http_client`` puts these tools on the same path the task tools
use, which means one auth story, one audit trail, and one store.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

try:
    from claude_agent_sdk import tool

    SDK_TOOLS_AVAILABLE = True
except ImportError:
    SDK_TOOLS_AVAILABLE = False
    tool = None  # type: ignore[assignment]


def _ok(data: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(data, indent=2, default=str)}]}


def _status_of(session: Any) -> dict[str, Any]:
    """Trim a full session payload to the lightweight status view.

    ``plan_status`` promises status + board column + gate result. The server has
    no narrower endpoint, so the projection happens here rather than shipping
    the whole session and calling it a status.
    """
    if not isinstance(session, dict):
        return {"error": "unexpected session payload"}
    review = session.get("review") or {}
    return {
        "session_id": session.get("session_id"),
        "status": session.get("status"),
        "board_state": session.get("board_state"),
        "gates_passed": review.get("gates_passed"),
        "aggregate_score": review.get("aggregate_score"),
    }


def _err(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": f"ERROR: {message}"}], "is_error": True}


def create_plan_tools() -> list:
    """Create the PFactory plan tools (empty list if the SDK isn't installed)."""
    if not SDK_TOOLS_AVAILABLE:
        return []

    from agents.tools_pkg import http_client

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
                "category": {
                    "type": "string",
                    "description": "Intake category (see plan_categories)",
                },
                "template": {
                    "type": "string",
                    "description": "Selected template (its policy is enforced)",
                },
            },
        },
    )
    async def plan_ingest(args: dict[str, Any]) -> dict[str, Any]:
        try:
            text, path = args.get("text"), args.get("path")
            if not text and not path:
                return _err("plan_ingest needs either `text` or `path`")
            common = {
                "title": args.get("title"),
                "category": args.get("category", ""),
                "template": args.get("template", ""),
            }
            if path:
                # Read here: the server has no access to the caller's disk. The
                # bytes go up as-is so server-side parsers still handle pdf/docx.
                source = Path(path).expanduser()
                if not source.is_file():
                    return _err(f"no such plan document: {source}")
                data = await asyncio.to_thread(source.read_bytes)
                form = {k: v for k, v in common.items() if v is not None}
                return _ok(
                    await http_client.request(
                        "POST",
                        "/api/plan/sessions/ingest",
                        files={"file": (source.name, data)},
                        data=form,
                    )
                )
            return _ok(
                await http_client.request(
                    "POST",
                    "/api/plan/sessions/ingest-text",
                    json={"text": text, **common},
                )
            )
        except Exception as exc:  # noqa: BLE001
            return _err(str(exc))

    tools.append(plan_ingest)

    @tool(
        "plan_process",
        "Run PFactory's pipeline for a session: enrich (cloud + provider MCPs) → "
        "feasibility (cost/time/access) → decompose → review gates. Returns the "
        "review summary with cited findings + cost/effort estimates.",
        {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    )
    async def plan_process(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return _ok(
                await http_client.request(
                    "POST", f"/api/plan/sessions/{args['session_id']}/process", json={}
                )
            )
        except Exception as exc:  # noqa: BLE001
            return _err(str(exc))

    tools.append(plan_process)

    @tool(
        "plan_status",
        "Lightweight status for a session: status, board column "
        "(backlog/in_progress/ai_review/human_review/done), and gate result.",
        {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    )
    async def plan_status(args: dict[str, Any]) -> dict[str, Any]:
        try:
            session = await http_client.request("GET", f"/api/plan/sessions/{args['session_id']}")
            return _ok(_status_of(session))
        except Exception as exc:  # noqa: BLE001
            return _err(str(exc))

    tools.append(plan_status)

    @tool(
        "plan_get",
        "Fuller view of a session: summary + review lenses/findings (with "
        "citations) + cost/effort estimates.",
        {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    )
    async def plan_get(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return _ok(await http_client.request("GET", f"/api/plan/sessions/{args['session_id']}"))
        except Exception as exc:  # noqa: BLE001
            return _err(str(exc))

    tools.append(plan_get)

    @tool(
        "plan_list",
        "List all PFactory plan sessions (summaries).",
        {"type": "object", "properties": {}},
    )
    async def plan_list(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return _ok(await http_client.request("GET", "/api/plan/sessions"))
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
            return _ok(await http_client.request("GET", "/api/plan/meta/categories"))
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
            return _ok(
                await http_client.request(
                    "POST",
                    f"/api/plan/sessions/{args['session_id']}/approve",
                    json={
                        "approver": args["approver"],
                        "feedback": args.get("feedback"),
                    },
                )
            )
        except Exception as exc:  # noqa: BLE001
            return _err(str(exc))

    tools.append(plan_approve)

    @tool(
        "plan_export_audit_pack",
        "Export the EU AI Act audit pack for a session (#122): a self-contained "
        "bundle of the source doc, review findings + citations, human approval, "
        "signed Task Contract, completion timeline, and TFactory verdict (if "
        "present), cross-referenced to EU AI Act obligation headings. `format` is "
        "'json' (default) or 'markdown'. NOTE: a descriptive evidence bundle, not "
        "a compliance attestation — the pack carries an explicit disclaimer.",
        {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "format": {"type": "string", "enum": ["json", "markdown"]},
            },
            "required": ["session_id"],
        },
    )
    async def plan_export_audit_pack(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return _ok(
                await http_client.request(
                    "GET",
                    f"/api/plan/sessions/{args['session_id']}/audit-pack",
                    params={"format": args.get("format", "json")},
                )
            )
        except Exception as exc:  # noqa: BLE001
            return _err(str(exc))

    tools.append(plan_export_audit_pack)

    return tools
