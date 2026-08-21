"""
Agent Session Logic
===================

Core agent interaction functions for running autonomous coding sessions.
Uses subtask-based implementation plans with minimal, focused prompts.

Architecture:
- Orchestrator (Python) handles all bookkeeping: memory, commits, progress
- Agent focuses ONLY on implementing code
- Post-session processing updates memory automatically (100% reliable)

Enhanced with status file updates for ccstatusline integration.
Enhanced with Graphiti memory for cross-session context retrieval.

NOTE: This module is now a facade that imports from agents/ submodules.
All logic has been refactored into focused modules for better maintainability.

This fork removed the coder agent (`run_autonomous_agent`), so only the names
`agents` actually exports are re-exported here. See agents/__init__.py's
docstring for what replaces it.

`run_followup_planner` was ALSO listed here as removed, and that was wrong: it
is alive at `agents/planner.py:42` and imports fine. Only its re-export through
this facade went away. The stale line cost real time during PFactory#607 --
it reads as "the function is gone", so a wrong-module import looks like a
missing capability, and the two need opposite fixes.
"""

# Re-export everything the agents module actually exports, to maintain
# backwards compatibility with callers of this pre-refactor import path.
from agents import (
    # Constants
    AUTO_CONTINUE_DELAY_SECONDS,
    HUMAN_INTERVENTION_FILE,
    # Memory functions
    debug_memory_system_status,
    find_phase_for_subtask,
    find_subtask_in_plan,
    get_commit_count,
    get_graphiti_context,
    # Utility functions
    get_latest_commit,
    load_test_plan,
    post_session_processing,
    # Session management
    run_agent_session,
    run_gen_functional,
    run_planner,
    save_session_memory,
    save_session_to_graphiti,
    schedule_gen_functional,
    schedule_planner,
    sync_plan_to_source,
)

# Ensure all exports are available at module level
__all__ = [
    "AUTO_CONTINUE_DELAY_SECONDS",
    "HUMAN_INTERVENTION_FILE",
    "debug_memory_system_status",
    "find_phase_for_subtask",
    "find_subtask_in_plan",
    "get_commit_count",
    "get_graphiti_context",
    "get_latest_commit",
    "load_test_plan",
    "post_session_processing",
    "run_agent_session",
    "run_gen_functional",
    "run_planner",
    "save_session_memory",
    "save_session_to_graphiti",
    "schedule_gen_functional",
    "schedule_planner",
    "sync_plan_to_source",
]
