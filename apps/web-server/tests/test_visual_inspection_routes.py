"""Tests for the visual-inspection portal routes (#170 / P4 #174).

End-to-end through the real packager → finalize → store → routes chain over a
temp store; no browser, no SUT.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from server.routes import visual_inspection as vi  # noqa: E402
from agents.visual_inspection import (  # noqa: E402
    StepResult,
    build_meta,
    finalize_run,
    package_run,
    store,
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PFACTORY_VISUAL_INSPECTION_ROOT", str(tmp_path / "store"))
    (tmp_path / "store").mkdir()
    # produce a real run through the chain
    meta = build_meta(
        run_id="snow-20260603130500",
        target={"name": "snow", "platform": "servicenow", "base_url": "https://acme.service-now.com"},
        steps=[StepResult(1, "login", "pass"), StepResult(2, "submit", "fail", error="boom")],
        created_at="2026-06-03T13:05:00Z",
    )
    ev = Path(tempfile.mkdtemp())
    pr = package_run(ev / "out", meta=meta, evidence_dir=ev)
    finalize_run(pr.run_dir, meta)
    store.write_run(pr.run_dir)

    app = FastAPI()
    app.include_router(vi.router)
    return TestClient(app)


_BASE = "/api/visual-inspections"


def test_list_then_detail_then_download(client) -> None:
    runs = client.get(_BASE).json()["runs"]
    assert len(runs) == 1 and runs[0]["verdict"] == "fail"
    assert runs[0]["counts"]["failed"] == 1

    detail = client.get(f"{_BASE}/snow-20260603130500").json()
    assert detail["reportMarkdown"] and detail["correctionPlanMarkdown"]
    assert detail["meta"]["verdict"] == "fail"

    dl = client.get(f"{_BASE}/snow-20260603130500/download/report.md")
    assert dl.status_code == 200 and "markdown" in dl.headers["content-type"]


def test_unknown_run_404(client) -> None:
    assert client.get(f"{_BASE}/nope").status_code == 404
    assert client.get(f"{_BASE}/nope/download/report.md").status_code == 404


def test_unknown_artifact_400(client) -> None:
    assert client.get(f"{_BASE}/snow-20260603130500/download/bogus").status_code == 400
