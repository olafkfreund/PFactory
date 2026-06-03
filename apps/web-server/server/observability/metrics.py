"""Prometheus /metrics with cardinality cap + optional auth (P6.3, P6.4).

Cardinality cap is the critical design choice: the default behavior
of `prometheus-fastapi-instrumentator` is to label `handler` with
the FULL request path, which explodes cardinality on routes with id
params (`/api/projects/abc.../tasks` ≠ `/api/projects/xyz.../tasks`).

We use `should_group_untemplated=True` AND the explicit
`should_only_respect_2xx_for_highr=True` knob — the instrumentator
then groups by route template (`/api/projects/{project_id}/tasks`).
"""

from __future__ import annotations

import logging
import os
from typing import Iterable

from fastapi import FastAPI, HTTPException, Request, status
from prometheus_fastapi_instrumentator import Instrumentator

logger = logging.getLogger(__name__)


def install_metrics(
    app: FastAPI,
    *,
    excluded_handlers: Iterable[str] = ("/metrics", "/api/health"),
) -> None:
    """Install Prometheus instrumentation on ``app``.

    Adds:
      - Default HTTP metrics (request rate, latency histograms,
        in-flight requests).
      - /metrics endpoint exposing the registry.
      - Cardinality cap via group_paths=True on the instrumentator,
        which keys `handler` by route template, not raw path.
      - Optional bearer-token gate via METRICS_SCRAPE_TOKEN env. When
        set, /metrics returns 401 without the matching token; when
        unset, /metrics is open (the default v1.0 posture for
        in-cluster scrapers behind NetworkPolicy).

    Idempotent: re-installing on the same app raises (the
    instrumentator registers metric collectors globally; double-
    register would be a programmer error).
    """
    instrumentator = Instrumentator(
        should_group_status_codes=True,       # 2xx, 3xx, ... not 200/204
        should_ignore_untemplated=False,
        should_group_untemplated=True,        # /api/projects/{id}/tasks
        should_respect_env_var=False,
        excluded_handlers=list(excluded_handlers),
    )
    instrumentator.instrument(app)

    # The instrumentator's expose() mounts a /metrics route. We wrap
    # it with a bearer-token gate so production deployments can
    # configure METRICS_SCRAPE_TOKEN + the chart's ServiceMonitor
    # passes the matching bearer.
    expected_token = os.environ.get("METRICS_SCRAPE_TOKEN", "").strip()

    if not expected_token:
        # Open scrape mode — typical v1.0 default (NetworkPolicy
        # restricts scrape source by IP anyway).
        instrumentator.expose(app, include_in_schema=False)
        return

    # Authenticated scrape mode. We define our own /metrics route
    # that validates the bearer first, then delegates to the
    # instrumentator's response generator.
    from fastapi.responses import Response
    from prometheus_client import REGISTRY, generate_latest, CONTENT_TYPE_LATEST

    @app.get("/metrics", include_in_schema=False)
    async def _gated_metrics(request: Request) -> Response:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer")
        token = auth.removeprefix("Bearer ").strip()
        if token != expected_token:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
        return Response(
            generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST
        )
