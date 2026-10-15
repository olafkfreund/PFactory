"""workspaces declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "workspaces",
    reason=(
        "Takes read-only snapshot copies of AIFactory specs under ~/.pfactory/workspaces per "
        "project and spec. Each snapshot is written once and never mutated, so no relation persists"
        " in the service between snapshots."
    ),
)
