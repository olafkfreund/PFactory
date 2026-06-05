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
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plan.service import PlanSession

SERVICE_NAME = "pfactory"

# Statuses a plan session can terminally land on (see ``PlanService``).
TERMINAL_STATUSES = frozenset({"emitted", "rejected"})

# The phase each terminal status is reported under, in the envelope.
_PHASE_BY_STATUS = {"emitted": "emit", "rejected": "review"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def synthetic_key(session_id: str) -> str:
    """Correlation key for a session with no emitted issue#."""
    return f"pf-{session_id}"


def correlation_key_for(session: PlanSession) -> str:
    """The shared correlation key: the emitted issue#, else a synthetic fallback."""
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
    return {
        "correlation_key": correlation_key_for(session),
        "service": SERVICE_NAME,
        "task_id": session.session_id,
        "status": session.status,
        "phase": _PHASE_BY_STATUS.get(session.status, session.status),
        "updated_at": now or _now_iso(),
        "correlation": {
            "session_id": session.session_id,
            "issue_number": session.emitted_issue_number,
            "aifactory_task_id": session.aifactory_task_id,
        },
        "usage": usage.as_event_block(),
    }


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
