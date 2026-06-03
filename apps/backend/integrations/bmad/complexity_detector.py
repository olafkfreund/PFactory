"""
Complexity Detection for BMad Method Integration.

Detects task complexity on a 5-level scale (0-4) and recommends appropriate planning tracks.
"""

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Track(Enum):
    """Planning track types based on BMad Method."""
    QUICK_FLOW = "quick_flow"
    STANDARD = "standard"
    ENTERPRISE = "enterprise"

    @property
    def display_name(self) -> str:
        """Human-readable track name."""
        names = {
            Track.QUICK_FLOW: "Quick Flow",
            Track.STANDARD: "Standard",
            Track.ENTERPRISE: "Enterprise"
        }
        return names.get(self, self.value)

    @property
    def description(self) -> str:
        """Track description for UI."""
        descriptions = {
            Track.QUICK_FLOW: "Fast iteration for simple tasks (3 phases, 5-15 min)",
            Track.STANDARD: "Comprehensive planning for features (6-7 phases, hours)",
            Track.ENTERPRISE: "Full planning with compliance (8+ phases, days)"
        }
        return descriptions.get(self, "")

    @property
    def phase_count(self) -> str:
        """Expected phase count."""
        counts = {
            Track.QUICK_FLOW: "3 phases",
            Track.STANDARD: "6-7 phases",
            Track.ENTERPRISE: "8+ phases"
        }
        return counts.get(self, "")


@dataclass
class ComplexityResult:
    """Result of complexity detection analysis."""
    level: int  # 0-4
    track: Track
    phases: list[str]
    estimated_stories: int
    recommended_docs: list[str]
    reasoning: str
    confidence: float  # 0.0-1.0
    track_rationale: str = ""  # Why this track was recommended
    alternative_tracks: list[Track] | None = None  # Other valid track options

    def __post_init__(self):
        if self.alternative_tracks is None:
            self.alternative_tracks = []


class ComplexityDetector:
    """
    Detects task complexity using keyword analysis and LLM-based story estimation.

    Based on BMad Method's 5-level complexity scale:
    - Level 0: Single atomic change (1 story)
    - Level 1: Small feature (1-10 stories)
    - Level 2: Medium project (5-15 stories)
    - Level 3: Complex system (12-40 stories, architecture required)
    - Level 4: Enterprise scale (40+ stories)
    """

    # Keyword patterns from BMad project-levels.yaml
    LEVEL_KEYWORDS = {
        0: ["fix", "bug", "typo", "small change", "quick update", "patch"],
        1: ["simple", "basic", "small feature", "add", "minor", "game", "script", "tool", "util"],
        2: ["dashboard", "several features", "admin panel", "medium", "crud", "form"],
        3: ["platform", "integration", "complex system", "microservice", "distributed", "multi-service architecture"],
        4: ["enterprise", "multi-tenant", "multiple products", "ecosystem", "large scale"],
    }

    # Story count ranges from BMad project-levels.yaml
    STORY_RANGES = {
        0: (1, 1),
        1: (1, 10),
        2: (5, 15),
        3: (12, 40),
        4: (40, 999),
    }

    def __init__(self):
        """Initialize complexity detector with BMad configuration."""
        self.levels_config = self._load_levels_config()

    def _load_levels_config(self) -> dict:
        """Load complexity levels configuration."""
        return {
            0: {
                "name": "Level 0",
                "title": "Single Atomic Change",
                "description": "Bug fix, tiny feature, one small change",
                "documentation": ["Minimal tech spec"],
                "architecture": False,
            },
            1: {
                "name": "Level 1",
                "title": "Small Feature",
                "description": "Small coherent feature, minimal documentation",
                "documentation": ["Tech spec"],
                "architecture": False,
            },
            2: {
                "name": "Level 2",
                "title": "Medium Project",
                "description": "Multiple features, focused PRD",
                "documentation": ["PRD", "Optional tech spec"],
                "architecture": False,
            },
            3: {
                "name": "Level 3",
                "title": "Complex System",
                "description": "Subsystems, integrations, full architecture",
                "documentation": ["PRD", "Architecture", "JIT tech specs"],
                "architecture": True,
            },
            4: {
                "name": "Level 4",
                "title": "Enterprise Scale",
                "description": "Multiple products, enterprise architecture",
                "documentation": ["PRD", "Architecture", "JIT tech specs"],
                "architecture": True,
            },
        }

    def detect(self, task_description: str, project_dir: Path | None = None) -> ComplexityResult:
        """
        Detect complexity level from task description.

        Algorithm:
        1. Keyword detection
        2. Story count estimation using pattern matching
        3. Map to complexity level and track
        4. Boost confidence when both methods agree

        Args:
            task_description: User's task description
            project_dir: Optional project directory for context

        Returns:
            ComplexityResult with level, track, phases, and reasoning
        """
        # Step 1: Try keyword detection
        keyword_level = self._detect_by_keywords(task_description)

        # Step 2: Estimate story count from description length and complexity
        estimated_stories = self._estimate_story_count(task_description)
        story_level = self._map_stories_to_level(estimated_stories)

        # Step 3: Determine final level and confidence
        if keyword_level is not None:
            # Keyword match found
            if keyword_level == story_level:
                # Both methods agree - high confidence (0.95)
                return self._build_result(
                    level=keyword_level,
                    estimated_stories=estimated_stories,
                    reasoning=f"Keyword and story analysis agree on Level {keyword_level}",
                    confidence=0.95
                )
            else:
                # Keyword takes priority but lower confidence due to disagreement
                return self._build_result(
                    level=keyword_level,
                    estimated_stories=self._estimate_stories_from_level(keyword_level),
                    reasoning=f"Detected by keywords matching Level {keyword_level} (story analysis suggested Level {story_level})",
                    confidence=0.80
                )

        # No keyword match - use story estimation with moderate confidence
        return self._build_result(
            level=story_level,
            estimated_stories=estimated_stories,
            reasoning=f"Estimated {estimated_stories} stories from task analysis",
            confidence=0.70
        )

    def _detect_by_keywords(self, task_description: str) -> int | None:
        """
        Detect complexity level using keyword matching.

        Returns level (0-4) if keywords found, None otherwise.
        """
        task_lower = task_description.lower()
        word_count = len(task_description.split())

        # For short descriptions (<30 words), check simple levels first
        # to avoid over-classification of brief tasks
        if word_count < 30:
            level_order = [0, 1, 2, 3, 4]
        else:
            # For longer descriptions, check complex levels first
            level_order = [4, 3, 2, 1, 0]

        for level in level_order:
            keywords = self.LEVEL_KEYWORDS[level]
            for keyword in keywords:
                if re.search(r'\b' + re.escape(keyword) + r'\b', task_lower):
                    return level

        return None

    def _estimate_story_count(self, task_description: str) -> int:
        """
        Estimate story count from task description.

        Uses heuristics based on:
        - Description length
        - Complexity indicators
        - Feature count
        """
        # Simple heuristic: longer descriptions = more stories
        word_count = len(task_description.split())

        # Count feature indicators
        feature_indicators = [
            "feature", "functionality", "component", "page", "screen",
            "module", "service", "endpoint", "api", "database"
        ]
        indicator_count = sum(
            task_description.lower().count(indicator)
            for indicator in feature_indicators
        )

        # Estimate based on word count and indicators
        if word_count < 20 and indicator_count == 0:
            return 1  # Very brief = likely 1 story
        elif word_count < 50:
            return max(3, indicator_count * 2)  # Short = small feature
        elif word_count < 150:
            return max(8, indicator_count * 3)  # Medium length
        elif word_count < 300:
            return max(20, indicator_count * 4)  # Long = complex
        else:
            return max(50, indicator_count * 5)  # Very long = enterprise

    def _map_stories_to_level(self, estimated_stories: int) -> int:
        """Map estimated story count to complexity level."""
        if estimated_stories == 1:
            return 0
        elif estimated_stories <= 10:
            return 1
        elif estimated_stories <= 15:
            return 2
        elif estimated_stories <= 40:
            return 3
        else:
            return 4

    def _estimate_stories_from_level(self, level: int) -> int:
        """Get typical story count for a given level."""
        story_ranges = {
            0: 1,
            1: 5,
            2: 10,
            3: 25,
            4: 50,
        }
        return story_ranges.get(level, 5)

    def _select_track(self, level: int) -> tuple[Track, str, list[Track]]:
        """
        Select planning track based on complexity level.

        Returns:
            tuple: (recommended_track, rationale, alternative_tracks)
        """
        if level == 0:
            return (
                Track.QUICK_FLOW,
                "Atomic change - Quick Flow is sufficient (discovery, tech-spec, validate)",
                []
            )
        elif level == 1:
            return (
                Track.QUICK_FLOW,
                "Small feature - Quick Flow provides fast iteration",
                [Track.STANDARD]  # Can opt for Standard if more planning needed
            )
        elif level == 2:
            return (
                Track.STANDARD,
                "Medium project - Standard track balances planning and speed",
                [Track.QUICK_FLOW, Track.ENTERPRISE]  # Can go faster or more thorough
            )
        elif level == 3:
            return (
                Track.STANDARD,
                "Complex system - Standard track with architecture phase",
                [Track.ENTERPRISE]  # Can opt for Enterprise if compliance needed
            )
        else:  # level == 4
            return (
                Track.ENTERPRISE,
                "Enterprise scale - Full planning with security and compliance",
                [Track.STANDARD]  # Can opt for Standard if willing to skip some phases
            )

    def _get_phases_for_level(self, level: int, track: Track) -> list[str]:
        """
        Get spec creation phase pipeline for complexity level and track.

        Phase pipelines:
        - Quick Flow (0-1): discovery → tech-spec → validate
        - Standard without arch (2): discovery → requirements → context → spec → plan → validate
        - Standard with arch (3): discovery → requirements → architecture → context → spec → plan → validate
        - Enterprise (4): discovery → requirements → architecture → security → context → spec → plan → validate
        """
        if track == Track.QUICK_FLOW:
            return ["discovery", "tech_spec", "validate"]

        if track == Track.STANDARD:
            if level <= 2:
                # No architecture for level 2
                return ["discovery", "requirements", "context", "spec", "plan", "validate"]
            else:
                # Architecture required for level 3
                return ["discovery", "requirements", "architecture", "context", "spec", "plan", "validate"]

        if track == Track.ENTERPRISE:
            # Full pipeline with security and devops
            return [
                "discovery",
                "requirements",
                "architecture",
                "security",
                "context",
                "spec",
                "plan",
                "validate"
            ]

        # Default fallback
        return ["discovery", "requirements", "context", "spec", "plan", "validate"]

    def _build_result(
        self,
        level: int,
        estimated_stories: int,
        reasoning: str,
        confidence: float
    ) -> ComplexityResult:
        """Build a ComplexityResult from detected level."""
        track, track_rationale, alternative_tracks = self._select_track(level)
        phases = self._get_phases_for_level(level, track)
        config = self.levels_config[level]

        return ComplexityResult(
            level=level,
            track=track,
            phases=phases,
            estimated_stories=estimated_stories,
            recommended_docs=config["documentation"],
            reasoning=f"{config['title']}: {reasoning}",
            confidence=confidence,
            track_rationale=track_rationale,
            alternative_tracks=alternative_tracks
        )
