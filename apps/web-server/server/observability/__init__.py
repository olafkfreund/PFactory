"""Observability layer for PFactory (Epic #26 P6).

Four concerns, four modules:
  - structlog_setup: JSON logging to stdout, correlation-ID bind.
  - correlation_id:  ASGI middleware + contextvar for X-Request-ID.
  - metrics:         prometheus-fastapi-instrumentator wiring with
                     cardinality cap + optional bearer-token gate.
  - tracing:         OTLP span export to the fleet collector, with a
                     startup probe so "enabled" is earned (Factory#516).
"""

from .correlation_id import (
    CORRELATION_ID_HEADER,
    CorrelationIdMiddleware,
    get_correlation_id,
    install_httpx_propagation,
    reset_correlation_id,
    set_correlation_id,
)
from .metrics import install_metrics
from .structlog_setup import configure_structlog, get_logger
from .tracing import init_tracing

__all__ = [
    "CORRELATION_ID_HEADER",
    "CorrelationIdMiddleware",
    "configure_structlog",
    "get_correlation_id",
    "get_logger",
    "init_tracing",
    "install_httpx_propagation",
    "install_metrics",
    "reset_correlation_id",
    "set_correlation_id",
]
