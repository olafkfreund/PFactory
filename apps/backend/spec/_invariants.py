"""spec declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "spec",
    reason=(
        "Two path helpers that locate a project's specs directory for the build CLI. They compute "
        "paths from their argument and hold nothing, deliberately unlike AIFactory's larger spec "
        "package."
    ),
)
