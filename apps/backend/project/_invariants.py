"""project declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "project",
    reason=(
        "Detects a project's stack and builds its command allowlist per project directory. The "
        "cross-package relation it feeds, that every command needing validation has a validator, is"
        " owned and checked by security.hooks."
    ),
)
