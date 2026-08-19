"""
Graphiti Integration
====================

Integration with Graphiti knowledge graph for semantic memory.
"""

from typing import TYPE_CHECKING

# Config imports don't require graphiti package
from .config import GraphitiConfig, validate_graphiti_config

if TYPE_CHECKING:
    # Bound lazily by __getattr__ below; declared here so static analysis
    # (CodeQL py/undefined-export, mypy, IDEs) can resolve every __all__ entry.
    from .memory import GraphitiMemory
    from .providers import create_embedder, create_llm_client

# Lazy imports for components that require graphiti package
__all__ = [
    "GraphitiConfig",
    "GraphitiMemory",
    "create_embedder",
    "create_llm_client",
    "validate_graphiti_config",
]


def __getattr__(name):
    """Lazy import to avoid requiring graphiti package for config-only imports."""
    if name == "GraphitiMemory":
        from .memory import GraphitiMemory

        return GraphitiMemory
    elif name == "create_llm_client":
        from .providers import create_llm_client

        return create_llm_client
    elif name == "create_embedder":
        from .providers import create_embedder

        return create_embedder
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
