"""
Base Sub-Agent Framework
=========================

Abstract base class and result types for BMad Method sub-agents.
Sub-agents are specialized, focused analysis tools invoked by main agents.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class SubAgentResult:
    """Result returned by a sub-agent invocation.

    Attributes:
        success: Whether the analysis completed successfully
        data: Analysis results (structure depends on sub-agent type)
        reasoning: Explanation of findings and methodology
        confidence: Confidence score (0.0-1.0)
        issues: List of issues or concerns found
        recommendations: List of recommendations
        metadata: Additional metadata (execution time, tokens used, etc.)
    """

    success: bool
    data: dict[str, Any]
    reasoning: str = ""
    confidence: float = 1.0
    issues: list[str] = None
    recommendations: list[str] = None
    metadata: dict[str, Any] = None

    def __post_init__(self):
        """Initialize default values for optional fields."""
        if self.issues is None:
            self.issues = []
        if self.recommendations is None:
            self.recommendations = []
        if self.metadata is None:
            self.metadata = {}


class SubAgent(ABC):
    """Abstract base class for BMad Method sub-agents.

    Sub-agents are specialized, single-purpose analysis tools that follow
    BMad Method principles:
    - Single responsibility (one focused task)
    - Composable (can be chained together)
    - Stateless (no cross-invocation memory)
    - Fast (optimized for quick analysis)

    Main agents (planner, architect, coder) invoke sub-agents for specialized
    analysis tasks like requirements validation, codebase exploration, or
    technical evaluation.
    """

    def __init__(self, project_dir: Path, spec_dir: Path | None = None):
        """Initialize sub-agent.

        Args:
            project_dir: Project root directory
            spec_dir: Spec directory (optional, for accessing spec files)
        """
        self.project_dir = project_dir
        self.spec_dir = spec_dir

    @abstractmethod
    def analyze(self, input_data: dict[str, Any]) -> SubAgentResult:
        """Run the sub-agent's analysis.

        Args:
            input_data: Input parameters specific to this sub-agent

        Returns:
            SubAgentResult with analysis findings
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the sub-agent's name."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Return the sub-agent's purpose and capabilities."""
        pass

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}: {self.name}>"
