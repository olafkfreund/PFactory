"""agents declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "agents",
    reason=(
        "Holds the planner, gen_functional, evaluator and triager stage agents. Process-wide "
        "state: the _BG_*_TASKS sets anchor fire-and-forget stage tasks and discard each when "
        "done, and evidence.http_recorder's _entries list is reset and drained inside one "
        "record_http_to_har block. Neither holds a relation that outlives the call that owns "
        "it."
    ),
)
