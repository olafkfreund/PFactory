"""test_plan declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "test_plan",
    reason=(
        "Dataclasses and enums for the subtask-based test_plan.json (phases, subtasks, "
        "verification). Pure data types whose consistency is enforced when a plan is built, holding"
        " no process state."
    ),
)
