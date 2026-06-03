"""v3.0.2 regression — P6 observability is actually wired into main.create_app().

v3.0.0 + v3.0.1 shipped with the P6 observability package
(server/observability/) present but never called from main.py. As a
result the production portal exposed neither /metrics nor structured
logs nor correlation IDs — despite all P6 unit tests passing (they
built their own minimal FastAPI app and called install_metrics()
directly, bypassing main.py).

This test imports main.create_app() and asserts:
  - /metrics returns Prometheus exposition format (P6.3).
  - CorrelationIdMiddleware echoes back the X-Request-ID header (P6.2).
  - The FastAPI app's title is the PFactory brand, not Magestic.
  - The OpenAPI version matches the package version.

Without this gate, the wiring can silently regress on any main.py
refactor.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def real_app(monkeypatch):
    """Build the real production app via main.create_app().

    The Prometheus client uses a process-global registry. To keep this
    test isolated from any other test that may have registered
    collectors earlier in the same process, wipe the registry first.
    """
    from prometheus_client import REGISTRY

    for c in list(REGISTRY._collector_to_names.keys()):  # type: ignore[attr-defined]
        try:
            REGISTRY.unregister(c)
        except KeyError:
            pass

    # Disable auth so /metrics doesn't accidentally require a token in
    # the test path. The METRICS_SCRAPE_TOKEN is read by install_metrics
    # at call time, so we strip it here.
    monkeypatch.delenv("METRICS_SCRAPE_TOKEN", raising=False)
    monkeypatch.setenv("APP_DISABLE_AUTH", "true")

    from server.main import create_app
    return create_app()


@pytest.mark.obs
def test_main_app_exposes_prometheus_metrics(real_app) -> None:
    """Regression: main.create_app() wires install_metrics(). v3.0.0/v3.0.1
    shipped without this wiring; the v3.0.2 patch added it."""
    from fastapi.testclient import TestClient

    with TestClient(real_app) as client:
        # Generate a request so there's a data point to expose.
        client.get("/api/health")
        resp = client.get("/metrics")

    assert resp.status_code == 200, (
        f"/metrics must be reachable on the production app; got "
        f"{resp.status_code} body={resp.text[:300]!r}"
    )
    assert resp.headers["content-type"].startswith("text/plain"), (
        f"/metrics must return Prometheus text format; got "
        f"{resp.headers['content-type']!r}"
    )
    # Standard FastAPI-instrumentator metric names.
    body = resp.text
    assert "http_requests_total" in body
    assert "http_request_duration_seconds" in body


@pytest.mark.obs
def test_main_app_correlation_id_middleware_wired(real_app) -> None:
    """Regression: CorrelationIdMiddleware echoes X-Request-ID on the
    real production app."""
    from fastapi.testclient import TestClient
    from server.observability import CORRELATION_ID_HEADER

    with TestClient(real_app) as client:
        resp = client.get(
            "/api/health", headers={CORRELATION_ID_HEADER: "test-rid-v302"}
        )
    assert resp.headers.get(CORRELATION_ID_HEADER) == "test-rid-v302", (
        f"CorrelationIdMiddleware should echo back X-Request-ID; got "
        f"{resp.headers.get(CORRELATION_ID_HEADER)!r}"
    )


@pytest.mark.obs
def test_main_app_title_is_pfactory(real_app) -> None:
    """Regression: app title is PFactory, not the old Magestic brand."""
    assert real_app.title == "PFactory Web API", (
        f"Expected 'PFactory Web API', got {real_app.title!r}"
    )
    # description should mention PFactory, not Magestic.
    assert "Magestic" not in (real_app.description or "")
    assert "PFactory" in (real_app.description or "")


@pytest.mark.obs
def test_main_app_version_matches_package(real_app) -> None:
    """The FastAPI app.version is the package version (not a stale '1.0.0').

    Reads the source-of-truth from apps/frontend-web/package.json (the
    file bump-version.js uses as primary) and asserts the running app
    reports the same.
    """
    import json
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    pkg_version = json.loads(
        (repo_root / "apps" / "frontend-web" / "package.json").read_text()
    )["version"]
    assert real_app.version == pkg_version, (
        f"app.version drift: app reports {real_app.version!r} but "
        f"package.json says {pkg_version!r}"
    )
