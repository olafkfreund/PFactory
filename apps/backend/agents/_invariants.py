"""agents declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "agents",
    reason=(
        "Holds the planner, generator, executor, evaluator and triager agents. Every relation they "
        "keep (lane outputs, verdict signals, evidence files) belongs to one task run and lives in "
        "that run's workspace, so no process-wide state exists to watch between runs."
    ),
)
