"""
Agents Module
=============

Modular agent system for PFactory's autonomous test pipeline.

Forked from the upstream agents/ package; the `coder` agent has been
removed (it's not part of PFactory's role). The Planner, per-lane
Generators, Executor, Evaluator, and Triager agents that replace it
are scheduled in Tasks 5-8 (see docs/design-plan/).

Uses lazy imports to avoid circular dependencies.
"""

__all__ = [
    # Memory
    "debug_memory_system_status",
    "get_graphiti_context",
    "save_session_memory",
    "save_session_to_graphiti",
    # Session
    "run_agent_session",
    "post_session_processing",
    # Utils
    "get_latest_commit",
    "get_commit_count",
    "load_test_plan",
    "find_subtask_in_plan",
    "find_phase_for_subtask",
    "sync_plan_to_source",
    # Constants
    "AUTO_CONTINUE_DELAY_SECONDS",
    "HUMAN_INTERVENTION_FILE",
    # PFactory Planner (Task 5, #6)
    "run_planner",
    "schedule_planner",
    # PFactory Gen-Functional (Task 6, #7)
    "run_gen_functional",
    "schedule_gen_functional",
]


def __getattr__(name):
    """Lazy imports to avoid circular dependencies."""
    if name in ("AUTO_CONTINUE_DELAY_SECONDS", "HUMAN_INTERVENTION_FILE"):
        from .base import AUTO_CONTINUE_DELAY_SECONDS, HUMAN_INTERVENTION_FILE

        return locals()[name]
    elif name in (
        "debug_memory_system_status",
        "get_graphiti_context",
        "save_session_memory",
        "save_session_to_graphiti",
    ):
        from .memory_manager import (
            debug_memory_system_status,
            get_graphiti_context,
            save_session_memory,
            save_session_to_graphiti,
        )

        return locals()[name]
    elif name in ("post_session_processing", "run_agent_session"):
        from .session import post_session_processing, run_agent_session

        return locals()[name]
    elif name in ("run_planner", "schedule_planner"):
        from .planner import run_planner, schedule_planner

        return locals()[name]
    elif name in ("run_gen_functional", "schedule_gen_functional"):
        from .gen_functional import run_gen_functional, schedule_gen_functional

        return locals()[name]
    elif name in (
        "find_phase_for_subtask",
        "find_subtask_in_plan",
        "get_commit_count",
        "get_latest_commit",
        "load_test_plan",
        "sync_plan_to_source",
    ):
        from .utils import (
            find_phase_for_subtask,
            find_subtask_in_plan,
            get_commit_count,
            get_latest_commit,
            load_test_plan,
            sync_plan_to_source,
        )

        return locals()[name]
    raise AttributeError(f"module 'agents' has no attribute '{name}'")
