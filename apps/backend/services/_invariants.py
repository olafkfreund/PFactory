"""services declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "services",
    reason=(
        "Starts and stops docker-compose or monorepo services for a test run, and tracks rollback "
        "attempts for a failing build. Both concern one run's containers and history, checked by "
        "that run's own health checks."
    ),
)
