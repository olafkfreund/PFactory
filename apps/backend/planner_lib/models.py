"""
Data models for the implementation planner.
"""

from dataclasses import dataclass
from typing import Any

from test_plan import WorkflowType


@dataclass
class PlannerContext:
    """Context gathered for planning."""

    spec_content: str
    project_index: dict[str, Any]
    task_context: dict[str, Any]
    services_involved: list[str]
    workflow_type: WorkflowType
    files_to_modify: list[dict[str, Any]]
    files_to_reference: list[dict[str, Any]]
