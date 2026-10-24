"""GET /api/drain: 200 when no live run watcher, else 503 (factory-gitops#303)."""

import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
for _root in (REPO / "apps" / "backend", REPO / "apps" / "web-server"):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from server.auth import TokenAuthMiddleware  # noqa: E402
from server.main import create_app  # noqa: E402
from server.services import run_leases  # noqa: E402


def _task(done: bool):
    return SimpleNamespace(done=lambda: done)


def test_idle_is_200(monkeypatch):
    monkeypatch.setattr(run_leases, "_WATCHERS", {})
    r = TestClient(create_app()).get("/api/drain")  # no Authorization header
    assert (r.status_code, r.json()) == (200, {"held": 0})


def test_live_watcher_is_503_and_done_ones_not_counted(monkeypatch):
    monkeypatch.setattr(
        run_leases, "_WATCHERS", {("a", "1"): _task(False), ("a", "2"): _task(True)}
    )
    r = TestClient(create_app()).get("/api/drain")
    assert (r.status_code, r.json()) == (503, {"held": 1})


def test_drain_is_public():
    assert "/api/drain" in TokenAuthMiddleware.PUBLIC_PATHS
