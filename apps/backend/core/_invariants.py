"""core declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "core",
    reason=(
        "Shared plumbing for agent sessions. Its process-lifetime state is caches and two "
        "one-shot flags. The caches: client._PROJECT_INDEX_CACHE (5-minute TTL, locked), "
        "cache._PREFIX_HASHES (only drives a cold-cache warning) and the lru_cache on "
        "mcp_credentials._load_operator_config. A stale entry costs a reload or a warning, not a "
        "wrong result, and its credential probes call external CLIs. The flags, "
        "fast_mode._fast_mode_atexit_registered and workspace/setup._git_hook_check_done, only "
        "stop a registration or a check repeating."
    ),
)
