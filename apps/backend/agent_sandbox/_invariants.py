"""agent_sandbox declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "agent_sandbox",
    reason=(
        "Vendored from the hub (Factory#3709) and stateless: env filtering and git config pins "
        "are pure functions of os.environ and the repo's git config at call time. The only "
        "process-wide state is a set of names already logged, which holds no relation that "
        "outlives a call."
    ),
)
