"""RFC-0016 (#217): PlanService integration with the durable job-state store.

These tests exercise the wiring in ``plan.service`` itself (no SQLAlchemy):

  - in-memory fallback when DATABASE_URL is unset (and the not-multi-replica
    -safe warning is logged),
  - that the service mirrors every lifecycle transition into the durable store
    when one is configured,
  - that ``process_async`` grants a slot via the store's FOR-UPDATE
    ``try_start`` when a durable store is present.

A lightweight in-process fake store stands in for the real SQLAlchemy-backed
``JobStateStore`` so this module stays in the dependency-light backend suite.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

import pytest
from plan.service import PlanService


class FakeStore:
    """In-process stand-in for ``server.jobstore.JobStateStore``.

    Mirrors the contract the service depends on: ``upsert`` records the latest
    native status + payload per job; ``try_start`` flips a row to running under
    a cap, raising ``SlotDenied`` when full (the service queues + retries);
    ``heartbeat`` renews a running row's lease (#300) and records the calls so
    the renewal path can be asserted.
    """

    class SlotDenied(RuntimeError):
        pass

    def __init__(self, max_concurrent: int = 4) -> None:
        self.rows: dict[str, dict] = {}
        self.max_concurrent = max_concurrent
        self._lock = threading.Lock()
        self.upsert_calls: list[tuple[str, str]] = []
        self.heartbeats: list[str] = []

    def upsert(self, job_id, *, service_status, **kw):
        with self._lock:
            row = self.rows.setdefault(job_id, {})
            row["service_status"] = service_status
            row.update(kw)
            self.upsert_calls.append((job_id, service_status))
            return row

    def running_count(self) -> int:
        return sum(1 for r in self.rows.values() if r.get("service_status") == "processing")

    def try_start(self, job_id):
        with self._lock:
            row = self.rows.setdefault(job_id, {})
            if row.get("service_status") == "processing":
                return row
            running = sum(1 for r in self.rows.values() if r.get("service_status") == "processing")
            if self.max_concurrent > 0 and running >= self.max_concurrent:
                raise FakeStore.SlotDenied("cap reached")
            row["service_status"] = "processing"
            return row

    def heartbeat(self, job_id, *, ttl_seconds=None) -> bool:
        """Renew a running row's lease; False when there is nothing running."""
        with self._lock:
            self.heartbeats.append(job_id)
            return self.rows.get(job_id, {}).get("service_status") == "processing"


@pytest.fixture(autouse=True)
def _patch_slotdenied(monkeypatch):
    """Stand in for the lazily-imported ``server.jobstore`` module.

    ``_durable_admit`` imports ``SlotDenied`` (so the queue-and-retry path
    catches the fake's exception type) and ``lease_heartbeat_interval`` (#300)
    lazily; both must resolve here. The interval is large so the renewal task
    never fires mid-test — the renewal path has its own test below.
    """
    import sys
    import types

    mod = types.ModuleType("server")
    sub = types.ModuleType("server.jobstore")
    sub.SlotDenied = FakeStore.SlotDenied
    sub.lease_heartbeat_interval = lambda: 3600.0
    mod.jobstore = sub
    monkeypatch.setitem(sys.modules, "server", mod)
    monkeypatch.setitem(sys.modules, "server.jobstore", sub)
    yield


_SOFTWARE_PLAN = """# Refund API
Add a REST API endpoint to the payments microservice with auth and a Kubernetes
Helm deploy.
## Acceptance Criteria
- User can request a refund through the API
- Refunds are written to the audit log
- The endpoint requires a valid JWT
"""


def _ingest(svc: PlanService):
    return svc.ingest_text(_SOFTWARE_PLAN, title="Refund API")


# ── in-memory fallback ───────────────────────────────────────────────────────


def test_in_memory_fallback_when_database_url_unset(monkeypatch, caplog):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with caplog.at_level(logging.WARNING):
        svc = PlanService(persist=False)
    assert svc._job_store is None
    # the warning must name the multi-replica hazard
    assert any("multi-replica" in r.message.lower() for r in caplog.records)
    # the service still works fully in-memory
    session = _ingest(svc)
    assert svc.get(session.session_id).status == "ingested"


def test_explicit_store_overrides_auto_resolution(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = FakeStore()
    svc = PlanService(persist=False, job_store=store)
    assert svc._job_store is store


# ── lifecycle is mirrored to the durable store ───────────────────────────────


def test_ingest_mirrors_initial_row(monkeypatch):
    store = FakeStore()
    svc = PlanService(persist=False, job_store=store)
    session = _ingest(svc)
    assert session.session_id in store.rows
    assert store.rows[session.session_id]["service_status"] == "ingested"


def test_process_mirrors_terminal_transition(monkeypatch):
    store = FakeStore()
    svc = PlanService(persist=False, job_store=store)
    session = _ingest(svc)
    svc.process(session.session_id, external_runner=lambda p, e: [])
    # the final mirrored status reflects the processed state
    assert store.rows[session.session_id]["service_status"] == "processed"


def test_reject_mirrors_failed_with_reason(monkeypatch):
    store = FakeStore()
    svc = PlanService(persist=False, job_store=store)
    session = _ingest(svc)
    svc.process(session.session_id, external_runner=lambda p, e: [])
    svc.reject(session.session_id, approver="me", feedback="no")
    row = store.rows[session.session_id]
    assert row["service_status"] == "rejected"
    assert row.get("error")  # a reason was carried


# ── process_async grants a slot via the durable store ────────────────────────


@pytest.mark.asyncio
async def test_process_async_uses_durable_admission(monkeypatch):
    store = FakeStore(max_concurrent=4)
    svc = PlanService(persist=False, job_store=store)
    session = _ingest(svc)
    # ingest mirrored ingested; clear the call log to isolate the admit path
    store.upsert_calls.clear()
    result = await svc.process_async(session.session_id, external_runner=lambda p, e: [])
    assert result.status == "processed"
    # try_start flipped the row to running ('processing') before process() ran;
    # the terminal mirror then moved it to 'processed'.
    assert store.rows[session.session_id]["service_status"] == "processed"


@pytest.mark.asyncio
async def test_process_async_queues_when_cap_full(monkeypatch):
    """A full cap makes process_async WAIT (queue), not error."""
    store = FakeStore(max_concurrent=1)
    svc = PlanService(persist=False, job_store=store)
    # occupy the only slot with a foreign job
    store.try_start("foreign-running")
    session = _ingest(svc)

    import asyncio

    task = asyncio.create_task(
        svc.process_async(session.session_id, external_runner=lambda p, e: [])
    )
    # give the admit loop a few backoff cycles; it must still be queued
    await asyncio.sleep(0.15)
    assert not task.done()

    # free the slot -> the queued job proceeds to completion
    store.rows["foreign-running"]["service_status"] = "emitted"
    result = await asyncio.wait_for(task, timeout=5.0)
    assert result.status == "processed"


# ── the lease is renewed while process() runs (#300) ─────────────────────────


@pytest.mark.asyncio
async def test_durable_admit_renews_the_lease_while_processing(monkeypatch):
    """A long plan must keep its lease alive, or the reclaimer kills it.

    The renewal runs on the event loop (which the off-loop worker keeps free),
    so a plan that takes many heartbeat intervals is never mistaken for a dead
    owner.
    """
    import sys

    sys.modules["server.jobstore"].lease_heartbeat_interval = lambda: 0.01
    store = FakeStore()
    svc = PlanService(persist=False, job_store=store)
    session = _ingest(svc)

    real_process = svc.process

    def slow_process(sid, **kw):
        time.sleep(0.2)  # ~20 heartbeat intervals
        return real_process(sid, **kw)

    monkeypatch.setattr(svc, "process", slow_process)
    await svc.process_async(session.session_id, external_runner=lambda p, e: [])

    assert store.heartbeats, "the lease was never renewed during a long plan"
    assert all(j == session.session_id for j in store.heartbeats)


@pytest.mark.asyncio
async def test_renew_lease_stops_when_the_row_is_no_longer_running():
    """No lease to renew -> the task retires instead of spinning forever."""
    store = FakeStore()
    store.upsert("gone", service_status="emitted")  # terminal: heartbeat -> False
    await asyncio.wait_for(PlanService._renew_lease(store, "gone", 0.01), timeout=2)
    assert store.heartbeats == ["gone"]


@pytest.mark.asyncio
async def test_renew_lease_survives_a_store_hiccup():
    """A transient DB error must not kill a healthy run (best-effort renewal)."""

    class Flaky(FakeStore):
        def heartbeat(self, job_id, *, ttl_seconds=None):
            self.heartbeats.append(job_id)
            raise RuntimeError("connection reset")

    store = Flaky()
    task = asyncio.create_task(PlanService._renew_lease(store, "j", 0.01))
    await asyncio.sleep(0.05)
    assert not task.done(), "a renewal hiccup must not end the renewal loop"
    task.cancel()
    assert len(store.heartbeats) >= 1
