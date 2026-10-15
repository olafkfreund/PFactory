"""agents declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "agents",
    reason=(
        "Holds the planner, gen_functional, evaluator and triager stage agents. Their only "
        "process-wide state is the _BG_*_TASKS sets that anchor fire-and-forget stage tasks and "
        "discard each one when it finishes; every other relation (lane outputs, verdicts, "
        "evidence) belongs to one task run's workspace."
    ),
)
