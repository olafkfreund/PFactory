"""Concurrency tests for PlanService offload + admission cap (RFC-0016 #217).

These prove the three guarantees of the event-loop-offload slice:

1. ``process_async`` runs the blocking pipeline OFF the event loop, so the loop
   stays free to serve other coroutines (e.g. ``/api/health``) while a slow
   ``process()`` is in flight.
2. The admission semaphore (``PFACTORY_MAX_CONCURRENT_PLANS``) bounds the number
   of in-flight runs; at the cap, callers WAIT (queue) — they never error.
3. The shared ``_sessions`` store and ``_next_seq`` counter are lock-guarded, so
   concurrent offloaded runs/ingests don't lose sessions or collide on the seq.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from plan.service import PlanService  # noqa: E402

_SOFTWARE_PLAN = """# Refund API
Add a REST API endpoint to the payments microservice with auth and a Kubernetes
Helm deploy.
## Acceptance Criteria
- User can request a refund through the API
- Refunds are written to the audit log
- The endpoint requires a valid JWT
"""


def _patch_slow_process(svc: PlanService, *, delay: float, marker: dict) -> None:
    """Replace ``svc.process`` with a slow stand-in that sleeps in its thread.

    ``marker`` records how many runs are concurrently in-flight (``active`` /
    ``max_active``) so a test can assert serialisation under the cap. The sleep
    is a real ``time.sleep`` (blocking, like the true CPU/IO pipeline) so it only
    yields the GIL — it does NOT cooperatively yield the event loop, which is the
    whole point: only the offload keeps the loop free.
    """
    real = svc.process
    lock = threading.Lock()

    def _slow(session_id, **kwargs):
        with lock:
            marker["active"] += 1
            marker["max_active"] = max(marker["max_active"], marker["active"])
        try:
            time.sleep(delay)
            return real(session_id, **kwargs)
        finally:
            with lock:
                marker["active"] -= 1

    svc.process = _slow  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_process_async_does_not_block_the_event_loop():
    """A slow process() in flight must not starve other coroutines (the loop).

    Simulates ``/api/health`` with a coroutine that just sleeps a tick: it must
    complete WHILE a slow process() runs, proving the loop wasn't blocked.
    """
    svc = PlanService(persist=False)
    sid = svc.ingest_text(_SOFTWARE_PLAN, title="Refund API").session_id
    marker = {"active": 0, "max_active": 0}
    _patch_slow_process(svc, delay=0.5, marker=marker)

    health_responses: list[float] = []

    async def health_pings():
        # Fire health checks repeatedly while process() is offloaded.
        deadline = time.monotonic() + 0.45
        while time.monotonic() < deadline:
            await asyncio.sleep(0.02)
            health_responses.append(time.monotonic())

    proc_task = asyncio.create_task(svc.process_async(sid))
    await health_pings()
    # Health pings completed many times BEFORE the slow process finished — the
    # loop was never blocked.
    assert len(health_responses) >= 5
    assert not proc_task.done()  # process still running in its worker thread
    result = await proc_task
    assert result.status == "processed"


@pytest.mark.asyncio
async def test_admission_cap_serializes_without_erroring(monkeypatch):
    """With cap=1 two concurrent process_async runs serialise; neither errors."""
    monkeypatch.setenv("PFACTORY_MAX_CONCURRENT_PLANS", "1")
    svc = PlanService(persist=False)
    sid_a = svc.ingest_text(_SOFTWARE_PLAN, title="A").session_id
    sid_b = svc.ingest_text(_SOFTWARE_PLAN, title="B").session_id
    marker = {"active": 0, "max_active": 0}
    _patch_slow_process(svc, delay=0.2, marker=marker)

    results = await asyncio.gather(
        svc.process_async(sid_a),
        svc.process_async(sid_b),
    )
    # Cap=1 ⇒ at most one run was ever in-flight at a time (serialised, queued).
    assert marker["max_active"] == 1
    assert {r.status for r in results} == {"processed"}


@pytest.mark.asyncio
async def test_admission_cap_allows_concurrency_below_cap(monkeypatch):
    """With cap=4 two runs overlap (max_active > 1) — the gate isn't over-tight."""
    monkeypatch.setenv("PFACTORY_MAX_CONCURRENT_PLANS", "4")
    svc = PlanService(persist=False)
    sid_a = svc.ingest_text(_SOFTWARE_PLAN, title="A").session_id
    sid_b = svc.ingest_text(_SOFTWARE_PLAN, title="B").session_id
    marker = {"active": 0, "max_active": 0}
    _patch_slow_process(svc, delay=0.2, marker=marker)

    await asyncio.gather(svc.process_async(sid_a), svc.process_async(sid_b))
    assert marker["max_active"] == 2


@pytest.mark.asyncio
async def test_unlimited_cap_means_no_gate(monkeypatch):
    """cap<=0 disables the admission gate (returns None)."""
    monkeypatch.setenv("PFACTORY_MAX_CONCURRENT_PLANS", "0")
    svc = PlanService(persist=False)
    assert svc._admission_semaphore() is None


# ── execution-model decision lock-in (RFC-0016 #218) ──────────────────────
#
# #218 asks PFactory to "run process() as a Job or pool worker". The decision
# (RFC-0016 §5(c)) is the *pool-worker* model: sub-second deterministic planning
# does not warrant a k8s Job pod per plan. These tests pin that contract so a
# regression toward Job-per-plan or a dropped admission cap is caught.


def test_execution_model_is_pooled_worker_not_job_per_task():
    """The Phase-2 execution seam is the off-loop pooled worker + admission cap.

    Asserts the structural contract of the chosen model: ``process_async`` (the
    pooled-worker entrypoint) exists and the admission gate is the cap mechanism.
    There is intentionally no Job-dispatch path for planning, so PlanService must
    not grow a per-plan k8s-Job dispatcher.
    """
    svc = PlanService(persist=False)
    # The async, off-loop worker entrypoint is the execution model.
    assert callable(svc.process_async)
    assert callable(svc._admission_semaphore)
    # No Job-per-task dispatch lives on the planning service — by design, sub-
    # second deterministic planning is NOT dispatched to a k8s Job. If a future
    # heavy/governed path adds one, it must be an opt-in, env-gated seam (see
    # CONTRIBUTING "Concurrency / execution model"), not a default on PlanService.
    for forbidden in ("dispatch_job", "build_job_manifest", "_dispatch_k8s_job"):
        assert not hasattr(svc, forbidden), (
            f"PlanService grew {forbidden!r}: planning must stay a pooled worker, "
            "not Job-per-task (RFC-0016 #218 / §5c)"
        )
    # The durable, cross-replica-safe half of the cap (#220) — which is what
    # replaces a Job scheduler for multi-replica admission — is exercised by
    # tests/test_plan_service_durable_state.py::test_process_async_uses_durable_admission.


def test_concurrent_ingests_do_not_lose_sessions_or_collide_seq():
    """Many threads ingesting at once: no lost sessions, no duplicate seq/ids.

    Exercises the ``_store`` / ``_next_seq`` / ``_save`` lock directly from real
    OS threads (the same way offloaded process() runs touch the store).
    """
    svc = PlanService(persist=False)
    n = 40
    barrier = threading.Barrier(n)
    ids: list[str] = []
    ids_lock = threading.Lock()

    def _ingest():
        barrier.wait()  # maximise contention — all fire together
        # Identical title on purpose: the plan_id is ``f"{seq:03d}-{slug(title)}"``
        # so two sessions only differ by their sequence number. If `_next_seq`
        # weren't lock-guarded, two threads would read the same length, mint the
        # same id, and the second would overwrite the first in `_sessions` — a
        # lost session. A clean run keeps all `n`.
        session = svc.ingest_text(_SOFTWARE_PLAN, title="same-plan")
        with ids_lock:
            ids.append(session.session_id)

    threads = [threading.Thread(target=_ingest) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Every ingest produced a session and none was dropped from the store.
    assert len(svc.list_sessions()) == n
    # No two sessions collided on their id (the plan_id derives from the seq).
    assert len(set(ids)) == n
    assert len(set(svc._sessions.keys())) == n
