#!/usr/bin/env python3
"""
Enumerations for Implementation Plan
=====================================

Defines all enum types used in implementation plans: workflow types,
phase types, subtask statuses, and verification types.
"""

from enum import Enum


class WorkflowType(str, Enum):
    """Types of workflows with different phase structures."""

    FEATURE = "feature"  # Multi-service feature (phases = services)
    REFACTOR = "refactor"  # Stage-based (add new, migrate, remove old)
    INVESTIGATION = "investigation"  # Bug hunting (investigate, hypothesize, fix)
    MIGRATION = "migration"  # Data migration (prepare, test, execute, cleanup)
    SIMPLE = "simple"  # Single-service, minimal overhead
    DEVELOPMENT = "development"  # General development work
    ENHANCEMENT = "enhancement"  # Improving existing features


class PhaseType(str, Enum):
    """Types of phases within a workflow."""

    SETUP = "setup"  # Project scaffolding, environment setup
    IMPLEMENTATION = "implementation"  # Writing code
    INVESTIGATION = "investigation"  # Research, debugging, analysis
    INTEGRATION = "integration"  # Wiring services together
    CLEANUP = "cleanup"  # Removing old code, polish


class SubtaskStatus(str, Enum):
    """Status of a subtask."""

    PENDING = "pending"  # Not started
    IN_PROGRESS = "in_progress"  # Currently being worked on
    COMPLETED = "completed"  # Completed successfully (matches JSON format)
    BLOCKED = "blocked"  # Can't start (dependency not met or undefined)
    FAILED = "failed"  # Attempted but failed


class VerificationType(str, Enum):
    """How to verify a subtask is complete."""

    COMMAND = "command"  # Run a shell command
    API = "api"  # Make an API request
    BROWSER = "browser"  # Browser automation check
    COMPONENT = "component"  # Component renders correctly
    MANUAL = "manual"  # Requires human verification
    NONE = "none"  # No verification needed (investigation)


class Lane(str, Enum):
    """PFactory test lanes — restructured in Task 0 of v0.2 (#16).

    v0.2 operating model is browser-first: when a feature can be exercised
    through a browser, generate a Browser test; otherwise an API test;
    otherwise an Integration test; Unit only as last resort. Mutation is
    orthogonal — validates the strength of whatever was generated.

    See docs/plans/2026-05-28-enterprise-test-frameworks-design.md
    Decision 2 for the rationale behind the new lane spine.

    v0.1 lanes (FUNCTIONAL / SAST / DAST / FUZZ) are deprecated aliases
    below — they emit a DeprecationWarning when accessed and map to
    UNIT for any plan-load compatibility. Aliases removed in v0.3.
    """

    # v0.2 spine
    UNIT = "unit"  # pytest / Jest / JUnit / xUnit / Go test — last-resort modality
    BROWSER = "browser"  # Playwright / Cypress / Selenium — headline capability
    API = "api"  # supertest / REST Assured / httpx / Karate — when no browser surface
    INTEGRATION = "integration"  # TestContainers / WireMock — cross-service + feature flag gates
    MUTATION = "mutation"  # mutmut / Stryker / PIT — strengthens whatever else was generated


# v0.1 → v0.2 lane name compatibility. These names appear in old test_plan.json
# files; we accept them on load (mapped to UNIT) with a warning. Removed in v0.3.
_V01_LANE_ALIASES: dict[str, Lane] = {
    "functional": Lane.UNIT,  # functional → unit (most v0.1 usage was unit tests)
    "sast": Lane.UNIT,  # SAST is out of scope per v0.2 design; collapse to unit so old plans still parse
    "dast": Lane.UNIT,  # same — out of scope
    "fuzz": Lane.UNIT,  # property-based testing was rare in v0.1
}


def _parse_lane_str(raw: str) -> Lane:
    """Parse a lane name string with v0.1 alias support + deprecation warning.

    Loaders (Subtask.from_dict, etc.) should call this instead of Lane(raw)
    directly so old test_plan.json files keep parsing through v0.2.
    """
    import warnings

    try:
        return Lane(raw)
    except ValueError:
        if raw in _V01_LANE_ALIASES:
            warnings.warn(
                f"Lane value {raw!r} is a deprecated v0.1 alias mapped to "
                f"{_V01_LANE_ALIASES[raw].value!r}. v0.3 will remove the alias. "
                f"See docs/plans/2026-05-28-enterprise-test-frameworks-design.md "
                f"Decision 2 for the new lane spine.",
                DeprecationWarning,
                stacklevel=2,
            )
            return _V01_LANE_ALIASES[raw]
        raise


# Backwards compatibility aliases
ChunkStatus = SubtaskStatus
