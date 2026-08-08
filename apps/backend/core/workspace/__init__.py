#!/usr/bin/env python3
"""
Workspace Management Package
=============================

Handles workspace isolation through Git worktrees, where each spec
gets its own isolated worktree in .pfactory/worktrees/tasks/{spec-name}/.

This package provides:
- Workspace setup and configuration
- Git operations and utilities
- Display and UI functions
- Finalization and user interaction
- Merge operations

Public API exported from sub-modules.
"""

# The merge operations below (`.merge`) reached this package through an
# importlib shim until #477: the logic lived in `core/workspace.py`, BESIDE this
# package, where Python could never load it — a package always wins over a
# same-named module — so __init__ executed the file by path under the separate
# name `workspace_module`. That made 1507 lines of merge logic invisible to
# every static tool and gave it a second module identity, so anything comparing
# types or module state across the two paths saw different objects. It now lives
# inside the package as `merge.py` and is imported like everything else here.

# Models and Enums
# Display Functions
from .display import (
    _print_conflict_info,
    # Export private names for backward compatibility
    _print_merge_success,
    print_conflict_info,
    print_merge_success,
    show_build_summary,
    show_changed_files,
)

# Finalization Functions
from .finalization import (
    check_existing_build,
    cleanup_all_worktrees,
    discard_existing_build,
    finalize_workspace,
    handle_workspace_choice,
    list_all_worktrees,
    review_existing_build,
)

# Git Utilities
from .git_utils import (
    BINARY_EXTENSIONS,
    LOCK_FILES,
    # Constants
    MAX_FILE_LINES_FOR_AI,
    MAX_PARALLEL_AI_MERGES,
    MAX_SYNTAX_FIX_RETRIES,
    MERGE_LOCK_TIMEOUT,
    _create_conflict_file_with_git,
    _get_changed_files_from_branch,
    _get_file_content_from_ref,
    _is_binary_file,
    _is_lock_file,
    # Export private names for backward compatibility
    _is_process_running,
    _validate_merged_syntax,
    create_conflict_file_with_git,
    get_changed_files_from_branch,
    get_current_branch,
    get_existing_build_worktree,
    get_file_content_from_ref,
    has_uncommitted_changes,
    is_binary_file,
    is_lock_file,
    is_process_running,
    validate_merged_syntax,
)
from .merge import _run_parallel_merges, merge_existing_build
from .models import (
    MergeLock,
    MergeLockError,
    ParallelMergeResult,
    ParallelMergeTask,
    WorkspaceChoice,
    WorkspaceMode,
)

# Setup Functions
from .setup import (
    # Export private names for backward compatibility
    _ensure_timeline_hook_installed,
    _initialize_timeline_tracking,
    choose_workspace,
    copy_spec_to_worktree,
    ensure_timeline_hook_installed,
    initialize_timeline_tracking,
    setup_workspace,
)

__all__ = [
    # Merge Operations (from workspace.py)
    "merge_existing_build",
    "_run_parallel_merges",  # Private but used internally
    # Models
    "WorkspaceMode",
    "WorkspaceChoice",
    "ParallelMergeTask",
    "ParallelMergeResult",
    "MergeLock",
    "MergeLockError",
    # Git Utils
    "has_uncommitted_changes",
    "get_current_branch",
    "get_existing_build_worktree",
    "get_file_content_from_ref",
    "get_changed_files_from_branch",
    "is_process_running",
    "is_binary_file",
    "validate_merged_syntax",
    "create_conflict_file_with_git",
    # Setup
    "choose_workspace",
    "copy_spec_to_worktree",
    "setup_workspace",
    "ensure_timeline_hook_installed",
    "initialize_timeline_tracking",
    # Display
    "show_build_summary",
    "show_changed_files",
    "print_merge_success",
    "print_conflict_info",
    # Finalization
    "finalize_workspace",
    "handle_workspace_choice",
    "review_existing_build",
    "discard_existing_build",
    "check_existing_build",
    "list_all_worktrees",
    "cleanup_all_worktrees",
]
