"""merge declares no runtime invariant (RFC-0024 §3)."""

from factory_invariants import registry

registry.register(
    "merge",
    reason=(
        "Intent-aware merge of parallel task branches, run once per merge on the git worktrees it "
        "is handed. Its timelines and baselines are files per project, so nothing lives in the "
        "service between merges."
    ),
)
