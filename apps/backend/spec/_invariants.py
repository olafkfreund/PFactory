"""spec declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "spec",
    reason=(
        "One path helper, get_specs_dir, and two directory-name constants that locate a "
        "project's specs directory for the build CLI. It computes paths from its argument and "
        "holds nothing, deliberately unlike AIFactory's larger spec package."
    ),
)
