"""
Context Sharding for Session Segmentation
==========================================

Extracts minimal context for per-story sessions to achieve token reduction.
Loads only the architecture sections and dependencies referenced by a story.
"""

import json
import re
from pathlib import Path


def extract_architecture_sections(
    architecture_file: Path, section_refs: list[str]
) -> str:
    """Extract specific sections from architecture.md.

    Args:
        architecture_file: Path to architecture.md
        section_refs: List of section references (e.g., ["architecture.md#3.1-authentication"])

    Returns:
        Extracted sections as markdown string
    """
    if not architecture_file.exists():
        return ""

    content = architecture_file.read_text()

    # If no specific refs, return empty (don't load full doc)
    if not section_refs:
        return ""

    extracted = []
    lines = content.split("\n")

    for ref in section_refs:
        # Parse reference: "architecture.md#3.1-authentication" or "#3.1"
        if "#" not in ref:
            continue

        anchor = ref.split("#")[1]

        # Find the section with this anchor
        section_lines = _find_section_by_anchor(lines, anchor)
        if section_lines:
            extracted.append("\n".join(section_lines))

    if extracted:
        header = "# Architecture Context (Relevant Sections)\n\n"
        return header + "\n\n---\n\n".join(extracted)

    return ""


def _find_section_by_anchor(lines: list[str], anchor: str) -> list[str]:
    """Find a markdown section by its anchor/heading.

    Args:
        lines: Lines of the markdown document
        anchor: Anchor string (e.g., "3.1-authentication")

    Returns:
        Lines of the section (including header)
    """
    # Try to match heading levels (## 3.1 Authentication, ### 3.1.1, etc.)
    # Normalize anchor for matching
    anchor_normalized = anchor.lower().replace("-", " ").replace("_", " ")

    section_lines = []
    in_section = False
    section_level = 0

    for i, line in enumerate(lines):
        # Check if this is a heading
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading_match:
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()
            heading_normalized = heading_text.lower()

            # Check if this matches our anchor
            if anchor_normalized in heading_normalized or heading_normalized in anchor_normalized:
                in_section = True
                section_level = level
                section_lines = [line]
            elif in_section and level <= section_level:
                # We've reached the next section at same or higher level
                break
        elif in_section:
            section_lines.append(line)

    return section_lines


def load_story_context(
    spec_dir: Path, story: dict, architecture_file: Path | None = None
) -> dict[str, str]:
    """Load minimal context for a story (for session segmentation).

    Args:
        spec_dir: Path to spec directory
        story: Story dictionary from test_plan.json
        architecture_file: Path to architecture.md (optional)

    Returns:
        Dictionary with minimal context for the story
    """
    context = {}

    # 1. Story-specific info
    context["story_id"] = story.get("story_id", story.get("id", "unknown"))
    context["title"] = story.get("title", story.get("description", ""))
    context["user_story"] = story.get("user_story", "")

    # 2. Acceptance criteria (critical for validation)
    context["acceptance_criteria"] = story.get("acceptance_criteria", [])

    # 3. Technical context
    tech_context = story.get("technical_context", {})
    context["stack"] = tech_context.get("stack", [])
    context["dependencies"] = tech_context.get("dependencies", [])
    context["technical_notes"] = tech_context.get("technical_notes", "")

    # 4. Load relevant architecture sections (if available)
    arch_refs = tech_context.get("architecture_references", [])
    if arch_refs and architecture_file:
        context["architecture_context"] = extract_architecture_sections(
            architecture_file, arch_refs
        )
    else:
        context["architecture_context"] = ""

    return context


def format_story_context(context: dict[str, str]) -> str:
    """Format story context as a prompt-ready string.

    Args:
        context: Story context dictionary from load_story_context()

    Returns:
        Formatted context string
    """
    lines = []

    lines.append(f"# Story Context: {context['story_id']}")
    lines.append("")

    lines.append("## Story")
    lines.append(f"**Title**: {context['title']}")
    if context.get("user_story"):
        lines.append(f"**User Story**: {context['user_story']}")
    lines.append("")

    # Acceptance Criteria
    if context.get("acceptance_criteria"):
        lines.append("## Acceptance Criteria")
        for i, ac in enumerate(context["acceptance_criteria"], 1):
            lines.append(f"{i}. {ac}")
        lines.append("")

    # Technical Context
    if context.get("stack"):
        lines.append("## Technology Stack")
        lines.append(", ".join(context["stack"]))
        lines.append("")

    if context.get("dependencies"):
        lines.append("## Dependencies")
        for dep in context["dependencies"]:
            lines.append(f"- {dep}")
        lines.append("")

    if context.get("technical_notes"):
        lines.append("## Technical Notes")
        lines.append(context["technical_notes"])
        lines.append("")

    # Architecture Context (sharded sections only)
    if context.get("architecture_context"):
        lines.append("---")
        lines.append("")
        lines.append(context["architecture_context"])
        lines.append("")

    return "\n".join(lines)


def get_session_id(spec_name: str, story_id: str) -> str:
    """Generate a unique session ID for a story.

    Args:
        spec_name: Spec identifier (e.g., "001-auth-feature")
        story_id: Story identifier (e.g., "US-001")

    Returns:
        Session ID string
    """
    return f"{spec_name}-{story_id}"


def save_session_metadata(
    spec_dir: Path, story_id: str, session_id: str, context_size: int
) -> None:
    """Save session metadata for tracking.

    Args:
        spec_dir: Path to spec directory
        story_id: Story identifier
        session_id: Session ID
        context_size: Size of context in characters
    """
    # Ensure spec_dir exists
    spec_dir.mkdir(parents=True, exist_ok=True)

    metadata_file = spec_dir / "session_metadata.json"

    # Load existing metadata
    metadata = {}
    if metadata_file.exists():
        try:
            with open(metadata_file) as f:
                metadata = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # Add this session
    if "sessions" not in metadata:
        metadata["sessions"] = {}

    metadata["sessions"][story_id] = {
        "session_id": session_id,
        "context_size": context_size,
    }

    # Save back
    with open(metadata_file, "w") as f:
        json.dump(metadata, f, indent=2)


def get_context_reduction_stats(spec_dir: Path, full_context_size: int) -> dict:
    """Calculate context reduction statistics.

    Args:
        spec_dir: Path to spec directory
        full_context_size: Size of full context (baseline)

    Returns:
        Dictionary with statistics
    """
    metadata_file = spec_dir / "session_metadata.json"

    if not metadata_file.exists():
        return {
            "full_context_size": full_context_size,
            "avg_sharded_size": 0,
            "reduction_percentage": 0,
            "sessions_count": 0,
        }

    try:
        with open(metadata_file) as f:
            metadata = json.load(f)

        sessions = metadata.get("sessions", {})
        if not sessions:
            return {
                "full_context_size": full_context_size,
                "avg_sharded_size": 0,
                "reduction_percentage": 0,
                "sessions_count": 0,
            }

        sizes = [s["context_size"] for s in sessions.values()]
        avg_size = sum(sizes) / len(sizes)
        reduction = ((full_context_size - avg_size) / full_context_size) * 100

        return {
            "full_context_size": full_context_size,
            "avg_sharded_size": int(avg_size),
            "reduction_percentage": round(reduction, 1),
            "sessions_count": len(sessions),
        }

    except (json.JSONDecodeError, OSError):
        return {
            "full_context_size": full_context_size,
            "avg_sharded_size": 0,
            "reduction_percentage": 0,
            "sessions_count": 0,
        }


if __name__ == "__main__":
    # Test context sharding
    print("=== Context Sharding Test ===\n")

    # Test story context loading
    test_story = {
        "story_id": "US-001",
        "title": "User login with email/password",
        "user_story": "As a user, I want to log in with email/password so that I can access my account",
        "acceptance_criteria": [
            "AC1: Login form accepts email and password",
            "AC2: Valid credentials redirect to dashboard",
            "AC3: Invalid credentials show error message",
        ],
        "technical_context": {
            "architecture_references": ["architecture.md#3.1-authentication"],
            "stack": ["FastAPI", "JWT", "bcrypt"],
            "dependencies": ["US-000"],
            "technical_notes": "Follow architecture decision ADR-001 (JWT for stateless auth)",
        },
    }

    context = load_story_context(Path("/tmp"), test_story)
    formatted = format_story_context(context)

    print("Story Context:")
    print("-" * 60)
    print(formatted)
    print()

    print(f"Context Size: {len(formatted)} characters")
    print(f"Session ID: {get_session_id('001-auth', 'US-001')}")
