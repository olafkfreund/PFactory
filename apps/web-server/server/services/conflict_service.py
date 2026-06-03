"""
Conflict Detection Service

Bridges the web-server with the backend's semantic conflict detection system.
Provides async methods for detecting and resolving conflicts during merge operations.
"""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from ..config import get_settings

logger = logging.getLogger(__name__)


class ConflictService:
    """
    Service to bridge web-server with backend conflict detection.

    Uses the backend's MergeOrchestrator and ConflictDetector to provide
    semantic conflict analysis beyond simple git merge-tree checks.
    """

    def __init__(self, project_path: Path):
        """
        Initialize the conflict service.

        Args:
            project_path: Path to the project directory
        """
        self.project_path = Path(project_path).resolve()
        self._backend_imported = False
        self._orchestrator = None

    def _ensure_backend_path(self) -> None:
        """Add backend to Python path for imports and load backend .env."""
        if self._backend_imported:
            return

        settings = get_settings()
        backend_path = Path(settings.BACKEND_PATH).resolve()

        if not backend_path.exists():
            logger.error(f"Backend path does not exist: {backend_path}")
            raise ValueError(f"Backend path not found: {backend_path}")

        # Add backend to PYTHONPATH if not already there
        backend_str = str(backend_path)
        if backend_str not in sys.path:
            sys.path.insert(0, backend_str)
            logger.debug(f"Added backend to Python path: {backend_str}")

        # Load backend .env for OAuth token and other settings
        import os
        backend_env = backend_path / ".env"
        if backend_env.exists():
            logger.debug(f"Loading backend .env from {backend_env}")
            with open(backend_env) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        # Only set if not already in environment
                        if key not in os.environ:
                            os.environ[key] = value
                            logger.debug(f"Set env var: {key}")

        self._backend_imported = True

    def _get_orchestrator(self):
        """Get or create the MergeOrchestrator instance."""
        if self._orchestrator is not None:
            return self._orchestrator

        self._ensure_backend_path()

        try:
            from merge.orchestrator import MergeOrchestrator

            self._orchestrator = MergeOrchestrator(
                project_dir=self.project_path,
                enable_ai=True,
                dry_run=True,  # Preview mode - don't write files
            )
            logger.info(f"Created MergeOrchestrator for {self.project_path}")
            return self._orchestrator

        except ImportError as e:
            logger.error(f"Failed to import merge modules: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to create MergeOrchestrator: {e}")
            raise

    async def detect_conflicts(
        self,
        task_id: str,
        worktree_path: Path,
        base_branch: str = "develop",
    ) -> dict[str, Any]:
        """
        Run semantic conflict detection for a task.

        Args:
            task_id: The task/spec ID
            worktree_path: Path to the task's worktree
            base_branch: The target branch for merge

        Returns:
            Dictionary with conflict analysis results
        """
        logger.info(f"Detecting conflicts for task {task_id}")

        try:
            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._detect_conflicts_sync,
                task_id,
                worktree_path,
                base_branch,
            )
            return result

        except Exception as e:
            logger.error(f"Conflict detection failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "conflicts": [],
                "stats": {
                    "totalFiles": 0,
                    "conflictFiles": 0,
                    "totalConflicts": 0,
                    "autoMergeable": 0,
                    "aiResolved": 0,
                    "humanRequired": 0,
                },
            }

    def _detect_conflicts_sync(
        self,
        task_id: str,
        worktree_path: Path,
        base_branch: str,
    ) -> dict[str, Any]:
        """Synchronous conflict detection (runs in executor)."""
        try:
            orchestrator = self._get_orchestrator()

            # Refresh evolution data from git for this task
            orchestrator.evolution_tracker.refresh_from_git(
                task_id, worktree_path, target_branch=base_branch
            )

            # Get the preview of what would happen
            preview = orchestrator.preview_merge(task_ids=[task_id])

            # Convert conflict regions to frontend format
            conflicts = []
            for conflict_data in preview.get("conflicts", []):
                conflicts.append({
                    "file": conflict_data.get("file", ""),
                    "location": conflict_data.get("location", ""),
                    "tasks": conflict_data.get("tasks", []),
                    "severity": conflict_data.get("severity", "medium"),
                    "canAutoMerge": conflict_data.get("can_auto_merge", False),
                    "strategy": conflict_data.get("strategy"),
                    "reason": conflict_data.get("reason", ""),
                    "type": "semantic",  # Mark as semantic conflict
                })

            # Build statistics
            summary = preview.get("summary", {})
            auto_mergeable = summary.get("auto_mergeable", 0)
            total_conflicts = summary.get("total_conflicts", 0)

            stats = {
                "totalFiles": summary.get("total_files", 0),
                "conflictFiles": summary.get("conflict_files", 0),
                "totalConflicts": total_conflicts,
                "autoMergeable": auto_mergeable,
                "aiResolved": 0,  # Will be updated after resolution
                "humanRequired": total_conflicts - auto_mergeable,
            }

            return {
                "success": True,
                "conflicts": conflicts,
                "stats": stats,
                "filesWithConflicts": preview.get("files_with_potential_conflicts", []),
                "filesToMerge": preview.get("files_to_merge", []),
            }

        except Exception as e:
            logger.warning(f"Semantic conflict detection failed (may be expected for simple merges): {e}")
            # Return empty result - semantic detection is optional
            return {
                "success": True,
                "conflicts": [],
                "stats": {
                    "totalFiles": 0,
                    "conflictFiles": 0,
                    "totalConflicts": 0,
                    "autoMergeable": 0,
                    "aiResolved": 0,
                    "humanRequired": 0,
                },
                "filesWithConflicts": [],
                "filesToMerge": [],
            }

    async def resolve_conflicts(
        self,
        task_id: str,
        worktree_path: Path,
        use_ai: bool = True,
        base_branch: str = "develop",
    ) -> dict[str, Any]:
        """
        Attempt to resolve conflicts using auto-merge or AI.

        Args:
            task_id: The task/spec ID
            worktree_path: Path to the task's worktree
            use_ai: Whether to use AI for ambiguous conflicts
            base_branch: The target branch for merge

        Returns:
            Dictionary with resolution results
        """
        logger.info(f"Resolving conflicts for task {task_id} (use_ai={use_ai})")

        try:
            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._resolve_conflicts_sync,
                task_id,
                worktree_path,
                use_ai,
                base_branch,
            )
            return result

        except Exception as e:
            logger.error(f"Conflict resolution failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "resolved": [],
                "remaining": [],
            }

    def _resolve_conflicts_sync(
        self,
        task_id: str,
        worktree_path: Path,
        use_ai: bool,
        base_branch: str,
    ) -> dict[str, Any]:
        """Synchronous conflict resolution (runs in executor)."""
        self._ensure_backend_path()

        try:
            from merge.orchestrator import MergeOrchestrator

            # Create a new orchestrator with AI enabled based on parameter
            orchestrator = MergeOrchestrator(
                project_dir=self.project_path,
                enable_ai=use_ai,
                dry_run=False,  # Actually resolve conflicts
            )

            # Refresh evolution data first
            orchestrator.evolution_tracker.refresh_from_git(
                task_id, worktree_path, target_branch=base_branch
            )

            # Run the merge
            report = orchestrator.merge_task(
                task_id=task_id,
                worktree_path=worktree_path,
                target_branch=base_branch,
            )

            # Convert to frontend format
            resolved = []
            remaining = []

            for file_path, result in report.file_results.items():
                for conflict in getattr(result, 'conflicts_resolved', []):
                    resolved.append({
                        "file": getattr(conflict, 'file_path', file_path),
                        "location": getattr(conflict, 'location', ''),
                        "severity": getattr(conflict.severity, 'value', 'medium') if hasattr(conflict, 'severity') else 'medium',
                        "strategy": conflict.merge_strategy.value if hasattr(conflict, 'merge_strategy') and conflict.merge_strategy else None,
                        "reason": getattr(conflict, 'reason', ''),
                    })

                for conflict in getattr(result, 'conflicts_remaining', []):
                    remaining.append({
                        "file": getattr(conflict, 'file_path', file_path),
                        "location": getattr(conflict, 'location', ''),
                        "severity": getattr(conflict.severity, 'value', 'medium') if hasattr(conflict, 'severity') else 'medium',
                        "reason": getattr(conflict, 'reason', ''),
                    })

            return {
                "success": report.success,
                "resolved": resolved,
                "remaining": remaining,
                "stats": {
                    "filesProcessed": report.stats.files_processed,
                    "filesAutoMerged": report.stats.files_auto_merged,
                    "filesAIMerged": report.stats.files_ai_merged,
                    "filesNeedReview": report.stats.files_need_review,
                    "filesFailed": report.stats.files_failed,
                    "conflictsResolved": report.stats.conflicts_auto_resolved + report.stats.conflicts_ai_resolved,
                    "aiCallsMade": report.stats.ai_calls_made,
                    "tokensUsed": report.stats.estimated_tokens_used,
                },
                "error": report.error,
            }

        except Exception as e:
            logger.error(f"Resolution sync failed: {e}")
            return {
                "success": False,
                "resolved": [],
                "remaining": [],
                "stats": {
                    "filesProcessed": 0,
                    "filesAutoMerged": 0,
                    "filesAIMerged": 0,
                    "filesNeedReview": 0,
                    "filesFailed": 0,
                    "conflictsResolved": 0,
                    "aiCallsMade": 0,
                    "tokensUsed": 0,
                },
                "error": str(e),
            }

    async def ai_merge_three_way(
        self,
        file_path: str,
        base_content: str,
        local_content: str,
        task_content: str,
        local_label: str = "local changes",
        task_label: str = "task changes",
    ) -> dict[str, Any]:
        """
        Use AI to perform a three-way merge of file contents.

        Args:
            file_path: Path to the file being merged
            base_content: Original content (from base branch)
            local_content: User's uncommitted local changes
            task_content: Changes from the task branch
            local_label: Human-readable label for local changes
            task_label: Human-readable label for task changes

        Returns:
            Dictionary with merged content and success status
        """
        logger.info(f"AI three-way merge for {file_path}")

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._ai_merge_three_way_sync,
                file_path,
                base_content,
                local_content,
                task_content,
                local_label,
                task_label,
            )
            return result

        except Exception as e:
            logger.error(f"AI merge failed: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    def _ai_merge_three_way_sync(
        self,
        file_path: str,
        base_content: str,
        local_content: str,
        task_content: str,
        local_label: str,
        task_label: str,
    ) -> dict[str, Any]:
        """Synchronous AI merge (runs in executor)."""
        self._ensure_backend_path()

        try:
            # Use the backend's simple client with OAuth authentication
            from core.simple_client import create_simple_client
            import asyncio

            prompt = f"""You are a code merge expert. Merge the following three versions of a file.

FILE: {file_path}

=== BASE VERSION (original) ===
{base_content or "(empty - new file)"}

=== VERSION A: {local_label} ===
{local_content or "(empty - deleted or not present)"}

=== VERSION B: {task_label} ===
{task_content or "(empty - deleted or not present)"}

TASK: Intelligently merge both sets of changes into the base.
- Include changes from BOTH versions where they don't conflict
- If changes conflict, prefer combining both if possible
- If truly incompatible, prioritize {task_label} but add a comment noting the conflict
- Preserve all functionality from both versions
- Output ONLY the merged file content, no explanations or markdown code blocks"""

            # Run the async SDK call in a new event loop
            # IMPORTANT: Client must be created INSIDE asyncio.run() context
            # to avoid event loop mismatch errors
            async def run_merge():
                # Create client inside async context (same event loop)
                client = create_simple_client(
                    agent_type="merge_resolver",
                    model="claude-sonnet-4-20250514",
                    max_turns=1,
                )
                response_text = ""
                async with client:
                    await client.query(prompt)
                    async for msg in client.receive_response():
                        msg_type = type(msg).__name__
                        if msg_type == "AssistantMessage" and hasattr(msg, "content"):
                            for block in msg.content:
                                if hasattr(block, "text"):
                                    response_text += block.text
                return response_text

            # Run in new event loop (we're in a thread from run_in_executor)
            merged_content = asyncio.run(run_merge())

            if merged_content:
                # Strip any markdown code blocks if present
                if merged_content.startswith("```"):
                    lines = merged_content.split("\n")
                    # Remove first line (```python or ```) and last line (```)
                    if lines[-1].strip() == "```":
                        lines = lines[1:-1]
                    elif lines[0].startswith("```"):
                        lines = lines[1:]
                    merged_content = "\n".join(lines)

                return {
                    "success": True,
                    "content": merged_content,
                }
            else:
                return {
                    "success": False,
                    "error": "No response from AI",
                }

        except Exception as e:
            logger.error(f"AI merge sync failed: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    async def resolve_conflict_markers(
        self,
        file_path: str,
        content: str,
    ) -> dict[str, Any]:
        """
        Use AI to resolve a file that has git merge conflict markers.

        Parses conflict blocks (<<<<<<< HEAD ... ======= ... >>>>>>>) and
        intelligently merges the conflicting sections.

        Args:
            file_path: Path to the file being resolved
            content: File content with conflict markers

        Returns:
            Dictionary with resolved content and success status
        """
        logger.info(f"Resolving conflict markers in {file_path}")

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._resolve_conflict_markers_sync,
                file_path,
                content,
            )
            return result

        except Exception as e:
            logger.error(f"Conflict marker resolution failed: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    def _resolve_conflict_markers_sync(
        self,
        file_path: str,
        content: str,
    ) -> dict[str, Any]:
        """Synchronous conflict marker resolution (runs in executor)."""
        self._ensure_backend_path()

        try:
            # Use the backend's simple client with OAuth authentication
            from core.simple_client import create_simple_client
            import asyncio

            # Count conflict blocks for context
            conflict_count = content.count("<<<<<<< ")

            prompt = f"""You are a code merge expert. Resolve ALL the git merge conflicts in this file.

FILE: {file_path}
CONFLICT COUNT: {conflict_count} conflict(s) detected

The file contains git merge conflict markers in this format:
<<<<<<< HEAD (or <<<<<<< branch-name)
... code from one side ...
=======
... code from the other side ...
>>>>>>> branch-name

CONTENT WITH CONFLICTS:
```
{content}
```

INSTRUCTIONS:
1. Resolve EVERY conflict block by intelligently merging both sides
2. Where changes don't conflict semantically, include BOTH changes
3. Where changes truly conflict, combine them if possible or choose the best version
4. REMOVE ALL conflict markers (<<<<<<, =======, >>>>>>>)
5. Ensure the resulting code is syntactically valid
6. Preserve ALL functionality from both sides when possible

OUTPUT: Return ONLY the fully resolved file content with NO conflict markers remaining.
Do NOT include markdown code blocks, explanations, or any other text.
Return ONLY the raw file content."""

            # Run the async SDK call in a new event loop
            # IMPORTANT: Client must be created INSIDE asyncio.run() context
            # to avoid event loop mismatch errors
            async def run_resolution():
                # Create client inside async context (same event loop)
                client = create_simple_client(
                    agent_type="merge_resolver",
                    model="claude-sonnet-4-20250514",
                    max_turns=1,
                )
                response_text = ""
                async with client:
                    await client.query(prompt)
                    async for msg in client.receive_response():
                        msg_type = type(msg).__name__
                        if msg_type == "AssistantMessage" and hasattr(msg, "content"):
                            for block in msg.content:
                                if hasattr(block, "text"):
                                    response_text += block.text
                return response_text

            # Run in new event loop (we're in a thread from run_in_executor)
            resolved_content = asyncio.run(run_resolution())

            if resolved_content:
                # Strip any markdown code blocks if AI included them despite instructions
                if resolved_content.startswith("```"):
                    lines = resolved_content.split("\n")
                    # Remove first line (```python or ```) and last line (```)
                    if lines[-1].strip() == "```":
                        lines = lines[1:-1]
                    elif lines[0].startswith("```"):
                        lines = lines[1:]
                    resolved_content = "\n".join(lines)

                # Verify conflict markers are removed
                markers_remaining = (
                    "<<<<<<< " in resolved_content or
                    "=======" in resolved_content or
                    ">>>>>>> " in resolved_content
                )

                if markers_remaining:
                    logger.warning(f"AI resolution still has conflict markers in {file_path}")
                    # Return success but let caller handle the markers
                    return {
                        "success": True,
                        "content": resolved_content,
                        "warning": "Some conflict markers may remain",
                    }

                return {
                    "success": True,
                    "content": resolved_content,
                }
            else:
                return {
                    "success": False,
                    "error": "No response from AI",
                }

        except Exception as e:
            logger.error(f"Conflict marker resolution sync failed: {e}")
            return {
                "success": False,
                "error": str(e),
            }


# Service instance cache
_conflict_services: dict[str, ConflictService] = {}


def get_conflict_service(project_path: Path) -> ConflictService:
    """
    Get or create a ConflictService for a project.

    Args:
        project_path: Path to the project directory

    Returns:
        ConflictService instance
    """
    path_str = str(project_path.resolve())

    if path_str not in _conflict_services:
        _conflict_services[path_str] = ConflictService(project_path)

    return _conflict_services[path_str]


def clear_conflict_service_cache() -> None:
    """Clear the conflict service cache."""
    _conflict_services.clear()
