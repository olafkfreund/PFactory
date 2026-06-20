"""
Graphiti Memory Integration V2 - Backward Compatibility Facade
================================================================

This module maintains backward compatibility by re-exporting the modular
memory system from the pfactory/graphiti/ package.

The refactored code is now organized as:
- graphiti/graphiti.py - Main GraphitiMemory class
- graphiti/client.py - LadybugDB client wrapper
- graphiti/queries.py - Graph query operations
- graphiti/search.py - Semantic search logic
- graphiti/schema.py - Graph schema definitions

This facade ensures existing imports continue to work:
    from graphiti_memory import GraphitiMemory, is_graphiti_enabled

New code should prefer importing from the graphiti package:
    from graphiti import GraphitiMemory
    from graphiti.schema import GroupIdMode

For detailed documentation on the memory system architecture and usage,
see graphiti/graphiti.py.
"""

from pathlib import Path

# Import config utilities
from graphiti_config import (
    GraphitiConfig,
    is_graphiti_enabled,
)

# Re-export from modular system (queries_pkg)
from .queries_pkg.graphiti import GraphitiMemory
from .queries_pkg.schema import (
    EPISODE_TYPE_CODEBASE_DISCOVERY,
    EPISODE_TYPE_GOTCHA,
    EPISODE_TYPE_HISTORICAL_CONTEXT,
    EPISODE_TYPE_PATTERN,
    EPISODE_TYPE_QA_RESULT,
    EPISODE_TYPE_SESSION_INSIGHT,
    EPISODE_TYPE_TASK_OUTCOME,
    MAX_CONTEXT_RESULTS,
    GroupIdMode,
)


# Convenience function for getting a memory manager
def get_graphiti_memory(
    spec_dir: Path,
    project_dir: Path,
    group_id_mode: str = GroupIdMode.SPEC,
) -> GraphitiMemory:
    """
    Get a GraphitiMemory instance for the given spec.

    This is the main entry point for other modules.

    Args:
        spec_dir: Spec directory
        project_dir: Project root directory
        group_id_mode: "spec" for isolated memory, "project" for shared

    Returns:
        GraphitiMemory instance
    """
    return GraphitiMemory(spec_dir, project_dir, group_id_mode)


async def test_graphiti_connection() -> tuple[bool, str]:
    """
    Test if LadybugDB is available and Graphiti can connect.

    Returns:
        Tuple of (success: bool, message: str)
    """
    config = GraphitiConfig.from_env()

    if not config.enabled:
        return False, "Graphiti not enabled (GRAPHITI_ENABLED not set to true)"

    # Validate provider configuration
    errors = config.get_validation_errors()
    if errors:
        return False, f"Configuration warnings: {'; '.join(errors)}"

    try:
        from graphiti_core import Graphiti
        from graphiti_providers import ProviderError, create_embedder, create_llm_client

        # Create providers
        try:
            llm_client = create_llm_client(config)
            embedder = create_embedder(config)
        except ProviderError as e:
            return False, f"Provider error: {e}"

        # Apply LadybugDB monkeypatch (same as client.py)
        from .queries_pkg.client import _apply_ladybug_monkeypatch

        if not _apply_ladybug_monkeypatch():
            return False, (
                "LadybugDB/kuzu not installed. "
                "Install with: pip install real_ladybug (requires Python 3.12+) or pip install kuzu"
            )

        # Use patched KuzuDriver (same as client.py)
        from .queries_pkg.kuzu_driver_patched import create_patched_kuzu_driver

        db_path = config.get_db_path()
        try:
            driver = create_patched_kuzu_driver(db=str(db_path))
        except Exception as e:
            return False, f"Failed to initialize LadybugDB driver at {db_path}: {e}"

        graphiti = Graphiti(
            graph_driver=driver,
            llm_client=llm_client,
            embedder=embedder,
        )

        # Try a simple operation
        await graphiti.build_indices_and_constraints()
        await graphiti.close()

        return True, (
            f"Connected to LadybugDB at {db_path} (providers: {config.get_provider_summary()})"
        )

    except ImportError as e:
        return False, f"Graphiti packages not installed: {e}"

    except Exception as e:
        return False, f"Connection failed: {e}"


async def test_provider_configuration() -> dict:
    """
    Test the current provider configuration and return detailed status.

    Returns:
        Dict with test results for each component
    """
    from graphiti_providers import (
        test_embedder_connection,
        test_llm_connection,
        test_ollama_connection,
    )

    config = GraphitiConfig.from_env()

    results = {
        "config_valid": config.is_valid(),
        "validation_errors": config.get_validation_errors(),
        "llm_provider": config.llm_provider,
        "embedder_provider": config.embedder_provider,
        "llm_test": None,
        "embedder_test": None,
    }

    # Test LLM
    llm_success, llm_msg = await test_llm_connection(config)
    results["llm_test"] = {"success": llm_success, "message": llm_msg}

    # Test embedder
    emb_success, emb_msg = await test_embedder_connection(config)
    results["embedder_test"] = {"success": emb_success, "message": emb_msg}

    # Extra test for Ollama
    if config.llm_provider == "ollama" or config.embedder_provider == "ollama":
        ollama_success, ollama_msg = await test_ollama_connection(config.ollama_base_url)
        results["ollama_test"] = {"success": ollama_success, "message": ollama_msg}

    return results


# Re-export all public APIs for backward compatibility
__all__ = [
    "EPISODE_TYPE_CODEBASE_DISCOVERY",
    "EPISODE_TYPE_GOTCHA",
    "EPISODE_TYPE_HISTORICAL_CONTEXT",
    "EPISODE_TYPE_PATTERN",
    "EPISODE_TYPE_QA_RESULT",
    "EPISODE_TYPE_SESSION_INSIGHT",
    "EPISODE_TYPE_TASK_OUTCOME",
    "MAX_CONTEXT_RESULTS",
    "GraphitiMemory",
    "GroupIdMode",
    "get_graphiti_memory",
    "is_graphiti_enabled",
    "test_graphiti_connection",
    "test_provider_configuration",
]
