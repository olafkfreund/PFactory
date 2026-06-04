"""Tool implementations for the Remote HTTP+SSE MCP server.

Each tool is a thin wrapper that:
  1. Checks the caller's scope (mcp:read or mcp:write)
  2. Calls the matching REST endpoint via loopback HTTP
  3. Returns a JSON-RPC ``content[]`` envelope

Why loopback HTTP rather than direct internal-function imports?
- The MCP surface IS the existing REST surface — any drift in REST
  behaviour shows up identically through MCP, no hidden divergence.
- The internal handlers are wrapped in FastAPI ``Depends(...)`` chains
  that pull in DB sessions / user-resolution / audit hooks; calling the
  HTTP endpoint reuses all of that for free.
- The audit log already attributes the call (it sees the caller's bearer
  token via ``request.state``), so adding ``X-MCP-Key-Id`` as a tracer
  header carries the MCP key id through to the audit row.

8 ship-in-this-PR tools (the ones backed by existing REST endpoints):

  ✓ Read (mcp:read)
      pfactory.list_projects        GET  /api/projects
      pfactory.list_tasks           GET  /api/projects/{id}/tasks
      pfactory.get_task             GET  /api/tasks/{id}
      pfactory.get_worktree_diff    GET  /api/tasks/{id}/worktree/diff

  ✓ Write (mcp:write)
      pfactory.start_task           POST /api/tasks/{id}/start
      pfactory.stop_task            POST /api/tasks/{id}/stop
      pfactory.approve_plan         POST /api/tasks/{id}/approve-plan
      pfactory.merge_pr             POST /api/tasks/{id}/worktree/merge

4 deferred (need new REST endpoints first — follow-up PR per #83's split):

  ⏳ pfactory.get_qa_report        — needs GET  /api/tasks/{id}/qa-report
  ⏳ pfactory.tail_agent_console   — needs GET  /api/tasks/{id}/agent-console/sse
  ⏳ pfactory.reject_plan          — needs POST /api/tasks/{id}/reject-plan
  ⏳ pfactory.recover_task         — needs unified POST /api/tasks/{id}/recover
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from .auth import (
    MCP_READ_SCOPE,
    MCP_WRITE_SCOPE,
    AuthenticatedKey,
    MCPAuthError,
    require_scope,
)

logger = logging.getLogger(__name__)


# Loopback URL the MCP tools use to call the web-server's own REST API.
# Defaults to localhost on the standard FastAPI port; override via env
# for tests that spin up the app on a random port.
DEFAULT_LOOPBACK_URL = "http://localhost:3114"


def _loopback_url() -> str:
    return os.environ.get("PFACTORY_MCP_LOOPBACK_URL", DEFAULT_LOOPBACK_URL).rstrip(
        "/"
    )


def _format_json(data: Any) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(data, indent=2, default=str)}]
    }


def _format_error(message: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": f"Error: {message}"}],
        "isError": True,
    }


async def _call_internal(
    method: str, path: str, key: AuthenticatedKey, **kwargs: Any
) -> Any:
    """Make a self-call against the web-server's own REST surface.

    The legacy ``settings.API_TOKEN`` from the env carries the request
    through ``TokenAuthMiddleware`` so the regular REST handlers run
    unchanged. The ``X-MCP-Key-Id`` header is a tracer the audit code
    can pick up to attribute the action to a specific MCP key.

    Returns the parsed JSON body, raising on any HTTP error so the
    caller's ``dispatch_tool_call`` ``except`` block formats it.
    """
    from ..config import get_settings

    settings = get_settings()
    headers = dict(kwargs.pop("headers", {}) or {})
    headers["Authorization"] = f"Bearer {settings.API_TOKEN}"
    headers["X-MCP-Key-Id"] = key.key_id

    base = _loopback_url()
    async with httpx.AsyncClient(base_url=base, timeout=30.0) as client:
        response = await client.request(method, path, headers=headers, **kwargs)
    response.raise_for_status()
    if not response.content:
        return {}
    return response.json()


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def get_tool_definitions() -> list[dict[str, Any]]:
    """Return the list of MCP tool descriptors the server advertises."""
    return [
        {
            "name": "pfactory.list_projects",
            "description": "List all projects registered with this PFactory install.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "pfactory.list_tasks",
            "description": "List tasks under a given project.",
            "inputSchema": {
                "type": "object",
                "properties": {"project_id": {"type": "string"}},
                "required": ["project_id"],
            },
        },
        {
            "name": "pfactory.get_task",
            "description": "Full task detail by id.",
            "inputSchema": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
        },
        {
            "name": "pfactory.get_worktree_diff",
            "description": "Worktree diff for a task (what the agent has written so far).",
            "inputSchema": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
        },
        {
            "name": "pfactory.start_task",
            "description": "Start a task's agent. Requires mcp:write scope.",
            "inputSchema": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
        },
        {
            "name": "pfactory.stop_task",
            "description": "Stop a running task. Requires mcp:write scope.",
            "inputSchema": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
        },
        {
            "name": "pfactory.approve_plan",
            "description": "Approve a task's implementation plan. Requires mcp:write scope.",
            "inputSchema": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
        },
        {
            "name": "pfactory.merge_pr",
            "description": "Merge the task's worktree PR. Requires mcp:write scope.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "merge_method": {
                        "type": "string",
                        "enum": ["merge", "squash", "rebase"],
                        "default": "merge",
                    },
                },
                "required": ["task_id"],
            },
        },
        # ── V1.1 — backed by new REST endpoints in routes/tasks.py ─────
        {
            "name": "pfactory.get_qa_report",
            "description": (
                "Return the qa_report.md content for a task that has reached "
                "the QA phase. 404s if the task hasn't generated a report yet."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
        },
        {
            "name": "pfactory.tail_agent_console",
            "description": (
                "Return the absolute URL of the SSE stream for a task's "
                "agent-console output. The MCP client follows up with its "
                "own GET to that URL (same Authorization header) to consume "
                "the stream — cleaner than wrapping SSE-in-MCP."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
        },
        {
            "name": "pfactory.reject_plan",
            "description": (
                "Reject a task's implementation plan and send the planner "
                "back to iterate. Optional feedback gets recorded on the "
                "review state's feedback log. Requires mcp:write scope."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "feedback": {
                        "type": "string",
                        "description": "Optional reason for rejection",
                    },
                },
                "required": ["task_id"],
            },
        },
        {
            "name": "pfactory.recover_task",
            "description": (
                "Recover a stuck task — resets its status and optionally "
                "restarts the agent. Same surface as the stdio task_recover "
                "tool. Requires mcp:write scope."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "auto_restart": {
                        "type": "boolean",
                        "default": False,
                        "description": "Restart the agent after recovery",
                    },
                },
                "required": ["task_id"],
            },
        },
    ]


# Scope requirement per tool — keep it next to the tool list above so
# they don't drift. ``dispatch_tool_call`` consults this map.
_SCOPE_FOR_TOOL: dict[str, str] = {
    # V1
    "pfactory.list_projects": MCP_READ_SCOPE,
    "pfactory.list_tasks": MCP_READ_SCOPE,
    "pfactory.get_task": MCP_READ_SCOPE,
    "pfactory.get_worktree_diff": MCP_READ_SCOPE,
    "pfactory.start_task": MCP_WRITE_SCOPE,
    "pfactory.stop_task": MCP_WRITE_SCOPE,
    "pfactory.approve_plan": MCP_WRITE_SCOPE,
    "pfactory.merge_pr": MCP_WRITE_SCOPE,
    # V1.1
    "pfactory.get_qa_report": MCP_READ_SCOPE,
    "pfactory.tail_agent_console": MCP_READ_SCOPE,
    "pfactory.reject_plan": MCP_WRITE_SCOPE,
    "pfactory.recover_task": MCP_WRITE_SCOPE,
}


async def dispatch_tool_call(
    tool_name: str,
    arguments: dict[str, Any] | None,
    key: AuthenticatedKey,
) -> dict[str, Any]:
    """Route a ``tools/call`` JSON-RPC request to the right handler.

    Handles scope enforcement + error formatting in one place so individual
    tool calls stay one-liners.
    """
    arguments = arguments or {}
    required_scope = _SCOPE_FOR_TOOL.get(tool_name)
    if required_scope is None:
        return _format_error(f"unknown tool: {tool_name}")

    try:
        require_scope(key, required_scope)
    except MCPAuthError as exc:
        return _format_error(str(exc))

    try:
        if tool_name == "pfactory.list_projects":
            return _format_json(await _call_internal("GET", "/api/projects", key))
        if tool_name == "pfactory.list_tasks":
            project_id = arguments["project_id"]
            return _format_json(
                await _call_internal(
                    "GET", f"/api/projects/{project_id}/tasks", key
                )
            )
        if tool_name == "pfactory.get_task":
            return _format_json(
                await _call_internal("GET", f"/api/tasks/{arguments['task_id']}", key)
            )
        if tool_name == "pfactory.get_worktree_diff":
            return _format_json(
                await _call_internal(
                    "GET",
                    f"/api/tasks/{arguments['task_id']}/worktree/diff",
                    key,
                )
            )
        if tool_name == "pfactory.start_task":
            return _format_json(
                await _call_internal(
                    "POST", f"/api/tasks/{arguments['task_id']}/start", key, json={}
                )
            )
        if tool_name == "pfactory.stop_task":
            return _format_json(
                await _call_internal(
                    "POST", f"/api/tasks/{arguments['task_id']}/stop", key, json={}
                )
            )
        if tool_name == "pfactory.approve_plan":
            return _format_json(
                await _call_internal(
                    "POST",
                    f"/api/tasks/{arguments['task_id']}/approve-plan",
                    key,
                    json={},
                )
            )
        if tool_name == "pfactory.merge_pr":
            return _format_json(
                await _call_internal(
                    "POST",
                    f"/api/tasks/{arguments['task_id']}/worktree/merge",
                    key,
                    json={"merge_method": arguments.get("merge_method", "merge")},
                )
            )
        # ── V1.1 tools ────────────────────────────────────────────────
        if tool_name == "pfactory.get_qa_report":
            return _format_json(
                await _call_internal(
                    "GET",
                    f"/api/tasks/{arguments['task_id']}/qa-report",
                    key,
                )
            )
        if tool_name == "pfactory.tail_agent_console":
            # SSE streams don't fit cleanly inside MCP's request/response
            # envelope. Instead of proxying the bytes, return the URL the
            # client should connect to itself. The client uses the same
            # Authorization header it already has.
            task_id = arguments["task_id"]
            url = f"{_loopback_url()}/api/tasks/{task_id}/agent-console/sse"
            return _format_json(
                {
                    "task_id": task_id,
                    "sse_url": url,
                    "auth_hint": (
                        "Connect to sse_url with the same Authorization header "
                        "you used for this MCP call. Stream emits 'data:' lines "
                        "as the agent's build-progress.txt grows; closes with "
                        "'event: done' on idle-timeout or max-duration."
                    ),
                }
            )
        if tool_name == "pfactory.reject_plan":
            payload = {}
            if arguments.get("feedback"):
                payload["feedback"] = arguments["feedback"]
            return _format_json(
                await _call_internal(
                    "POST",
                    f"/api/tasks/{arguments['task_id']}/reject-plan",
                    key,
                    json=payload,
                )
            )
        if tool_name == "pfactory.recover_task":
            return _format_json(
                await _call_internal(
                    "POST",
                    f"/api/tasks/{arguments['task_id']}/recover",
                    key,
                    json={"autoRestart": arguments.get("auto_restart", False)},
                )
            )
    except KeyError as exc:
        return _format_error(f"missing required argument: {exc.args[0]}")
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:500] if exc.response.content else ""
        return _format_error(
            f"{tool_name} returned HTTP {exc.response.status_code}: {body}"
        )
    except Exception as exc:  # noqa: BLE001  -- surface as content, not 500
        logger.exception("MCP tool %s failed", tool_name)
        return _format_error(f"{tool_name} failed: {exc}")

    return _format_error(f"unimplemented tool: {tool_name}")
