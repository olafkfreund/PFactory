"""Smoke tests for GET /api/projects/{id}/mcp-status.

Endpoint shape (and the catalog × credentials engine behind it) is
exhaustively tested by ``test_mcp_catalog.py`` + ``test_mcp_credentials.py``;
these tests just verify the FastAPI layer:
- 404 for unknown project
- 200 with the expected JSON envelope for a real project
- catalog ordering preserved (GitHub first, etc.)
- markers populated from filesystem scan
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make sure the web-server module is importable
_WEB_SERVER_DIR = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A FastAPI TestClient with isolated projects.json + no real creds."""
    # Isolate projects data dir
    projects_dir = tmp_path / "projects-data"
    projects_dir.mkdir()
    monkeypatch.setenv("PROJECTS_DATA_DIR", str(projects_dir))

    # Wipe creds env so probes consistently return unavailable
    for var in (
        "GITHUB_TOKEN",
        "GITHUB_PERSONAL_ACCESS_TOKEN",
        "GH_TOKEN",
        "KUBECONFIG",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        monkeypatch.delenv(var, raising=False)
    # Re-point HOME so default ~/.aws/credentials etc. don't bleed in
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()

    from core import mcp_credentials
    mcp_credentials.reset_cache()

    # Build the FastAPI app and TestClient
    # Import the router directly — the full app pulls in OIDC / DB setup
    # that we don't need. We mount mcp.router on a minimal app.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server.config import get_settings
    from server.routes import mcp as mcp_route

    # Override settings to use our tmp projects dir
    settings = get_settings()
    settings.PROJECTS_DATA_DIR = str(projects_dir)

    app = FastAPI()
    app.include_router(mcp_route.router)
    return TestClient(app), projects_dir


def _seed_project(projects_dir: Path, project_id: str, project_path: Path) -> None:
    """Write the projects.json the loader expects."""
    projects_file = projects_dir / "projects.json"
    projects_file.write_text(
        json.dumps({project_id: {"path": str(project_path), "name": "test-project"}})
    )


def test_404_for_unknown_project(client):
    test_client, _ = client
    resp = test_client.get("/api/projects/does-not-exist/mcp-status")
    assert resp.status_code == 404


def test_200_with_full_catalog_for_known_project(client, tmp_path):
    test_client, projects_dir = client
    project_path = tmp_path / "my-project"
    project_path.mkdir()
    _seed_project(projects_dir, "p1", project_path)

    resp = test_client.get("/api/projects/p1/mcp-status")
    assert resp.status_code == 200
    body = resp.json()

    assert body["project"]["id"] == "p1"
    assert body["project"]["path"] == str(project_path)

    server_ids = [s["id"] for s in body["servers"]]
    # V1 catalog order is preserved (GitHub first)
    assert server_ids[0] == "github"
    assert {"github", "kubernetes", "aws", "azure"}.issubset(set(server_ids))


def test_markers_reflect_filesystem(client, tmp_path):
    """A project with charts/ marks has_kubernetes as matched."""
    test_client, projects_dir = client
    project_path = tmp_path / "k8s-project"
    project_path.mkdir()
    (project_path / "charts").mkdir()  # has_kubernetes signal
    _seed_project(projects_dir, "p2", project_path)

    resp = test_client.get("/api/projects/p2/mcp-status")
    body = resp.json()
    k8s = next(s for s in body["servers"] if s["id"] == "kubernetes")
    assert k8s["markers"]["matches"] is True
    assert "has_kubernetes" in k8s["markers"]["matched"]


def test_would_enable_requires_creds_AND_markers(client, tmp_path, monkeypatch):
    """No creds → would_enable=False even when markers match."""
    test_client, projects_dir = client
    project_path = tmp_path / "k8s-project"
    project_path.mkdir()
    (project_path / "charts").mkdir()
    _seed_project(projects_dir, "p3", project_path)

    # No creds set up → kubernetes would_enable should be False
    resp = test_client.get("/api/projects/p3/mcp-status")
    body = resp.json()
    k8s = next(s for s in body["servers"] if s["id"] == "kubernetes")
    assert k8s["markers"]["matches"] is True
    assert k8s["credentials"]["available"] is False
    assert k8s["would_enable"] is False


def test_github_is_always_on_marker_wise(client, tmp_path):
    """GitHub catalog entry has empty markers → markers.matches always True."""
    test_client, projects_dir = client
    project_path = tmp_path / "any-project"
    project_path.mkdir()
    _seed_project(projects_dir, "p4", project_path)

    resp = test_client.get("/api/projects/p4/mcp-status")
    body = resp.json()
    gh = next(s for s in body["servers"] if s["id"] == "github")
    assert gh["markers"]["matches"] is True
    assert gh["markers"]["reason"] == "always-on"
