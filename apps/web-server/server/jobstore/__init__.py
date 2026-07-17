"""Durable, multi-replica-safe job-state store (RFC-0016 #217).

This package backs PFactory's plan/session lifecycle with shared Postgres
instead of the per-pod in-memory ``_sessions`` dict (and the optional
per-pod JSON mirror). One row per plan job conforms to the Factory hub's
``apis/job-state.schema.json`` (``service="pfactory"``, ``kind="plan"``).

The store survives a control-plane restart and is consistent across
replicas: the admission cap reads a live count from this table, and a
concurrency slot is granted inside a ``SELECT ... FOR UPDATE`` transaction
so two replicas cannot exceed the cap or double-start one ``job_id``
(see ``apis/concurrency-conventions.md`` §1).

Only used when ``DATABASE_URL`` is set; the in-memory path remains a
single-pod-dev fallback (and logs that it is not multi-replica safe).
"""

from __future__ import annotations

from .lifecycle import lifecycle_state_for
from .store import (
    JobStateStore,
    SlotDenied,
    jobstore_enabled,
    lease_heartbeat_interval,
    lease_ttl_seconds,
    lifecycle_filter,
)

__all__ = [
    "JobStateStore",
    "SlotDenied",
    "jobstore_enabled",
    "lease_heartbeat_interval",
    "lease_ttl_seconds",
    "lifecycle_filter",
    "lifecycle_state_for",
]
