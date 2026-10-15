"""memory declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "memory",
    reason=(
        "Persists session learnings as files in each spec's memory directory, or in Graphiti when "
        "enabled. The files belong to one spec and are read back by the next session for that spec,"
        " so no process-wide relation exists."
    ),
)
