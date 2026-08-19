"""
Core Framework Module
=====================

Core components for the PFactory autonomous coding framework.
"""

# Note: We use lazy imports here because the full agent module has many dependencies
# that may not be needed for basic operations like workspace management.

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Bound lazily by __getattr__ below; declared here so static analysis
    # (CodeQL py/undefined-export, mypy, IDEs) can resolve every __all__ entry.
    from .worktree import WorktreeManager

__all__ = [
    "WorktreeManager",
]


def __getattr__(name):
    """Lazy imports to avoid circular dependencies and heavy imports."""
    if name == "WorktreeManager":
        from .worktree import WorktreeManager

        return WorktreeManager
    elif name in ("create_claude_client", "ClaudeClient"):
        from . import client as _client

        return getattr(_client, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
