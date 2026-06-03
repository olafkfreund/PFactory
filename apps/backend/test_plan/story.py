#!/usr/bin/env python3
"""
Story Models (BMad Method)
===========================

User story with acceptance criteria, technical context, and BMad Method patterns.
This extends the subtask model to include product-oriented story structure.
"""

from dataclasses import dataclass, field
from datetime import datetime

from .enums import SubtaskStatus
from .verification import Verification


@dataclass
class TechnicalContext:
    """Technical context for implementing a story."""

    architecture_references: list[str] = field(default_factory=list)
    stack: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    technical_notes: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            "architecture_references": self.architecture_references,
            "stack": self.stack,
            "dependencies": self.dependencies,
            "technical_notes": self.technical_notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TechnicalContext":
        """Create TechnicalContext from dictionary."""
        return cls(
            architecture_references=data.get("architecture_references", []),
            stack=data.get("stack", []),
            dependencies=data.get("dependencies", []),
            technical_notes=data.get("technical_notes", ""),
        )


@dataclass
class Story:
    """
    A user story with acceptance criteria (BMad Method pattern).

    Extends the subtask model with product-oriented structure:
    - User story format: "As a [role], I want [capability] so that [benefit]"
    - Acceptance criteria: Testable conditions that define "done"
    - Technical context: References to architecture, stack, dependencies
    - Story points: Effort estimate (1, 2, 3, 5, 8, 13...)
    """

    id: str  # Story ID (e.g., "US-001")
    title: str  # Brief title
    user_story: str  # "As a..., I want..., so that..."
    acceptance_criteria: list[str] = field(default_factory=list)  # AC1, AC2, ...
    technical_context: TechnicalContext = field(default_factory=TechnicalContext)

    # Estimation
    story_points: int = 3  # Fibonacci: 1, 2, 3, 5, 8, 13, 21
    priority: str = "medium"  # high, medium, low

    # Status tracking (same as Subtask for compatibility)
    status: SubtaskStatus = SubtaskStatus.PENDING

    # Scoping (same as Subtask)
    service: str | None = None
    all_services: bool = False

    # Files (same as Subtask)
    files_to_modify: list[str] = field(default_factory=list)
    files_to_create: list[str] = field(default_factory=list)
    patterns_from: list[str] = field(default_factory=list)

    # Verification (same as Subtask)
    verification: Verification | None = None

    # Tracking (same as Subtask)
    started_at: str | None = None
    completed_at: str | None = None
    session_id: int | None = None
    critique_result: dict | None = None

    # Legacy compatibility fields
    description: str | None = None  # Falls back to user_story

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        result = {
            "id": self.id,
            "title": self.title,
            "user_story": self.user_story,
            "acceptance_criteria": self.acceptance_criteria,
            "technical_context": self.technical_context.to_dict(),
            "story_points": self.story_points,
            "priority": self.priority,
            "status": self.status.value,
        }

        # Optional fields
        if self.service:
            result["service"] = self.service
        if self.all_services:
            result["all_services"] = True
        if self.files_to_modify:
            result["files_to_modify"] = self.files_to_modify
        if self.files_to_create:
            result["files_to_create"] = self.files_to_create
        if self.patterns_from:
            result["patterns_from"] = self.patterns_from
        if self.verification:
            result["verification"] = self.verification.to_dict()
        if self.started_at:
            result["started_at"] = self.started_at
        if self.completed_at:
            result["completed_at"] = self.completed_at
        if self.session_id is not None:
            result["session_id"] = self.session_id
        if self.critique_result:
            result["critique_result"] = self.critique_result

        return result

    @classmethod
    def from_dict(cls, data: dict) -> "Story":
        """Create Story from dictionary."""
        # Parse technical context
        tech_context = TechnicalContext()
        if "technical_context" in data:
            tech_context = TechnicalContext.from_dict(data["technical_context"])

        # Parse verification
        verification = None
        if "verification" in data:
            verification = Verification.from_dict(data["verification"])

        return cls(
            id=data["id"],
            title=data.get("title", ""),
            user_story=data.get("user_story", ""),
            acceptance_criteria=data.get("acceptance_criteria", []),
            technical_context=tech_context,
            story_points=data.get("story_points", 3),
            priority=data.get("priority", "medium"),
            status=SubtaskStatus(data.get("status", "pending")),
            service=data.get("service"),
            all_services=data.get("all_services", False),
            files_to_modify=data.get("files_to_modify", []),
            files_to_create=data.get("files_to_create", []),
            patterns_from=data.get("patterns_from", []),
            verification=verification,
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            session_id=data.get("session_id"),
            critique_result=data.get("critique_result"),
            description=data.get("description"),  # Legacy compatibility
        )

    @classmethod
    def from_subtask(cls, subtask_data: dict) -> "Story":
        """
        Convert old subtask format to story format (migration helper).

        Args:
            subtask_data: Old subtask dictionary

        Returns:
            Story with generated user story and acceptance criteria
        """
        # Extract ID and description
        subtask_id = subtask_data["id"]
        description = subtask_data["description"]

        # Generate basic user story from description
        user_story = f"As a developer, I want to {description.lower()} so that the feature works correctly"

        # Generate basic acceptance criteria
        acceptance_criteria = [
            f"AC1: {description}",
            "AC2: Code follows project conventions",
            "AC3: Changes are tested and verified",
        ]

        # Create story with subtask fields preserved
        return cls(
            id=subtask_id,
            title=description[:50],  # Truncate for title
            user_story=user_story,
            acceptance_criteria=acceptance_criteria,
            technical_context=TechnicalContext(),
            story_points=3,  # Default estimate
            priority="medium",
            status=SubtaskStatus(subtask_data.get("status", "pending")),
            service=subtask_data.get("service"),
            all_services=subtask_data.get("all_services", False),
            files_to_modify=subtask_data.get("files_to_modify", []),
            files_to_create=subtask_data.get("files_to_create", []),
            patterns_from=subtask_data.get("patterns_from", []),
            verification=Verification.from_dict(subtask_data["verification"])
            if "verification" in subtask_data
            else None,
            started_at=subtask_data.get("started_at"),
            completed_at=subtask_data.get("completed_at"),
            session_id=subtask_data.get("session_id"),
            critique_result=subtask_data.get("critique_result"),
            description=description,  # Preserve original
        )

    def get_display_description(self) -> str:
        """Get description for display (user story or fallback to title)."""
        return self.user_story or self.title or self.description or f"Story {self.id}"

    # Methods for compatibility with Subtask interface

    def start(self, session_id: int):
        """Mark story as in progress."""
        self.status = SubtaskStatus.IN_PROGRESS
        self.started_at = datetime.now().isoformat()
        self.session_id = session_id
        self.completed_at = None

    def complete(self, output: str | None = None):
        """Mark story as done."""
        self.status = SubtaskStatus.COMPLETED
        self.completed_at = datetime.now().isoformat()
        # Note: Stories don't have actual_output field like subtasks

    def fail(self, reason: str | None = None):
        """Mark story as failed."""
        self.status = SubtaskStatus.FAILED
        self.completed_at = None
        # Note: Stories don't have actual_output field like subtasks
