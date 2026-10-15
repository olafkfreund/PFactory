"""Runtime invariants in production: critical filter, timer body, readiness (PFactory#865).

Covers RFC-0024 section 4: ``register(critical=)``, ``check_all(critical=)``,
the on-by-default switch, the non-critical timer body and ``GET /api/ready``.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

REPO = Path(__file__).resolve().parents[1]
for _root in (REPO / "apps" / "backend", REPO / "apps" / "web-server"):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

import factory_invariants as fi  # noqa: E402
from server.background import invariants as inv  # noqa: E402
from server.config import Settings  # noqa: E402
from server.main import create_app  # noqa: E402


def _failing(detail: str = "broken"):
    return lambda: iter([detail])


def _registry(*, critical: bool, module: str = "pkgx.mod", check=None) -> fi.InvariantRegistry:
    reg = fi.InvariantRegistry(enabled=True)
    reg.register(module, check or _failing(), critical=critical)
    return reg


def test_check_all_filters_by_critical():
    reg = fi.InvariantRegistry(enabled=True)
    reg.register("a.crit", _failing(), critical=True)
    reg.register("b.plain", _failing())
    assert [v.module for v in reg.check_all(critical=True)] == ["a.crit"]
    assert [v.module for v in reg.check_all(critical=False)] == ["b.plain"]
    assert [v.module for v in reg.check_all()] == ["a.crit", "b.plain"]


def test_a_reason_cannot_be_critical():
    with pytest.raises(ValueError, match="cannot be critical"):
        fi.InvariantRegistry(enabled=True).register(
            "a.mod", reason="owns nothing observable at runtime in this process", critical=True
        )


def test_registry_is_on_unless_switched_off(monkeypatch):
    monkeypatch.delenv("FACTORY_INVARIANTS", raising=False)
    assert fi._from_env().enabled is True
    monkeypatch.setenv("FACTORY_INVARIANTS", "0")
    assert fi._from_env().enabled is False


def test_run_once_logs_and_counts(monkeypatch, caplog):
    monkeypatch.setattr(inv, "registry", _registry(critical=False))
    counter = inv.VIOLATIONS.labels(service="pfactory", package="pkgx")
    before = counter._value.get()
    with caplog.at_level(logging.ERROR, logger=inv.logger.name):
        inv.run_once()
    assert any("pkgx" in r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)
    assert counter._value.get() == before + 1


def test_readiness_names_failing_package_and_caches(monkeypatch):
    calls = []

    def check():
        calls.append(1)
        return iter(["broken"])

    monkeypatch.setattr(inv, "registry", _registry(critical=True, check=check))
    monkeypatch.setattr(inv, "_ready_cache", None)
    clock = [1000.0]
    monkeypatch.setattr(inv.time, "monotonic", lambda: clock[0])
    assert asyncio.run(inv.readiness()) == ["pkgx"]
    assert asyncio.run(inv.readiness()) == ["pkgx"]
    assert len(calls) == 1
    clock[0] += 11
    asyncio.run(inv.readiness())
    assert len(calls) == 2


def test_readiness_hung_check_is_single_flight_and_times_out(monkeypatch):
    release = threading.Event()
    calls = []

    def hung():
        calls.append(1)
        release.wait(5)
        return iter([])

    monkeypatch.setattr(inv, "registry", _registry(critical=True, check=hung))
    monkeypatch.setattr(inv, "_ready_cache", None)
    monkeypatch.setattr(inv, "_ready_run", None)
    monkeypatch.setattr(inv, "READY_TIMEOUT_SECONDS", 0.2)
    counter = inv.VIOLATIONS.labels(service="pfactory", package="pkgx")
    before = counter._value.get()

    async def probes():
        first = await asyncio.gather(inv.readiness(), inv.readiness())
        inv._ready_cache = None  # cache expired, run still hung
        return first, await inv.readiness()

    try:
        first, later = asyncio.run(probes())
    finally:
        release.set()
    assert first == [["pkgx"], ["pkgx"]]
    assert later == ["pkgx"]
    assert len(calls) == 1  # one thread, however many probes
    assert counter._value.get() == before + 3  # one per probe that timed out


def test_invariants_period_has_a_floor():
    with pytest.raises(ValidationError):
        Settings(INVARIANTS_PERIOD_SECONDS=0)


def test_ready_endpoint(monkeypatch):
    # No `with`: the lifespan (DB, migrations) is not needed. Auth stays on.
    client = TestClient(create_app())

    monkeypatch.setattr(inv, "_ready_cache", None)
    monkeypatch.setattr(inv, "_ready_run", None)
    monkeypatch.setattr(inv, "registry", _registry(critical=True))
    r = client.get("/api/ready")
    assert r.status_code == 503
    assert r.json()["failing"] == ["pkgx"]

    monkeypatch.setattr(inv, "_ready_cache", None)
    monkeypatch.setattr(inv, "registry", fi.InvariantRegistry(enabled=True))
    r = client.get("/api/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready"}

    r = client.get("/api/health")
    assert r.status_code == 200
    assert set(r.json()) == {"status", "version", "provider_auth"}
