"""planner_lib declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "planner_lib",
    reason=(
        "Generates an implementation plan from one spec by reading that spec and the codebase it "
        "names, and returns the plan to its caller. It has no module state that could drift."
    ),
)
