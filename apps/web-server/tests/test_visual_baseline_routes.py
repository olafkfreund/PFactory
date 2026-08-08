"""Tests for the visual-baseline portal routes (#109).

Mounts the pfactory_tasks router with a TestClient over a temp workspace; the
routes wrap ``agents.evidence.visual_baseline`` (list / serve / accept).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from server.routes import pfactory_tasks  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    spec = tmp_path / "workspaces" / "proj1" / "specs" / "001-feat"
    spec.mkdir(parents=True)
    (spec / "status.json").write_text("{}")
    ev = spec / "findings" / "evidence"
    ev.mkdir(parents=True)
    (ev / "homepage-actual.png").write_bytes(b"\x89PNG\r\n\x1a\nCAPTURED")
    monkeypatch.setenv("PFACTORY_WORKSPACE_ROOT", str(tmp_path))
    app = FastAPI()
    app.include_router(pfactory_tasks.router, prefix="/api/pfactory/tasks")
    return TestClient(app)


_BASE = "/api/pfactory/tasks/001-feat/visual-baselines"


def test_list_empty_then_accept_then_relist(client) -> None:
    assert client.get(_BASE, params={"target": "web"}).json()["baselines"] == []

    r = client.post(
        f"{_BASE}/web/homepage.png/accept", json={"source": "findings/evidence/homepage-actual.png"}
    )
    assert r.status_code == 200 and r.json()["accepted"] is True

    snaps = [b["snapshot"] for b in client.get(_BASE, params={"target": "web"}).json()["baselines"]]
    assert snaps == ["homepage.png"]


def test_get_baseline_image_bytes(client) -> None:
    client.post(
        f"{_BASE}/web/homepage.png/accept", json={"source": "findings/evidence/homepage-actual.png"}
    )
    r = client.get(f"{_BASE}/web/homepage.png")
    assert r.status_code == 200 and r.content.endswith(b"CAPTURED")


def test_get_missing_baseline_404(client) -> None:
    assert client.get(f"{_BASE}/web/nope.png").status_code == 404


def test_accept_rejects_path_traversal_source(client) -> None:
    r = client.post(f"{_BASE}/web/x.png/accept", json={"source": "../../../../etc/passwd"})
    assert r.status_code == 400


def test_unknown_task_404(client) -> None:
    r = client.get("/api/pfactory/tasks/999-nope/visual-baselines", params={"target": "web"})
    assert r.status_code == 404
