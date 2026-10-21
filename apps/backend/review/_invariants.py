"""review declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "review",
    reason=(
        "Human review checkpoint between spec and build, reading and writing review_state.json "
        "inside each spec directory. Approval state belongs to one spec on disk, not to the running"
        " service."
    ),
)
