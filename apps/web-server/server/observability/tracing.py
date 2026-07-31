"""OpenTelemetry tracing — OTLP export to the fleet's collector (Factory#516).

A PARR run crosses PFactory -> AIFactory -> TFactory and is displayed by
CFactory. Only AIFactory ever exported a span; this service had no
``OTEL_*`` configuration at all, so it logged no export failure and its
silence read as health. A trace could never span a handoff, which is the
one question distributed tracing exists to answer.

## Off by default, and honest when on

``OTEL_EXPORTER_OTLP_ENDPOINT`` unset (the default outside the cluster,
and in every test run) means no exporter is installed: spans still get
created by the FastAPI instrumentation, cost nothing, and are dropped.

When the endpoint IS set, the exporter is installed AND PROVEN before
this module claims anything. Constructing an ``OTLPSpanExporter`` touches
no network, so "tracing enabled" logged straight after that constructor
describes configuration, not delivery — and in AIFactory it described
delivery that had never once happened: every batch came back 401 for the
entire life of the fleet because the exporter sent ``Authorization:
Bearer`` and OpenObserve v0.90.3 answers Bearer with ``401 Not
Supported``. It authenticates OTLP ingest with HTTP Basic and nothing
else.

So ``init_tracing()`` spends one empty OTLP POST first. ``export([])``
round-trips the auth header without carrying any span data, which
separates "configured" from "accepted":

    accepted    -> INFO    "OTel tracing enabled ... (startup export accepted)"
    rejected    -> ERROR   "... REJECTED the startup export: NO SPANS WILL LAND"
    unprobeable -> WARNING "... enabled but UNVERIFIED"

The exporter is installed in all three cases: a collector that is merely
restarting must be able to recover without a pod bounce, so a failed
probe downgrades the claim, never the wiring. The error names the auth
*scheme* and never its value — the scheme was the whole diagnosis, and it
leaks nothing.

## Noise

A persistently rejected exporter emits an identical ERROR every export
interval — in AIFactory, 199 of the last 300 log lines. A log that has to
be read through ``grep -v otlp`` is how a real error gets missed. The SDK
exporter's own logger is rate-limited to one line per
``OTEL_EXPORT_ERROR_INTERVAL`` seconds (default 300) plus a
suppressed-count summary on the next line through.

## What is instrumented

FastAPI (server spans, and W3C ``traceparent`` EXTRACTION from inbound
requests) and httpx (client spans, and ``traceparent`` INJECTION into
outbound ones). Those two are what make a trace cross a service boundary;
the cross-factory calls in this fleet are httpx calls into another
factory's FastAPI. Database and cache instrumentation is deliberately not
included — it adds span volume without adding a handoff.

Refs: Factory#516, Factory#465.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

# Default when OTEL_SERVICE_NAME is unset. Distinguishes this service in
# the collector; the four factories share one OpenObserve org.
DEFAULT_SERVICE_NAME = "pfactory-web"

# The only OTLP transport this service ships an exporter for.
PROTOCOL = "http/protobuf"

# How long a rejected exporter stays quiet between log lines, seconds.
_EXPORT_ERROR_INTERVAL = float(os.environ.get("OTEL_EXPORT_ERROR_INTERVAL", "300"))

# Set on the first init_tracing() call; later calls no-op so tests can
# re-enter cheaply.
_initialized = False
_tracer_provider: Any = None


def init_tracing(app: Any = None) -> None:
    """Install the tracer provider, the OTLP exporter and instrumentation.

    Idempotent, and never raises: a broken tracing subsystem must not stop
    the service from starting. Reads

    - ``OTEL_EXPORTER_OTLP_ENDPOINT`` — unset/empty means no exporter
    - ``OTEL_EXPORTER_OTLP_PROTOCOL`` — ``http/protobuf`` (the default, and
      the only value this service ships an exporter for)
    - ``OTEL_EXPORTER_OTLP_HEADERS`` — read by the exporter package itself
    - ``OTEL_SERVICE_NAME`` — defaults to ``DEFAULT_SERVICE_NAME``

    Pass ``app`` to instrument that FastAPI application.
    """
    # PLW0603: the provider is a process-wide singleton by construction —
    # OTel's own trace.set_tracer_provider() is global too.
    global _initialized, _tracer_provider  # noqa: PLW0603
    if _initialized:
        return

    try:
        from opentelemetry import trace  # noqa: PLC0415
        from opentelemetry.sdk.resources import Resource  # noqa: PLC0415
        from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415

        service_name = os.environ.get("OTEL_SERVICE_NAME", DEFAULT_SERVICE_NAME)

        # A test fixture may already have installed a provider with an
        # in-memory exporter. Respect it rather than clobbering it.
        current = trace.get_tracer_provider()
        if isinstance(current, TracerProvider):
            _tracer_provider = current
        else:
            _tracer_provider = TracerProvider(
                resource=Resource.create({"service.name": service_name})
            )
            trace.set_tracer_provider(_tracer_provider)

        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        if endpoint:
            _install_exporter(endpoint, service_name)
        else:
            logger.info(
                "OTel tracing initialised without an exporter "
                "(OTEL_EXPORTER_OTLP_ENDPOINT unset) — spans build in memory "
                "and drop. service=%s",
                service_name,
            )

        _instrument(app)
        _initialized = True
    # BLE001 x5 in this module: tracing must never be able to stop the
    # service starting, so every failure path here is deliberately broad.
    except Exception:  # noqa: BLE001
        logger.warning(
            "init_tracing() failed; continuing without distributed tracing",
            exc_info=True,
        )


def _install_exporter(endpoint: str, service_name: str) -> None:
    """Install the OTLP span exporter, then prove it before claiming it."""
    protocol = os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL", PROTOCOL).strip().lower()
    if protocol != PROTOCOL:
        # ponytail: http/protobuf only. The collector is OpenObserve over
        # OTLP/HTTP and only `opentelemetry-exporter-otlp-proto-http` is
        # installed, so a grpc branch here would be a code path that cannot
        # import — and it would fail with a confusing ImportError rather than
        # naming the actual mistake. Say the actual mistake. Adding grpc means
        # adding the exporter package and one branch here.
        logger.error(
            "OTLP exporter NOT installed: OTEL_EXPORTER_OTLP_PROTOCOL=%s, but "
            "this service ships only the http/protobuf exporter. NO SPANS WILL "
            "LAND. endpoint=%s service=%s",
            protocol,
            endpoint,
            service_name,
        )
        return
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: PLC0415
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: PLC0415

        exporter = OTLPSpanExporter()
        # Quieten the SDK exporter BEFORE the probe, so a broken credential
        # costs one line here rather than one every few seconds forever.
        rate_limit_exporter_log(exporter)
        _tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
        verify_export_auth(exporter, endpoint, protocol, service_name)
    except Exception:  # noqa: BLE001
        logger.warning(
            "Failed to install the OTLP exporter; tracing degrades to "
            "build-in-memory and drop. endpoint=%s protocol=%s",
            endpoint,
            protocol,
            exc_info=True,
        )


def verify_export_auth(exporter: Any, endpoint: str, protocol: str, service_name: str) -> bool:
    """Prove the endpoint and the credential with one empty export.

    ``export([])`` posts an empty OTLP payload: one request, no span data,
    and it still round-trips the auth header. Measured against OpenObserve
    v0.90.3 on 2026-07-30: a Bearer header returns FAILURE (401 Not
    Supported), Basic root credentials return SUCCESS.

    Returns whether the collector accepted it. Never raises — an exporter
    that cannot be probed stays installed and is reported as unverified
    rather than torn down.
    """
    try:
        from opentelemetry.sdk.trace.export import SpanExportResult  # noqa: PLC0415

        ok = exporter.export([]) is SpanExportResult.SUCCESS
    except Exception:  # noqa: BLE001
        logger.warning(
            "OTel tracing enabled but UNVERIFIED — the startup export probe "
            "could not run. endpoint=%s protocol=%s service=%s",
            endpoint,
            protocol,
            service_name,
            exc_info=True,
        )
        return False

    if ok:
        logger.info(
            "OTel tracing enabled — endpoint=%s protocol=%s service=%s (startup export accepted)",
            endpoint,
            protocol,
            service_name,
        )
        return True

    logger.error(
        "OTel tracing is configured but the collector REJECTED the startup "
        "export: NO SPANS WILL LAND. endpoint=%s protocol=%s service=%s "
        "auth=%s. Check OTEL_EXPORTER_OTLP_HEADERS against what the collector "
        "accepts (Factory#465).",
        endpoint,
        protocol,
        service_name,
        auth_scheme(),
    )
    return False


def auth_scheme() -> str:
    """The scheme name from OTEL_EXPORTER_OTLP_HEADERS, never its value.

    "Bearer where the collector only accepts Basic" was the whole
    diagnosis for Factory#465, and naming the scheme leaks nothing.
    """
    raw = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "")
    for part in raw.split(","):
        name, _, value = part.partition("=")
        if name.strip().lower() == "authorization":
            return value.strip().split(" ")[0] or "<empty>"
    return "<no Authorization header>"


class _RateLimitFilter(logging.Filter):
    """Pass one record per ``interval``; count and summarise the rest.

    ponytail: one counter, not one per message key. The OTLP exporter logs
    a single message shape ("Failed to export ... code: N"), so keying by
    message would buy nothing and cost a dict.
    """

    def __init__(self, interval: float) -> None:
        super().__init__()
        self._interval = interval
        self._last = 0.0
        self._suppressed = 0

    def filter(self, record: logging.LogRecord) -> bool:
        now = time.monotonic()
        if self._last and now - self._last < self._interval:
            self._suppressed += 1
            return False
        if self._suppressed:
            record.msg = (
                f"{record.getMessage()} [+{self._suppressed} identical messages "
                f"suppressed in the previous {self._interval:.0f}s]"
            )
            record.args = ()
            self._suppressed = 0
        self._last = now
        return True


def rate_limit_exporter_log(exporter: Any) -> None:
    """Rate-limit whichever SDK logger this exporter actually uses.

    Derived from the exporter's own module so it is exact for both the
    http and grpc variants. A filter on a parent logger would not fire:
    filters do not run on propagated records.
    """
    try:
        logging.getLogger(type(exporter).__module__).addFilter(
            _RateLimitFilter(_EXPORT_ERROR_INTERVAL)
        )
    except Exception:  # noqa: BLE001
        logger.debug("could not rate-limit the OTLP exporter log", exc_info=True)


def _instrument(app: Any) -> None:
    """FastAPI + httpx instrumentation, each independently best-effort.

    FastAPI extracts an inbound W3C ``traceparent``; httpx injects one on
    the way out. Together they are what makes one trace span a handoff.
    """
    if app is not None:
        _instrument_one("FastAPI", lambda: _instrument_fastapi(app))
    _instrument_one("httpx", _instrument_httpx)


def _instrument_one(name: str, fn: Any) -> None:
    """Install one instrumentor, and SAY whether it worked (Factory#516).

    Both halves used to log at DEBUG, which the deployed pods do not emit. That
    made the only two outcomes indistinguishable at INFO: instrumentation
    installed, or instrumentation raised and was swallowed. In the second case
    no span is ever created, yet init_tracing still logs "OTel tracing enabled
    ... (startup export accepted)" -- because that line is earned by the
    EXPORTER probe, which is a different thing and passes either way.

    A service claiming tracing while producing zero spans is precisely what
    Factory#516 is about, so the failure is a WARNING and the success is an INFO
    naming what is actually instrumented.
    """
    try:
        fn()
        logger.info("OTel instrumentation installed: %s", name)
    except Exception:  # noqa: BLE001 — instrumentation must never stop the service
        logger.warning(
            "OTel instrumentation FAILED: %s — no spans will be created for it, "
            "even though the exporter is configured and its startup probe passed "
            "(Factory#516).",
            name,
            exc_info=True,
        )


def _instrument_fastapi(app: Any) -> None:
    from opentelemetry.instrumentation.fastapi import (  # noqa: PLC0415
        FastAPIInstrumentor,
    )

    FastAPIInstrumentor.instrument_app(app)


def _instrument_httpx() -> None:
    from opentelemetry.instrumentation.httpx import (  # noqa: PLC0415
        HTTPXClientInstrumentor,
    )

    HTTPXClientInstrumentor().instrument()
