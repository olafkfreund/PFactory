"""task_logger declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "task_logger",
    reason=(
        "Writes phase-structured task logs as JSON into each spec directory and streams markers to "
        "the UI. Each log belongs to one task, so there is no service-wide relation to assert."
    ),
)
