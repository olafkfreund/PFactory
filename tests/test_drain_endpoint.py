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
from server.services import agent_service, run_leases  # noqa: E402


def _task(done: bool):
    return SimpleNamespace(done=lambda: done)


def _procs(**rc):
    return SimpleNamespace(
        running_tasks={k: SimpleNamespace(returncode=v) for k, v in rc.items()}
    )


def test_idle_is_200(monkeypatch):
    monkeypatch.setattr(run_leases, "_WATCHERS", {})
    monkeypatch.setattr(agent_service, "_agent_service", None)
    r = TestClient(create_app()).get("/api/drain")  # no Authorization header
    assert (r.status_code, r.json()) == (200, {"held": 0})


def test_live_watcher_is_503_and_done_ones_not_counted(monkeypatch):
    monkeypatch.setattr(
        run_leases, "_WATCHERS", {("a", "1"): _task(False), ("a", "2"): _task(True)}
    )
    monkeypatch.setattr(agent_service, "_agent_service", None)
    r = TestClient(create_app()).get("/api/drain")
    assert (r.status_code, r.json()) == (503, {"held": 1})


def test_running_agent_process_without_watcher_is_503(monkeypatch):
    monkeypatch.setattr(run_leases, "_WATCHERS", {})
    monkeypatch.setattr(agent_service, "_agent_service", _procs(a=None, b=0))
    r = TestClient(create_app()).get("/api/drain")
    assert (r.status_code, r.json()) == (503, {"held": 1})


def test_finished_agent_process_is_not_counted(monkeypatch):
    monkeypatch.setattr(run_leases, "_WATCHERS", {})
    monkeypatch.setattr(agent_service, "_agent_service", _procs(a=0))
    r = TestClient(create_app()).get("/api/drain")
    assert (r.status_code, r.json()) == (200, {"held": 0})


def test_drain_is_public():
    assert "/api/drain" in TokenAuthMiddleware.PUBLIC_PATHS
