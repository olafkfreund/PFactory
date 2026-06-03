"""
Session Spawner for Per-Story Execution
========================================

Spawns fresh agent sessions per story with minimal context for token optimization.
Implements BMad Method's session segmentation pattern.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

# Handle both relative and absolute imports
try:
    from .context_shard import (
        format_story_context,
        get_context_reduction_stats,
        get_session_id,
        load_story_context,
        save_session_metadata,
    )
    from .session_config import is_session_segmentation_enabled_for_spec
except ImportError:
    from context_shard import (
        format_story_context,
        get_context_reduction_stats,
        get_session_id,
        load_story_context,
        save_session_metadata,
    )
    from session_config import is_session_segmentation_enabled_for_spec

logger = logging.getLogger(__name__)


class SessionSpawner:
    """Manages per-story session spawning with context sharding."""

    def __init__(self, project_dir: Path, spec_dir: Path):
        """Initialize session spawner.

        Args:
            project_dir: Project root directory
            spec_dir: Spec directory
        """
        self.project_dir = project_dir
        self.spec_dir = spec_dir
        self.architecture_file = spec_dir / "architecture.md"
        self.sessions = {}

    def should_use_segmentation(self) -> bool:
        """Check if session segmentation should be used for this spec.

        Returns:
            True if session segmentation is enabled
        """
        return is_session_segmentation_enabled_for_spec(self.spec_dir)

    def prepare_story_context(self, story: dict) -> dict[str, str]:
        """Prepare minimal context for a story.

        Args:
            story: Story dictionary from test_plan.json

        Returns:
            Context dictionary for the story
        """
        arch_file = self.architecture_file if self.architecture_file.exists() else None
        return load_story_context(self.spec_dir, story, arch_file)

    def format_context_for_agent(self, context: dict[str, str]) -> str:
        """Format story context for agent prompt.

        Args:
            context: Context dictionary from prepare_story_context()

        Returns:
            Formatted context string
        """
        return format_story_context(context)

    def get_story_session_id(self, story_id: str) -> str:
        """Generate session ID for a story.

        Args:
            story_id: Story identifier

        Returns:
            Unique session ID
        """
        spec_name = self.spec_dir.name
        return get_session_id(spec_name, story_id)

    def track_session(self, story_id: str, context_size: int) -> None:
        """Track session metadata for statistics.

        Args:
            story_id: Story identifier
            context_size: Size of context in characters
        """
        session_id = self.get_story_session_id(story_id)
        save_session_metadata(self.spec_dir, story_id, session_id, context_size)
        self.sessions[story_id] = {
            "session_id": session_id,
            "context_size": context_size,
            "timestamp": datetime.now().isoformat(),
        }

    def get_reduction_stats(self, full_context_size: int) -> dict:
        """Get context reduction statistics.

        Args:
            full_context_size: Size of full context (baseline)

        Returns:
            Dictionary with statistics
        """
        return get_context_reduction_stats(self.spec_dir, full_context_size)

    async def spawn_story_session(
        self,
        story: dict,
        run_agent_fn,
        full_context: str | None = None,
    ) -> tuple[bool, str]:
        """Spawn a fresh agent session for a story.

        Args:
            story: Story dictionary
            run_agent_fn: Async function to run the agent with prompt
            full_context: Optional full context for comparison

        Returns:
            Tuple of (success, output)
        """
        # Prepare minimal context for this story
        story_context = self.prepare_story_context(story)
        formatted_context = self.format_context_for_agent(story_context)

        # Track session
        story_id = story.get("story_id", story.get("id", "unknown"))
        self.track_session(story_id, len(formatted_context))

        # Log context reduction
        if full_context:
            reduction = (
                (len(full_context) - len(formatted_context)) / len(full_context)
            ) * 100
            logger.info(
                f"Context reduction for {story_id}: {reduction:.1f}% "
                f"({len(full_context)} → {len(formatted_context)} chars)"
            )

        # Spawn agent session with minimal context
        try:
            success, output = await run_agent_fn(formatted_context)
            return success, output
        except Exception as e:
            logger.error(f"Session spawn failed for {story_id}: {e}")
            return False, str(e)


def create_session_spawner(project_dir: Path, spec_dir: Path) -> SessionSpawner:
    """Create a session spawner instance.

    Args:
        project_dir: Project root directory
        spec_dir: Spec directory

    Returns:
        SessionSpawner instance
    """
    return SessionSpawner(project_dir, spec_dir)


def should_use_session_segmentation(spec_dir: Path) -> bool:
    """Check if session segmentation should be used.

    Args:
        spec_dir: Spec directory

    Returns:
        True if session segmentation is enabled
    """
    return is_session_segmentation_enabled_for_spec(spec_dir)


def get_segmentation_info(spec_dir: Path) -> dict:
    """Get information about session segmentation for a spec.

    Args:
        spec_dir: Spec directory

    Returns:
        Dictionary with segmentation info
    """
    enabled = is_session_segmentation_enabled_for_spec(spec_dir)

    # Load session metadata if available
    metadata_file = spec_dir / "session_metadata.json"
    sessions_count = 0
    if metadata_file.exists():
        try:
            with open(metadata_file) as f:
                metadata = json.load(f)
                sessions_count = len(metadata.get("sessions", {}))
        except (json.JSONDecodeError, OSError):
            pass

    return {
        "enabled": enabled,
        "sessions_count": sessions_count,
        "metadata_file": str(metadata_file) if metadata_file.exists() else None,
    }


if __name__ == "__main__":
    # Test session spawner
    print("=== Session Spawner Test ===\n")

    test_spec_dir = Path("/tmp/test-spec")
    test_spec_dir.mkdir(exist_ok=True)

    spawner = SessionSpawner(Path("/tmp"), test_spec_dir)

    # Test story
    test_story = {
        "story_id": "US-001",
        "title": "User authentication",
        "user_story": "As a user, I want to log in",
        "acceptance_criteria": ["AC1: Login works", "AC2: Logout works"],
        "technical_context": {
            "stack": ["FastAPI", "JWT"],
            "dependencies": [],
            "technical_notes": "Use bcrypt for passwords",
        },
    }

    # Prepare context
    context = spawner.prepare_story_context(test_story)
    formatted = spawner.format_context_for_agent(context)

    print(f"Session ID: {spawner.get_story_session_id('US-001')}")
    print(f"Context Size: {len(formatted)} characters")
    print(f"Should use segmentation: {spawner.should_use_segmentation()}")
    print()

    # Track session
    spawner.track_session("US-001", len(formatted))

    # Get stats (with example full context)
    full_context_size = 5000  # Example baseline
    stats = spawner.get_reduction_stats(full_context_size)

    print("Context Reduction Stats:")
    print(f"  Full context: {stats['full_context_size']} chars")
    print(f"  Avg sharded: {stats['avg_sharded_size']} chars")
    print(f"  Reduction: {stats['reduction_percentage']}%")
    print(f"  Sessions: {stats['sessions_count']}")

    # Cleanup
    import shutil

    shutil.rmtree(test_spec_dir)
