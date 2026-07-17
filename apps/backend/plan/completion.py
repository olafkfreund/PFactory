"""Standardized completion-event envelope for the planning flow (#47).

Conforms to the Factory correlation-key RFC (``olafkfreund/Factory`` #4): when a
plan session reaches a terminal status, PFactory emits a normalized event
``{correlation_key, service, task_id, status, phase, updated_at}`` so downstream
services (CFactory observability, AIFactory) can trace one unit of work end to
end across the suite — ``pfactory.session_id → issue# → aifactory.task_id``.

The shared correlation key is the emitted GitHub issue number, with a synthetic
``pf-<session_id>`` fallback for sessions that never reach emit (e.g. rejected).

Transport mirrors the test-pipeline Triager's completion callback (#85): an
opt-in webhook POST is the standardized transport, and an opt-in same-host
sentinel file is a convenience. Both are best-effort — every failure is swallowed
so a notification can never break the pipeline. Stdlib-only.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plan.service import PlanSession

logger = logging.getLogger(__name__)

SERVICE_NAME = "pfactory"

# Statuses a plan session can terminally land on (see ``PlanService``).
TERMINAL_STATUSES = frozenset({"emitted", "rejected"})

# The phase each terminal status is reported under, in the envelope.
_PHASE_BY_STATUS = {"emitted": "emit", "rejected": "review"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def synthetic_key(session_id: str) -> str:
    """Correlation key for a session with no emitted issue#."""
    return f"pf-{session_id}"


def origin_key(repo: str | None, issue_number: int) -> str:
    """Correlation key for a plan that originated from an upstream issue.

    Repo-qualified because issue numbers only identify an issue *within* a repo
    (``pf-`` synthetic keys already establish that a key need not be numeric).
    """
    return f"{repo}#{issue_number}" if repo else str(issue_number)


def correlation_key_for(session: PlanSession) -> str:
    """The shared correlation key for a session.

    Precedence — the ORIGIN issue the plan came from (RFC-0011 hard-tier intake,
    AIFactory#874) wins over the emitted epic, because the origin issue is the
    thing the whole PARR chain must thread back to: it is the issue a human
    filed, and it is stable from ingest onward. The emitted epic is an artifact
    PFactory *creates* downstream of it, so keying on the epic would silently
    re-point the chain at PFactory's own output at emit time and lose the link to
    the request. Falls back to the emitted epic issue# (the pre-#874 behaviour,
    unchanged for every session with no origin), else a synthetic key.
    """
    if session.origin_issue_number is not None:
        return origin_key(session.repo, session.origin_issue_number)
    if session.emitted_issue_number is not None:
        return str(session.emitted_issue_number)
    return synthetic_key(session.session_id)


def build_completion_event(session: PlanSession, *, now: str | None = None) -> dict:
    """The normalized completion-event envelope (Factory#4 RFC).

    The six RFC fields, plus a ``correlation`` sub-object carrying the
    upstream/downstream chain links so a consumer gets the full PARR thread, plus
    the additive RFC-0001 v1.1 ``usage`` block (#60) summing the run's LLM token
    usage + cost — zeros when the pipeline ran deterministically. Additive and
    optional: consumers that don't know ``usage`` keep working.
    """
    from plan.usage import PlanUsage

    usage = getattr(session, "usage", None) or PlanUsage()

    # RFC-0001a evidence gate: a plan may only claim the success status
    # ("emitted") if the emit actually created issues. An "emitted" with no epic
    # issue produced nothing — downgrade it to "failed" so no consumer renders a
    # plan that created no governed work item as green. The epic number is the
    # minimal proof (a single-issue plan may legitimately have no children);
    # child_count rides along as evidence either way.
    status = session.status
    evidence: dict | None = None
    halt_reason: str | None = None
    if status == "emitted":
        emit_result = getattr(session, "emit_result", None) or {}
        epic = session.emitted_issue_number
        child_count = len(emit_result.get("child_numbers") or {})
        aif_task = getattr(session, "aifactory_task_id", None)
        if epic is None and aif_task:
            # Trusted-plan / contract-sync path: emit-contract signs the Task
            # Contract straight to AIFactory and starts the build — by design it
            # creates no GitHub issues. The accepted build (aifactory_task_id) IS
            # the evidence, so a successful contract hand-off must not be
            # downgraded to "failed" (which surfaced as false plan-stage
            # failures in the cockpit for issue-less runs).
            evidence = {"proof_kind": "contract", "aifactory_task_id": aif_task}
        else:
            evidence = {
                "proof_kind": "issues",
                "epic_issue": epic,
                "child_count": child_count,
            }
            if epic is None:
                status = "failed"
                halt_reason = "no_evidence: emit created no issues"

    event = {
        "correlation_key": correlation_key_for(session),
        "service": SERVICE_NAME,
        "task_id": session.session_id,
        "status": status,
        "phase": _PHASE_BY_STATUS.get(status, status),
        "updated_at": now or _now_iso(),
        "correlation": {
            "session_id": session.session_id,
            "issue_number": session.emitted_issue_number,
            "aifactory_task_id": session.aifactory_task_id,
        },
        "usage": usage.as_event_block(),
    }
    if evidence is not None:
        event["evidence"] = evidence
    if halt_reason is not None:
        event["halt_reason"] = halt_reason
    # RFC-0014 (#283): the routing decision the planning LLM call resolved —
    # actual model + tier + precedence source (evidence-gate pattern). Absent on
    # deterministic runs. Factory#273: the intake injection-scan verdict
    # ({verdict: pass|flagged|skipped, reason}) so CFactory can display it.
    routing = getattr(getattr(session, "epic", None), "routing", None)
    if routing:
        event["routing"] = routing
    injection_scan = getattr(session, "injection_scan", None)
    if injection_scan:
        event["injection_scan"] = injection_scan
    return event


def _write_sentinel(event: dict) -> None:
    """Opt-in same-host sentinel. Written only when ``PFACTORY_COMPLETION_SENTINEL``
    is truthy *and* ``PFACTORY_COMPLETION_SENTINEL_DIR`` points at a writable dir
    (the in-memory plan service has no per-session workspace, so the dir is
    explicit). A same-host watcher can stat it instead of receiving the webhook."""
    if not _truthy(os.environ.get("PFACTORY_COMPLETION_SENTINEL")):
        return
    base = (os.environ.get("PFACTORY_COMPLETION_SENTINEL_DIR") or "").strip()
    if not base:
        return
    try:
        out_dir = Path(base) / str(event["task_id"])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "COMPLETED.json").write_text(json.dumps(event, indent=2))
    except OSError:
        pass


def _post_webhook(event: dict) -> None:
    """Opt-in webhook POST — the standardized transport. Best-effort."""
    url = (os.environ.get("PFACTORY_COMPLETION_WEBHOOK") or "").strip()
    if not url:
        return
    try:
        import urllib.request

        timeout = float(os.environ.get("PFACTORY_COMPLETION_WEBHOOK_TIMEOUT", "5"))
        req = urllib.request.Request(
            url,
            data=json.dumps(event).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=timeout).close()  # noqa: S310
    except Exception:
        # Best-effort; a failing target never breaks the pipeline.
        pass


def notify_completion(session: PlanSession, *, now: str | None = None) -> dict:
    """Best-effort terminal notification. Builds the envelope, writes the opt-in
    sentinel, POSTs the opt-in webhook, and returns the event (for callers/tests).
    Never raises."""
    event = build_completion_event(session, now=now)
    _write_sentinel(event)
    _post_webhook(event)
    return event


def emit_usage_snapshot(
    session: PlanSession, *, now: str | None = None
) -> dict[str, object] | None:
    """Emit a NON-terminal, usage-bearing event so the cockpit reflects RUNNING
    cost for a plan session that has spent LLM tokens but not yet reached a
    terminal status (``emitted``/``rejected``) — e.g. ``processed`` + awaiting
    human approval, or a run abandoned before approval.

    Cost accrues continuously: a session that planned + ran governance gates has
    already spent tokens, so usage must reach CFactory independent of terminal
    completion (``notify_completion`` only fires on terminal statuses). Rides the
    SAME envelope + transport (``build_completion_event`` carries the ``usage``
    block with the session's current non-terminal status; CFactory records usage
    from any event that carries it). Best-effort; returns ``None`` when there is
    nothing to report (no usage yet) — never raises. Mirrors AIFactory's
    ``emit_usage_snapshot``.
    """
    usage = getattr(session, "usage", None)
    if usage is None or usage.total_tokens <= 0:
        return None
    try:
        if not getattr(session, "correlation_key", None):
            session.correlation_key = correlation_key_for(session)
        event = build_completion_event(session, now=now)
        _post_webhook(event)
        return event
    except Exception:  # noqa: BLE001 — usage reporting must never break planning
        logger.debug("plan usage snapshot emit failed (best-effort)", exc_info=True)
        return None
