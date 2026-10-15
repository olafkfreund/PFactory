"""core declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "core",
    reason=(
        "Shared plumbing for agent sessions: SDK client construction, auth token lookup, worktree "
        "setup and progress files, all built per session from configuration and that session's "
        "directory. Its credential probes call external CLIs, which a cheap local check must not "
        "do."
    ),
)
