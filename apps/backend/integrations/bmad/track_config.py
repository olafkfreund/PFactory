#!/usr/bin/env python3
"""
Track Configuration for BMad Method Integration
================================================

Defines three planning tracks (Quick Flow, Standard, Enterprise) with their
phase pipelines and characteristics.
"""

from dataclasses import dataclass

# Handle both relative and absolute imports
try:
    from .complexity_detector import Track
except ImportError:
    from complexity_detector import Track


@dataclass
class TrackConfig:
    """Configuration for a planning track."""
    track: Track
    display_name: str
    description: str
    phase_pipeline: list[str]
    estimated_time: str
    use_cases: list[str]
    includes: list[str]


# Track Configurations

QUICK_FLOW_CONFIG = TrackConfig(
    track=Track.QUICK_FLOW,
    display_name="Quick Flow",
    description="Fast iteration for simple tasks (bugs, small features)",
    phase_pipeline=[
        "discovery",
        "tech_spec",
        "validate"
    ],
    estimated_time="5-15 minutes",
    use_cases=[
        "Bug fixes",
        "Small UI changes",
        "Simple feature additions",
        "Configuration updates"
    ],
    includes=[
        "Quick discovery of requirements",
        "Minimal tech spec",
        "Rapid validation"
    ]
)

STANDARD_CONFIG = TrackConfig(
    track=Track.STANDARD,
    display_name="Standard",
    description="Comprehensive planning for features",
    phase_pipeline=[
        "discovery",
        "requirements",
        "architecture",  # Only for Level 3+
        "context",
        "spec_writing",
        "planning",
        "validation"
    ],
    estimated_time="Hours to days",
    use_cases=[
        "New features",
        "Multi-component changes",
        "Complex systems (with architecture)",
        "Service integrations"
    ],
    includes=[
        "Requirements gathering",
        "Architecture design (Level 3+)",
        "Comprehensive spec",
        "Story-based implementation plan",
        "Validation checks"
    ]
)

ENTERPRISE_CONFIG = TrackConfig(
    track=Track.ENTERPRISE,
    display_name="Enterprise",
    description="Full planning with compliance and security",
    phase_pipeline=[
        "discovery",
        "requirements",
        "architecture",
        "security_review",
        "context",
        "spec_writing",
        "planning",
        "devops_planning",
        "validation"
    ],
    estimated_time="Days to weeks",
    use_cases=[
        "Enterprise-scale systems",
        "Multi-product platforms",
        "Compliance-heavy projects",
        "Critical infrastructure"
    ],
    includes=[
        "Full requirements analysis",
        "Architecture design with security review",
        "Security & compliance checks",
        "DevOps planning",
        "Comprehensive documentation",
        "Thorough validation"
    ]
)


# Track Registry
TRACK_REGISTRY = {
    Track.QUICK_FLOW: QUICK_FLOW_CONFIG,
    Track.STANDARD: STANDARD_CONFIG,
    Track.ENTERPRISE: ENTERPRISE_CONFIG
}


def get_track_config(track: Track) -> TrackConfig:
    """
    Get configuration for a track.

    Args:
        track: Track enum value

    Returns:
        TrackConfig for the track

    Raises:
        ValueError: If track is not recognized
    """
    if track not in TRACK_REGISTRY:
        raise ValueError(f"Unknown track: {track}")
    return TRACK_REGISTRY[track]


def get_phases_for_track(track: Track, complexity_level: int | None) -> list[str]:
    """
    Get phase pipeline for a track, adjusted for complexity level.

    Args:
        track: Track enum value
        complexity_level: Complexity level (0-4), or None if BMad detection didn't run

    Returns:
        List of phase names

    Note:
        For Standard track, architecture phase is only included for Level 3+
        If complexity_level is None, architecture is excluded (defaults to Level 0-2 behavior)
    """
    config = get_track_config(track)
    phases = config.phase_pipeline.copy()

    # Special case: Standard track without architecture for Level 0-2
    # Also remove if complexity_level is None (BMad detection didn't run)
    if track == Track.STANDARD and (complexity_level is None or complexity_level < 3):
        if "architecture" in phases:
            phases.remove("architecture")

    return phases


def describe_track(track: Track) -> str:
    """
    Get a human-readable description of a track.

    Args:
        track: Track enum value

    Returns:
        Formatted description string
    """
    config = get_track_config(track)

    desc = f"**{config.display_name}** ({config.estimated_time})\n"
    desc += f"{config.description}\n\n"
    desc += f"**Phases:** {len(config.phase_pipeline)}\n"
    desc += "**Use Cases:**\n"
    for use_case in config.use_cases:
        desc += f"  - {use_case}\n"
    desc += "\n**Includes:**\n"
    for item in config.includes:
        desc += f"  - {item}\n"

    return desc


def compare_tracks() -> str:
    """
    Generate a comparison table of all tracks.

    Returns:
        Formatted comparison string
    """
    comparison = "| Feature | Quick Flow | Standard | Enterprise |\n"
    comparison += "|---------|-----------|-----------|------------|\n"

    # Time
    comparison += f"| **Time** | {QUICK_FLOW_CONFIG.estimated_time} | {STANDARD_CONFIG.estimated_time} | {ENTERPRISE_CONFIG.estimated_time} |\n"

    # Phases
    comparison += f"| **Phases** | {len(QUICK_FLOW_CONFIG.phase_pipeline)} | {len(STANDARD_CONFIG.phase_pipeline)} (6-7) | {len(ENTERPRISE_CONFIG.phase_pipeline)} |\n"

    # Architecture
    comparison += "| **Architecture** | No | Yes (Level 3+) | Yes |\n"

    # Security Review
    comparison += "| **Security Review** | No | No | Yes |\n"

    # DevOps Planning
    comparison += "| **DevOps Planning** | No | No | Yes |\n"

    # Best For
    comparison += "| **Best For** | Bugs, small changes | Features, systems | Enterprise, compliance |\n"

    return comparison


if __name__ == "__main__":
    # Test the track configuration
    print("=== BMad Method Track Configuration ===\n")

    for track in [Track.QUICK_FLOW, Track.STANDARD, Track.ENTERPRISE]:
        print(describe_track(track))
        print()

    print("\n=== Track Comparison ===\n")
    print(compare_tracks())
