"""Tests for the cloud portal routes — the launch gate (#133).

Mounts only the cloud router with a TestClient; portal_run is monkeypatched so
nothing touches a real cloud or Docker.
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

from server.routes import cloud  # noqa: E402
from agents.cloud import portal_run  # noqa: E402


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(cloud.router)
    return TestClient(app)


def test_run_rejects_unknown_provider(client) -> None:
    r = client.post("/api/cloud/assessments/run", json={"provider": "oracle"})
    assert r.status_code == 400


def test_run_gate_no_access_does_not_background(client, monkeypatch) -> None:
    monkeypatch.setattr(portal_run, "preflight",
                        lambda *a, **k: {"ok": False, "error": "az account show failed",
                                         "account": None, "identity": None, "inventory": {}})
    started = []
    monkeypatch.setattr(portal_run, "run_and_store", lambda *a, **k: started.append(1))
    r = client.post("/api/cloud/assessments/run", json={"provider": "azure"})
    assert r.status_code == 200
    body = r.json()
    assert body["gate"] == "no_access" and body["error"]
    assert started == []  # never proceeds to the assessment without access


def test_run_gate_ok_returns_inventory_and_starts(client, monkeypatch) -> None:
    inv = {"provider": "gcp", "account": "sarc-493418", "identity": "olaf@x",
           "global": {"storage": {"count": 1}}}
    monkeypatch.setattr(portal_run, "preflight",
                        lambda *a, **k: {"ok": True, "account": "sarc-493418",
                                         "identity": "olaf@x", "inventory": inv, "error": None})
    monkeypatch.setattr(portal_run, "run_and_store",
                        lambda *a, **k: {"assessment_id": "gcp-x-1", "verdict": "reject", "fail_counts": {}})
    r = client.post("/api/cloud/assessments/run",
                    json={"provider": "gcp", "profile": "sarc-493418", "services": ["iam"]})
    assert r.status_code == 200
    body = r.json()
    assert body["gate"] == "ok" and body["status"] == "running"
    assert body["account"] == "sarc-493418"
    assert body["inventory"]["global"]["storage"]["count"] == 1
