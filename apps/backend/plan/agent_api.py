"""Agent-facing plan API — the MCP/tool surface other hosts drive (#2, Phase F).

These are thin, framework-agnostic callables over the shared
:data:`plan.service.SERVICE` singleton, returning JSON-serialisable dicts. They
are what the MCP tools (``mcp__pfactory__plan_*``) wrap and what the HTTP routes
(`/api/plan/sessions/*`) mirror — so Claude Code, Antigravity, and Codex/Copilot
can hand a plan to PFactory and track it, over MCP or plain HTTP.

Handoff arrives on the ``agent`` source channel. Everything here is read/compute
only — no GitHub/AIFactory side-effects (those stay behind the human-approved
emit path).
"""

from __future__ import annotations

from pathlib import Path

from plan.service import SERVICE, PlanServiceError


def plan_ingest(
    *,
    text: str | None = None,
    path: str | None = None,
    title: str | None = None,
    category: str = "",
    template: str = "",
    channel: str = "agent",
) -> dict:
    """Ingest a plan (inline ``text`` or a file ``path``) into a new session.

    Exactly one of ``text``/``path``. Optional ``category``/``template`` record the
    user's intake choice (a selected template's policy is enforced at process).
    Returns the session summary (``session_id``, ``status``, ``board_state``, …).
    """
    if (text is None) == (path is None):
        raise ValueError("provide exactly one of 'text' or 'path'.")
    if path is not None:
        data = Path(path).read_bytes()
        session = SERVICE.ingest_bytes(
            data, filename=Path(path).name, title=title, channel=channel,
            category=category, template=template,
        )
    else:
        session = SERVICE.ingest_text(
            text, title=title, channel=channel,
            category=category, template=template,
        )
    return session.summary()


def plan_process(session_id: str) -> dict:
    """Run the full pipeline for a session; return the summary + review gist."""
    session = SERVICE.process(session_id)
    return _with_review(session)


def plan_status(session_id: str) -> dict:
    """Lightweight status: status, board column, and gate result."""
    session = SERVICE.get(session_id)
    return {
        "session_id": session.session_id,
        "status": session.status,
        "board_state": session.board_state(),
        "gates_passed": session.review.gates_passed if session.review else None,
        "needs_human": session.board_state() == "human_review",
    }


def plan_get(session_id: str) -> dict:
    """Fuller view of a session (summary + review + feasibility estimates)."""
    return _with_review(SERVICE.get(session_id))


def plan_list() -> dict:
    """All known sessions (summaries)."""
    return {"sessions": SERVICE.list_sessions()}


def plan_approve(session_id: str, *, approver: str, feedback: str | None = None) -> dict:
    """Human approval (the agent passes the approver's identity through)."""
    session = SERVICE.approve(session_id, approver=approver, feedback=feedback)
    return plan_status(session_id) | {"approved_by": approver}


def _with_review(session) -> dict:
    out = session.summary()
    if session.review is not None:
        out["review"] = {
            "gates_passed": session.review.gates_passed,
            "aggregate_score": session.review.aggregate_score,
            "lenses": [
                {"lens": ls.lens, "score": ls.score,
                 "findings": [{"title": f.title, "severity": f.severity,
                               "source": f.source, "citations": [c.model_dump() for c in f.citations]}
                              for f in ls.findings]}
                for ls in session.review.lenses
            ],
        }
    if session.epic is not None:
        out["cost_estimate"] = session.epic.cost_estimate.model_dump() if session.epic.cost_estimate else None
        out["effort_estimate"] = session.epic.effort_estimate.model_dump() if session.epic.effort_estimate else None
    if session.suggested_template:
        out["suggested_template"] = session.suggested_template
    return out


__all__ = [
    "PlanServiceError",
    "plan_approve",
    "plan_get",
    "plan_ingest",
    "plan_list",
    "plan_process",
    "plan_status",
]
