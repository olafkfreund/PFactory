"""The tracing module must not claim an export that was refused (Factory#516).

The bug this locks: "OTel tracing enabled" was logged straight after the
``OTLPSpanExporter`` constructor, which touches no network. For the whole
life of the fleet that line was true about configuration and false about
delivery — every batch came back 401. So the only thing worth testing here
is that the claim tracks the collector's answer.

Mutation-checked: make ``verify_export_auth`` ignore its result and
``test_rejected_export_*`` fail; make ``_RateLimitFilter.filter`` return
True unconditionally and ``test_rate_limit_suppresses`` fails.
"""

import logging
from contextlib import contextmanager

import pytest
from opentelemetry.sdk.trace.export import SpanExportResult

from server.observability.tracing import (
    _install_exporter,
    _RateLimitFilter,
    auth_scheme,
    rate_limit_exporter_log,
    verify_export_auth,
)
from server.observability.tracing import (
    logger as tracing_logger,
)


class _ListHandler(logging.Handler):
    """Collect records in a list. Subclassed rather than monkeypatching
    ``emit``, which mypy rightly refuses on a bound method."""

    def __init__(self, sink: list[logging.LogRecord]) -> None:
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self.sink.append(record)


@contextmanager
def captured():
    """Capture this module's own records, whatever else touched logging.

    Deliberately NOT ``caplog``: caplog reads through the ROOT logger, and a
    suite that has run ``logging.config.fileConfig`` anywhere (alembic's
    stock ``env.py`` does, with ``disable_existing_loggers`` defaulting to
    True) leaves this module's logger ``disabled``, so caplog.text comes
    back empty and the test fails for a reason that has nothing to do with
    what it is testing. Measured in CFactory#276.
    """
    records: list[logging.LogRecord] = []
    handler = _ListHandler(records)
    was_disabled, was_level = tracing_logger.disabled, tracing_logger.level
    tracing_logger.disabled = False
    tracing_logger.setLevel(logging.DEBUG)
    tracing_logger.addHandler(handler)
    try:
        yield records
    finally:
        tracing_logger.removeHandler(handler)
        tracing_logger.disabled = was_disabled
        tracing_logger.setLevel(was_level)


def text(records) -> str:
    return "\n".join(r.getMessage() for r in records)


class _Exporter:
    """Minimal stand-in: export() is the only thing the probe calls."""

    def __init__(self, result):
        self._result = result
        self.calls = []

    def export(self, spans):
        self.calls.append(spans)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def test_accepted_export_is_reported_as_enabled():
    exporter = _Exporter(SpanExportResult.SUCCESS)
    with captured() as records:
        assert verify_export_auth(exporter, "http://collector", "http/protobuf", "svc")
    # The probe must actually spend a request, and carry no span data.
    assert exporter.calls == [[]]
    assert "startup export accepted" in text(records)


def test_rejected_export_is_reported_as_no_spans_landing():
    exporter = _Exporter(SpanExportResult.FAILURE)
    with captured() as records:
        assert not verify_export_auth(exporter, "http://collector", "http/protobuf", "svc")
    assert "NO SPANS WILL LAND" in text(records)
    # #465 was a Bearer/Basic mismatch, so the scheme has to be in the line.
    assert "auth=" in text(records)


def test_unprobeable_exporter_is_unverified_not_enabled():
    exporter = _Exporter(RuntimeError("collector restarting"))
    with captured() as records:
        assert not verify_export_auth(exporter, "http://collector", "http/protobuf", "svc")
    assert "UNVERIFIED" in text(records)
    # An exporter that cannot be probed must never claim the enabled line.
    assert "startup export accepted" not in text(records)


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ("Authorization=Bearer abc123secret", "Bearer"),
        ("Authorization=Basic dXNlcjpwYXNz", "Basic"),
        ("", "<no Authorization header>"),
        ("x-other=1", "<no Authorization header>"),
    ],
)
def test_auth_scheme_names_the_scheme_and_never_the_value(monkeypatch, headers, expected):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", headers)
    got = auth_scheme()
    assert got == expected
    assert "abc123secret" not in got
    assert "dXNlcjpwYXNz" not in got


def test_rate_limit_suppresses_repeats_and_reports_the_count():
    f = _RateLimitFilter(interval=3600)
    rec = lambda: logging.LogRecord(  # noqa: E731
        "x", logging.ERROR, __file__, 1, "Failed to export", None, None
    )
    assert f.filter(rec()) is True
    assert f.filter(rec()) is False
    assert f.filter(rec()) is False

    f._last = 0.0  # pretend the interval elapsed
    passed = rec()
    assert f.filter(passed) is True
    assert "2 identical messages suppressed" in passed.getMessage()


def test_rate_limit_attaches_to_the_exporters_own_logger():
    """A filter on a parent logger would not fire: filters do not run on
    propagated records. So it must land on the exporter's own module."""
    exporter = _Exporter(SpanExportResult.SUCCESS)
    target = logging.getLogger(type(exporter).__module__)
    before = len(target.filters)
    rate_limit_exporter_log(exporter)
    try:
        assert len(target.filters) == before + 1
    finally:
        target.filters = target.filters[:before]


def test_unsupported_protocol_installs_nothing_and_says_so(monkeypatch):
    """Only http/protobuf ships an exporter, so grpc must not fail silently.

    Without this branch the grpc import would raise ImportError and be
    swallowed by the broad handler as "failed to install", which names the
    symptom rather than the configuration mistake.
    """
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    with captured() as records:
        _install_exporter("http://collector", "svc")
    assert "NO SPANS WILL LAND" in text(records)
    assert "http/protobuf" in text(records)
