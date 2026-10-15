"""runners declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "runners",
    reason=(
        "Namespace package that re-exports nothing since #43. Its submodules (the GitHub client and"
        " providers, the artifact store, the changelog runner) are imported directly by consumers "
        "and act on the repository or API call they are given."
    ),
)
