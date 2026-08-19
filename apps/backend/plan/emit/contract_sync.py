"""Consume downstream RFC-0001 completion events + reconcile (epic #65, child 9).

The reverse of ``plan/completion.py`` (which *emits* PFactory's terminal event):
this consumes the completion events AIFactory and TFactory emit for the work
PFactory handed off, reconciles them on the shared ``correlation_key``, and
decides whether the unit needs a **handback** (a downstream failure means the
plan/code must be revised).

Pure + stdlib/pydantic only — the web-server route (or a poller) feeds raw event
dicts into :class:`ContractSyncRegistry`; the registry tracks per-key state and
surfaces which units need handback. No transport or persistence here.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Outcome = Literal["success", "failure", "in_progress"]

# Status vocabularies per downstream service (lowercased). Anything unknown falls
# through to the keyword heuristic in :func:`classify_outcome`.
_AIFACTORY_FAILURE = frozenset({"failed", "qa_failed", "gen_failed", "stuck", "error"})
_AIFACTORY_SUCCESS = frozenset({"qa_approved", "done", "completed", "merged"})
_TFACTORY_FAILURE = frozenset({"triager_failed", "rejected", "failed", "stuck"})
_TFACTORY_SUCCESS = frozenset({"triaged", "triaged_empty", "passed", "done"})

_FAILURE_HINTS = ("fail", "reject", "stuck", "error")
_SUCCESS_HINTS = ("done", "approved", "passed", "complete", "triaged", "merged")

_REQUIRED_FIELDS = ("correlation_key", "service", "status")


class SyncError(ValueError):
    """Raised when an inbound completion event is malformed.

    Verified safe to return to the client verbatim (Factory#718): both raise
    sites in this module describe the caller's own payload shape, never an
    inner exception. See ``client_errors.client_error``.
    """

    @property
    def client_message(self) -> str:
        return str(self)


class CompletionEvent(BaseModel):
    """A normalized inbound RFC-0001 completion event."""

    correlation_key: str
    service: str
    status: str
    task_id: str = ""
    phase: str = ""
    updated_at: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


def parse_completion_event(payload: Any) -> CompletionEvent:
    """Validate + normalize a raw event dict (RFC-0001 envelope)."""
    if not isinstance(payload, dict):
        raise SyncError("completion event must be an object")
    missing = [k for k in _REQUIRED_FIELDS if not payload.get(k)]
    if missing:
        raise SyncError(f"completion event missing required fields: {missing}")
    return CompletionEvent(
        correlation_key=str(payload["correlation_key"]),
        service=str(payload["service"]).strip().lower(),
        status=str(payload["status"]).strip().lower(),
        task_id=str(payload.get("task_id", "")),
        phase=str(payload.get("phase", "")),
        updated_at=str(payload.get("updated_at", "")),
        raw=payload,
    )


def classify_outcome(service: str, status: str) -> Outcome:
    """Classify a single service's status as success/failure/in_progress."""
    s = status.strip().lower()
    svc = service.strip().lower()
    if svc == "aifactory":
        if s in _AIFACTORY_FAILURE:
            return "failure"
        if s in _AIFACTORY_SUCCESS:
            return "success"
    elif svc == "tfactory":
        if s in _TFACTORY_FAILURE:
            return "failure"
        if s in _TFACTORY_SUCCESS:
            return "success"
    if any(h in s for h in _FAILURE_HINTS):
        return "failure"
    if any(h in s for h in _SUCCESS_HINTS):
        return "success"
    return "in_progress"


class ContractSyncState(BaseModel):
    """Reconciled downstream state for one emitted contract (by correlation_key)."""

    correlation_key: str
    aifactory_status: str = ""
    tfactory_status: str = ""
    outcome: Outcome = "in_progress"
    needs_handback: bool = False
    history: list[dict[str, str]] = Field(default_factory=list)

    def recompute(self) -> ContractSyncState:
        ai = (
            classify_outcome("aifactory", self.aifactory_status)
            if self.aifactory_status
            else "in_progress"
        )
        tf = (
            classify_outcome("tfactory", self.tfactory_status)
            if self.tfactory_status
            else "in_progress"
        )
        if "failure" in (ai, tf):
            self.outcome = "failure"
            self.needs_handback = True
        elif ai == "success" and tf in ("success", "in_progress"):
            # AIFactory done; TFactory either passed or not yet reported.
            self.outcome = "success" if tf == "success" else "in_progress"
            self.needs_handback = False
        else:
            self.outcome = "in_progress"
            self.needs_handback = False
        return self


def reconcile(state: ContractSyncState, event: CompletionEvent) -> ContractSyncState:
    """Fold one event into a state by service, then recompute the outcome."""
    if event.service == "aifactory":
        state.aifactory_status = event.status
    elif event.service == "tfactory":
        state.tfactory_status = event.status
    # Unknown services are recorded in history but don't change the outcome.
    state.history.append(
        {"service": event.service, "status": event.status, "updated_at": event.updated_at}
    )
    return state.recompute()


class ContractSyncRegistry:
    """Tracks reconciled downstream state across correlation keys."""

    def __init__(self) -> None:
        self._by_key: dict[str, ContractSyncState] = {}

    def apply(self, payload: Any) -> ContractSyncState:
        """Parse + reconcile a raw event; returns the updated state for its key."""
        event = parse_completion_event(payload)
        state = self._by_key.get(event.correlation_key) or ContractSyncState(
            correlation_key=event.correlation_key
        )
        self._by_key[event.correlation_key] = reconcile(state, event)
        return self._by_key[event.correlation_key]

    def get(self, correlation_key: str) -> ContractSyncState | None:
        return self._by_key.get(correlation_key)

    def needing_handback(self) -> list[ContractSyncState]:
        """All tracked units whose downstream outcome calls for a handback."""
        return [s for s in self._by_key.values() if s.needs_handback]
