"""Map PFactory's native session status -> canonical lifecycle_state.

The canonical lifecycle set lives in the Factory hub's
``apis/status-taxonomy.json`` (``queued · running · review · done ·
failed · stuck``). RFC-0016 requires every job-state row to carry the
canonical ``lifecycle_state`` while keeping the raw ``service_status``
alongside, so any consumer (CFactory, a sibling replica) can classify a
PFactory job without knowing PFactory's private vocabulary.

PFactory's :class:`plan.service.PlanSession` statuses are::

    ingested | processing | reviewing | processed | approved | rejected | emitted

We classify them EXPLICITLY rather than by loose token containment so the
mapping is auditable and stable:

  - ``ingested``               -> queued    (admitted, awaiting a slot / process)
  - ``processing``             -> running   (pipeline executing in a worker thread)
  - ``reviewing``              -> running   (AI gates executing — still running)
  - ``processed``              -> review    (pipeline done; awaiting human approve)
  - ``approved``               -> review    (approved but NOT terminal until emit)
  - ``rejected``               -> failed    (terminal, unhealthy)
  - ``emitted``                -> done       (terminal, healthy)

Note ``approved`` maps to ``review`` (not ``done``): in PFactory a plan is
only *terminal* once it is emitted (or rejected). The taxonomy's ``done``
token set contains "approved", but that loose-token rule is for unknown
third-party statuses; for our own vocabulary we use the precise semantics
above so a still-in-flight approved plan is not reported as terminal.
"""

from __future__ import annotations

# Explicit PFactory-native -> canonical lifecycle map (single source of truth).
_NATIVE_TO_LIFECYCLE: dict[str, str] = {
    "ingested": "queued",
    "processing": "running",
    "reviewing": "running",
    "processed": "review",
    "approved": "review",
    "rejected": "failed",
    "emitted": "done",
    # Synthetic crash/abort marker (not a PFactory board status): the admission
    # path stamps this when process() raises so the durable row leaves `running`
    # with a terminal `failed` lifecycle, freeing the concurrency slot.
    "failed": "failed",
    "stuck": "stuck",
}

# Canonical states considered terminal (per status-taxonomy classification_rules).
TERMINAL_LIFECYCLE: frozenset[str] = frozenset({"done", "failed"})

# Canonical states that count against the in-flight admission cap (RFC-0016 §1:
# "reconstructs in-flight state by querying lifecycle_state in (queued, running)").
IN_FLIGHT_LIFECYCLE: tuple[str, ...] = ("queued", "running")


def lifecycle_state_for(native_status: str | None) -> str:
    """Return the canonical lifecycle_state for a PFactory native status.

    An unknown / falsy status reads as ``running`` — the taxonomy's
    running-fallback rule ("a present-but-unrecognized status reads as
    running"). This keeps a just-started job out of the terminal buckets.
    """
    if not native_status:
        return "running"
    return _NATIVE_TO_LIFECYCLE.get(native_status, "running")


def is_terminal(native_status: str | None) -> bool:
    """True when the native status maps to a terminal lifecycle state."""
    return lifecycle_state_for(native_status) in TERMINAL_LIFECYCLE
