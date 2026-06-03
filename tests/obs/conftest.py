"""Pytest fixtures for P6 observability acceptance tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_SERVER_ROOT = REPO_ROOT / "apps" / "web-server"

if str(WEB_SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(WEB_SERVER_ROOT))


@pytest.fixture
def fresh_obs_app():
    """Build a minimal FastAPI app wired with observability middleware.

    Avoids the full main.py app — the obs surface is independent of
    the DB / auth / OIDC layers, so isolating it makes the tests
    fast (~1s) and self-contained.
    """
    from fastapi import FastAPI

    app = FastAPI()

    @app.get("/api/health")
    async def _health():
        return {"status": "healthy"}

    @app.get("/api/projects/{project_id}/tasks")
    async def _tasks(project_id: str):
        return {"project_id": project_id, "tasks": []}

    return app
