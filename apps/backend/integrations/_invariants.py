"""integrations declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "integrations",
    reason=(
        "Wraps the optional Graphiti knowledge-graph memory, which is off unless GRAPHITI_ENABLED "
        "is set and talks to an external graph database. The one relation worth watching is that "
        "database's reachability, a network call and so not a local invariant."
    ),
)
