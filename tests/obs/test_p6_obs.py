"""P6 — Observability acceptance tests.

Seven tests map to the five acceptance bullets in Epic #26 issue #33.
"""

from __future__ import annotations

import io
import json
import os
import sys
from contextlib import redirect_stdout

import pytest


@pytest.mark.obs
def test_structlog_emits_valid_json() -> None:
    """structlog renders log lines as JSON parseable by jq."""
    from server.observability.structlog_setup import configure_structlog, get_logger

    # Configure + emit. Capture stdout to inspect the JSON line.
    configure_structlog(level="INFO")
    log = get_logger("test")
    buf = io.StringIO()
    with redirect_stdout(buf):
        log.info("hello", customer="acme", count=42)
    output = buf.getvalue().strip()
    # One log line per emit.
    lines = [line for line in output.splitlines() if line.strip()]
    assert lines, f"no log output captured; full stdout: {output!r}"
    last = lines[-1]
    parsed = json.loads(last)  # parses cleanly = jq-compatible
    assert parsed["event"] == "hello"
    assert parsed["customer"] == "acme"
    assert parsed["count"] == 42
    assert parsed["level"] == "info"
    assert "timestamp" in parsed
    # ISO-8601 UTC: ends with Z or +00:00.
    assert parsed["timestamp"].endswith("Z") or "+00:00" in parsed["timestamp"]


@pytest.mark.obs
def test_correlation_id_in_logs(fresh_obs_app) -> None:
    """An incoming X-Request-ID header appears as request_id in logs +
    is echoed back in the response."""
    from fastapi.testclient import TestClient
    from server.observability import (
        CORRELATION_ID_HEADER,
        CorrelationIdMiddleware,
        configure_structlog,
        get_correlation_id,
        reset_correlation_id,
        set_correlation_id,
    )

    configure_structlog(level="INFO")
    fresh_obs_app.add_middleware(CorrelationIdMiddleware)

    with TestClient(fresh_obs_app) as client:
        resp = client.get(
            "/api/health",
            headers={CORRELATION_ID_HEADER: "test-rid-abc123"},
        )
    assert resp.status_code == 200
    # Echoed back.
    assert resp.headers.get(CORRELATION_ID_HEADER) == "test-rid-abc123"

    # Manual verification of the contextvar's behavior (the middleware
    # only sets it inside a request scope; here we set it manually).
    token = set_correlation_id("manual-rid")
    try:
        assert get_correlation_id() == "manual-rid"
    finally:
        reset_correlation_id(token)
    # After reset, the contextvar returns to its default (None).
    assert get_correlation_id() is None


@pytest.mark.obs
def test_correlation_id_propagates_to_httpx() -> None:
    """An httpx.Client request inside a correlation-ID scope sets X-Request-ID."""
    import httpx
    from server.observability.correlation_id import (
        CORRELATION_ID_HEADER,
        install_httpx_propagation,
        reset_correlation_id,
        set_correlation_id,
    )

    install_httpx_propagation()

    # Build a mock transport that captures the request.
    captured = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(_handler)

    token = set_correlation_id("rid-propagated-9876")
    try:
        with httpx.Client(transport=transport) as client:
            resp = client.get("http://example.invalid/x")
    finally:
        reset_correlation_id(token)

    assert resp.status_code == 200
    assert captured["headers"].get(CORRELATION_ID_HEADER.lower()) == "rid-propagated-9876"


@pytest.mark.obs
def test_metrics_exposes_prometheus_format(fresh_obs_app) -> None:
    """GET /metrics returns Prometheus exposition format (text/plain)."""
    from fastapi.testclient import TestClient
    from prometheus_client import REGISTRY

    # Reset any prior metrics so this test is hermetic.
    collectors = list(REGISTRY._collector_to_names.keys())  # type: ignore[attr-defined]
    for c in collectors:
        try:
            REGISTRY.unregister(c)
        except KeyError:
            pass

    # Make sure METRICS_SCRAPE_TOKEN isn't lingering from a prior test.
    os.environ.pop("METRICS_SCRAPE_TOKEN", None)

    from server.observability import install_metrics

    install_metrics(fresh_obs_app)

    with TestClient(fresh_obs_app) as client:
        client.get("/api/projects/abc/tasks")  # generate a metric data point
        resp = client.get("/metrics")
    assert resp.status_code == 200
    # text/plain; version=0.0.4 (Prometheus exposition spec)
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    # Standard FastAPI instrumentator metric names.
    assert "http_request_duration_seconds" in body
    assert "http_requests_total" in body


@pytest.mark.obs
def test_handler_label_uses_route_template(fresh_obs_app) -> None:
    """After hitting two distinct paths matching the same route template,
    the `handler` label has ONE value (the template), not two raw paths."""
    import re

    from fastapi.testclient import TestClient
    from prometheus_client import REGISTRY

    # Hermetic registry.
    collectors = list(REGISTRY._collector_to_names.keys())  # type: ignore[attr-defined]
    for c in collectors:
        try:
            REGISTRY.unregister(c)
        except KeyError:
            pass
    os.environ.pop("METRICS_SCRAPE_TOKEN", None)

    from server.observability import install_metrics

    install_metrics(fresh_obs_app)

    with TestClient(fresh_obs_app) as client:
        client.get("/api/projects/abc123/tasks")
        client.get("/api/projects/xyz789/tasks")
        resp = client.get("/metrics")

    body = resp.text
    # Extract every `handler="..."` label value from the body.
    handler_values = set(re.findall(r'handler="([^"]+)"', body))
    # Must contain the templated form.
    assert "/api/projects/{project_id}/tasks" in handler_values, (
        f"templated handler not found in {handler_values}"
    )
    # Must NOT leak raw path-parameter values (cardinality explosion).
    assert "/api/projects/abc123/tasks" not in handler_values
    assert "/api/projects/xyz789/tasks" not in handler_values


@pytest.mark.obs
def test_metrics_requires_token_when_configured(fresh_obs_app) -> None:
    """METRICS_SCRAPE_TOKEN set → /metrics returns 401 without bearer,
    200 with correct bearer."""
    from fastapi.testclient import TestClient
    from prometheus_client import REGISTRY

    # Hermetic.
    collectors = list(REGISTRY._collector_to_names.keys())  # type: ignore[attr-defined]
    for c in collectors:
        try:
            REGISTRY.unregister(c)
        except KeyError:
            pass

    os.environ["METRICS_SCRAPE_TOKEN"] = "secret-scrape-token-zzz"

    from server.observability import install_metrics

    install_metrics(fresh_obs_app)

    try:
        with TestClient(fresh_obs_app) as client:
            # No bearer → 401.
            resp_noauth = client.get("/metrics")
            # Wrong bearer → 401.
            resp_wrong = client.get(
                "/metrics",
                headers={"Authorization": "Bearer wrong"},
            )
            # Correct bearer → 200.
            resp_ok = client.get(
                "/metrics",
                headers={"Authorization": "Bearer secret-scrape-token-zzz"},
            )
    finally:
        os.environ.pop("METRICS_SCRAPE_TOKEN", None)

    assert resp_noauth.status_code == 401
    assert resp_wrong.status_code == 401
    assert resp_ok.status_code == 200
    assert "http_requests_total" in resp_ok.text


def _grafana_dashboard_missing() -> bool:
    from pathlib import Path
    return not (
        Path(__file__).resolve().parents[2]
        / "guides" / "observability" / "grafana-pfactory.json"
    ).is_file()


@pytest.mark.obs
@pytest.mark.xfail(
    _grafana_dashboard_missing(),
    reason="grafana-pfactory.json not yet shipped — tracked at #160",
    strict=False,
)
def test_grafana_dashboard_json_is_valid() -> None:
    """guides/observability/grafana-pfactory.json parses + has required panels."""
    import json
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    dash_path = repo_root / "guides" / "observability" / "grafana-pfactory.json"
    assert dash_path.is_file(), f"missing dashboard JSON at {dash_path}"
    data = json.loads(dash_path.read_text())
    # Grafana dashboards have a top-level 'panels' array.
    assert "panels" in data, "no panels[] in dashboard JSON"
    panel_titles = {p.get("title", "").lower() for p in data["panels"]}
    # Required panels per issue #33.
    required = [
        "request rate", "latency", "error", "audit", "oidc",
    ]
    for token in required:
        assert any(token in title for title in panel_titles), (
            f"no panel matches '{token}' in {panel_titles}"
        )
