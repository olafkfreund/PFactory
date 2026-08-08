"""PFactory MCP server — planning-context tools (epic #87 / #89, Component 3).

Exposes PFactory's planning outputs as MCP tools so the GitHub Copilot cloud
agent (and AIFactory / TFactory) can retrieve planning context during
implementation and testing — the runtime bridge for RFC-0002 Task Contract v2.

Transport: a minimal POST-only MCP HTTP transport (JSON-RPC 2.0 over
``POST /mcp``). The Copilot cloud agent's MCP client supports POST-only mode
without the GET server-push channel; full Streamable-HTTP compliance
(server-initiated messages, Mcp-Session-Id lifecycle) is intentionally out of
scope. The ``mcp`` Python SDK can replace this hand-rolled router later with a
minimal interface change.

Data source: the in-memory ``plan.service.SERVICE`` singleton shared with the
plan-pipeline routes. Sessions are looked up by the emitted GitHub epic issue
number (``PlanSession.emitted_issue_number``) or directly by ``session_id``.

Auth: Bearer ``PFACTORY_MCP_SECRET`` (when set). Mirrors AIFactory#459.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

# Resolve the backend package so `plan.service` imports (mirrors plan_pipeline.py).
_BACKEND_DIR = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

logger = logging.getLogger(__name__)

router = APIRouter(tags=["MCP-RPC"])

SERVER_NAME = "pfactory"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2025-06-18"

# JSON-RPC 2.0 error codes
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603


# ---------------------------------------------------------------------------
# Tool catalog
# ---------------------------------------------------------------------------

_ISSUE_OR_SESSION = {
    "type": "object",
    "properties": {
        "issue_number": {"type": "integer", "description": "Emitted GitHub epic issue number"},
        "session_id": {"type": "string", "description": "PFactory plan session id"},
    },
}

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "pfactory_get_epic",
        "description": "Get the epic decomposition (title, body, summary, cost/effort) for a planning session by issue number or session id.",
        "inputSchema": _ISSUE_OR_SESSION,
    },
    {
        "name": "pfactory_get_requirements",
        "description": "Get the normalised requirements (title, description, acceptance criteria, target kind) for a planning session.",
        "inputSchema": _ISSUE_OR_SESSION,
    },
    {
        "name": "pfactory_get_decomposition",
        "description": "Get the child-issue decomposition tree (keys, titles, dependencies, acceptance criteria, complexity) for a planning session.",
        "inputSchema": _ISSUE_OR_SESSION,
    },
    {
        "name": "pfactory_get_task_contract",
        "description": "Get the signed RFC-0002 Task Contract v2 payload (plan + execution + tfactory + verification) for a planning session. Builds it on demand (dry-run) if not already emitted.",
        "inputSchema": _ISSUE_OR_SESSION,
    },
    {
        "name": "pfactory_get_review_status",
        "description": "Get the governance review-gate status (aggregate score, per-lens scores, blocking findings, human approval) for a planning session.",
        "inputSchema": _ISSUE_OR_SESSION,
    },
    {
        "name": "pfactory_resolve_plan_doc",
        "description": (
            "Resolve a plan's documentation entry by correlation_key — the durable "
            "cross-factory memory read. Returns the doc file, dependencies, epic, "
            "plan_id and content_hash from the plan-docs registry. Pass a "
            "correlation_key directly (from the pfactory:meta block), or an "
            "issue_number / session_id to derive it from a live session."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "correlation_key": {
                    "type": "string",
                    "description": "The plan's correlation key (shared across factories).",
                },
                "issue_number": {
                    "type": "integer",
                    "description": "Emitted GitHub epic issue number",
                },
                "session_id": {"type": "string", "description": "PFactory plan session id"},
            },
        },
    },
]

_TOOL_NAMES = {t["name"] for t in MCP_TOOLS}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _is_truthy_env(name: str) -> bool:
    """True when an env flag is set to an explicit truthy value."""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _check_auth(request: Request) -> bool:
    """Return True when the request is authorised.

    Fail-closed (issue #128): the ``/mcp`` endpoint is mounted at top level so
    the global ``TokenAuthMiddleware`` (which only guards ``/api``) never sees
    it — ``PFACTORY_MCP_SECRET`` is its only protection. Previously an unset
    secret left the endpoint fully open by default, exposing plan / epic /
    requirements / decomposition / signed-contract / review-status to any
    caller.

    Now, when ``PFACTORY_MCP_SECRET`` is unset we DENY by default. Opening the
    endpoint without a secret requires an explicit dev opt-in
    (``PFACTORY_MCP_DEV=true`` or ``APP_DISABLE_AUTH=true``) — never the
    production default. When the secret is set, a constant-time-compared Bearer
    token is required.
    """
    expected = os.environ.get("PFACTORY_MCP_SECRET", "").strip()
    if not expected:
        # No secret configured: only allow when an explicit dev flag is set.
        if _is_truthy_env("PFACTORY_MCP_DEV") or _is_truthy_env("APP_DISABLE_AUTH"):
            return True
        logger.warning(
            "MCP request denied: PFACTORY_MCP_SECRET is unset and no dev flag "
            "(PFACTORY_MCP_DEV) is set — failing closed (issue #128)."
        )
        return False
    header = request.headers.get("Authorization", "")
    token = header[7:].strip() if header.startswith("Bearer ") else header.strip()
    return bool(token) and secrets.compare_digest(token, expected)


# ---------------------------------------------------------------------------
# Session resolution
# ---------------------------------------------------------------------------


class _ToolError(Exception):
    """Raised by a tool handler to surface a user-facing error string."""


def _resolve_session(args: dict[str, Any]):
    """Look up a PlanSession by session_id (preferred) or emitted issue number."""
    from plan.service import SERVICE

    session_id = args.get("session_id")
    issue_number = args.get("issue_number")

    if session_id:
        sess = SERVICE._sessions.get(session_id)
        if sess is None:
            raise _ToolError(f"no plan session with id {session_id!r}")
        return sess

    if issue_number is not None:
        for sess in SERVICE._sessions.values():
            if sess.emitted_issue_number == issue_number:
                return sess
        raise _ToolError(f"no plan session emitted for issue #{issue_number}")

    raise _ToolError("provide either 'issue_number' or 'session_id'")


# ---------------------------------------------------------------------------
# Tool handlers — each returns a JSON-serialisable dict
# ---------------------------------------------------------------------------


def _tool_get_epic(args: dict[str, Any]) -> dict[str, Any]:
    sess = _resolve_session(args)
    if sess.epic is None:
        raise _ToolError("plan not yet processed — no epic available")
    return sess.epic.model_dump()


def _tool_get_requirements(args: dict[str, Any]) -> dict[str, Any]:
    sess = _resolve_session(args)
    plan = sess.plan
    return {
        "plan_id": plan.plan_id,
        "title": plan.title,
        "description": plan.description,
        "target_kind": plan.target_kind,
        "plan_type": plan.plan_type,
        "acceptance_criteria": [{"id": c.id, "text": c.text} for c in plan.criteria],
    }


def _tool_get_decomposition(args: dict[str, Any]) -> dict[str, Any]:
    sess = _resolve_session(args)
    if sess.epic is None:
        raise _ToolError("plan not yet processed — no decomposition available")
    return {
        "plan_id": sess.epic.plan_id,
        "epic_title": sess.epic.epic_title,
        "children": [child.model_dump() for child in sess.epic.children],
        "decompose_method": sess.epic.decompose_method,
    }


def _tool_get_task_contract(args: dict[str, Any]) -> dict[str, Any]:
    sess = _resolve_session(args)
    if sess.contract_result is not None:
        return sess.contract_result
    # Build on demand (dry-run) if the plan has been processed.
    if sess.epic is None:
        raise _ToolError("plan not yet processed — cannot build a task contract")
    from plan.service import SERVICE

    try:
        SERVICE.emit_contract(sess.session_id, dry_run=True)
    except Exception as exc:  # noqa: BLE001 — surface as a tool error
        logger.exception("failed to build task contract for session %s", sess.session_id)
        raise _ToolError("failed to build task contract") from exc
    if sess.contract_result is None:
        raise _ToolError("task contract could not be built")
    return sess.contract_result


def _tool_get_review_status(args: dict[str, Any]) -> dict[str, Any]:
    sess = _resolve_session(args)
    if sess.review is None:
        raise _ToolError("plan not yet reviewed — no gate status available")
    review = sess.review
    return {
        "plan_id": review.plan_id,
        "gates_passed": review.gates_passed,
        "aggregate_score": review.aggregate_score,
        "threshold": review.threshold,
        "lenses": [
            {
                "lens": ls.lens,
                "score": ls.score,
                "max": ls.max,
                "blocking": ls.blocking,
                "findings": [
                    {
                        "title": f.title,
                        "severity": f.severity,
                        "blocking": f.blocking,
                        "source": f.source,
                    }
                    for f in ls.findings
                ],
            }
            for ls in review.lenses
        ],
        "human_approval": review.human_approval.model_dump(),
    }


def _tool_resolve_plan_doc(args: dict[str, Any]) -> dict[str, Any]:
    """Resolve a plan's docs-registry entry by correlation_key.

    The durable complement to the live ``pfactory_get_*`` tools: it reads the
    ``registry.json`` the docs emit writes, so a sibling factory can ask "what's
    the plan + dependencies behind this epic?" even after the session is gone.
    """
    from plan.emit.docs import PlanDocsResolver, docs_root

    key = (args.get("correlation_key") or "").strip()
    if not key:
        # No key given — derive it from a live/persisted session.
        sess = _resolve_session(args)
        key = (getattr(sess, "correlation_key", None) or "").strip()
        if not key:
            from plan.completion import correlation_key_for

            key = correlation_key_for(sess)
    if not key:
        raise _ToolError("provide 'correlation_key', or an issue_number/session_id")

    entry = PlanDocsResolver.from_dir(docs_root()).resolve(key)
    if entry is None:
        raise _ToolError(
            f"no plan doc registered for correlation_key {key!r} "
            "(was the plan emitted with PFACTORY_DOCS_EMIT enabled?)"
        )
    return {"correlation_key": key, **entry}


_TOOL_HANDLERS = {
    "pfactory_get_epic": _tool_get_epic,
    "pfactory_get_requirements": _tool_get_requirements,
    "pfactory_get_decomposition": _tool_get_decomposition,
    "pfactory_get_task_contract": _tool_get_task_contract,
    "pfactory_get_review_status": _tool_get_review_status,
    "pfactory_resolve_plan_doc": _tool_resolve_plan_doc,
}


# ---------------------------------------------------------------------------
# JSON-RPC envelope helpers
# ---------------------------------------------------------------------------


def _ok(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _dispatch(method: str, params: dict[str, Any], req_id: Any) -> dict[str, Any] | None:
    """Handle a single JSON-RPC request object. Returns None for notifications."""
    if method == "initialize":
        return _ok(
            req_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )

    if method in ("notifications/initialized", "initialized"):
        return None  # notification — no response

    if method == "ping":
        return _ok(req_id, {})

    if method == "tools/list":
        return _ok(req_id, {"tools": MCP_TOOLS})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in _TOOL_NAMES:
            return _err(req_id, _METHOD_NOT_FOUND, f"unknown tool: {name!r}")
        handler = _TOOL_HANDLERS[name]
        try:
            result = handler(arguments)
        except _ToolError as exc:
            # Tool-level error: report inside the result with isError=True so the
            # MCP client sees a structured failure rather than a transport error.
            return _ok(
                req_id,
                {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("MCP tool %s crashed", name)
            return _ok(
                req_id,
                {
                    "content": [{"type": "text", "text": "internal error"}],
                    "isError": True,
                },
            )
        return _ok(
            req_id,
            {
                "content": [{"type": "text", "text": json.dumps(result, default=str)}],
                "isError": False,
            },
        )

    return _err(req_id, _METHOD_NOT_FOUND, f"unknown method: {method!r}")


@router.post("/mcp")
async def mcp_rpc(request: Request):
    """JSON-RPC 2.0 endpoint for the PFactory MCP server."""
    if not _check_auth(request):
        return JSONResponse(
            status_code=401,
            content=_err(None, _INVALID_REQUEST, "Invalid or missing MCP token"),
        )

    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse(status_code=400, content=_err(None, _PARSE_ERROR, "Invalid JSON"))

    # JSON-RPC batch support.
    if isinstance(payload, list):
        responses = []
        for item in payload:
            resp = _handle_one(item)
            if resp is not None:
                responses.append(resp)
        return JSONResponse(content=responses)

    resp = _handle_one(payload)
    if resp is None:
        # Pure notification — 202 Accepted with empty body.
        return JSONResponse(status_code=202, content=None)
    return JSONResponse(content=resp)


def _handle_one(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return _err(None, _INVALID_REQUEST, "request must be a JSON object")
    req_id = item.get("id")
    method = item.get("method")
    if not isinstance(method, str):
        return _err(req_id, _INVALID_REQUEST, "missing 'method'")
    params = item.get("params") or {}
    if not isinstance(params, dict):
        return _err(req_id, _INVALID_PARAMS, "'params' must be an object")
    return _dispatch(method, params, req_id)
