"""PlanService — orchestrates the full pipeline behind the portal/API (#20).

A thin, dependency-light application service the web-server (and the MCP tool)
call to drive a plan through every stage and hold the working state between
HTTP requests. Pure Python + in-memory store, so it is fully unit-testable
without FastAPI. The web route layer (``server/routes/plan_pipeline.py``) is a
thin wrapper over a module-level :data:`SERVICE` singleton.

Flow:  ingest → process (detect → plan-type → decompose → synthesize → gates)
       → approve/reject → emit (dry-run by default).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

from pydantic import BaseModel, Field, PrivateAttr

from plan.annotate import AnnotationResult, annotate_plan
from plan.completion import (
    correlation_key_for,
    emit_usage_snapshot,
    notify_completion,
)
from plan.decompose.migration_planner import (
    build_equivalence_block,
    build_golden_corpus_manifest,
)
from plan.decompose.models import EpicPlan
from plan.decompose.planner import decompose
from plan.detect.migration_classifier import classify_migration
from plan.detect.source_inspector import inspect_source
from plan.detect.target_classifier import apply as detect_apply, classify_plan
from plan.ingest.channels import ingest_bytes, ingest_text
from plan.models import Criterion, NormalizedPlan
from plan.plan_types import apply as plan_type_apply, select_for
from plan.recon import classify_change_mode, reconnoiter
from plan.review.approval import approve as approve_review, reject as reject_review
from plan.review.gates import refresh_readiness, run_gates
from plan.review.models import PlanReview
from plan.service_helpers import (
    BoardColumn,
    attach_deployment as _attach_deployment,
    board_state,
    carry_tier as _carry_tier,
    default_store_dir as _default_store_dir,
    knowledge_connector_kwargs as _knowledge_connector_kwargs,
    load_access_inputs as _load_access_inputs,
    persist_enabled as _persist_enabled,
    route_tier as _route_tier,
    template_findings as _template_findings,
)
from plan.synthesize.models import SynthesizedArtifact
from plan.synthesize.run import synthesize
from plan.usage import PlanUsage

# Lifecycle status. The first five are persisted stages; `processing`/`reviewing`
# are transient sub-states set during process() so the board shows live progress.
SessionStatus = str
# ingested | processing | reviewing | processed | approved | rejected | emitted

# The board-column projection, env plumbing, RFC-0011 tier routing and the
# additive RFC-0013/#E review-finding seams now live in ``plan.service_helpers``
# (#194 decomposition); they are re-exported above under their historical
# underscore names so every existing import path keeps working.

# Note: ``SERVICE`` is intentionally absent from ``__all__`` — it is a lazily
# constructed module attribute resolved via ``__getattr__`` (PEP 562) below, so
# it is not a statically defined name. ``from plan.service import SERVICE`` and
# attribute access still work; only ``import *`` skips it.
__all__ = [
    "BoardColumn",
    "EmitInProgressError",
    "PlanService",
    "PlanServiceError",
    "PlanSession",
    "SessionStatus",
    "StaleSessionError",
    "board_state",
]


class PlanSession(BaseModel):
    """All working state for one plan as it moves through the pipeline."""

    session_id: str
    status: SessionStatus = "ingested"
    plan: NormalizedPlan
    # Multi-tenancy (#308): the tenant this session belongs to, resolved from the
    # X-Tenant-Id header at intake when PFACTORY_MULTI_TENANT is on. Defaults to
    # "default" so single-tenant behaviour (and old persisted sessions) are
    # unchanged.
    tenant_id: str = "default"
    # RFC-0010: the target repo this plan changes, captured at ingest so the
    # reconnaissance stage (Phase 2) can read it during process(). Today `repo`
    # was only known at emit; threading it here lets planning be code-aware.
    # `base_ref` defaults to the repo's default branch when omitted.
    repo: str | None = None
    base_ref: str | None = None
    epic: EpicPlan | None = None
    artifacts: list[SynthesizedArtifact] = Field(default_factory=list)
    review: PlanReview | None = None
    annotation: AnnotationResult | None = None  # honoured doc + suggested edits (#D)
    # Why this session was abandoned (#360): {actor, reason, at, from_status}.
    # A discard leaves no plan, no review and no emitted issue behind, so unlike
    # every other terminal path there is no artefact that explains itself — this
    # dict IS the record.
    discard: dict | None = None
    original_filename: str = ""  # the uploaded document's name, for rendering
    selected_category: str = ""  # category the user chose at intake (#E)
    selected_template: str = ""  # template the user chose — its policy IS enforced (#E)
    suggested_template: str = ""  # best keyword match — informational only
    emit_result: dict | None = None
    docs_result: list[dict] | None = None  # docs emit per-target results (P1)
    contract_result: dict | None = None  # RFC-0002 signed Task Contract v2 emit (#65)
    # RFC-0016 #190: object-store references (URIs, never blobs) to the plan's
    # emitted documents (plan/spec markdown + audit pack), uploaded best-effort
    # on terminal emit and mirrored onto the durable job_states `artifacts[]`.
    artifact_refs: list[dict] = Field(default_factory=list)
    # RFC-0007 (#86): human-verified access curation. `access_approvals` maps a
    # resource -> approval record (applied at the next emit); `access_audit` is the
    # append-only RFC-0001a curation trail (refs only, never secrets).
    access_approvals: dict = Field(default_factory=dict)
    access_audit: list = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    # PARR correlation chain (#47): pfactory.session_id → issue# → aifactory.task_id.
    # `correlation_key` is the shared key (the emitted GitHub issue #, with a
    # synthetic `pf-<session_id>` fallback when no issue exists yet — e.g. a
    # rejected plan). The two ids below are the upstream/downstream links.
    correlation_key: str | None = None
    emitted_issue_number: int | None = None  # upstream link — the emitted epic issue#
    # RFC-0011 hard tier (AIFactory#874): the ORIGIN issue this plan was raised
    # from, when it arrived via from-issue intake. Distinct from
    # `emitted_issue_number` (the epic PFactory *creates*) — this is the issue a
    # human filed, and it is what `correlation_key_for` keys on so plan → code →
    # verify thread back to the request rather than to PFactory's own output.
    # None for every other channel (portal/CLI/MCP), which keeps prior behaviour.
    origin_issue_number: int | None = None
    aifactory_task_id: str | None = None  # downstream link — the handed-off task

    # Token usage accumulated across the run's LLM seams (#60). Zero by default —
    # the pipeline is deterministic unless an LLM is supplied — and surfaced as
    # the additive `usage` block on the completion event (CFactory Tokens page).
    usage: PlanUsage = Field(default_factory=PlanUsage)

    # Factory#273 intake injection-scan verdict: {"verdict": pass|flagged|skipped,
    # "reason": str}. None until classification runs; surfaced on the completion
    # event so CFactory can display it. A `flagged` verdict forces tier=hard
    # (blocking human review) via route_tier — see _detect_and_plan_type.
    injection_scan: dict | None = None

    # The shared-store row version this copy was read at (#758): 0 for a
    # session never stored, None when unknown (listed, or imported by another
    # replica). Private, so payloads and the JSON mirror are unchanged. It
    # lives on the copy rather than in a dict keyed by id, because two copies
    # of one session in the same process (a long process() and a human's
    # approve) must not be able to overwrite each other either.
    _store_version: int | None = PrivateAttr(default=None)

    def record_usage(self, usage: PlanUsage | None) -> None:
        """Fold an LLM call's usage into the run total (no-op for ``None``)."""
        self.usage.add(usage)

    def board_state(self) -> BoardColumn:
        return board_state(self.status, self.review)

    def _review_summary(self) -> dict | None:
        """Compact per-lens verdict for the cockpit (CFactory#245).

        ``gates_passed`` alone tells a consumer THAT a plan is blocked but not
        WHY, so CFactory could only render an enabled Approve button and let the
        click 409 with a lens name the card never showed. This carries the
        per-lens scores and their findings so the button can be disabled with
        the reason attached, before the click.

        Deliberately not the whole PlanReview: the lens list is short, but
        findings carry ``detail`` prose and citations that no cockpit control
        needs. ``aggregate_score`` IS included and is explicitly NOT the test --
        every lens must clear ``threshold`` individually, which is why the
        refusal message says so. A consumer reading the aggregate as the verdict
        would disagree with the server that produced it.
        """
        if self.review is None:
            return None
        return {
            "gates_passed": self.review.gates_passed,
            "threshold": self.review.threshold,
            "aggregate_score": self.review.aggregate_score,
            "lenses": [
                {
                    "lens": ls.lens,
                    "score": ls.score,
                    "findings": [
                        {"title": f.title, "severity": f.severity, "blocking": f.blocking}
                        for f in ls.findings
                    ],
                }
                for ls in self.review.lenses
            ],
        }

    def summary(self) -> dict:
        return {
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "title": self.plan.title,
            "status": self.status,
            "board_state": self.board_state(),
            "target_kind": self.plan.target_kind,
            "plan_type": self.plan.plan_type,
            "children": len(self.epic.children) if self.epic else 0,
            "gates_passed": self.review.gates_passed if self.review else None,
            # CFactory#245: the per-lens detail behind `gates_passed`.
            "review": self._review_summary(),
            "created_at": self.created_at,
            "correlation_key": self.correlation_key,
            "issue_number": self.emitted_issue_number,
            "origin_issue_number": self.origin_issue_number,
            "aifactory_task_id": self.aifactory_task_id,
            "injection_scan": self.injection_scan,
        }


class PlanServiceError(RuntimeError):
    """Raised for invalid session ids or out-of-order stage calls.

    Verified safe to return to the client verbatim (Factory#718): every raise
    site in this module passes a developer-written literal, never an inner
    exception. ``client_message`` opts this type into
    :func:`client_errors.client_error`, so that trust is enforced by a type
    check at the read site rather than by every route remembering not to
    change it.
    """

    @property
    def client_message(self) -> str:
        return str(self)


def _criterion_id(title: str) -> str:
    """The ``AC#N`` a suggestion's title names, or "" when it names none."""
    match = re.search(r"AC#\d+", title or "")
    return match.group(0) if match else ""


class PlanInputError(PlanServiceError):
    """A human's edit was rejected: bad shape or empty required field (#692).

    Split from the base so the route can answer 400 for "you sent something
    invalid" while still answering 404 for "no such session". Both carry a
    developer-written literal, so both stay safe to return verbatim.
    """


class EmitInProgressError(PlanServiceError):
    """A live emit of this session is running, or cannot be ruled out (#758).

    Raised when another thread or replica holds the emit, and also when the
    shared store cannot be asked (fail closed): a refused emit costs a retry, a
    duplicate epic costs a manual cleanup. Routes answer 409.
    """


class StaleSessionError(PlanServiceError):
    """This copy of a session is older than the stored row (#758).

    Another replica, or another request in this one, wrote the session after
    this copy was read, so saving it would silently undo that write. Routes
    answer 409; the caller reloads and retries.
    """


logger = logging.getLogger(__name__)


# Process-wide cache of the durable store, keyed by DATABASE_URL. Every
# PlanService shares ONE store (hence one background loop + one connection
# pool), matching production (a single shared Postgres) and — critically —
# preventing the test suite from spawning a fresh loop/pool per PlanService
# instance (which would exhaust Postgres connections + balloon runtime).
_JOB_STORE_CACHE: dict[str, object] = {}
_JOB_STORE_LOCK = threading.Lock()


def _resolve_job_store() -> object | None:
    """Return the shared durable job-state store, or ``None`` (in-memory path).

    Returns a process-wide singleton :class:`server.jobstore.JobStateStore`
    (cached by ``DATABASE_URL``) when the env var is set and the web-server DB
    layer is importable (the normal in-cluster case). Returns ``None`` — and
    logs a clear not-multi-replica-safe warning — when ``DATABASE_URL`` is unset
    (single-pod dev) or the SQLAlchemy-backed store cannot be imported (e.g. the
    dependency-light backend test venv). Never raises: a store-construction
    hiccup degrades to the in-memory path rather than breaking the pipeline.
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        logger.warning(
            "PFactory plan state is IN-MEMORY (DATABASE_URL unset): NOT "
            "multi-replica safe and lost on restart. Set DATABASE_URL to a "
            "shared Postgres for durable, multi-replica admission (RFC-0016)."
        )
        return None
    with _JOB_STORE_LOCK:
        cached = _JOB_STORE_CACHE.get(url)
        if cached is not None:
            return cached
        try:
            # Deferred + optional: server.jobstore (SQLAlchemy-backed) is not
            # importable in the dependency-light backend venv, so this import
            # MUST stay inside the guard (PLC0415 is intentional here).
            from server.jobstore import (  # noqa: PLC0415
                JobStateStore,
            )

            store = JobStateStore()
            # Verify the store can actually serve requests (DB reachable +
            # job_states table migrated). If not — e.g. a process that set
            # DATABASE_URL but never ran migrations — close it and fall back to
            # the in-memory path rather than failing every transition (RFC-0016
            # graceful-degradation). This keeps unit tests that point at a bare
            # Postgres green and matches the conventions' fallback intent.
            if not store.is_ready():
                store.close()
                logger.warning(
                    "DATABASE_URL is set but the job_states table is not "
                    "ready (DB unreachable or migrations not applied); using "
                    "the IN-MEMORY path, which is NOT multi-replica safe "
                    "(RFC-0016). Run `alembic upgrade head`."
                )
                return None
            _JOB_STORE_CACHE[url] = store
            logger.info(
                "PFactory plan state is DURABLE: backed by the shared "
                "job_states table (RFC-0016 #217)."
            )
            return store
        except Exception as exc:  # noqa: BLE001 — degrade to in-memory, never fatal
            logger.warning(
                "DATABASE_URL is set but the durable job-state store is "
                "unavailable (%s); falling back to the IN-MEMORY path, which is "
                "NOT multi-replica safe (RFC-0016).",
                exc,
            )
            return None


class SessionStore(Protocol):
    """What :class:`PlanService` needs of the shared session store (#755).

    Structural, so the real ``PlanSessionStore`` and a test fake both satisfy
    it without importing the web-server DB layer here (this module must import
    cleanly in the dependency-light backend venv).
    """

    def get(self, session_id: str) -> tuple[str, int] | None:
        """The stored payload and its row version, or None."""
        ...

    def list_payloads(self, *, tenant_id: str | None = ...) -> list[str]: ...

    def upsert(
        self,
        session_id: str,
        *,
        payload: str,
        seq: int,
        tenant_id: str | None,
        expected_version: int,
    ) -> int | None:
        """Compare-and-set write (#758); the new version, or None on conflict.

        ``expected_version`` 0 inserts a new row (None if it already exists);
        a positive value updates only a row still at that version.
        """
        ...

    def acquire_emit_lease(self, session_id: str, owner: str, ttl_seconds: int) -> bool: ...

    def release_emit_lease(self, session_id: str, owner: str) -> None: ...

    def next_seq(self) -> int: ...

    def session_ids(self) -> set[str]: ...

    def is_ready(self) -> bool: ...

    def close(self) -> None: ...


def _seq_of(session_id: str) -> int:
    """The numeric prefix of a ``NNN-slug`` session id, or 0.

    Stored alongside the payload so ``next_seq()`` can allocate the next id
    with ``max(seq)+1`` in one transaction (#755). A hand-made id with no
    numeric prefix simply never raises the high-water mark.
    """
    head = session_id.split("-", 1)[0]
    return int(head) if head.isdigit() else 0


_EMIT_LEASE_TTL_DEFAULT = 1800


def _emit_lease_ttl() -> int:
    """``PFACTORY_EMIT_LEASE_TTL_SECONDS``, or 1800 when unset or invalid (#758).

    The lease outlives any observed live emit (minutes, #725) and expires on
    its own if the holder dies, so a crashed pod blocks a session for at most
    this long.
    """
    raw = os.environ.get("PFACTORY_EMIT_LEASE_TTL_SECONDS", "").strip()
    try:
        ttl = int(raw)
    except ValueError:
        return _EMIT_LEASE_TTL_DEFAULT
    return ttl if ttl > 0 else _EMIT_LEASE_TTL_DEFAULT


def _warn_if_multi_replica_without_store(store: SessionStore | None) -> None:
    """Shout when this process is one of several with no shared store (#755).

    The pre-existing WARNING about the in-memory path was logged throughout the
    prod incident and nobody saw it, so this is an ERROR naming the
    consequence. With PFACTORY_REQUIRE_SHARED_STORE=1 it refuses to start
    instead, so the KEDA pin can be lifted against a guarantee rather than a
    hope.
    """
    if store is not None:
        return
    try:
        replicas = int(os.environ.get("PFACTORY_REPLICA_COUNT", "1").strip() or "1")
    except ValueError:
        replicas = 1
    if replicas <= 1:
        return
    message = (
        f"PFACTORY_REPLICA_COUNT={replicas} but plan sessions are PER-PROCESS: "
        "writes on this replica will be invisible to the others and session "
        "ids can collide (#755). Set DATABASE_URL to a shared Postgres and "
        "run `alembic upgrade head`, or pin the deployment to one replica."
    )
    require = os.environ.get("PFACTORY_REQUIRE_SHARED_STORE", "").strip().lower()
    if require in ("1", "true", "yes", "on"):
        raise RuntimeError(message)
    logger.error(message)


_SESSION_STORE_CACHE: dict[str, SessionStore] = {}
# URLs whose store could not serve requests — do not rebuild one per service.
_SESSION_STORE_UNAVAILABLE: set[str] = set()
_SESSION_STORE_LOCK = threading.Lock()


def _resolve_session_store() -> SessionStore | None:
    """Return the shared plan-session store, or ``None`` (per-process path).

    The sibling of :func:`_resolve_job_store`, and degrades the same way: no
    ``DATABASE_URL``, an unimportable web-server DB layer, or an unmigrated
    table all fall back to the in-memory dict rather than failing a request.

    Without it, `_sessions` is per process: a write on one replica is invisible
    to the others until they restart (#755, measured in prod with 4 pods). The
    JSON mirror on the PVC does not close that gap — the volume is
    ReadWriteOnce/local-path, so replicas share it only while co-scheduled.
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url or url in _SESSION_STORE_UNAVAILABLE:
        # Remembering the failure matters: without it every PlanService in a
        # process retries the connection and builds (then discards) a store.
        return None
    with _SESSION_STORE_LOCK:
        cached = _SESSION_STORE_CACHE.get(url)
        if cached is not None:
            return cached
        try:
            from server.jobstore import PlanSessionStore  # noqa: PLC0415

            store = PlanSessionStore()
            if not store.is_ready():
                # Close it: the instance owns an event loop, a thread and a
                # connection pool, and a process that builds many PlanServices
                # would leak all three per construction.
                store.close()
                _SESSION_STORE_UNAVAILABLE.add(url)
                logger.warning(
                    "DATABASE_URL is set but the plan_sessions table is not "
                    "ready (DB unreachable or migrations not applied); plan "
                    "sessions stay PER-PROCESS, which is NOT multi-replica "
                    "safe (#755). Run `alembic upgrade head`."
                )
                return None
            typed = cast("SessionStore", store)
            _SESSION_STORE_CACHE[url] = typed
            logger.info(
                "PFactory plan sessions are SHARED: backed by the plan_sessions table (#755)."
            )
            return typed
        except Exception as exc:  # noqa: BLE001 — degrade, never fatal
            _SESSION_STORE_UNAVAILABLE.add(url)
            logger.warning(
                "DATABASE_URL is set but the shared plan-session store is "
                "unavailable (%s); plan sessions stay PER-PROCESS, which is "
                "NOT multi-replica safe (#755).",
                exc,
            )
            return None


class PlanService:
    """Orchestrator for plan sessions, with durable + disk-backed persistence.

    The working store is in-memory for speed/testability. Two durability layers
    sit alongside it:

      - RFC-0016 (#217): when ``DATABASE_URL`` is set, every state transition is
        mirrored into a shared Postgres ``job_states`` row and the admission
        cap/queue is granted via a ``SELECT ... FOR UPDATE`` transaction, so
        state survives a restart and is consistent across replicas. When
        ``DATABASE_URL`` is unset the in-memory path is used and a clear
        not-multi-replica-safe warning is logged.
      - The legacy opt-in (``PFACTORY_PLAN_PERSIST``) JSON disk mirror still
        works for single-pod dev that wants restart survival without a database.
    """

    def __init__(
        self,
        *,
        store_dir: Path | None = None,
        persist: bool | None = None,
        job_store: object | None = None,
        session_store: SessionStore | None = None,
    ) -> None:
        self._sessions: dict[str, PlanSession] = {}
        # RFC-0016 (#217): process() now runs in a worker thread (see
        # `process_async`), so concurrent offloaded runs mutate the shared
        # `_sessions` store + the `_next_seq` counter from different threads.
        # This lock guards every mutation of `_sessions` / persistence / the
        # sequence so the store cannot be corrupted or collide on the seq. It is
        # a plain re-usable mutex (held only for the brief dict/disk write, never
        # across the long pipeline body) — minimal and correct.
        self._store_lock = threading.Lock()
        # One emit (issues or contract) per session at a time (#725). Emits run
        # in a worker thread, so without this a double-click would run two that
        # both see "no epic yet" and both create one. Per-process is enough:
        # replicaCount is pinned to 1.
        self._emit_locks: dict[str, threading.Lock] = {}
        # Monotonic sequence counter (RFC-0016 #217). Was derived as
        # ``len(_sessions)+1`` at each call — a TOCTOU race under concurrent
        # ingests (two readers see the same length → identical seq → identical
        # plan_id → a lost session). A dedicated counter, bumped atomically under
        # the lock, never reuses a value. Seeded below from the loaded store so
        # the first id after a restart keeps the historical numbering.
        self._seq = 0
        self._persist = _persist_enabled() if persist is None else persist
        self._store_dir = store_dir or _default_store_dir()
        if self._persist:
            self._load_all()
        self._seq = len(self._sessions)

        # RFC-0016 (#217): durable, multi-replica-safe job-state store. When
        # ``DATABASE_URL`` is set we back the session lifecycle + the admission
        # cap/queue with a Postgres ``job_states`` row so state survives a
        # restart and is consistent across replicas. When it is unset we stay on
        # the in-memory path above and WARN that it is not multi-replica safe
        # (per apis/concurrency-conventions.md §1). An explicit ``job_store``
        # (tests) wins over auto-resolution.
        self._job_store = job_store if job_store is not None else _resolve_job_store()
        # #755: the authoritative, cross-replica copy of every session. None
        # keeps today's per-process behaviour (local dev, the CLI, tests).
        self._session_store: SessionStore | None = (
            session_store if session_store is not None else _resolve_session_store()
        )
        if self._session_store is not None:
            self._import_sessions_into_store()
        _warn_if_multi_replica_without_store(self._session_store)

    # ── persistence (opt-in via PFACTORY_PLAN_PERSIST) ──────────────────

    def _load_all(self) -> None:
        """Repopulate ``_sessions`` from ``<store_dir>/*.json`` on startup.

        Best-effort: a missing dir yields an empty store; an unreadable or
        schema-incompatible file is skipped (logged), never fatal.
        """
        try:
            self._store_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("plan store dir unavailable (%s); running in-memory", exc)
            self._persist = False
            return
        for path in sorted(self._store_dir.glob("*.json")):
            try:
                session = PlanSession.model_validate_json(path.read_text())
                self._sessions[session.session_id] = session
            except Exception as exc:  # noqa: BLE001 — skip corrupt/old payloads
                logger.warning("skipping unreadable plan session %s: %s", path.name, exc)
        if self._sessions:
            logger.info("loaded %d persisted plan session(s)", len(self._sessions))

    def _save(self, session: PlanSession) -> None:
        """Persist one session: durable job-state row + optional disk mirror.

        Every state transition funnels through here, so this is where the
        durable RFC-0016 row is kept in lockstep with the in-memory session
        (independent of the opt-in JSON disk mirror). Never raises — both the
        durable mirror and the disk write are best-effort telemetry of state,
        not part of the request's success contract.

        One exception (#758): :class:`StaleSessionError` propagates. It means
        this copy is older than the shared row, so writing it would undo
        someone else's change; nothing is written anywhere and the caller's
        operation stops. Every other store failure is still swallowed.
        """
        # The shared session row first (#755): it is what other replicas read,
        # so it must be written before we consider the transition visible.
        self._upsert_session(session)
        # Durable job-state row (RFC-0016 #217) — runs whether or not the
        # JSON disk mirror is enabled; a no-op when no durable store is set.
        self._mirror(session)
        if not self._persist:
            return
        # Serialise concurrent disk writes (RFC-0016 #217): two offloaded runs
        # writing the same/neighbouring session files must not interleave. The
        # lock is held only for this one session's atomic temp-write + rename.
        with self._store_lock:
            try:
                self._store_dir.mkdir(parents=True, exist_ok=True)
                dest = self._store_dir / f"{session.session_id}.json"
                tmp = dest.with_suffix(".json.tmp")
                tmp.write_text(session.model_dump_json())
                tmp.replace(dest)
            except Exception as exc:  # noqa: BLE001 — disk hiccup must not break a run
                logger.warning("failed to persist plan session %s: %s", session.session_id, exc)

    # ── durable job-state mirror (RFC-0016 #217) ────────────────────────

    def _upsert_session(self, session: PlanSession) -> None:
        """Compare-and-set the session into the shared store (#758).

        Raises :class:`StaleSessionError`, after refreshing the cached copy,
        when the row moved on since this copy was read. Any other store
        failure is logged and swallowed: a DB hiccup must not fail the request.
        """
        store = self._session_store
        if store is None:
            return
        sid = session.session_id
        try:
            expected = session._store_version
            if expected is None:
                # ponytail: a listed copy has no version, so learn it now. A
                # write landing between this read and the upsert is lost; only
                # the store-down fallback in get() writes such a copy.
                row = store.get(sid)
                expected = row[1] if row is not None else 0
            version = store.upsert(
                sid,
                payload=session.model_dump_json(),
                seq=_seq_of(sid),
                tenant_id=session.tenant_id,
                expected_version=expected,
            )
        except Exception as exc:  # noqa: BLE001 — a DB hiccup must not fail the request
            logger.warning("shared plan-session write failed for %s: %s", sid, exc)
            return
        if version is None:
            # A missing row also fails the compare-and-set: tell the two apart.
            try:
                gone = store.get(sid) is None
            except Exception:  # noqa: BLE001 — cannot tell; report the common case
                gone = False
            if gone:
                with self._store_lock:
                    self._sessions.pop(sid, None)
                raise StaleSessionError(f"session {sid!r} was deleted on another replica")
            self._load_from_store(sid)
            raise StaleSessionError(
                f"session {sid!r} was changed by another replica; reload and retry"
            )
        session._store_version = version

    @staticmethod
    def _insert_new(store: SessionStore, session: PlanSession) -> None:
        """Insert a session not yet in the store; a row already there stands.

        Sets the copy's version only when it inserted. May raise a store
        error; the caller decides whether that matters.
        """
        version = store.upsert(
            session.session_id,
            payload=session.model_dump_json(),
            seq=_seq_of(session.session_id),
            tenant_id=session.tenant_id,
            expected_version=0,
        )
        if version is not None:
            session._store_version = version

    def _import_sessions_into_store(self) -> None:
        """One-shot: copy sessions already on disk into the shared store (#755).

        A deployment that gains DATABASE_URL would otherwise start with an
        empty table while its sessions sit on the PVC. Keyed by session_id and
        skipping ids already stored, so running it on every boot is a no-op
        after the first. Best-effort: never raises, never overwrites a row that
        another replica may have advanced.
        """
        store = self._session_store
        if store is None or not self._sessions:
            return
        try:
            existing = store.session_ids()
        except Exception as exc:  # noqa: BLE001
            logger.warning("shared plan-session import skipped (%s)", exc)
            return
        imported = 0
        for session in list(self._sessions.values()):
            if session.session_id in existing:
                continue
            try:
                self._insert_new(store, session)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "shared plan-session import failed for %s: %s", session.session_id, exc
                )
                continue
            if session._store_version is not None:  # None: another replica inserted it first
                imported += 1
        if imported:
            logger.info("imported %d on-disk plan session(s) into the shared store", imported)

    def _mirror(self, session: PlanSession) -> None:
        """Mirror a session's current lifecycle into the durable store.

        Best-effort and never raises: the durable row is the source of truth
        for cross-replica admission + restart recovery, but a transient DB
        hiccup must not break the request (the in-memory session still drives
        the response). A no-op when no durable store is configured.

        Maps the session's native status -> canonical lifecycle in the store
        (see ``server.jobstore.lifecycle``), and carries the terminal payload
        (``result`` / ``error`` / ``usage``) so a terminal row is complete.
        """
        store = self._job_store
        if store is None:
            return
        result: dict | None = None
        error: str | None = None
        if session.status == "emitted":
            result = session.emit_result or session.contract_result or {}
        elif session.status == "rejected":
            result = {"rejected": True}
            error = "plan rejected at review"
        try:
            store.upsert(
                session.session_id,
                service_status=session.status,
                correlation_key=session.correlation_key or session.emitted_issue_number,
                result=result,
                error=error,
                usage=session.usage.model_dump() if session.usage else None,
                artifacts=session.artifact_refs or None,
                tenant_id=session.tenant_id,
            )
        except Exception as exc:  # noqa: BLE001 — durable mirror is best-effort
            logger.warning(
                "failed to mirror plan session %s to the durable store: %s",
                session.session_id,
                exc,
            )

    # ── ingest ─────────────────────────────────────────────────────────

    def _store(self, plan: NormalizedPlan) -> PlanSession:
        session = PlanSession(session_id=plan.plan_id, plan=plan)
        session._store_version = 0  # new: insert, and refuse if the id is taken
        # Guard the shared-store insertion so a concurrent offloaded run (or a
        # concurrent ingest) cannot lose this session via a non-atomic dict
        # write (RFC-0016 #217). `_save` takes the lock separately afterwards.
        with self._store_lock:
            self._sessions[session.session_id] = session
        self._save(session)
        return session

    def ingest_text(
        self,
        text: str,
        *,
        title: str | None = None,
        channel: str = "portal",
        category: str = "",
        template: str = "",
        repo: str | None = None,
        base_ref: str | None = None,
        autonomy_tier: str | None = None,
        origin_issue_number: int | None = None,
        tenant_id: str = "default",
    ) -> PlanSession:
        plan = ingest_text(text, source_channel=channel, title=title, seq=self._next_seq())
        # RFC-0011: carry the label-driven difficulty tier from intake so process()
        # can route hard and emit can override the contract blocks (#182).
        plan = _carry_tier(plan, autonomy_tier)
        session = self._store(plan)
        session.tenant_id = tenant_id or "default"  # #308: stamp at intake
        session.selected_category = category
        session.selected_template = template
        session.repo = repo or None  # RFC-0010: target repo for reconnaissance
        session.base_ref = base_ref or None
        # RFC-0011 hard tier (AIFactory#874): record the origin issue and resolve
        # the correlation key NOW rather than at emit, so the chain back to the
        # filed issue is observable from the moment the session exists (and a
        # session that never reaches emit still carries it).
        session.origin_issue_number = origin_issue_number
        if origin_issue_number is not None:
            session.correlation_key = correlation_key_for(session)
        self._save(session)
        return session

    def ingest_bytes(
        self,
        data: bytes,
        *,
        filename: str,
        title: str | None = None,
        channel: str = "portal",
        category: str = "",
        template: str = "",
        repo: str | None = None,
        base_ref: str | None = None,
        autonomy_tier: str | None = None,
        tenant_id: str = "default",
    ) -> PlanSession:
        plan = ingest_bytes(
            data,
            filename=filename,
            source_channel=channel,
            title=title,
            seq=self._next_seq(),
        )
        plan = _carry_tier(plan, autonomy_tier)  # RFC-0011 (#182)
        session = self._store(plan)
        session.tenant_id = tenant_id or "default"  # #308: stamp at intake
        session.original_filename = filename  # preserve for honouring the doc (#D)
        session.selected_category = category
        session.selected_template = template
        session.repo = repo or None  # RFC-0010: target repo for reconnaissance
        session.base_ref = base_ref or None
        self._save(session)
        return session

    def _next_seq(self) -> int:
        # With a shared store the counter must be allocated in the DB (#755):
        # `self._seq` starts from this process's own view, so two replicas
        # would mint the same NNN-slug id.
        store = self._session_store
        if store is not None:
            try:
                return int(store.next_seq())
            except Exception as exc:  # noqa: BLE001 — fall back to the local counter
                logger.warning("shared session-id allocation failed (%s); using local", exc)
        # Atomic increment under the lock so two concurrent ingests in THIS
        # process can never mint the same sequence number (RFC-0016 #217).
        with self._store_lock:
            self._seq += 1
            return self._seq

    # ── query ──────────────────────────────────────────────────────────

    def list_sessions(self, *, tenant_id: str | None = None) -> list[dict]:
        """Session summaries; ``tenant_id`` filters to one tenant (#308).

        ``None`` (the default, and the single-tenant path) lists everything.
        """
        sessions = self._all_sessions()
        return [s.summary() for s in sessions if tenant_id is None or s.tenant_id == tenant_id]

    def _all_sessions(self) -> list[PlanSession]:
        """Every session: from the shared store when set, else the local dict."""
        store = self._session_store
        if store is None:
            return list(self._sessions.values())
        try:
            payloads = store.list_payloads()
        except Exception as exc:  # noqa: BLE001 — degrade to the cache
            logger.warning("shared plan-session list failed: %s", exc)
            return list(self._sessions.values())
        out: list[PlanSession] = []
        for raw in payloads:
            try:
                out.append(PlanSession.model_validate_json(raw))
            except Exception as exc:  # noqa: BLE001 — skip a corrupt row
                logger.warning("skipping unreadable stored plan session: %s", exc)
        with self._store_lock:
            for session in out:
                self._sessions[session.session_id] = session
        return out

    def get(self, session_id: str) -> PlanSession:
        # Read through the shared store when there is one (#755): the local
        # dict is a cache, and another replica may have written since we loaded
        # it. Without a store this is the historical dict lookup.
        session = self._load_from_store(session_id)
        if session is not None:
            return session
        try:
            return self._sessions[session_id]
        except KeyError:
            raise PlanServiceError(f"unknown session '{session_id}'") from None

    def _load_from_store(self, session_id: str) -> PlanSession | None:
        """Refresh one session from the shared store. None when unavailable.

        Best-effort: a store hiccup degrades to the cached copy rather than
        failing the read — the same contract the durable job-state mirror has.
        """
        store = self._session_store
        if store is None:
            return None
        try:
            row = store.get(session_id)
        except Exception as exc:  # noqa: BLE001 — degrade to the cache
            logger.warning("shared plan-session read failed for %s: %s", session_id, exc)
            return None
        if row is None:
            return None
        raw, version = row
        try:
            session = PlanSession.model_validate_json(raw)
        except Exception as exc:  # noqa: BLE001 — a corrupt row must not break reads
            logger.warning("unreadable stored plan session %s: %s", session_id, exc)
            return None
        session._store_version = version
        with self._store_lock:
            self._sessions[session.session_id] = session
        return session

    def update_plan(
        self,
        session_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        criteria: list[dict[str, str]] | None = None,
    ) -> PlanSession:
        """Apply a human's edits to the plan's authored fields (#692).

        The revise loop: process surfaces gaps, a human fixes the wording, the
        plan is re-processed and re-reviewed. Only the three fields a human
        authors are editable — everything else on the plan is derived by the
        pipeline and would be overwritten by the next process() anyway.

        Editing INVALIDATES sign-off. A review (and an approval) attests to the
        text that was reviewed; letting either survive an edit would let approved
        wording be swapped for unapproved wording while still reading "approved".
        So the session drops back to `ingested` with its review cleared, and the
        caller must process and re-approve. `_save` persists, so a crash between
        the edit and the re-process cannot leave a stale approval behind.
        """
        session = self.get(session_id)
        if session.status in {"emitted", "discarded"}:
            raise PlanServiceError(
                f"session '{session_id}' is {session.status}; edits no longer apply"
            )
        if title is not None:
            if not title.strip():
                raise PlanInputError("title cannot be empty")
            session.plan.title = title
        if description is not None:
            session.plan.description = description
        if criteria is not None:
            if not criteria:
                raise PlanInputError("a plan needs at least one acceptance criterion")
            # A criterion arrives from an HTTP client, so a missing key is
            # input, not a bug: indexing blind turned a malformed body into a
            # KeyError and a 500 (PR #696 review).
            parsed = []
            for i, c in enumerate(criteria):
                missing = [k for k in ("id", "text") if k not in c]
                if missing:
                    raise PlanInputError(
                        f"criterion {i} is missing {' and '.join(missing)}; "
                        "each criterion needs an 'id' and a 'text'"
                    )
                parsed.append(Criterion(id=str(c["id"]), text=str(c["text"])))
            session.plan.criteria = parsed
        session.review = None
        session.status = "ingested"
        self._save(session)
        return session

    def apply_suggestions(
        self, session_id: str, accepted: list[dict[str, Any]]
    ) -> tuple[PlanSession, list[dict[str, Any]]]:
        """Apply the suggestions a human accepted, then hand back what changed (#701).

        ``accepted`` is ``[{"id": ..., "replacement": ...}]``. The client sends
        only the id and the (possibly human-edited) text; HOW to apply it is
        resolved here from the stored annotation, so a client cannot ask for a
        criterion to be rewritten by claiming a different mode.

        Applying does not re-process — the caller decides — but it does go
        through :meth:`update_plan`, so the review and any approval are
        invalidated exactly as a hand edit would invalidate them.

        Returns ``(session, applied)`` where ``applied`` records each change for
        the audit trail. Raises :class:`PlanInputError` for an unknown id, an
        empty replacement, or a criterion that no longer exists — never silently
        skips, because a suggestion the human accepted and that quietly did not
        land is the worst outcome available here.
        """
        session = self.get(session_id)
        annotation = session.annotation
        if annotation is None or not annotation.suggestions:
            raise PlanInputError(f"session '{session_id}' has no suggestions to apply")

        by_id = {s.id: s for s in annotation.suggestions if s.id}
        description = session.plan.description
        criteria = [c.model_copy() for c in session.plan.criteria]
        applied: list[dict[str, Any]] = []

        for item in accepted:
            sid = str(item.get("id") or "")
            suggestion = by_id.get(sid)
            if suggestion is None:
                raise PlanInputError(f"unknown suggestion id '{sid}'")
            replacement = str(item.get("replacement") or suggestion.replacement or "").strip()
            if not replacement:
                raise PlanInputError(
                    f"suggestion '{sid}' has no replacement text to apply; "
                    "edit it first or leave it unselected"
                )

            if suggestion.mode == "replace_criterion":
                target = _criterion_id(suggestion.suggestion)
                match = next((c for c in criteria if c.id == target), None)
                if match is None:
                    raise PlanInputError(
                        f"suggestion '{sid}' rewrites criterion '{target}', "
                        "which is no longer in this plan"
                    )
                match.text = replacement
            else:
                # append_tag, insert_section and anything a human hand-drafted
                # all land in the description. Blank-line separated so an
                # appended `key: value` tag stays on its own line, which is what
                # the template tag scan needs to see it.
                description = (
                    f"{description.rstrip()}\n\n{replacement}\n" if description else replacement
                )
            applied.append(
                {
                    "id": sid,
                    "mode": suggestion.mode,
                    "suggestion": suggestion.suggestion,
                    "source": suggestion.source,
                }
            )

        session = self.update_plan(
            session_id,
            description=description,
            criteria=[{"id": c.id, "text": c.text} for c in criteria],
        )
        return session, applied

    # ── process (the pipeline core) ────────────────────────────────────

    def process(self, session_id: str, *, external_runner=None, llm=None) -> PlanSession:
        """Detect → plan-type → enrich → decompose → synthesize → review gates.

        When no ``external_runner`` is supplied, the provider-MCP runner is used by
        default so live runs get provider best-practice findings + suggest-install
        advisories (#3). It never raises and adds no score penalty when providers
        are absent, so the default is safe.

        ``llm`` is the optional decomposer seam: when supplied, decomposition runs
        through it and its token usage is recorded on the session (#60). Left
        ``None`` (the default), the pipeline is fully deterministic and usage
        stays at zero.
        """
        if external_runner is None:
            from plan.providers.review_runner import provider_runner

            external_runner = provider_runner
        session = self.get(session_id)
        # Transient sub-states so a background-run session animates on the board:
        # in-progress while we detect/enrich/decompose/synthesize, AI-review while
        # the gates run. (Synchronous callers just see the final "processed".)
        session.status = "processing"

        plan, descriptor = self._detect_and_plan_type(session)
        epic = self._decompose(session, plan, descriptor, llm=llm)
        artifacts = synthesize(plan, epic, descriptor=descriptor)

        composed_runner = self._build_review_runner(
            session, plan, epic, descriptor, external_runner
        )

        session.status = "reviewing"
        review = run_gates(plan, epic, external_runner=composed_runner)

        session.plan = plan
        session.epic = epic
        session.artifacts = artifacts
        session.review = review
        # Honour the document: anchored, cited suggestions + improved draft (#D).
        session.annotation = annotate_plan(plan, review)
        session.status = "processed"
        self._save(session)
        # Running-cost: planning + governance gates spent LLM tokens, but the
        # session is only ``processed`` (awaiting human approval), not terminal —
        # notify_completion fires only on emitted/rejected, so the accrued cost
        # would never reach the cockpit for a plan parked at review (or abandoned
        # before approval). Emit a non-terminal usage snapshot here. Best-effort.
        emit_usage_snapshot(session)
        return session

    # ── execution model: pooled worker, NOT Job-per-task (RFC-0016 #218) ─
    #
    # RFC-0016 §5(c) gives PFactory an explicit choice for Phase-2 execution:
    # a k8s **Job-per-task** OR a **thread/process-pool worker model**, noting
    # that "default planning is deterministic (no LLM) and light, so PFactory
    # may run a thread/process-pool worker model rather than full Job-per-task
    # if Jobs prove heavy for sub-second planning."
    #
    # DECISION (#218): PFactory uses the **bounded pooled-worker model**, NOT a
    # Job-per-task. ``process_async`` offloads the pipeline onto a worker thread
    # (:func:`asyncio.to_thread`) under an admission cap whose state is durable +
    # multi-replica-safe in Postgres (:meth:`_durable_admit`, #220). This IS the
    # Phase-2 execution model for planning. WHY:
    #
    #   * Planning is deterministic and sub-second (no LLM by default): detect →
    #     plan-type → decompose → synthesize → gates are pure-Python passes over
    #     the contract. A k8s Job's spin-up cost (image pull, pod schedule, warm
    #     nix-store mount) is measured in seconds — i.e. >> the work it would run,
    #     so Job-per-plan would make planning slower and burn cluster churn for no
    #     isolation benefit.
    #   * The two scaling hazards a Job would address are already handled here:
    #     event-loop starvation (fixed by the off-loop worker, #219) and unbounded
    #     fan-out (fixed by the durable, cross-replica admission cap, #220). Multi-
    #     replica correctness comes from the Postgres ``SELECT ... FOR UPDATE``
    #     slot grant, not from the pod boundary.
    #
    # The heavy outlier is the optional RFC-0010 reconnaissance git-clone of a
    # large target repo (:meth:`_reconnoiter`) — but it is read-only and static
    # (never executes repo code; see :mod:`plan.recon.clone`) and bounded by the
    # same admission cap, so it does not by itself justify a Job pod per plan. If a
    # genuinely heavy/governed (LLM) planning path ever lands, the shared
    # ``scripts/job_dispatch.py`` builder + ``apis/concurrency-conventions.md`` §3
    # are the seam to add an opt-in, env-gated Job path for THAT path only — it is
    # deliberately not added now (no consumer warrants it). See CONTRIBUTING.md
    # ("Concurrency / execution model").
    #
    # ── async offload + admission control (RFC-0016 #217) ───────────────

    def _admission_semaphore(self) -> asyncio.Semaphore | None:
        """The per-event-loop admission gate, or ``None`` when unlimited.

        ``process()`` is CPU/IO-bound (git clone, decompose, gates). Running it
        in a worker thread (see :meth:`process_async`) frees the event loop, but
        an unbounded fan-out of worker threads is its own hazard — so we cap the
        number of in-flight runs with an :class:`asyncio.Semaphore` sized from
        ``PFACTORY_MAX_CONCURRENT_PLANS`` (default 4; ``<=0`` means unlimited).

        The semaphore is created lazily and cached *per running loop* (it must be
        bound to the loop that awaits it), so a fresh test loop gets a fresh gate.
        """
        try:
            cap = int(os.environ.get("PFACTORY_MAX_CONCURRENT_PLANS", "4"))
        except ValueError:
            cap = 4
        if cap <= 0:
            return None  # unlimited — no admission gate
        loop = asyncio.get_running_loop()
        cached = getattr(self, "_admission", None)
        if cached is None or cached[0] is not loop or cached[1] != cap:
            sem = asyncio.Semaphore(cap)
            self._admission = (loop, cap, sem)
            return sem
        return cached[2]

    async def process_async(
        self, session_id: str, *, external_runner=None, llm=None
    ) -> PlanSession:
        """Run :meth:`process` off the event loop, under the admission cap.

        Behaviour-identical to ``await``-ing the synchronous :meth:`process`: the
        return value and exceptions are unchanged. The difference is *where* the
        blocking pipeline runs — in a worker thread via :func:`asyncio.to_thread`
        — so the single uvicorn event loop stays free to serve ``/api/health``
        and other sessions concurrently while one plan is being processed.

        When the admission cap is reached, callers WAIT (queue) on the gate
        rather than erroring; ``/api/health`` never goes through this path so it
        is unaffected.

        Admission gate selection (RFC-0016 #217):
          - durable store set  → grant the slot in Postgres via a
            ``SELECT ... FOR UPDATE`` transaction (:meth:`_durable_admit`), so
            the cap holds ACROSS replicas and survives a restart.
          - no durable store   → the in-process :class:`asyncio.Semaphore`
            (single-pod dev only).
        """
        if self._job_store is not None:
            return await self._durable_admit(session_id, external_runner=external_runner, llm=llm)
        sem = self._admission_semaphore()
        if sem is None:
            return await asyncio.to_thread(
                self.process, session_id, external_runner=external_runner, llm=llm
            )
        async with sem:
            return await asyncio.to_thread(
                self.process, session_id, external_runner=external_runner, llm=llm
            )

    async def _durable_admit(
        self, session_id: str, *, external_runner=None, llm=None
    ) -> PlanSession:
        """Grant a slot via the durable store, then run ``process`` off-loop.

        The slot grant is a ``SELECT ... FOR UPDATE`` transaction in the store
        (:meth:`server.jobstore.JobStateStore.try_start`): two replicas racing
        for the last slot serialise on the row locks, so the cap can never be
        exceeded and a ``job_id`` cannot be double-started. When the cap is full
        the store raises ``SlotDenied`` and we WAIT (poll with bounded backoff)
        until a slot frees — preserving the in-memory semaphore's queue-don't-
        error behaviour. The terminal transition inside ``process`` flips the
        row off ``running`` via ``_save``/``_mirror``, freeing the slot.

        Crash safety (#300): the two paths above only free the slot when this
        process lives long enough to write. A SIGKILL (OOM, eviction, node loss,
        a failed liveness probe) runs NO cleanup code — not ``except``, not
        ``finally``, not an atexit/signal handler — so the row would stay
        ``running`` forever and permanently burn one of the cap's slots for
        every replica. We therefore hold a LEASE while ``process`` runs: the
        grant stamps an expiry, ``_renew_lease`` below refreshes it from this
        (free) event loop, and if this pod dies the lease simply lapses and the
        next grant reclaims the row. Nothing here has to run for that to work —
        that is the point.
        """
        # Deferred + optional import (see _resolve_job_store): only reached when
        # a durable store is configured, so server.jobstore is importable here.
        from server.jobstore import (  # noqa: PLC0415
            SlotDenied,
            lease_heartbeat_interval,
        )

        store = self._job_store
        delay = 0.05
        while True:
            try:
                # Grant happens in a worker thread (the store's sync API drives
                # its own loop via asyncio.run; calling it on the event loop
                # would conflict), so run it via to_thread.
                await asyncio.to_thread(store.try_start, session_id)
                break
            except SlotDenied:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 1.0)  # bounded exponential backoff
        # Renew this job's lease while the pipeline runs, so a HEALTHY long plan
        # is never reclaimed as a dead one. Runs on the event loop, which the
        # off-loop worker deliberately keeps free (#219).
        renew = asyncio.create_task(
            self._renew_lease(store, session_id, lease_heartbeat_interval())
        )
        try:
            return await asyncio.to_thread(
                self.process, session_id, external_runner=external_runner, llm=llm
            )
        except Exception as exc:
            # A crashed run must not leak its concurrency slot: mark the durable
            # row failed (terminal, off `running`) with a reason so the cap
            # frees and never-overclaim holds. Best-effort; re-raise the
            # original error so the caller's contract is unchanged.
            try:
                await asyncio.to_thread(
                    store.upsert,
                    session_id,
                    service_status="failed",
                    error=f"process() raised: {exc}",
                )
            except Exception:  # noqa: BLE001 — never mask the original failure
                logger.warning(
                    "failed to mark durable job %s failed after process() raised",
                    session_id,
                )
            raise
        finally:
            renew.cancel()

    @staticmethod
    async def _renew_lease(store, job_id: str, interval: float) -> None:
        """Refresh ``job_id``'s lease every ``interval`` seconds until cancelled.

        Best-effort: a transient DB hiccup must not kill the run, and one missed
        renewal is harmless (the TTL is several intervals wide). Stops early once
        the row is no longer ``running`` — the job went terminal on its own.
        """
        while True:
            await asyncio.sleep(interval)
            try:
                if not await asyncio.to_thread(store.heartbeat, job_id):
                    return
            except Exception as exc:  # noqa: BLE001 — a renewal is best-effort
                logger.warning("failed to renew the lease for plan %s: %s", job_id, exc)

    def _detect_and_plan_type(self, session: PlanSession) -> tuple[NormalizedPlan, object]:
        """Detect → reconnoiter → route tier → enrich → plan-type select.

        Returns the fully-classified, enriched plan and its plan-type descriptor.
        """
        # RFC-0010: reconnaissance runs between detect and plan-type — software is
        # already known (skip recon otherwise), and the RepoMap must inform
        # plan-type selection, decomposition and the language used at emit.
        detected = detect_apply(session.plan)
        detected = self._reconnoiter(session, detected)
        # Factory#273 (#283): lightweight injection scan over the intake TEXT of
        # untrusted (issue/spec-derived) content — one more classification
        # signal on the RFC-0011 tier seam, not a new pipeline stage. A flagged
        # body forces tier=hard below, so it lands in human review instead of
        # auto-tiering. Trusted operator text skips the scan.
        session.injection_scan = self._scan_intake_text(detected)
        # RFC-0011 (#182): resolve the final difficulty tier now that recon has
        # set change_mode. A migration (rewrite) forces `hard` — opus, full
        # decompose (PFactory never skips planning in process — the wave executor
        # skip only applies downstream in AIFactory), blocking human approval.
        # Highest of {carried, forced} wins. Stamped on the plan so the emit-time
        # tier_profile (#181) overrides execution/review_tier/tfactory accordingly.
        resolved_tier = _route_tier(
            detected.autonomy_tier,
            is_migration=detected.change_mode == "migration",
            injection_flagged=session.injection_scan.get("verdict") == "flagged",
        )
        if resolved_tier is not None and resolved_tier != detected.autonomy_tier:
            detected = detected.model_copy(update={"autonomy_tier": resolved_tier})
        plan = self._enrich(plan_type_apply(detected))
        descriptor = select_for(plan)
        return plan, descriptor

    @staticmethod
    def _scan_intake_text(plan: NormalizedPlan) -> dict:
        """Injection-scan verdict for the plan's intake text (Factory#273).

        TEXT ONLY — scans the ingested issue/spec body (``raw_text``, falling
        back to title + description), never repo content (that is AIFactory's
        pre-coder gate, AIFactory#805). Trusted operator content is skipped.
        """
        from plan.detect.content_scan import scan_text  # noqa: PLC0415

        if plan.content_trust != "untrusted_user_content":
            return {"verdict": "skipped", "reason": "content not marked untrusted"}
        text = plan.raw_text or f"{plan.title}\n{plan.description}"
        hits = scan_text(text)
        if hits:
            return {
                "verdict": "flagged",
                "reason": f"likely injection payload: {'; '.join(hits[:3])}",
            }
        return {"verdict": "pass", "reason": ""}

    def _decompose(
        self, session: PlanSession, plan: NormalizedPlan, descriptor: object, *, llm=None
    ) -> EpicPlan:
        """Decompose into an epic, record LLM usage, inject implicit requirements."""
        usage_sink: list[PlanUsage] = []
        epic = decompose(plan, descriptor=descriptor, llm=llm, usage_sink=usage_sink)
        for u in usage_sink:
            session.record_usage(u)
        # Complete the plan (RFC-0008 §3.1, #166): a user describes feature intent
        # but never the implicit runtime requirements of a deployable service
        # (boots / declares dependencies / health check / deployable). Inject them
        # as acceptance criteria BEFORE synthesize + gates so the completeness lens
        # and the service-requirements-covered readiness check see — and enforce —
        # a plan that demands the service actually runs.
        from plan.decompose.implicit_requirements import inject_into_epic

        inject_into_epic(plan, epic, descriptor)
        return epic

    def _build_review_runner(
        self,
        session: PlanSession,
        plan: NormalizedPlan,
        epic: EpicPlan,
        descriptor: object,
        external_runner,
    ):
        """Assemble the composed review runner: feasibility + deployment + template.

        Runs the additive analysis stages (each stamps the epic + yields findings)
        and returns a runner that folds their findings into the provider runner's
        output. Behaviour-preserving: each stage runs in the same order as before
        (feasibility → deployment → template) so the epic mutations and finding
        ordering are unchanged.
        """
        # Feasibility (#C): price the proposed shape, verify access. Estimates are
        # attached to the epic; findings are folded into the feasibility lens via a
        # composed external runner. RFC-0014: no dev-day effort estimate — the
        # scorer's difficulty/risk/autonomy on the contract replaces it.
        from plan.feasibility import assess_feasibility

        feasibility = assess_feasibility(plan, epic)
        epic.cost_estimate = feasibility.cost
        epic.access_requirements = feasibility.access

        # Deployment-aware planning (RFC-0013, #190): derive the `deployment`
        # block — CI/deploy surface, risk/scan/gate policy, best-effort DORA
        # context, deploy readiness — from reconnaissance + blast radius. Runs
        # BETWEEN feasibility and gates so the deployment ACs + readiness gaps it
        # injects are seen and enforced by review. Additive: yields no block/no
        # findings when there is no deployment dimension. Never raises.
        deployment_review_findings = _attach_deployment(plan, epic)

        # Template policy (#E). Enforcement is OPT-IN: a template's embedded policy
        # (required tags / allowed regions / IAM / baselines) gates review only when
        # the user explicitly selected it at intake — auto-matching is recorded as a
        # non-gating suggestion (help, never override).
        template_findings = _template_findings(session, plan, descriptor)

        def _composed_runner(p, e):
            out = list(external_runner(p, e)) if external_runner else []
            out.extend(feasibility.findings)
            out.extend(deployment_review_findings)
            out.extend(template_findings)
            return out

        return _composed_runner

    def _reconnoiter(self, session: PlanSession, plan: NormalizedPlan) -> NormalizedPlan:
        """Attach a static :class:`RepoMap` of the target repo (RFC-0010, #108).

        Reads the repo **statically, read-only** — never executes its code (see
        :mod:`plan.recon.clone`). Skipped for non-software plans and when no
        target repo was supplied at ingest; in both cases the plan stays in
        greenfield mode (``repo_map`` left ``None``). Never raises:
        :func:`reconnoiter` already degrades unreachable repos to an unavailable
        RepoMap, so a failure here cannot break the run.
        """
        if plan.target_kind == "non-software" or not session.repo:
            return plan
        repo_map = reconnoiter(session.repo, session.base_ref)
        # RFC-0015 §3.1: capture the per-project constitution from the same
        # read-only checkout so emit + the readiness check can consume it without
        # cloning again. Best-effort: None when the repo carries no constitution.
        from plan.emit.constitution import (  # noqa: PLC0415 - lazy: keep emit out of the service import graph
            read_constitution_md,
        )

        constitution_md = read_constitution_md(session.repo, session.base_ref)
        # RFC-0010 #111: a directional rewrite ("port X from L1 to L2", L1 == repo
        # language) is a migration, not a #109 language conflict.
        signal = classify_migration(plan, repo_map)
        change_mode = classify_change_mode(repo_map, is_migration=signal.is_migration)
        update: dict = {"repo_map": repo_map, "change_mode": change_mode}
        if constitution_md is not None:
            update["constitution_md"] = constitution_md
        if signal.is_migration:
            update["source_language"] = signal.source_language
            update["target_language"] = signal.target_language
            # Extract the behavioral contract (AST-only) + declare the migration
            # metadata the downstream factories consume.
            contract = inspect_source(
                session.repo, session.base_ref, signal.source_language or "python"
            )
            if contract is not None:
                update["migration"] = {
                    "source_language": signal.source_language,
                    "target_language": signal.target_language,
                    "behavioral_contract": contract.to_dict(),
                    "golden_corpus": build_golden_corpus_manifest(contract),
                    "equivalence": build_equivalence_block(
                        contract, signal.target_language or "rust"
                    ),
                }
        return plan.model_copy(update=update)

    def _enrich(self, plan: NormalizedPlan) -> NormalizedPlan:
        """Attach live infra context from the adapters named in
        ``PFACTORY_ENRICH_ADAPTERS`` (comma-separated, e.g. ``aws``).

        Off by default (empty env). Each adapter's ``to_enrichment()`` is
        read-only and never raises, so a failed/absent environment just yields
        an ``available: false`` finding.
        """
        text = " ".join(
            [
                plan.title,
                plan.description,
                *(c.text for c in plan.criteria),
                plan.raw_text or "",
            ]
        )
        enrichment = plan.enrichment.model_copy(deep=True)

        # Does this plan target cloud/cluster infrastructure at all? Shared
        # heuristic so the readiness `enrichment-integrity` check, the infra
        # adapters below and the knowledge connectors below THAT can never
        # disagree — which they did, and #386 is the result.
        from plan.enrich.relevance import is_cloud_relevant

        cloud_relevant = is_cloud_relevant(plan)

        # ── infra adapters (probe AWS / k8s / …) ───────────────────────
        adapters = [
            n.strip()
            for n in os.environ.get("PFACTORY_ENRICH_ADAPTERS", "").split(",")
            if n.strip()
        ]
        if adapters:
            # Only probe cloud/cluster infra when the plan actually targets it.
            cloud_adapters = {"aws", "azure", "gcp", "kubernetes", "openshift"}
            adapters = [n for n in adapters if n not in cloud_adapters or cloud_relevant]
        if adapters:
            from plan.enrich.base import get_adapter

            for mod in ("kubernetes", "openshift", "azure", "aws", "gcp"):
                try:
                    __import__(f"plan.enrich.adapters.{mod}")
                except Exception:
                    pass
            # Replace prior snapshots so a re-process doesn't multiply findings.
            infra = [
                e
                for e in enrichment.infra
                if not (isinstance(e, dict) and e.get("adapter") in adapters)
            ]
            for name in adapters:
                try:
                    infra.append(get_adapter(name).to_enrichment())
                except Exception as exc:
                    infra.append({"adapter": name, "available": False, "error": str(exc)})
            enrichment = enrichment.model_copy(update={"infra": infra})

        # ── knowledge connectors (review wiki / search best practices) ──
        # `best-practices` is a wholly cloud-infrastructure catalogue (EKS, RDS,
        # ElastiCache, Well-Architected), so it is gated on the SAME judgement
        # the infra adapters above are gated on. Without that, a plan whose own
        # cost estimate reads `source: no-resources` was cited AWS RDS Multi-AZ
        # and EKS network management — matched out of its own OUT-OF-SCOPE
        # sentence ("Out of scope: ... a database") — under a `why` string that
        # asserts "the plan should follow it" (#386).
        connectors = [
            n.strip()
            for n in os.environ.get("PFACTORY_ENRICH_CONNECTORS", "").split(",")
            if n.strip() and (n.strip() != "best-practices" or cloud_relevant)
        ]
        if connectors:
            from plan.enrich.knowledge.base import get_connector

            for mod in (
                "git_markdown",
                "backstage",
                "confluence",
                "gitbook",
                "notion",
                "best_practices",
            ):
                try:
                    __import__(f"plan.enrich.knowledge.{mod}")
                except Exception:
                    pass
            wiki_root = os.environ.get("PFACTORY_WIKI_ROOT")
            knowledge = [
                k
                for k in enrichment.knowledge
                if not (isinstance(k, dict) and k.get("connector") in connectors)
            ]
            for name in connectors:
                try:
                    kw = _knowledge_connector_kwargs(name, wiki_root)
                    knowledge.extend(get_connector(name, **kw).to_enrichment(text, limit=8))
                except Exception:
                    continue
            enrichment = enrichment.model_copy(update={"knowledge": knowledge})

        return plan.model_copy(update={"enrichment": enrichment})

    # ── approval ───────────────────────────────────────────────────────

    def _refresh_readiness(self, session: PlanSession) -> PlanSession:
        """Bring the stored readiness verdicts up to the current gate logic (#450).

        Every gate that consults ``unwaived_hard_failures`` routes through here
        first, so a fixed check unblocks the sessions it wrongly failed without a
        re-plan and without a waiver for a defect that never existed. Cheap and
        LLM-free (the checks are pure); the recomputed verdicts are persisted so
        the stored record matches the decision that was just made.
        """
        if session.review is None or session.epic is None:
            return session
        refresh_readiness(session.review, session.plan, session.epic)
        self._save(session)
        return session

    def re_gate(self, session_id: str) -> PlanSession:
        """Recompute readiness alone, without re-running planning (#450).

        The cheap, non-destructive remedy for a stale verdict: no LLM, no lost
        review state, no waiver on the audit trail.
        """
        session = self.get(session_id)
        if session.review is None or session.epic is None:
            raise PlanServiceError("process the plan before re-running the readiness gate")
        return self._refresh_readiness(session)

    def approve(
        self, session_id: str, *, approver: str, feedback: str | None = None
    ) -> PlanSession:
        session = self.get(session_id)
        if session.review is None:
            raise PlanServiceError("process the plan before approving")
        self._refresh_readiness(session)
        approve_review(session.review, session.plan, approver=approver, feedback=feedback)
        session.status = "approved"
        self._save(session)
        return session

    def waive(
        self, session_id: str, *, check_ids: list[str], reason: str, waived_by: str
    ) -> PlanSession:
        """Record a human waiver of one or more hard readiness failures (#77).

        Mirrors :meth:`approve`'s shape: requires the plan to have been processed
        (so a readiness report exists). Lets :class:`WaiverError` propagate — the
        route maps it to 400.
        """
        from plan.review.readiness.waiver import waive as waive_review

        session = self.get(session_id)
        if session.review is None:
            raise PlanServiceError("process the plan before waiving")
        # #450: waive against the CURRENT verdict. Waiving a stale failure would
        # record a human accepting a risk the code no longer finds — the audit
        # trail's worst outcome. `waive_review` refuses a check that is not
        # currently a hard failure, so refreshing first is what makes that true.
        self._refresh_readiness(session)
        waive_review(
            session.review,
            session.plan,
            check_ids=check_ids,
            reason=reason,
            waived_by=waived_by,
        )
        self._save(session)
        return session

    def reject(self, session_id: str, *, approver: str, feedback: str) -> PlanSession:
        session = self.get(session_id)
        if session.review is None:
            raise PlanServiceError("process the plan before rejecting")
        reject_review(session.review, session.plan, approver=approver, feedback=feedback)
        session.status = "rejected"
        # Terminal too: emit the completion event with a synthetic key (no issue#).
        session.correlation_key = correlation_key_for(session)
        notify_completion(session)
        self._save(session)
        return session

    def discard(self, session_id: str, *, actor: str, reason: str) -> PlanSession:
        """Abandon a session outright — "this should not exist" (#360).

        Deliberately NOT the same thing as :meth:`reject`, and deliberately not
        implemented in terms of it:

        * ``reject`` means *this plan is wrong, fix it*. It writes a rejection
          into ``session.review``, so it REQUIRES a processed session, and it
          leaves the card in ``human_review`` awaiting that edit.
        * ``discard`` means *this was never real work* — a mis-ingested probe, a
          duplicate, an experiment. It is valid from ANY status, which is the
          whole point: it works precisely where ``reject`` cannot, on a session
          that was ingested and never planned.

        Before this existed such a session could not be cleared at all: ``reject``
        refused for want of a review, no delete route existed, and the only
        columns reaching ``done`` were ``approved``/``emitted`` — so the only way
        to clear the cockpit was to record junk as an approved plan. A false
        audit trail is worse than a stuck card, so the honest fix is a status
        that says what actually happened.

        ``reason`` is required rather than optional: removal is the one
        transition with no artefact left behind to explain itself, so if nobody
        has to say why, the record of why is simply lost.

        Discarding an already-discarded session is a no-op rather than an error —
        the caller's intent is satisfied, and a cleanup action that fails on a
        retry invites exactly the double-click that would otherwise 400.
        """
        session = self.get(session_id)
        if session.status == "discarded":
            return session
        # Captured BEFORE the mutation below — board_state() is derived from
        # status, so reading it afterwards would report "done" for every discard
        # and lose the only interesting part of the record.
        from_status = session.status
        session.status = "discarded"
        session.discard = {
            "actor": actor,
            "reason": reason,
            "at": datetime.now(UTC).isoformat(),
            # The status it was abandoned FROM. Without it the record cannot
            # distinguish a probe binned before anyone looked at it from a fully
            # reviewed plan someone decided to drop.
            "from_status": from_status,
        }
        # Terminal: same synthetic-key path as reject, since a discarded session
        # may never have reached emit and so carries no issue number.
        session.correlation_key = correlation_key_for(session)
        notify_completion(session)
        self._save(session)
        return session

    # ── emit ───────────────────────────────────────────────────────────

    @contextmanager
    def _emit_lock(self, session_id: str, *, live: bool = False) -> Iterator[None]:
        """One emit per session: in this process, and for live emits everywhere.

        The thread lock covers this process (#725). A live emit with a shared
        store also takes the session row's emit lease (#758), so a second
        replica is refused rather than creating a second epic. A store that
        cannot be asked refuses too: we cannot confirm no emit is running. Dry
        runs create nothing, so they never wait on or block a real emit.
        """
        with self._store_lock:
            lock = self._emit_locks.setdefault(session_id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise EmitInProgressError(f"an emit is already running for session {session_id!r}")
        try:
            store = self._session_store if live else None
            if store is None:
                yield
                return
            owner = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
            cached = self._sessions.get(session_id)
            unknown = False
            try:
                # The lease lives on the row, and a missing row cannot be
                # leased: store a session that never reached the store (it was
                # down at the time), or False would falsely mean "held".
                if store.get(session_id) is None:
                    if cached is None:
                        unknown = True
                    else:
                        self._insert_new(store, cached)
                acquired = not unknown and store.acquire_emit_lease(
                    session_id, owner, _emit_lease_ttl()
                )
            except Exception as exc:  # noqa: BLE001 — fail closed, never emit unguarded
                logger.warning("emit lease for %s could not be taken: %s", session_id, exc)
                raise EmitInProgressError(
                    f"cannot confirm no concurrent emit of session {session_id!r}; retry shortly"
                ) from None
            if unknown:
                raise PlanServiceError(f"unknown session '{session_id}'")
            if not acquired:
                raise EmitInProgressError(
                    f"an emit of session {session_id!r} is already running on another replica"
                )
            try:
                yield
            finally:
                try:
                    store.release_emit_lease(session_id, owner)
                except Exception as exc:  # noqa: BLE001 — the TTL frees it
                    logger.warning("emit lease for %s was not released: %s", session_id, exc)
        finally:
            lock.release()

    def emit(self, session_id: str, **kwargs: Any) -> PlanSession:
        """Emit the epic + children to GitHub; see :meth:`_emit`."""
        live = not kwargs.get("dry_run", True)
        with self._emit_lock(session_id, live=live):
            if live and self._session_store is not None:
                # Under the lease, read what the last holder left: if another
                # replica already finished this emit, there is nothing to do.
                # A partial emit (#119) is not "emitted", so it still resumes.
                current = self.get(session_id)
                if current.status == "emitted" and current.emitted_issue_number is not None:
                    return current
            return self._emit(session_id, **kwargs)

    def emit_contract(self, session_id: str, **kwargs: Any) -> PlanSession:
        """Emit the signed Task Contract; see :meth:`_emit_contract`."""
        with self._emit_lock(session_id, live=not kwargs.get("dry_run", True)):
            return self._emit_contract(session_id, **kwargs)

    def _emit(
        self,
        session_id: str,
        *,
        repo: str,
        dry_run: bool = True,
        gh=None,
        docs_connections: list[dict] | None = None,
        docs_selected: list[str] | None = None,
    ) -> PlanSession:
        from plan.emit.github_emitter import EmitResult, emit_to_github
        from plan.emit.labels import pfactory_meta_block, taxonomy_labels

        session = self.get(session_id)
        if session.epic is None:
            raise PlanServiceError("process the plan before emitting")
        # #450: `emit_to_github` refuses unless `review.ready_to_emit(plan)`, which
        # reads the readiness report — so it needs the same refresh as approve.
        if not dry_run:
            self._refresh_readiness(session)
        # A live emit needs a real `gh` runner (#52). Construct the default CLI
        # runner when none is injected; tests/callers may pass a fake. Dry-run
        # needs no runner — nothing is created.
        if gh is None and not dry_run:
            from plan.emit.gh_runner import GhCliRunner

            gh = GhCliRunner(repo)
        # Apply the taxonomy (#H): pfactory + type/plan-type/priority/sev labels and
        # the machine-readable pfactory:meta block AIFactory/TFactory parse.
        labels = taxonomy_labels(session.plan, session.epic, session.review)
        meta = pfactory_meta_block(session.plan, session.epic, session.review)
        # Idempotent re-emit (#119): if a prior attempt already created the epic
        # (and possibly some children) for this session, reuse them instead of
        # creating duplicates. The numbers survive a partial failure because we
        # persist them below regardless of `result.errors`.
        prior = session.emit_result or {}
        existing_epic = session.emitted_issue_number or prior.get("epic_number")
        existing_children = prior.get("child_numbers") or {}

        def _save_progress(epic_number: int, child_numbers: dict[str, int]) -> None:
            # Persist each number as its issue appears, so a kill mid-emit leaves
            # the #119 resume path something to resume from (#725).
            session.emitted_issue_number = epic_number
            session.emit_result = EmitResult(
                dry_run=False, epic_number=epic_number, child_numbers=child_numbers
            ).model_dump()
            self._save(session)

        result = emit_to_github(
            session.epic,
            repo=repo,
            review=session.review,
            plan=session.plan,
            dry_run=dry_run,
            extra_labels=labels,
            meta_block=meta,
            gh=gh,
            existing_epic_number=existing_epic if not dry_run else None,
            existing_child_numbers=existing_children if not dry_run else None,
            on_progress=None if dry_run else _save_progress,
        )
        session.emit_result = result.model_dump()
        # Persist the epic number as soon as it exists — even on a PARTIAL emit
        # (some children failed). This is what makes a retry idempotent: the next
        # emit reuses this epic rather than spawning a duplicate (#119).
        if not dry_run and result.epic_number is not None:
            session.emitted_issue_number = result.epic_number
        if not dry_run and not result.errors:
            session.status = "emitted"
            # Persist the shared correlation key, then emit the terminal
            # completion event (#47).
            session.correlation_key = correlation_key_for(session)
            notify_completion(session)
            # Documentation emit (P1) — gated + best-effort. Default OFF, never
            # raises, so it cannot affect the GitHub emit / completion above.
            try:
                from plan.emit.docs import emit_docs, is_enabled

                if is_enabled():
                    session.docs_result = emit_docs(
                        session,
                        repo=repo,
                        connections=docs_connections,
                        selected=docs_selected,
                    )
            except Exception:
                logger.warning("plan docs emit failed", exc_info=True)
            # RFC-0016 #190: upload the plan's documents (plan/spec markdown +
            # audit pack) to the object store and stamp the artifacts[] URIs onto
            # the durable row (via _save -> _mirror -> upsert). Fail-open: never
            # blocks or changes the emit.
            try:
                from plan.emit.plan_artifacts import (  # noqa: PLC0415 - lazy by design
                    emit_plan_artifacts,
                )

                session.artifact_refs = emit_plan_artifacts(
                    session,
                    job_id=session.session_id,
                    correlation_key=session.correlation_key or session.emitted_issue_number,
                )
            except Exception:  # noqa: BLE001 — artifact emit must never break a plan emit
                logger.warning("plan artifact emit failed", exc_info=True)
        self._save(session)
        return session

    def _emit_contract(
        self,
        session_id: str,
        *,
        repo: str | None = None,
        project_id: str | None = None,
        dry_run: bool = True,
        http=None,
        base_url: str | None = None,
        key: str | None = None,
    ) -> PlanSession:
        """Emit the RFC-0002 signed Task Contract v2 for a session (#65).

        Assembles the full contract (plan + execution + tfactory + verification),
        validates + signs it, and (unless ``dry_run``) POSTs it to AIFactory's
        skip-planning ``/api/tasks/from-plan`` endpoint. Dry-run by default; the
        result is stored on ``session.contract_result``.
        """
        from plan.emit.contract_emit import emit_contract as _emit_contract

        session = self.get(session_id)
        if session.epic is None:
            raise PlanServiceError("process the plan before emitting a contract")
        # RFC-0011 (#182): a `hard` tier means blocking — a live contract emit is
        # HELD until a human approves. (low/medium and dry-runs are unaffected.)
        # The GitHub epic emit is already gated by review.ready_to_emit; the
        # contract fast-path needs the same human gate so opus/migration work
        # cannot skip sign-off. "emitted" is accepted too: a hard session only
        # reaches it after emit_to_github enforced ready_to_emit (human approval
        # recorded), so approve -> emit issues -> emit-contract must not 400 (#317).
        if (
            not dry_run
            and session.plan.autonomy_tier == "hard"
            and session.status not in ("approved", "emitted")
        ):
            raise PlanServiceError(
                "tier=hard requires human approval before emitting the contract: "
                f"approve session '{session_id}' first (status={session.status!r})"
            )
        # #326: the same hard readiness failures that block `approve` must block a
        # live emit, or skipping approve walks straight around the gate.
        if not dry_run and session.review is not None and session.review.readiness is not None:
            # #450: same refresh as approve — a live emit must be blocked by what
            # the checks say now, not by what a pre-fix build once said.
            self._refresh_readiness(session)
            unwaived = session.review.readiness.unwaived_hard_failures(session.plan)
            if unwaived:
                failing = ", ".join(r.check_id for r in unwaived)
                raise PlanServiceError(
                    f"cannot emit: unwaived hard readiness failures ({failing}). "
                    "Fix the plan or record a waiver first."
                )
        base = base_url or os.environ.get("PFACTORY_AIFACTORY_API_URL", "http://localhost:3101")
        pid = project_id or repo or session.plan.plan_id
        corr = session.correlation_key or correlation_key_for(session)
        # RFC-0007 (#84): discover the access block from the snapshotted
        # .pfactory.yml for this spec. Best-effort — when no snapshot exists the
        # block is simply omitted (the task declares no external resource).
        access_config, access_spec_text = _load_access_inputs(pid, session.plan.plan_id)
        result = _emit_contract(
            session.plan,
            session.epic,
            session.review,
            base_url=base,
            project_id=pid,
            http=http,
            key=key,
            repo=repo,
            correlation_key=corr,
            config=access_config,
            spec_text=access_spec_text,
            approvals=session.access_approvals or None,
            dry_run=dry_run,
            tenant_id=session.tenant_id,
        )
        session.contract_result = result
        if result.get("ok") and not dry_run:
            resp = result.get("response") if isinstance(result.get("response"), dict) else {}
            task_id = (resp or {}).get("taskId") or (resp or {}).get("task_id")
            if task_id:
                session.aifactory_task_id = str(task_id)
            session.status = "emitted"
            session.correlation_key = corr
            notify_completion(session)
        self._save(session)
        return session

    def approve_access(
        self,
        session_id: str,
        resource: str,
        *,
        approved_by: str,
        scope: str,
        approved_at: str | None = None,
        ref_exists=None,
    ) -> dict:
        """Record a human-verified access approval for one resource (RFC-0007 #86).

        The resource must appear in the last emitted contract's ``access`` block
        (run ``emit_contract`` dry-run first to discover requirements). Runs the
        curation gate: a non-D requirement whose credential is present (probed,
        never resolved into the open) is curated, the approval is stored for the
        next emit to apply, and an RFC-0001a audit record is appended. Returns
        ``{ok, resource, state?, audit?, reason?}``. Never stores/logs a secret.
        """
        from pfactory_secrets.probe import probe_ref_exists
        from plan.access_discovery import curate_requirement

        session = self.get(session_id)
        block = ((session.contract_result or {}).get("contract") or {}).get("access") or {}
        req = next(
            (r for r in (block.get("requirements") or []) if r.get("resource") == resource),
            None,
        )
        if req is None:
            raise PlanServiceError(
                f"resource '{resource}' not in the contract access block; emit a "
                "dry-run contract first to discover access requirements"
            )
        approval = {
            "approved_by": approved_by,
            "scope": scope,
            "approved_at": approved_at or datetime.now(UTC).isoformat(),
        }
        probe = ref_exists or probe_ref_exists

        def liveness(r) -> bool:  # credential must be present to curate at approval
            return probe(r.get("credential_ref")) is True

        _curated, audit = curate_requirement(req, approval=approval, liveness_check=liveness)
        if audit is None:
            return {
                "ok": False,
                "resource": resource,
                "reason": "cannot curate now: class D (un-automatable), or the "
                "credential is not present/verifiable at approval time",
            }
        session.access_approvals[resource] = approval
        session.access_audit.append(audit)
        self._save(session)
        return {"ok": True, "resource": resource, "state": "curated", "audit": audit}

    def classify_preview(self, session_id: str) -> dict:
        """Lightweight classification preview (no full pipeline run)."""
        session = self.get(session_id)
        return classify_plan(session.plan).__dict__


# Module-level singleton the route layer + MCP tool share.
#
# Lazily constructed on first access via module ``__getattr__`` (PEP 562, #194):
# importing this module no longer eagerly builds a ``PlanService`` (which, when
# ``PFACTORY_PLAN_PERSIST`` is set, reads the whole on-disk store). The instance
# is created the first time ``plan.service.SERVICE`` is read — e.g. by
# ``from plan.service import SERVICE`` — and cached as a real module attribute so
# subsequent reads (and ``monkeypatch.setattr``) behave exactly as before.


def __getattr__(name: str) -> object:
    """Lazily instantiate the shared :data:`SERVICE` singleton (PEP 562)."""
    if name == "SERVICE":
        service = PlanService()
        # Cache as a real module attribute so future lookups skip __getattr__.
        globals()["SERVICE"] = service
        return service
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
