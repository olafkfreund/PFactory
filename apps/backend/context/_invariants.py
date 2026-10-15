"""context declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "context",
    reason=(
        "Builds one task's coding context (keyword extraction, file categorisation, service "
        "matching) from the project directory it is given, per call, and caches nothing in the "
        "process between calls."
    ),
)
