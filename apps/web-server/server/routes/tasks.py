"""
Task management routes.

Handles CRUD operations for tasks (specs) within projects.
"""

import json
import logging
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from server.services.git_utils import safe_spec_component

from ..paths import get_data_dir, get_data_file
from ..services.git_utils import run_git_command
from .projects import load_projects

logger = logging.getLogger(__name__)

router = APIRouter()


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


# Frontend-compatible task statuses (matches frontend KanbanBoard columns)
TaskStatus = Literal[
    "backlog",
    "in_progress",
    "ai_review",
    "human_review",
    "done",
]

# Backend statuses that get mapped to frontend statuses:
# backlog -> backlog           (not started)
# planning -> backlog          (still in queue)
# in_progress -> in_progress   (actively building)
# review -> human_review       (build finished, needs merge review)
# qa_pending -> ai_review      (QA running)
# qa_failed -> human_review    (QA failed, needs human attention)
# completed -> human_review    (finished, needs final approval/merge)
# cancelled -> backlog         (cancelled, shown in backlog)


class SubtaskVerification(BaseModel):
    """Verification configuration for a subtask."""

    type: str = "command"  # Verification type (e.g., "command", "browser", "manual", "code_review", "testing", etc.)
    run: str | None = None  # Command to run (e.g., "npm test")
    scenario: str | None = None  # Browser test scenario


class Subtask(BaseModel):
    """Subtask model from implementation plan."""

    id: str
    title: str
    description: str | None = None
    status: Literal["pending", "in_progress", "completed", "failed"] = "pending"
    files: list[str] = Field(default_factory=list)  # Files affected by this subtask
    verification: SubtaskVerification | None = None  # How to verify completion


class TaskBase(BaseModel):
    """Base task model."""

    title: str = Field(..., description="Task title")
    description: str = Field(..., description="Task description/requirements")


class TaskCreate(TaskBase):
    """Model for creating a new task."""

    project_id: str = Field(..., description="ID of the project this task belongs to")
    metadata: Optional["TaskMetadataUpdate"] = Field(None, description="Optional task metadata")


class SelectedSkill(BaseModel):
    """A skill selected to be applied to a task."""

    id: str           # '{category}/{skill_name}'
    name: str         # human-readable display name
    category: str     # parent category
    source: str | None = None  # optional source URL from skill metadata


class TaskMetadata(BaseModel):
    """Task metadata fields."""

    sourceType: str | None = None
    category: str | None = None
    priority: str | None = None
    complexity: str | None = None
    impact: str | None = None
    # GitHub integration
    githubIssueNumber: int | None = None
    affectedFiles: list[str] | None = None
    acceptanceCriteria: list[str] | None = None
    model: str | None = None
    thinkingLevel: str | None = None
    requireReviewBeforeCoding: bool | None = None
    # Execution mode: 'quick' uses simplified prompts (~70% fewer tokens)
    mode: str | None = None  # 'quick' or 'full'
    # Phase-specific model/thinking configuration (Auto profile)
    isAutoProfile: bool | None = None
    phaseModels: dict | None = None
    phaseThinking: dict | None = None
    # Git options
    baseBranch: str | None = None
    # Archive info
    archivedAt: str | None = None
    archivedInVersion: str | None = None
    # Skills attached to this task
    selectedSkills: list[SelectedSkill] | None = None


class Task(TaskBase):
    """Full task model with all fields."""

    id: str = Field(..., description="Unique task ID")
    spec_id: str = Field(..., description="Spec directory name (e.g., '001-feature')")
    project_id: str = Field(..., description="Project ID")
    status: TaskStatus = Field("backlog", description="Current task status")
    phase: str | None = Field(None, description="Current execution phase")
    subtasks: list[Subtask] = Field(default_factory=list)
    created_at: str = Field(..., description="ISO timestamp")
    updated_at: str = Field(..., description="ISO timestamp")
    worktree_path: str | None = Field(None, description="Path to git worktree if active")
    branch_name: str | None = Field(None, description="Git branch name")
    metadata: TaskMetadata | None = Field(None, description="Task metadata")
    review_reason: str | None = Field(None, description="Reason for human review (e.g., 'plan_review')")
    repo: str | None = Field(
        None,
        description="Target repo (owner/name) this task runs against, for "
        "cross-portal tracking + visibility (W5, Factory #218).",
    )


class TaskList(BaseModel):
    """Response model for listing tasks."""

    tasks: list[Task]
    total: int


class TaskMetadataUpdate(BaseModel):
    """Model for updating task metadata fields.

    Fields can be set to None to explicitly clear them from the task.
    When a field is not provided (excluded from the request), it won't be modified.
    When a field is set to null/None, it will be removed from the task metadata.
    """

    model: str | None = None
    thinkingLevel: str | None = None
    requireReviewBeforeCoding: bool | None = None
    category: str | None = None
    priority: str | None = None
    complexity: str | None = None
    impact: str | None = None
    # Phase-specific model/thinking configuration (Auto profile)
    isAutoProfile: bool | None = None
    phaseModels: dict | None = None  # {"spec": "sonnet", "planning": "opus", ...}
    phaseThinking: dict | None = None  # {"spec": "medium", "planning": "high", ...}
    # Git options
    baseBranch: str | None = None
    # Execution mode: 'quick' uses simplified prompts (~70% fewer tokens)
    mode: str | None = None  # 'quick' or 'full'
    # Image attachments (can be null to clear)
    attachedImages: list | None = None
    # Referenced files (can be null to clear)
    referencedFiles: list | None = None
    # Skills attached to this task (can be null to clear)
    selectedSkills: list[SelectedSkill] | None = None


class TaskUpdate(BaseModel):
    """Model for updating task fields."""

    title: str | None = None
    description: str | None = None
    status: TaskStatus | None = None
    metadata: TaskMetadataUpdate | None = None


class ClarificationQuestion(BaseModel):
    """A single clarification question with multiple-choice options."""

    id: str
    question: str
    options: list[str] = Field(default_factory=list)


class ClarificationResponse(BaseModel):
    """Response from clarification question generation."""

    questions: list[ClarificationQuestion] = Field(default_factory=list)
    skip: bool = False
    skip_reason: str = Field("", alias="skipReason")

    model_config = {"populate_by_name": True}


class ClarificationAnswer(BaseModel):
    """A single answered clarification question."""

    question_id: str = Field(..., alias="questionId")
    question: str
    answer: str

    model_config = {"populate_by_name": True}


class ClarificationAnswersRequest(BaseModel):
    """Request to submit clarification answers."""

    answers: list[ClarificationAnswer]


# --------------------------------------------------------------------------
# Helper Functions
# --------------------------------------------------------------------------


def get_spec_dirs(project_path: Path) -> list[Path]:
    """Get all spec directories in a project."""
    specs_dir = project_path / ".pfactory" / "specs"
    if not specs_dir.exists():
        return []
    return sorted([d for d in specs_dir.iterdir() if d.is_dir()])


def get_next_spec_id(project_path: Path, title: str) -> str:
    """Generate the next spec ID (e.g., '003-feature-name').

    Uses a counter file (.pfactory/specs/.counter) to ensure IDs
    never get reused after deletion.
    """
    specs_dir = project_path / ".pfactory" / "specs"
    counter_file = specs_dir / ".counter"

    # Read persisted counter (highest ID ever assigned)
    persisted_max = 0
    if counter_file.exists():
        try:
            persisted_max = int(counter_file.read_text().strip())
        except (ValueError, OSError):
            pass

    # Also check existing directories in case counter file is missing
    existing = get_spec_dirs(project_path)
    dir_max = 0
    for spec_dir in existing:
        match = re.match(r"(\d+)-", spec_dir.name)
        if match:
            dir_max = max(dir_max, int(match.group(1)))

    # Use the higher of persisted counter and directory scan
    max_num = max(persisted_max, dir_max)
    next_num = max_num + 1

    # Persist the new counter
    specs_dir.mkdir(parents=True, exist_ok=True)
    counter_file.write_text(str(next_num))

    # Generate slug from title
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:30]

    # Fallback to "untitled-task" if slug is empty
    if not slug:
        slug = "untitled-task"

    return f"{next_num:03d}-{slug}"


def split_task_id(task_id: str) -> tuple[str, str]:
    """Split a ``"<project_id>:<spec_id>"`` task id, validating the spec half.

    Every handler that takes a task id joins the spec half onto a project root
    and then reads or writes there, so this is the choke point where the
    untrusted component has to be checked -- 18 call sites route through it
    (#335). A traversal component raises ValueError, which the FastAPI layer
    surfaces as a 400 rather than reaching the filesystem.
    """
    project_id, _, spec_id = task_id.partition(":")
    try:
        return project_id, safe_spec_component(spec_id)
    except ValueError:
        # This is the request boundary, so answer with a 400. Letting the
        # ValueError escape would surface a 500 and a stack trace for what is
        # simply a malformed (or hostile) id.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid task id"
        ) from None


def get_worktree_spec_dir(project_path: Path, spec_id: str) -> Path | None:
    """Get the worktree spec directory if it exists.

    Worktree layout: .pfactory/worktrees/tasks/{spec_id}/.pfactory/specs/{spec_id}/

    Raises:
        ValueError: if ``spec_id`` could escape the project root (#335).
    """
    spec_id = safe_spec_component(spec_id)
    worktree_spec_dir = (
        project_path
        / ".pfactory"
        / "worktrees"
        / "tasks"
        / spec_id
        / ".pfactory"
        / "specs"
        / spec_id
    )
    if worktree_spec_dir.exists():
        return worktree_spec_dir
    return None


def sync_worktree_to_main_spec(project_path: Path, spec_id: str) -> bool:
    """Sync test_plan.json from worktree to main spec if worktree has newer data.

    Returns True if sync was performed, False otherwise.

    Raises:
        ValueError: if ``spec_id`` could escape the project root (#335).
    """
    spec_id = safe_spec_component(spec_id)
    main_spec_dir = project_path / ".pfactory" / "specs" / spec_id
    worktree_spec_dir = get_worktree_spec_dir(project_path, spec_id)

    if not worktree_spec_dir:
        return False

    worktree_plan_file = worktree_spec_dir / "test_plan.json"
    main_plan_file = main_spec_dir / "test_plan.json"

    if not worktree_plan_file.exists():
        return False

    try:
        worktree_plan = json.loads(worktree_plan_file.read_text())
        main_plan = {}
        if main_plan_file.exists():
            main_plan = json.loads(main_plan_file.read_text())

        # Count completed subtasks in each plan
        def count_completed(plan: dict) -> int:
            count = 0
            for phase in plan.get("phases", []):
                for subtask in phase.get("subtasks", []):
                    if subtask.get("status") == "completed":
                        count += 1
            return count

        worktree_completed = count_completed(worktree_plan)
        main_completed = count_completed(main_plan)

        # Only sync if worktree has more progress (more completed subtasks)
        if worktree_completed > main_completed:
            logger.info(
                f"[WorktreeSync] Syncing plan for {spec_id}: "
                f"worktree has {worktree_completed} completed vs main {main_completed}"
            )
            main_plan_file.write_text(json.dumps(worktree_plan, indent=2))
            return True

        return False
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[WorktreeSync] Failed to sync {spec_id}: {e}")
        return False


def validate_done_status(plan: dict) -> tuple[bool, str]:
    """Validate that all subtasks are completed before allowing 'done' status.

    Returns (is_valid, error_message).
    """
    phases = plan.get("phases", [])
    if not phases:
        # No phases means no subtasks to validate
        return True, ""

    total_subtasks = 0
    completed_subtasks = 0

    for phase in phases:
        for subtask in phase.get("subtasks", []):
            total_subtasks += 1
            if subtask.get("status") == "completed":
                completed_subtasks += 1

    if total_subtasks == 0:
        return True, ""

    if completed_subtasks < total_subtasks:
        return False, (
            f"Cannot mark as done: only {completed_subtasks}/{total_subtasks} "
            f"subtasks are completed. Complete all subtasks first or check if "
            f"worktree has newer progress."
        )

    return True, ""


def get_plan_with_worktree_sync(project_path: Path, spec_id: str) -> tuple[dict, Path]:
    """Get implementation plan, syncing from worktree first if needed.

    Returns (plan_dict, plan_file_path).

    Raises:
        ValueError: if ``spec_id`` could escape the project root (#335).
    """
    spec_id = safe_spec_component(spec_id)
    # Sync worktree to main spec first
    sync_worktree_to_main_spec(project_path, spec_id)

    # Read from main spec (now potentially updated)
    main_spec_dir = project_path / ".pfactory" / "specs" / spec_id
    plan_file = main_spec_dir / "test_plan.json"

    plan = {}
    if plan_file.exists():
        try:
            plan = json.loads(plan_file.read_text())
        except json.JSONDecodeError:
            pass

    return plan, plan_file


def load_spec_metadata(spec_dir: Path) -> dict:
    """Load metadata for a spec from its files."""
    metadata = {
        "title": spec_dir.name,
        "description": "",
        "status": "backlog",
        "phase": None,
        "subtasks": [],
        "worktree_path": None,
        "branch_name": None,
        "archivedAt": None,
        "archivedInVersion": None,
        "reviewReason": None,
    }

    # Try to load requirements.json for title/description (most accurate source)
    requirements_file = spec_dir / "requirements.json"
    if requirements_file.exists():
        try:
            requirements = json.loads(requirements_file.read_text())
            if "title" in requirements:
                metadata["title"] = requirements["title"]
            if "description" in requirements:
                metadata["description"] = requirements["description"]
        except (json.JSONDecodeError, KeyError):
            pass

    # Fall back to spec.md if requirements.json not available
    if not metadata["description"]:
        spec_file = spec_dir / "spec.md"
        if spec_file.exists():
            content = spec_file.read_text()
            # Extract title from first # heading if not already set
            if not metadata["title"] or metadata["title"] == spec_dir.name:
                title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
                if title_match:
                    metadata["title"] = title_match.group(1)
            # Use first paragraph as description (no truncation)
            paragraphs = re.split(r"\n\n+", content)
            for p in paragraphs[1:]:  # Skip title
                if p.strip() and not p.startswith("#"):
                    metadata["description"] = p.strip()
                    break

    # Try to load task_logs.json for active phase status (most accurate)
    task_logs_file = spec_dir / "task_logs.json"
    if task_logs_file.exists():
        try:
            logs = json.loads(task_logs_file.read_text())
            phases = logs.get("phases", {})

            # First check for any active phase
            has_active_phase = False
            for phase_name, phase_data in phases.items():
                if phase_data.get("status") == "active":
                    metadata["phase"] = phase_name
                    metadata["status"] = "in_progress"
                    has_active_phase = True
                    break

            # If no active phase, check for terminal states
            if not has_active_phase:
                # Check if any phase failed → task needs human intervention
                has_failed_phase = any(
                    phase_data.get("status") == "failed"
                    for phase_data in phases.values()
                )
                if has_failed_phase:
                    metadata["status"] = "human_review"
                    metadata["reviewReason"] = "errors"
                else:
                    # Check validation phase completed (strongest completion signal)
                    validation_phase = phases.get("validation", {})
                    if validation_phase.get("status") == "completed" and validation_phase.get("entries"):
                        metadata["phase"] = "validation"
                        metadata["status"] = "human_review"
                        metadata["reviewReason"] = "completed"
                    else:
                        # Fall back to coding phase completed
                        coding_phase = phases.get("coding", {})
                        if coding_phase.get("status") == "completed" and coding_phase.get("entries"):
                            metadata["phase"] = "coding"
                            metadata["status"] = "human_review"
                            metadata["reviewReason"] = "completed"
        except (json.JSONDecodeError, KeyError):
            pass

    # Try to load test_plan.json for status/subtasks
    plan_file = spec_dir / "test_plan.json"
    explicit_status = None  # Track if user explicitly set status via kanban
    if plan_file.exists():
        try:
            plan = json.loads(plan_file.read_text())
            # Only set phase from plan if not already set from task_logs
            if not metadata["phase"]:
                metadata["phase"] = plan.get("phase")

            # If no explicit phase, try to detect from phases array
            if not metadata["phase"] and "phases" in plan:
                for phase in plan["phases"]:
                    if isinstance(phase, dict):
                        phase_status = phase.get("status", "")
                        if phase_status == "in_progress":
                            metadata["phase"] = phase.get("name", phase.get("id"))
                            break

            # Check if status was explicitly set (kanban drag-drop saves this)
            # "done" and "completed" statuses ALWAYS take precedence (task was explicitly finished)
            # Other statuses only apply if we didn't already detect active status from task_logs
            if "status" in plan:
                explicit_status = plan["status"]
                if explicit_status in ("done", "completed"):
                    # Task was explicitly marked as done - always honor this
                    metadata["status"] = explicit_status
                elif metadata["status"] == "backlog":
                    # Only override backlog with other statuses
                    metadata["status"] = explicit_status

            # Load reviewReason if present (e.g., 'plan_review')
            if "reviewReason" in plan:
                metadata["reviewReason"] = plan["reviewReason"]

            # Check for qa_signoff.status == "approved" which means task completed QA
            # This should show as human_review for final merge approval
            qa_signoff = plan.get("qa_signoff") or {}
            if qa_signoff.get("status") == "approved" and metadata["status"] == "backlog":
                metadata["status"] = "human_review"
                metadata["reviewReason"] = "completed"

            # Load archive metadata
            if "archivedAt" in plan:
                metadata["archivedAt"] = plan["archivedAt"]
            if "archivedInVersion" in plan:
                metadata["archivedInVersion"] = plan["archivedInVersion"]

            # Load subtasks - can be at top level or nested in phases
            all_subtasks = []

            # First check for top-level subtasks (legacy format).
            # Tolerate both list shape (canonical) and dict shape
            # (partial-sync artifact from agent_service that maps
            # subtask_id -> {status, notes, ...}).  Without this guard,
            # iterating a dict yields the keys as strings and the loop
            # at the bottom blows up with AttributeError on st.get(...).
            if "subtasks" in plan:
                raw_subtasks = plan["subtasks"]
                if isinstance(raw_subtasks, list):
                    all_subtasks.extend(raw_subtasks)
                elif isinstance(raw_subtasks, dict):
                    for sid, st in raw_subtasks.items():
                        if isinstance(st, dict):
                            st_copy = dict(st)
                            st_copy.setdefault("id", sid)
                            all_subtasks.append(st_copy)

            # Then check for subtasks nested in phases (current format)
            if "phases" in plan:
                for phase in plan["phases"]:
                    if isinstance(phase, dict) and "subtasks" in phase:
                        phase_name = phase.get("name", "")
                        for st in phase["subtasks"]:
                            # Prefix subtask with phase name for clarity
                            st_copy = st.copy() if isinstance(st, dict) else {}
                            if phase_name and "title" not in st_copy:
                                st_copy["title"] = st_copy.get("description", "Subtask")
                            all_subtasks.append(st_copy)

            if all_subtasks:
                metadata["subtasks"] = []
                for i, st in enumerate(all_subtasks):
                    # Build files list from 'file' (single) or 'files'
                    # (array) fields.  Tolerate three shapes the planner
                    # has been observed to emit:
                    #   files: "path/to/x.py"                  (str)
                    #   files: ["a.py", "b.py"]                (list[str])
                    #   files: {"create": ["a.py"], "modify": ["b.py"]}
                    #     (dict — happens when the planner groups files
                    #     by intent; flatten the values into a single
                    #     list of strings)
                    files = []
                    if st.get("file"):
                        files.append(st["file"])
                    raw_files = st.get("files")
                    if isinstance(raw_files, str):
                        files.append(raw_files)
                    elif isinstance(raw_files, list):
                        files.extend(f for f in raw_files if isinstance(f, str))
                    elif isinstance(raw_files, dict):
                        for v in raw_files.values():
                            if isinstance(v, list):
                                files.extend(f for f in v if isinstance(f, str))
                            elif isinstance(v, str):
                                files.append(v)

                    # Build verification from 'verification' or 'verification_method' fields
                    verification = None
                    if st.get("verification"):
                        v = st["verification"]
                        if isinstance(v, dict):
                            verification = SubtaskVerification(
                                type=v.get("type", "command"),
                                run=v.get("run") or v.get("command"),
                                scenario=v.get("scenario"),
                            )
                        elif isinstance(v, str):
                            # Simple string verification becomes a command
                            verification = SubtaskVerification(type="command", run=v)
                    elif st.get("verification_method"):
                        verification = SubtaskVerification(type="command", run=st["verification_method"])

                    metadata["subtasks"].append(Subtask(
                        id=st.get("id", str(i)),
                        title=st.get("title") or st.get("description", f"Subtask {i+1}")[:80],
                        description=st.get("description") or st.get("notes"),
                        status=st.get("status", "pending"),
                        files=files,
                        verification=verification,
                    ))
        except (json.JSONDecodeError, KeyError):
            pass

    # Check for worktree
    worktree_marker = spec_dir / ".worktree_path"
    if worktree_marker.exists():
        metadata["worktree_path"] = worktree_marker.read_text().strip()
        metadata["branch_name"] = f"pfactory/{spec_dir.name}"

    # Load task metadata from requirements.json
    requirements_file = spec_dir / "requirements.json"
    if requirements_file.exists():
        try:
            requirements = json.loads(requirements_file.read_text())
            metadata["task_metadata"] = requirements.get("metadata", {})
        except (json.JSONDecodeError, KeyError):
            metadata["task_metadata"] = {}
    else:
        metadata["task_metadata"] = {}

    # Detect status from subtask progress if not already set
    # If any subtasks are completed but not all done, task is in_progress
    if metadata["status"] == "backlog" and metadata.get("subtasks"):
        subtasks = metadata["subtasks"]
        completed_count = sum(1 for st in subtasks if st.status == "completed")
        in_progress_count = sum(1 for st in subtasks if st.status == "in_progress")
        if completed_count > 0 and completed_count < len(subtasks):
            # Work has been done but not finished
            metadata["status"] = "in_progress"
            metadata["phase"] = "coding"
        elif in_progress_count > 0:
            # Currently working on subtasks
            metadata["status"] = "in_progress"
            metadata["phase"] = "coding"
        elif completed_count == len(subtasks) and len(subtasks) > 0:
            # All subtasks completed - needs review
            metadata["status"] = "human_review"
            metadata["reviewReason"] = "completed"

    # Final safety: "done"/"completed" always wins over all auto-detection
    # This guards against task_logs or subtask detection overriding user intent
    if explicit_status in ("done", "completed"):
        metadata["status"] = explicit_status

    # Only use file-based status detection if no explicit status was set via kanban
    # AND status wasn't already determined from task_logs.json (coding completed)
    # This allows users to override status via drag-and-drop
    if explicit_status is None and metadata["status"] == "backlog":
        if (spec_dir / "QA_FIX_REQUEST.md").exists():
            metadata["status"] = "human_review"
            metadata["reviewReason"] = "qa_rejected"
        elif (spec_dir / "qa_report.md").exists():
            report = (spec_dir / "qa_report.md").read_text()
            if "PASSED" in report.upper():
                metadata["status"] = "human_review"
                metadata["reviewReason"] = "completed"
            elif "FAILED" in report.upper():
                metadata["status"] = "human_review"
                metadata["reviewReason"] = "qa_rejected"
            else:
                metadata["status"] = "ai_review"  # QA still in progress
        elif metadata["phase"]:
            metadata["status"] = "in_progress"

    return metadata


def project_repo(project_data: dict) -> str | None:
    """Best-effort target repo (owner/name) for a project (W5, Factory #218).

    Surfaced on every task so all four portals can show which repo a task runs
    against. Prefers the ``repo`` stamped on a tracked project (W4), then the
    project's git settings, falling back to parsing a clone URL. Returns
    ``None`` when no repo is configured.
    """
    repo = project_data.get("repo")
    if repo and "/" in str(repo):
        return str(repo)
    settings = project_data.get("settings") or {}
    for key in ("githubRepo", "gitRepo", "github_repo"):
        value = settings.get(key)
        if value and "/" in str(value):
            return str(value)
    org, name = settings.get("gitOrg"), settings.get("gitProject")
    if org and name:
        return f"{org}/{name}"
    git_url = project_data.get("gitUrl") or project_data.get("git_url")
    if git_url:
        # git@github.com:owner/name.git  or  https://host/owner/name(.git)
        match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", str(git_url))
        if match:
            return match.group(1)
    return None


def spec_to_task(project_id: str, spec_dir: Path) -> Task:
    """Convert a spec directory to a Task model."""
    metadata = load_spec_metadata(spec_dir)

    # Get timestamps from directory
    stat = spec_dir.stat()

    # Map backend status to frontend-compatible status
    frontend_status = map_backend_status_to_frontend(metadata["status"])

    # Build task metadata if available
    task_metadata = None
    if metadata.get("task_metadata"):
        task_metadata = TaskMetadata(**metadata["task_metadata"])

    return Task(
        id=f"{project_id}:{spec_dir.name}",
        spec_id=spec_dir.name,
        project_id=project_id,
        title=metadata["title"],
        description=metadata["description"],
        status=frontend_status,
        phase=metadata["phase"],
        subtasks=metadata["subtasks"],
        created_at=datetime.fromtimestamp(stat.st_ctime).isoformat(),
        updated_at=datetime.fromtimestamp(stat.st_mtime).isoformat(),
        worktree_path=metadata["worktree_path"],
        branch_name=metadata["branch_name"],
        metadata=task_metadata,
        review_reason=metadata.get("reviewReason"),
    )


def map_backend_status_to_frontend(backend_status: str) -> str:
    """Map backend task status to frontend-compatible status.

    Backend statuses: backlog, planning, in_progress, review, qa_pending, qa_failed, completed, cancelled
    Frontend statuses: backlog, in_progress, ai_review, human_review, done
    """
    status_mapping = {
        # Backend statuses -> frontend statuses
        "backlog": "backlog",
        "planning": "backlog",  # Planning tasks go in backlog column
        "in_progress": "in_progress",
        "review": "human_review",  # Build ready for review/merge - needs human action
        "qa_pending": "ai_review",
        "qa_failed": "human_review",  # Failed QA needs human attention
        "completed": "human_review",  # Completed tasks need merge approval
        "cancelled": "backlog",  # Cancelled tasks shown in backlog (could be hidden later)
        # Frontend statuses (pass through when already mapped or set via kanban)
        "ai_review": "ai_review",
        "human_review": "human_review",
        "done": "done",
    }
    return status_mapping.get(backend_status, "backlog")


def get_execution_progress(spec_dir: Path, subtasks: list) -> dict | None:
    """Compute execution progress from task_logs.json and subtasks.

    Returns ExecutionProgress dict or None if not available.
    """
    # Also check worktree for task_logs.json
    project_path = spec_dir.parent.parent  # .pfactory/specs -> project root
    worktree_spec_dir = (
        project_path / "worktrees" / "tasks" / spec_dir.name / ".pfactory" / "specs" / spec_dir.name
    )

    task_logs_file = None
    for check_dir in [worktree_spec_dir, spec_dir]:
        candidate = check_dir / "task_logs.json"
        if candidate.exists():
            task_logs_file = candidate
            break

    if not task_logs_file:
        return None

    try:
        task_logs = json.loads(task_logs_file.read_text())
        phases = task_logs.get("phases", {})

        # Determine current phase from task_logs status
        # Maps task_logs.json phase names to frontend ExecutionPhase values
        phase_map = {
            "planning": "planning",
            "plan_review": "plan_review",
            "coding": "coding",
            "validation": "qa_review",
            "qa_review": "qa_review",
            "qa_fixing": "qa_fixing",
            "complete": "complete",
            "failed": "failed",
        }

        # Phase order for progress calculation
        phase_order = ["planning", "plan_review", "coding", "validation", "qa_fixing"]
        phase_weights = {
            "planning": 10,
            "plan_review": 5,
            "coding": 60,
            "validation": 15,
            "qa_fixing": 10,
        }  # % of total progress

        current_phase = "idle"
        current_phase_key = None
        started_at = None
        phase_progress = 0

        for log_phase, log_data in phases.items():
            # Get earliest started_at from any phase
            if log_data.get("started_at") and not started_at:
                started_at = log_data["started_at"]
            elif log_data.get("started_at") and started_at:
                # Keep the earliest timestamp
                if log_data["started_at"] < started_at:
                    started_at = log_data["started_at"]

            if log_data.get("status") == "active":
                current_phase = phase_map.get(log_phase, log_phase)
                current_phase_key = log_phase

        # If no active phase, check for terminal states (completed/failed)
        if current_phase == "idle" and phases:
            has_failed = any(p.get("status") == "failed" for p in phases.values())
            has_completed = any(p.get("status") == "completed" for p in phases.values())

            if has_failed:
                current_phase = "failed"
            elif has_completed:
                validation = phases.get("validation", {})
                coding = phases.get("coding", {})
                if validation.get("status") == "completed":
                    current_phase = "complete"
                elif coding.get("status") == "completed":
                    current_phase = "complete"

        # Calculate overall progress from subtasks
        completed = sum(1 for s in subtasks if s.status == "completed")
        total = len(subtasks)
        overall_progress = int((completed / total) * 100) if total > 0 else 0

        # Override progress for terminal states
        if current_phase in ("complete", "failed"):
            phase_progress = 100
            overall_progress = 100

        # Calculate phase-specific progress
        if current_phase_key:
            phase_data = phases.get(current_phase_key, {})
            entries = phase_data.get("entries", [])
            # Estimate phase progress based on entries (simple heuristic)
            if entries:
                # Count completed tools vs total activity
                tool_starts = sum(1 for e in entries if e.get("type") == "tool_start")
                tool_ends = sum(1 for e in entries if e.get("type") == "tool_end")
                if tool_starts > 0:
                    phase_progress = min(100, int((tool_ends / tool_starts) * 100))
                else:
                    phase_progress = 50  # Activity detected but no tools tracked
            else:
                phase_progress = 10  # Phase started but no entries yet

        # Find current subtask
        current_subtask = None
        for s in subtasks:
            if s.status == "in_progress":
                current_subtask = s.title
                break

        # Generate sequence number from file modification time for stale update detection
        sequence_number = int(task_logs_file.stat().st_mtime * 1000)

        return {
            "phase": current_phase,
            "phaseProgress": phase_progress,
            "overallProgress": overall_progress,
            "currentSubtask": current_subtask,
            "message": f"{completed}/{total} subtasks completed",
            "startedAt": started_at,
            "sequenceNumber": sequence_number,
        }
    except (json.JSONDecodeError, Exception):
        return None


def task_to_dict(task: Task) -> dict:
    """Convert a Task model to a dict with camelCase keys for frontend."""
    # Get execution progress and archive metadata if task has a spec directory
    execution_progress = None
    archive_metadata = {}
    specs_path = None
    if task.spec_id:
        # Try to find spec dir for this task
        projects = load_projects()
        if task.project_id in projects:
            project_path = Path(projects[task.project_id]["path"])
            spec_dir = project_path / ".pfactory" / "specs" / task.spec_id
            if spec_dir.exists():
                specs_path = str(spec_dir)  # Store path for frontend Files tab
                execution_progress = get_execution_progress(spec_dir, task.subtasks)
                # Load archive metadata from plan file
                plan_file = spec_dir / "test_plan.json"
                if plan_file.exists():
                    try:
                        plan = json.loads(plan_file.read_text())
                        if "archivedAt" in plan:
                            archive_metadata["archivedAt"] = plan["archivedAt"]
                        if "archivedInVersion" in plan:
                            archive_metadata["archivedInVersion"] = plan["archivedInVersion"]
                    except json.JSONDecodeError:
                        pass

    result = {
        "id": task.id,
        "specId": task.spec_id,
        "projectId": task.project_id,
        "title": task.title,
        "description": task.description,
        "status": map_backend_status_to_frontend(task.status),
        "phase": task.phase,
        "subtasks": [
            {
                "id": s.id,
                "title": s.title,
                "description": s.description,
                "status": s.status,
                "files": s.files,
                "verification": {
                    "type": s.verification.type,
                    "run": s.verification.run,
                    "scenario": s.verification.scenario,
                } if s.verification else None,
            }
            for s in task.subtasks
        ],
        "logs": [],  # Required by frontend Task interface
        "createdAt": task.created_at,
        "updatedAt": task.updated_at,
        "worktreePath": task.worktree_path,
        "branchName": task.branch_name,
        "reviewReason": task.review_reason,
        "specsPath": specs_path,  # Path to spec directory for Files tab
        # W5 (Factory #218): target repo (owner/name) for cross-portal tracking.
        "repo": task.repo,
    }

    if execution_progress:
        result["executionProgress"] = execution_progress

    # Include task metadata (settings from requirements.json)
    metadata_payload = task.metadata.model_dump(exclude_none=True) if task.metadata else {}
    if archive_metadata:
        metadata_payload.update(archive_metadata)  # Add archive info if any
    if metadata_payload:
        result["metadata"] = metadata_payload

    return result


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.get("", response_model=TaskList)
async def list_tasks(
    project_id: str | None = Query(None, description="Filter by project ID"),
    status: TaskStatus | None = Query(None, description="Filter by status"),
):
    """List all tasks, optionally filtered by project or status."""
    projects = load_projects()

    # Filter projects
    if project_id:
        if project_id not in projects:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )
        project_ids = [project_id]
    else:
        project_ids = list(projects.keys())

    # Collect tasks from all projects
    all_tasks = []
    for pid in project_ids:
        project_path = Path(projects[pid]["path"])
        spec_dirs = get_spec_dirs(project_path)
        repo = project_repo(projects[pid])  # W5 (#218): target repo on every task
        for spec_dir in spec_dirs:
            task = spec_to_task(pid, spec_dir)
            task.repo = repo
            if status is None or task.status == status:
                all_tasks.append(task)

    # Sort by created_at descending
    all_tasks.sort(key=lambda t: t.created_at, reverse=True)

    return TaskList(tasks=all_tasks, total=len(all_tasks))


@router.get("/{task_id}")
async def get_task(task_id: str):
    """Get a specific task by ID.

    Returns full task details including execution progress and metadata
    (archivedAt, archivedInVersion).
    """
    # Parse task ID (format: project_id:spec_id)
    if ":" not in task_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid task ID format. Expected 'project_id:spec_id'",
        )

    project_id, spec_id = split_task_id(task_id)
    projects = load_projects()

    if project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    project_path = Path(projects[project_id]["path"])
    spec_dir = project_path / ".pfactory" / "specs" / spec_id

    if not spec_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )

    task = spec_to_task(project_id, spec_dir)
    return task_to_dict(task)


@router.post("", response_model=Task, status_code=status.HTTP_201_CREATED)
async def create_task(task: TaskCreate):
    """Create a new task (spec) in a project."""
    projects = load_projects()

    if task.project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    project_path = Path(projects[task.project_id]["path"])

    # Ensure .pfactory/specs exists
    specs_dir = project_path / ".pfactory" / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)

    # Generate spec ID and create directory
    spec_id = get_next_spec_id(project_path, task.title)
    spec_dir = specs_dir / spec_id
    spec_dir.mkdir()

    # Create initial spec.md
    spec_content = f"""# {task.title}

{task.description}

## Acceptance Criteria

- [ ] Feature works as described
- [ ] Tests pass
- [ ] Code review approved

## Notes

Created via PFactory Web UI
"""
    (spec_dir / "spec.md").write_text(spec_content)

    # Create requirements.json with metadata
    requirements: dict = {
        "title": task.title,
        "description": task.description,
        "created_at": datetime.now().isoformat(),
    }

    # Add metadata if provided
    if task.metadata:
        metadata_dict = task.metadata.model_dump(exclude_none=True)
        if metadata_dict:
            requirements["metadata"] = metadata_dict

            # Sync task_metadata.json for phase_config.py to read model/thinking settings
            # Also include selectedSkills so agent_service.py can inject skill context
            model_fields = ["model", "thinkingLevel", "isAutoProfile", "phaseModels", "phaseThinking", "mode", "selectedSkills"]
            task_metadata = {field: metadata_dict[field] for field in model_fields if field in metadata_dict}
            if task_metadata:
                (spec_dir / "task_metadata.json").write_text(json.dumps(task_metadata, indent=2))

    (spec_dir / "requirements.json").write_text(json.dumps(requirements, indent=2))

    return spec_to_task(task.project_id, spec_dir)


# --------------------------------------------------------------------------
# Clarification Endpoints
# --------------------------------------------------------------------------


def _resolve_task(task_id: str) -> tuple[str, str, Path, Path]:
    """Resolve task_id (projectId:specId) to project_id, spec_id, project_path, spec_dir.

    Raises HTTPException on invalid input or missing resources.
    """
    if ":" not in task_id:
        raise HTTPException(status_code=400, detail="Invalid task_id format (expected projectId:specId)")

    project_id, spec_id = split_task_id(task_id)
    projects = load_projects()

    if project_id not in projects:
        raise HTTPException(status_code=404, detail="Project not found")

    project_path = Path(projects[project_id]["path"])
    spec_dir = project_path / ".pfactory" / "specs" / spec_id

    if not spec_dir.exists():
        raise HTTPException(status_code=404, detail="Task spec not found")

    return project_id, spec_id, project_path, spec_dir


@router.post("/{task_id}/clarifications", response_model=ClarificationResponse)
async def generate_clarifications(task_id: str):
    """Generate clarification questions for a task using an LLM."""
    from ..services.clarification_service import generate_clarification_questions

    project_id, spec_id, project_path, spec_dir = _resolve_task(task_id)

    # Load task title and description from requirements.json
    req_file = spec_dir / "requirements.json"
    if not req_file.exists():
        return ClarificationResponse(skip=True, skipReason="No requirements found.")

    requirements = json.loads(req_file.read_text())
    title = requirements.get("title", "")
    description = requirements.get("description", "")

    result = await generate_clarification_questions(title, description, project_path)

    return ClarificationResponse(
        questions=[ClarificationQuestion(**q) for q in result.get("questions", [])],
        skip=result.get("skip", False),
        skipReason=result.get("skipReason", ""),
    )


@router.post("/{task_id}/clarifications/answers", response_model=Task)
async def submit_clarification_answers(task_id: str, request: ClarificationAnswersRequest):
    """Submit answers to clarification questions and append them to the task."""
    project_id, spec_id, project_path, spec_dir = _resolve_task(task_id)

    if not request.answers:
        return spec_to_task(project_id, spec_dir)

    # Build clarification appendix
    lines = ["\n\n## Clarifications\n"]
    for answer in request.answers:
        if answer.answer.strip():
            lines.append(f"**Q: {answer.question}**")
            lines.append(f"A: {answer.answer.strip()}\n")
    appendix = "\n".join(lines)

    # Update requirements.json description
    req_file = spec_dir / "requirements.json"
    if req_file.exists():
        requirements = json.loads(req_file.read_text())
        requirements["description"] = requirements.get("description", "") + appendix
        req_file.write_text(json.dumps(requirements, indent=2))

    # Append to spec.md
    spec_file = spec_dir / "spec.md"
    if spec_file.exists():
        content = spec_file.read_text()
        # Insert before ## Notes section if it exists, otherwise append
        if "\n## Notes\n" in content:
            content = content.replace("\n## Notes\n", f"{appendix}\n## Notes\n")
        else:
            content += appendix
        spec_file.write_text(content)

    return spec_to_task(project_id, spec_dir)


def _try_close_github_issue(project_path: Path, spec_dir: Path) -> None:
    """Try to close a linked GitHub issue. Logs but doesn't raise on failure."""
    try:
        req_file = spec_dir / "requirements.json"
        if not req_file.exists():
            return
        reqs = json.loads(req_file.read_text())
        # Check metadata.githubIssueNumber (set by task creation from issue)
        issue_number = None
        if isinstance(reqs.get("metadata"), dict):
            issue_number = reqs["metadata"].get("githubIssueNumber")
        # Also check githubIssue.number (set by import endpoint)
        if not issue_number and isinstance(reqs.get("githubIssue"), dict):
            issue_number = reqs["githubIssue"].get("number")
        if not issue_number:
            return
        from .github import run_gh_command

        result = run_gh_command(
            ["issue", "close", str(issue_number)],
            cwd=str(project_path),
        )
        if result["success"]:
            logger.info(f"Auto-closed GitHub issue #{issue_number}")
        else:
            logger.warning(
                f"Failed to auto-close GitHub issue #{issue_number}: {result.get('error', 'unknown')}"
            )
    except Exception as e:
        logger.warning(f"Error auto-closing GitHub issue: {e}")


class TaskStatusUpdate(BaseModel):
    """Model for updating only task status (for kanban)."""

    status: TaskStatus
    force: bool = False  # Skip validation (e.g., after successful merge)


@router.patch("/{task_id}/status", response_model=Task)
async def update_task_status(task_id: str, update: TaskStatusUpdate):
    """Update a task's status (for kanban drag-and-drop)."""
    if ":" not in task_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid task ID format",
        )

    project_id, spec_id = split_task_id(task_id)
    projects = load_projects()

    if project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    project_path = Path(projects[project_id]["path"])
    spec_dir = project_path / ".pfactory" / "specs" / spec_id

    if not spec_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )

    # Sync from worktree first to get latest progress
    plan, plan_file = get_plan_with_worktree_sync(project_path, spec_id)

    # Validate "done" status - ensure all subtasks are completed
    # Skip validation when force=True (e.g., after successful merge)
    if update.status == "done" and not update.force:
        is_valid, error_msg = validate_done_status(plan)
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_msg,
            )

    plan["status"] = update.status
    plan_file.write_text(json.dumps(plan, indent=2))

    # Auto-close linked GitHub issue when task is marked done
    if update.status == "done":
        _try_close_github_issue(project_path, spec_dir)

    return spec_to_task(project_id, spec_dir)


@router.put("/{task_id}", response_model=Task)
@router.patch("/{task_id}", response_model=Task)
async def update_task(task_id: str, update: TaskUpdate):
    """Update a task's metadata (supports both PUT and PATCH)."""
    if ":" not in task_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid task ID format",
        )

    project_id, spec_id = split_task_id(task_id)
    projects = load_projects()

    if project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    project_path = Path(projects[project_id]["path"])
    spec_dir = project_path / ".pfactory" / "specs" / spec_id

    if not spec_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )

    # Update spec.md if title/description changed
    if update.title or update.description:
        spec_file = spec_dir / "spec.md"
        current_content = spec_file.read_text() if spec_file.exists() else ""

        if update.title:
            # Replace first heading
            current_content = re.sub(
                r"^#\s+.+$",
                f"# {update.title}",
                current_content,
                count=1,
                flags=re.MULTILINE,
            )

        if update.description:
            # Replace description paragraph (second section after title)
            # Split by double newline: [title, description, rest...]
            sections = current_content.split('\n\n', 2)
            if len(sections) >= 2:
                sections[1] = update.description
                current_content = '\n\n'.join(sections)

        spec_file.write_text(current_content)

    # Update status in test_plan.json
    if update.status:
        # Sync from worktree first to get latest progress
        plan, plan_file = get_plan_with_worktree_sync(project_path, spec_id)

        # Validate "done" status - ensure all subtasks are completed
        if update.status == "done":
            is_valid, error_msg = validate_done_status(plan)
            if not is_valid:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=error_msg,
                )

        plan["status"] = update.status
        plan_file.write_text(json.dumps(plan, indent=2))

    # Update requirements.json with title, description, and metadata
    requirements_file = spec_dir / "requirements.json"
    if update.title or update.description or update.metadata:
        requirements = {}
        if requirements_file.exists():
            try:
                requirements = json.loads(requirements_file.read_text())
            except json.JSONDecodeError:
                pass

        if update.title:
            requirements["title"] = update.title
        if update.description:
            requirements["description"] = update.description

        if update.metadata:
            if "metadata" not in requirements:
                requirements["metadata"] = {}

            # Get all fields that were explicitly set in the request (including None/null)
            # model_dump(exclude_unset=True) returns only fields that were explicitly set
            metadata_dict = update.metadata.model_dump(exclude_unset=True)

            # Process each field: null values clear the field, non-null values update it
            for field, value in metadata_dict.items():
                if value is None:
                    # Explicitly clear this field
                    requirements["metadata"].pop(field, None)
                else:
                    # Update the field
                    requirements["metadata"][field] = value

            # Sync task_metadata.json for phase_config.py to read model/thinking settings
            task_metadata_file = spec_dir / "task_metadata.json"
            task_metadata = {}
            if task_metadata_file.exists():
                try:
                    task_metadata = json.loads(task_metadata_file.read_text())
                except json.JSONDecodeError:
                    pass

            # Update model-related fields that phase_config.py expects
            # Also include selectedSkills so agent_service.py can inject skill context
            model_fields = ["model", "thinkingLevel", "isAutoProfile", "phaseModels", "phaseThinking", "mode", "requireReviewBeforeCoding", "selectedSkills"]
            for field in model_fields:
                if field in metadata_dict:
                    if metadata_dict[field] is None:
                        # Clear field from task_metadata
                        task_metadata.pop(field, None)
                    else:
                        task_metadata[field] = metadata_dict[field]

            if task_metadata:
                task_metadata_file.write_text(json.dumps(task_metadata, indent=2))
            elif task_metadata_file.exists():
                # If all model fields were cleared, remove the file
                task_metadata_file.unlink()

        requirements_file.write_text(json.dumps(requirements, indent=2))

    return spec_to_task(project_id, spec_dir)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: str):
    """Delete a task (removes spec directory)."""
    if ":" not in task_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid task ID format",
        )

    project_id, spec_id = split_task_id(task_id)
    projects = load_projects()

    if project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    project_path = Path(projects[project_id]["path"])
    spec_dir = project_path / ".pfactory" / "specs" / spec_id

    if not spec_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )

    # Remove directory (recursively)
    import shutil

    shutil.rmtree(spec_dir)


class ApprovePlanRequest(BaseModel):
    """Request to approve a plan."""

    auto_restart: bool = Field(True, description="Auto-restart task after approval")


class RejectPlanRequest(BaseModel):
    """Request to reject a plan with feedback for the planner.

    Mirrors ApprovePlanRequest's shape but carries the operator's reason so the
    planner's next iteration sees it in the spec's review feedback log.
    """

    feedback: str | None = Field(
        None,
        description="Optional reason for rejection — gets recorded on the review state's feedback log.",
    )


@router.post("/{task_id}/approve-plan")
async def approve_plan(task_id: str, request: ApprovePlanRequest = ApprovePlanRequest()):
    """Approve a task's plan to allow coding to proceed.

    When a task is in plan_review status (waiting for human approval),
    this endpoint marks the plan as approved and optionally restarts the task.
    """
    if ":" not in task_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid task ID format",
        )

    project_id, spec_id = split_task_id(task_id)
    projects = load_projects()

    if project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    project_path = Path(projects[project_id]["path"])
    spec_dir = project_path / ".pfactory" / "specs" / spec_id

    if not spec_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )

    # Import ReviewState from backend
    import sys

    backend_path = Path(__file__).parent.parent.parent.parent / "backend"
    if str(backend_path) not in sys.path:
        sys.path.insert(0, str(backend_path))

    from review import ReviewState

    # Approve the plan
    review_state = ReviewState.load(spec_dir)
    review_state.approve(spec_dir, approved_by="web_user")

    # Update test_plan.json status back to in_progress
    plan_file = spec_dir / "test_plan.json"
    plan_updated = False
    if plan_file.exists():
        try:
            logger.info(f"[ApprovePlan] Reading plan file: {plan_file}")
            plan = json.loads(plan_file.read_text())
            logger.info(f"[ApprovePlan] Current status: {plan.get('status')}, planStatus: {plan.get('planStatus')}, reviewReason: {plan.get('reviewReason')}")

            # Update BOTH status and planStatus fields
            plan["status"] = "in_progress"
            plan["planStatus"] = "in_progress"
            plan.pop("reviewReason", None)

            plan_file.write_text(json.dumps(plan, indent=2))
            plan_updated = True
            logger.info("[ApprovePlan] Updated plan file - status: in_progress, planStatus: in_progress")
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"[ApprovePlan] Failed to update plan file: {e}")
    else:
        logger.warning(f"[ApprovePlan] Plan file does not exist: {plan_file}")

    # Emit status change via WebSocket
    from ..websockets.events import emit_task_status

    await emit_task_status(task_id, "in_progress")

    auto_restarted = False

    # Auto-restart if requested
    if request.auto_restart:
        try:
            from ..services.agent_service import get_agent_service

            agent_service = get_agent_service()

            # Clean up stale spec creation process if still tracked as running.
            # The spec_runner process may have exited but the monitor may not have
            # cleaned up running_tasks (e.g., if the process hung or monitor failed).
            if agent_service.is_running(task_id):
                logger.info(f"[ApprovePlan] Cleaning up stale spec creation process for {task_id}")
                try:
                    await agent_service.stop_task(task_id)
                except Exception as stop_err:
                    logger.warning(f"[ApprovePlan] Failed to stop stale process: {stop_err}")
                    # Force-remove from running_tasks as fallback
                    agent_service.running_tasks.pop(task_id, None)

            # Read mode from task_metadata.json
            task_metadata_file = spec_dir / "task_metadata.json"
            mode = "full"
            if task_metadata_file.exists():
                try:
                    metadata = json.loads(task_metadata_file.read_text())
                    mode = metadata.get("mode", "full")
                except (json.JSONDecodeError, OSError):
                    pass

            await agent_service.start_task_execution(
                task_id=task_id,
                project_path=project_path,
                spec_id=spec_id,
                auto_continue=True,
                mode=mode,
                force=True,  # Bypass approval check since plan was manually approved
            )
            auto_restarted = True
        except Exception as e:
            # If auto-restart fails, still return success for approval
            logger.warning(f"Auto-restart failed for {task_id}: {e}")

    return {
        "success": True,
        "task_id": task_id,
        "message": "Plan approved" + (" and task restarted" if auto_restarted else ""),
        "autoRestarted": auto_restarted,
    }


@router.post("/{task_id}/reject-plan")
async def reject_plan(task_id: str, request: RejectPlanRequest = RejectPlanRequest()):
    """Reject a task's plan and send the planner back to iterate.

    Used by the human-review checkpoint when the implementation plan needs
    rework. The optional ``feedback`` field is appended to the spec's
    review-state feedback log so the planner's next pass picks it up.
    """
    if ":" not in task_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid task ID format"
        )

    project_id, spec_id = split_task_id(task_id)
    projects = load_projects()
    if project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    project_path = Path(projects[project_id]["path"])
    spec_dir = project_path / ".pfactory" / "specs" / spec_id
    if not spec_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    # Import ReviewState the same way approve_plan does (sys.path shim
    # because the web-server doesn't have ``backend`` on its PYTHONPATH
    # in every install layout).
    import sys

    backend_path = Path(__file__).parent.parent.parent.parent / "backend"
    if str(backend_path) not in sys.path:
        sys.path.insert(0, str(backend_path))

    from review.state import ReviewState

    review_state = ReviewState.load(spec_dir)
    review_state.reject(spec_dir)
    if request.feedback:
        review_state.add_feedback(request.feedback, spec_dir=spec_dir)

    # Mirror approve_plan's bookkeeping: flip the plan back to "needs work"
    # so the next planner pass sees a clean slate.
    plan_file = spec_dir / "test_plan.json"
    if plan_file.exists():
        try:
            plan = json.loads(plan_file.read_text())
            plan["status"] = "rejected"
            plan["planStatus"] = "rejected"
            if request.feedback:
                plan["reviewReason"] = request.feedback
            plan_file.write_text(json.dumps(plan, indent=2))
        except (OSError, json.JSONDecodeError) as exc:
            # Plan file unreadable — review state was already updated, so
            # the reject took effect even if the bookkeeping fails. Log
            # and continue.

            logger.warning(
                f"[RejectPlan] couldn't update test_plan.json: {exc}"
            )

    return {
        "success": True,
        "task_id": task_id,
        "feedback_recorded": bool(request.feedback),
    }


@router.get("/{task_id}/qa-report")
async def get_qa_report(task_id: str):
    """Return the QA report markdown for a task.

    Tasks that have completed the QA phase have a ``qa_report.md`` written
    to their spec dir. This endpoint surfaces that content + a few derived
    fields so an MCP client can show it inline without separately reading
    the filesystem.
    """
    if ":" not in task_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid task ID format"
        )

    project_id, spec_id = split_task_id(task_id)
    projects = load_projects()
    if project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    project_path = Path(projects[project_id]["path"])
    spec_dir = project_path / ".pfactory" / "specs" / spec_id
    qa_report_file = spec_dir / "qa_report.md"

    if not qa_report_file.exists():
        # 404 is the right answer — clients should treat "no report yet"
        # as "task hasn't reached QA" rather than a hard error.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="QA report not found — task may not have reached the QA phase yet",
        )

    try:
        content = qa_report_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not read QA report: {exc}",
        ) from exc

    return {
        "task_id": task_id,
        "spec_id": spec_id,
        "exists": True,
        "size_bytes": qa_report_file.stat().st_size,
        "modified_at": qa_report_file.stat().st_mtime,
        "content": content,
    }


@router.get("/{task_id}/agent-console/sse")
async def stream_agent_console(task_id: str):
    """Server-Sent Events stream of the running agent's console output.

    V1.1 strategy: read ``build-progress.txt`` from the spec dir and emit
    deltas as they appear. This is the same file the portal's progress
    sidebar polls — it covers the 80% case (the user wants to *watch* an
    agent without needing the rmux pane).

    The richer rmux-driven SSE re-broadcast (which would let an MCP client
    drive a live terminal) is a follow-up — it depends on the rmux bridge
    being enabled, which isn't a given on all deployments. The poll-based
    fallback here works regardless.

    Client behaviour: subscribe to the stream, receive ``data:`` events,
    detect ``event: done`` when the agent finishes.
    """
    if ":" not in task_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid task ID format"
        )

    project_id, spec_id = split_task_id(task_id)
    projects = load_projects()
    if project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    project_path = Path(projects[project_id]["path"])
    spec_dir = project_path / ".pfactory" / "specs" / spec_id
    if not spec_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    progress_file = spec_dir / "build-progress.txt"

    async def event_generator():
        """Yield SSE-formatted deltas from build-progress.txt.

        Sleeps 1s between polls. Emits an ``event: done`` line + closes
        when the file stops growing for 30s (heuristic: agent finished
        or the file isn't being written anymore). Caps total stream
        duration at 30 minutes to avoid leaking connections from
        misbehaving clients.
        """
        import asyncio

        max_duration_s = 30 * 60
        idle_timeout_s = 30
        poll_interval_s = 1.0
        start = asyncio.get_event_loop().time()
        last_size = 0
        last_change = start

        # Emit a kickoff event so the client knows the stream is live
        # even before there's content (useful when the agent hasn't
        # started writing yet).
        yield f"event: open\ndata: {json.dumps({'task_id': task_id, 'spec_id': spec_id})}\n\n"

        try:
            while True:
                now = asyncio.get_event_loop().time()
                if now - start > max_duration_s:
                    yield 'event: done\ndata: {"reason": "max-duration"}\n\n'
                    return

                if progress_file.exists():
                    current_size = progress_file.stat().st_size
                    if current_size > last_size:
                        with progress_file.open("rb") as fh:
                            fh.seek(last_size)
                            chunk = fh.read(current_size - last_size)
                        last_size = current_size
                        last_change = now
                        # SSE data lines: encode each newline as its own
                        # ``data:`` so multi-line chunks render correctly
                        # in standard EventSource clients.
                        text = chunk.decode("utf-8", errors="replace")
                        for line in text.splitlines():
                            yield f"data: {line}\n"
                        yield "\n"  # blank line terminates the event
                    elif now - last_change > idle_timeout_s:
                        yield 'event: done\ndata: {"reason": "idle-timeout"}\n\n'
                        return
                else:
                    # File doesn't exist yet — keep waiting, may appear
                    # once the agent starts writing.
                    if now - last_change > idle_timeout_s:
                        yield 'event: done\ndata: {"reason": "no-progress-file"}\n\n'
                        return

                await asyncio.sleep(poll_interval_s)
        except asyncio.CancelledError:
            # Client disconnected — fastapi cancels the generator.
            return

    from fastapi.responses import StreamingResponse

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/{task_id}/plan-html")
async def get_plan_html(task_id: str):
    """Generate and return HTML view of the implementation plan.

    Creates a temporary HTML file with nicely formatted plan for review.
    """
    if ":" not in task_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid task ID format",
        )

    project_id, spec_id = split_task_id(task_id)
    projects = load_projects()

    if project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    project_path = Path(projects[project_id]["path"])
    spec_dir = project_path / ".pfactory" / "specs" / spec_id

    if not spec_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )

    # Import HTML generator from backend
    import sys

    backend_path = Path(__file__).parent.parent.parent.parent / "backend"
    if str(backend_path) not in sys.path:
        sys.path.insert(0, str(backend_path))

    try:
        from review.html_generator import generate_html_plan_review

        # Generate HTML file
        html_file = generate_html_plan_review(spec_dir)

        # Return the HTML content
        from fastapi.responses import HTMLResponse

        return HTMLResponse(content=html_file.read_text(), status_code=200)

    except ImportError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"HTML generator not available: {str(e)}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate plan HTML: {str(e)}",
        )


@router.get("/{task_id}/logs")
async def get_task_logs(task_id: str):
    """Get logs for a task.

    Returns phase-based logs from task_logs.json if available,
    checking both main spec dir and worktree.
    """

    logger.info(f"[GetTaskLogs] Called with task_id: {task_id}")

    if ":" not in task_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid task ID format",
        )

    project_id, spec_id = split_task_id(task_id)
    logger.info(f"[GetTaskLogs] project_id={project_id}, spec_id={spec_id}")

    projects = load_projects()

    if project_id not in projects:
        logger.error(f"[GetTaskLogs] Project not found: {project_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    project_path = Path(projects[project_id]["path"])
    logger.info(f"[GetTaskLogs] project_path: {project_path}")

    spec_dir = project_path / ".pfactory" / "specs" / spec_id
    worktree_spec_dir = (
        project_path
        / ".pfactory"
        / "worktrees"
        / "tasks"
        / spec_id
        / ".pfactory"
        / "specs"
        / spec_id
    )

    logger.info(f"[GetTaskLogs] Checking spec_dir: {spec_dir}")
    logger.info(f"[GetTaskLogs] Checking worktree_spec_dir: {worktree_spec_dir}")

    # Check for task_logs.json (phase-based logs) - prefer worktree if exists
    task_logs_file = None
    for check_dir in [worktree_spec_dir, spec_dir]:
        candidate = check_dir / "task_logs.json"
        logger.info(f"[GetTaskLogs] Checking {candidate}, exists: {candidate.exists()}")
        if candidate.exists():
            task_logs_file = candidate
            logger.info(f"[GetTaskLogs] Found task_logs.json at: {task_logs_file}")
            break

    if task_logs_file:
        try:
            task_logs = json.loads(task_logs_file.read_text())
            logger.info(f"[GetTaskLogs] Successfully loaded task_logs.json, has phases: {'phases' in task_logs}")
            result = {
                "specId": task_logs.get("spec_id", spec_id),
                "createdAt": task_logs.get("created_at"),
                "updatedAt": task_logs.get("updated_at"),
                "phases": task_logs.get("phases", {}),
            }

            # Also include build-progress.txt if it exists (detailed human-readable logs)
            for check_dir in [worktree_spec_dir, spec_dir]:
                build_progress = check_dir / "build-progress.txt"
                if build_progress.exists():
                    result["buildProgress"] = build_progress.read_text()
                    break

            logger.info(f"[GetTaskLogs] Returning phase-based logs with {len(result.get('phases', {}))} phases")
            return result
        except json.JSONDecodeError as e:
            logger.error(f"[GetTaskLogs] JSON decode error: {e}")
            pass
    else:
        logger.warning("[GetTaskLogs] No task_logs.json found, returning fallback format")

    # Fallback: Collect logs from legacy sources
    logs = []

    # Implementation plan logs
    plan_file = spec_dir / "test_plan.json"
    if plan_file.exists():
        try:
            plan = json.loads(plan_file.read_text())
            if "logs" in plan:
                logs.extend(plan["logs"])
        except json.JSONDecodeError:
            pass

    # QA report
    qa_report = spec_dir / "qa_report.md"
    if qa_report.exists():
        logs.append({
            "type": "qa_report",
            "content": qa_report.read_text(),
            "timestamp": datetime.fromtimestamp(qa_report.stat().st_mtime).isoformat(),
        })

    result = {"logs": logs, "total": len(logs)}

    # Include build-progress.txt if it exists
    for check_dir in [worktree_spec_dir, spec_dir]:
        build_progress = check_dir / "build-progress.txt"
        if build_progress.exists():
            result["buildProgress"] = build_progress.read_text()
            break

    return result


@router.post("/{task_id}/logs/watch")
async def watch_task_logs(task_id: str):
    """
    Start watching task logs (stub endpoint for frontend compatibility).

    Note: Log streaming is handled via WebSocket, this endpoint is a no-op
    that prevents 404 errors in the frontend.
    """
    return {"success": True, "message": "Log watching handled via WebSocket"}


@router.post("/{task_id}/logs/unwatch")
async def unwatch_task_logs(task_id: str):
    """
    Stop watching task logs (stub endpoint for frontend compatibility).

    Note: Log streaming is handled via WebSocket, this endpoint is a no-op
    that prevents 404 errors in the frontend.
    """
    return {"success": True, "message": "Log unwatching handled via WebSocket"}


# ============================================
# Worktree Merge Routes
# ============================================


class CreatePRFromTaskOptions(BaseModel):
    title: str | None = None
    body: str | None = None
    draft: bool = False
    baseBranch: str | None = None
    targetRepo: str | None = None  # "owner/repo" for cross-fork PRs


class WorktreeMergeOptions(BaseModel):
    noCommit: bool | None = False


class ConflictResolveOptions(BaseModel):
    """Options for conflict resolution."""

    useAI: bool = True
    strategy: str | None = None


@router.get("/{task_id}/worktree/merge-preview")
async def get_worktree_merge_preview(task_id: str):
    """
    Preview what will happen when merging the worktree.
    Returns conflict info and files that will be merged.
    """
    import subprocess

    # #335 sanitize the caller-supplied task id before it is joined onto a root
    try:
        task_id = safe_spec_component(task_id, field="task_id")
    except ValueError:
        return {"success": False, "error": "invalid task id"}

    # Find the task's spec directory and worktree
    projects_data_dir = get_data_dir()
    projects_file = projects_data_dir / "projects.json"

    if not projects_file.exists():
        return {"success": False, "error": "No projects configured"}

    projects_data = json.loads(projects_file.read_text())

    # Find the task across all projects
    task_info = None
    project_path = None

    # Handle both dict format (id -> project) and list format
    if isinstance(projects_data, dict):
        projects = list(projects_data.values())
    else:
        projects = projects_data

    for project in projects:
        if isinstance(project, str):
            project_path = Path(project)
        else:
            project_path = Path(project.get("path", ""))

        spec_dir = project_path / ".pfactory" / "specs" / task_id

        if spec_dir.exists():
            # Found the task
            impl_plan = spec_dir / "test_plan.json"
            if impl_plan.exists():
                task_info = json.loads(impl_plan.read_text())
            break
    else:
        return {"success": False, "error": f"Task {task_id} not found"}

    # Find the worktree
    worktree_path = project_path / ".pfactory" / "worktrees" / "tasks" / task_id

    if not worktree_path.exists():
        return {"success": False, "error": "No worktree found for this task"}

    # Get the branch name from the worktree
    branch_result = run_git_command(["rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree_path)
    if not branch_result["success"]:
        return {"success": False, "error": "Could not determine worktree branch"}
    worktree_branch = branch_result["output"]

    # Get the base branch (usually develop or main)
    base_result = run_git_command(["rev-parse", "--abbrev-ref", "HEAD"], cwd=project_path)
    base_branch = base_result["output"] if base_result["success"] else "develop"

    # Get list of changed files
    changed_files = []
    diff_result = run_git_command(["diff", "--name-status", f"{base_branch}...{worktree_branch}"], cwd=project_path)
    if diff_result["success"]:
        for line in diff_result["output"].split("\n"):
            if line:
                parts = line.split("\t")
                if len(parts) >= 2:
                    status = parts[0]
                    filename = parts[1]
                    changed_files.append({
                        "path": filename,
                        "status": "added" if status == "A" else "modified" if status == "M" else "deleted" if status == "D" else status
                    })

    # Check for potential conflicts using merge-tree (dry run)
    # Git 2.38+ uses new merge-tree format with --write-tree mode by default
    has_conflicts = False
    conflicting_files = []
    try:
        # Use --write-tree explicitly for git 2.38+ behavior
        result = subprocess.run(
            ["git", "merge-tree", "--write-tree", base_branch, worktree_branch],
            cwd=project_path,
            capture_output=True,
            text=True,
        )
        # Git 2.38+: Return code 1 means conflicts exist
        # stdout format: "<tree_oid>\nCONFLICT (type): description"
        if result.returncode == 1:
            has_conflicts = True
            # Parse CONFLICT lines to get conflicting files
            for line in result.stdout.split('\n'):
                if line.startswith('CONFLICT'):
                    # Extract filename from "CONFLICT (content): Merge conflict in path/file"
                    if ' in ' in line:
                        file_path = line.split(' in ')[-1].strip()
                        if file_path:
                            conflicting_files.append(file_path)
        # Fallback: Check for CONFLICT keyword even on return code 0
        # (some edge cases may not set return code correctly)
        elif "CONFLICT" in result.stdout or "CONFLICT" in result.stderr:
            has_conflicts = True
            for line in (result.stdout + result.stderr).split('\n'):
                if line.startswith('CONFLICT') and ' in ' in line:
                    file_path = line.split(' in ')[-1].strip()
                    if file_path:
                        conflicting_files.append(file_path)
        # Legacy fallback: Check for conflict markers (older git versions < 2.38)
        elif "<<<<<<" in result.stdout:
            has_conflicts = True
    except subprocess.CalledProcessError as e:
        # Command failed - check output for conflict indicators
        output = (e.stdout or '') + (e.stderr or '')
        if "CONFLICT" in output or "<<<<<<" in output:
            has_conflicts = True
            for line in output.split('\n'):
                if line.startswith('CONFLICT') and ' in ' in line:
                    file_path = line.split(' in ')[-1].strip()
                    if file_path:
                        conflicting_files.append(file_path)

    # Filter out gitignored files from conflict list (e.g. build artifacts)
    if conflicting_files:
        try:
            result = subprocess.run(
                ["git", "check-ignore"] + conflicting_files,
                cwd=project_path,
                capture_output=True,
                text=True,
            )
            ignored = set(result.stdout.strip().splitlines())
            conflicting_files = [f for f in conflicting_files if f not in ignored]
            if not conflicting_files:
                has_conflicts = False
        except Exception:
            pass  # If check-ignore fails, keep the original list

    # Check if there's an active merge in progress (MERGE_HEAD exists)
    # This is different from the merge-tree dry run above - this means a real merge
    # is in progress with unresolved conflict markers in files
    merge_in_progress = False
    merge_head_file = project_path / ".git" / "MERGE_HEAD"
    if merge_head_file.exists():
        merge_in_progress = True

    # Get commit counts
    commits_ahead = 0
    ahead_result = run_git_command(["rev-list", "--count", f"{base_branch}..{worktree_branch}"], cwd=project_path)
    if ahead_result["success"]:
        try:
            commits_ahead = int(ahead_result["output"])
        except ValueError:
            commits_ahead = 0

    commits_behind = 0
    behind_result = run_git_command(["rev-list", "--count", f"{worktree_branch}..{base_branch}"], cwd=project_path)
    if behind_result["success"]:
        try:
            commits_behind = int(behind_result["output"])
        except ValueError:
            commits_behind = 0

    # Detect uncommitted changes in the main project that could conflict
    uncommitted_files = []
    uncommitted_conflicting_files = []
    # Get uncommitted files in main project
    status_result = run_git_command(["status", "--porcelain"], cwd=project_path)
    if status_result["success"]:
        for line in status_result["output"].split("\n"):
            if line:
                # Format: "XY filename" or "XY original -> renamed"
                parts = line[3:].split(" -> ")
                filename = parts[-1].strip()  # Use renamed name if present
                if filename:
                    uncommitted_files.append(filename)

        # Get files modified in task branch (for conflict detection)
        if uncommitted_files:
            task_files_result = run_git_command(
                ["diff", "--name-only", f"{base_branch}...{worktree_branch}"],
                cwd=project_path,
            )
            if task_files_result["success"]:
                task_files = set(task_files_result["output"].split('\n'))
                # Find files that overlap (uncommitted in main AND modified in task)
                uncommitted_conflicting_files = list(set(uncommitted_files) & task_files)

                # Filter out gitignored files (e.g. build artifacts)
                # check-ignore prints matched paths to stdout and exits 1 when
                # none match, so we need stdout regardless of exit code — kept raw.
                if uncommitted_conflicting_files:
                    try:
                        ignored_result = subprocess.run(
                            ["git", "check-ignore"] + uncommitted_conflicting_files,
                            cwd=project_path,
                            capture_output=True,
                            text=True,
                        )
                        ignored = set(ignored_result.stdout.strip().splitlines())
                        uncommitted_conflicting_files = [f for f in uncommitted_conflicting_files if f not in ignored]
                    except Exception:
                        pass

    # Run semantic conflict detection using backend merge system
    semantic_conflicts = []
    semantic_stats = {
        "totalFiles": len(changed_files),
        "conflictFiles": 0,
        "totalConflicts": 0,
        "autoMergeable": 0,
        "aiResolved": 0,
        "humanRequired": 0,
    }

    try:
        from ..services.conflict_service import get_conflict_service

        conflict_service = get_conflict_service(project_path)
        semantic_result = await conflict_service.detect_conflicts(
            task_id=task_id,
            worktree_path=worktree_path,
            base_branch=base_branch,
        )

        if semantic_result.get("success"):
            semantic_conflicts = semantic_result.get("conflicts", [])
            semantic_stats = semantic_result.get("stats", semantic_stats)

    except Exception as e:
        # Log but don't fail - semantic detection is optional enhancement
        logger.warning(f"Semantic conflict detection failed: {e}")

    # Merge results: combine git conflicts with semantic conflicts
    all_conflicts = semantic_conflicts.copy()

    # Determine overall merge status
    total_conflicts = len(all_conflicts)
    auto_mergeable = sum(1 for c in all_conflicts if c.get("canAutoMerge", False))
    has_any_conflicts = has_conflicts or total_conflicts > 0
    can_merge = not has_conflicts and (total_conflicts == 0 or total_conflicts == auto_mergeable)

    # Build preview response with all merge information
    preview_data = {
        "files": [f["path"] for f in changed_files],
        "conflicts": all_conflicts,  # Semantic conflicts from merge system
        "summary": {
            "totalFiles": len(changed_files),
            "conflictFiles": semantic_stats.get("conflictFiles", 0),
            "totalConflicts": total_conflicts,
            "autoMergeable": auto_mergeable,
            "aiResolved": semantic_stats.get("aiResolved", 0),
            "humanRequired": total_conflicts - auto_mergeable,
        },
        "gitConflicts": {
            "hasConflicts": has_conflicts,
            "commitsAhead": commits_ahead,
            "commitsBehind": commits_behind,
            "conflictingFiles": conflicting_files,
            "needsRebase": commits_behind > 0,
            "baseBranch": base_branch,
            "specBranch": worktree_branch,
            "mergeInProgress": merge_in_progress,
        },
        "uncommittedChanges": {
            "hasChanges": len(uncommitted_files) > 0,
            "files": uncommitted_files,
            "count": len(uncommitted_files),
            "conflictingFiles": uncommitted_conflicting_files,
            "hasConflicts": len(uncommitted_conflicting_files) > 0,
        } if uncommitted_files else None,
    }

    return {
        "success": True,
        "data": {
            "canMerge": can_merge,
            "hasConflicts": has_any_conflicts,
            "changedFiles": changed_files,
            "conflicts": all_conflicts,
            "stats": preview_data["summary"],
            "gitConflicts": preview_data["gitConflicts"],
            "worktreeBranch": worktree_branch,
            "baseBranch": base_branch,
            "preview": preview_data,
        },
    }


@router.post("/{task_id}/worktree/resolve-conflicts")
async def resolve_worktree_conflicts(task_id: str, options: ConflictResolveOptions = None):
    """
    Resolve merge conflicts between the worktree branch and the base branch using AI.

    This endpoint performs a real git merge and resolves any conflict markers
    using AI. Process:
    1. Gets the worktree branch name
    2. Starts a git merge of the worktree branch into the current branch
    3. If conflicts arise, uses AI to resolve each conflicted file
    4. Stages resolved files and commits the merge
    """

    if options is None:
        options = ConflictResolveOptions()

    # Parse task_id to get spec_id
    # task_id could be "project_id:spec_id" or just "spec_id"
    if ":" in task_id:
        project_id, spec_id = split_task_id(task_id)
        # Look up project path
        projects_file = get_data_file("projects.json")
        if not projects_file.exists():
            return {"success": False, "error": "Projects file not found"}

        projects_data = json.loads(projects_file.read_text())

        # Handle dict format where keys are project IDs
        if isinstance(projects_data, dict):
            project = projects_data.get(project_id)
            if not project:
                return {"success": False, "error": f"Project not found: {project_id}"}
            project_path = Path(project["path"])
        else:
            # Handle list format where each item has an "id" field
            project = None
            for p in projects_data:
                if isinstance(p, dict) and p.get("id") == project_id:
                    project = p
                    break
            if not project:
                return {"success": False, "error": f"Project not found: {project_id}"}
            project_path = Path(project["path"])
    else:
        return {"success": False, "error": "Task ID must include project ID (format: project_id:spec_id)"}

    spec_dir = project_path / ".pfactory" / "specs" / spec_id
    worktree_path = project_path / ".pfactory" / "worktrees" / "tasks" / spec_id

    if not spec_dir.exists():
        return {"success": False, "error": f"Task {task_id} not found"}

    if not worktree_path.exists():
        return {"success": False, "error": "No worktree found for this task"}

    # Get worktree branch name
    branch_result = run_git_command(["rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree_path)
    if not branch_result["success"]:
        return {"success": False, "error": "Could not determine worktree branch"}
    worktree_branch = branch_result["output"]

    # Check if a merge is already in progress
    merge_head = project_path / ".git" / "MERGE_HEAD"
    if merge_head.exists():
        logger.info(f"Merge already in progress for {task_id}, resolving existing conflicts")
    else:
        # Start the git merge (allow conflicts)
        logger.info(f"Starting git merge of {worktree_branch} into current branch for task {task_id}")
        merge_result = subprocess.run(
            ["git", "merge", worktree_branch, "--no-commit", "--no-ff"],
            cwd=project_path,
            capture_output=True,
            text=True,
        )

        if merge_result.returncode == 0:
            # Clean merge, no conflicts - commit it
            logger.info(f"Clean merge for {task_id}, committing")
            run_git_command(
                ["commit", "-m", f"Merge {worktree_branch} into current branch"],
                cwd=project_path,
            )
            return {
                "success": True,
                "data": {
                    "resolved": [],
                    "remaining": [],
                    "stats": {"message": "Clean merge - no conflicts"},
                },
            }
        elif merge_result.returncode != 1 and "CONFLICT" not in merge_result.stdout:
            # Unexpected error (not a conflict)
            logger.error(f"Git merge failed unexpectedly: {merge_result.stderr}")
            return {
                "success": False,
                "error": f"Git merge failed: {merge_result.stderr.strip()}",
            }
        else:
            logger.info(f"Merge has conflicts for {task_id}, resolving with AI")

    if not options.useAI:
        return {
            "success": False,
            "error": "Conflicts detected but AI resolution is disabled",
            "data": {
                "resolved": [],
                "remaining": [],
                "stats": {"message": "Merge started with conflicts, AI resolution disabled"},
            },
        }

    # Get list of conflicted files
    conflicted_files = []
    diff_u_result = run_git_command(["diff", "--name-only", "--diff-filter=U"], cwd=project_path)
    if diff_u_result["success"] and diff_u_result["output"]:
        conflicted_files = [f for f in diff_u_result["output"].split('\n') if f]

    # Fallback: check git status for unmerged files
    if not conflicted_files:
        status_result = run_git_command(["status", "--porcelain"], cwd=project_path)
        if status_result["success"]:
            for line in status_result["output"].split('\n'):
                if line and line[:2] in ('UU', 'AA', 'DD', 'AU', 'UA', 'DU', 'UD'):
                    file_path = line[3:].strip()
                    if file_path:
                        conflicted_files.append(file_path)

    if not conflicted_files:
        # No conflicts found - the merge may have already been resolved
        # Try to commit
        run_git_command(["commit", "--no-edit"], cwd=project_path)
        return {
            "success": True,
            "data": {
                "resolved": [],
                "remaining": [],
                "stats": {"message": "No conflicted files found"},
            },
        }

    logger.info(f"Found {len(conflicted_files)} conflicted files: {conflicted_files}")

    # Resolve each conflicted file using AI
    resolved_files = []
    failed_files = []

    from ..services.conflict_service import get_conflict_service

    conflict_service = get_conflict_service(project_path)

    for file_path in conflicted_files:
        try:
            full_path = project_path / file_path
            if not full_path.exists():
                logger.warning(f"Conflicted file not found: {full_path}")
                failed_files.append({"file": file_path, "error": "File not found"})
                continue

            content = full_path.read_text()

            if "<<<<<<< " not in content:
                logger.info(f"File {file_path} has no conflict markers, staging")
                run_git_command(["add", file_path], cwd=project_path)
                resolved_files.append(file_path)
                continue

            # Use AI to resolve conflict markers
            merge_result = await conflict_service.resolve_conflict_markers(
                file_path=file_path,
                content=content,
            )

            if merge_result.get("success"):
                resolved_content = merge_result.get("content", "")

                # Clean up any remaining markers
                if (
                    "<<<<<<< " in resolved_content
                    or "=======" in resolved_content
                    or ">>>>>>> " in resolved_content
                ):
                    logger.warning(f"AI resolution for {file_path} still has markers, cleaning up")
                    resolved_content = _clean_conflict_markers(resolved_content)

                full_path.write_text(resolved_content)
                logger.info(f"Wrote resolved content to {full_path}")

                add_result = run_git_command(["add", file_path], cwd=project_path)
                if add_result["success"]:
                    resolved_files.append(file_path)
                    logger.info(f"Staged resolved file: {file_path}")
                else:
                    logger.warning(f"Failed to stage {file_path}: {add_result['error']}")
                    failed_files.append(
                        {"file": file_path, "error": "Failed to stage resolved file"}
                    )
            else:
                error_msg = merge_result.get("error", "AI resolution failed")
                logger.error(f"AI resolution failed for {file_path}: {error_msg}")
                failed_files.append({"file": file_path, "error": "AI conflict resolution failed"})

        except Exception:
            logger.exception(f"Failed to resolve {file_path}")
            failed_files.append({"file": file_path, "error": "Failed to resolve conflict"})

    if failed_files:
        return {
            "success": len(resolved_files) > 0,
            "data": {
                "resolved": resolved_files,
                "failed": failed_files,
                "remaining": [f["file"] for f in failed_files],
                "stats": {"message": f"Resolved {len(resolved_files)} files, {len(failed_files)} failed"},
            },
            "error": f"{len(failed_files)} files could not be resolved",
        }

    # All conflicts resolved - commit the merge
    try:
        commit_msg = f"Merge {worktree_branch} (AI-resolved conflicts)"
        commit_result = run_git_command(["commit", "-m", commit_msg], cwd=project_path)
        if not commit_result["success"]:
            logger.warning(f"Merge commit failed: {commit_result['error']}")
            return {
                "success": True,
                "data": {
                    "resolved": resolved_files,
                    "remaining": [],
                    "stats": {
                        "message": f"Resolved {len(resolved_files)} files but the merge commit failed"
                    },
                },
            }
    except Exception:
        logger.exception("Merge commit failed")

    return {
        "success": True,
        "data": {
            "resolved": resolved_files,
            "remaining": [],
            "stats": {"message": f"Successfully resolved and merged {len(resolved_files)} conflicting files"},
        },
    }


@router.post("/{task_id}/worktree/resolve-uncommitted")
async def resolve_uncommitted_conflicts(task_id: str):
    """
    Resolve conflicts between uncommitted local changes and task branch changes using AI.

    This endpoint:
    1. Stashes uncommitted changes in the main project
    2. For each conflicting file, gets the stash, task branch, and base versions
    3. Uses AI to intelligently merge the three versions
    4. Writes merged content to working directory
    5. Drops the stash after successful merge
    """

    logger.info(f"Resolving uncommitted conflicts for task {task_id}")

    # #335 sanitize the caller-supplied task id before it is joined onto a root
    try:
        task_id = safe_spec_component(task_id, field="task_id")
    except ValueError:
        return {"success": False, "error": "invalid task id"}

    # Find the task's project
    projects_data_dir = get_data_dir()
    projects_file = projects_data_dir / "projects.json"

    if not projects_file.exists():
        return {"success": False, "error": "No projects configured"}

    projects_data = json.loads(projects_file.read_text())

    # Find the task across all projects
    project_path = None
    worktree_path = None

    if isinstance(projects_data, dict):
        projects = list(projects_data.values())
    else:
        projects = projects_data

    for project in projects:
        if isinstance(project, str):
            project_path = Path(project)
        else:
            project_path = Path(project.get("path", ""))

        spec_dir = project_path / ".pfactory" / "specs" / task_id

        if spec_dir.exists():
            worktree_path = project_path / ".pfactory" / "worktrees" / "tasks" / task_id
            break
    else:
        return {"success": False, "error": f"Task {task_id} not found"}

    if not worktree_path or not worktree_path.exists():
        return {"success": False, "error": "No worktree found for this task"}

    # Get branch names
    base_result = run_git_command(["rev-parse", "--abbrev-ref", "HEAD"], cwd=project_path)
    spec_result = run_git_command(["rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree_path)
    if not base_result["success"] or not spec_result["success"]:
        return {"success": False, "error": "Could not determine branches"}
    base_branch = base_result["output"]
    spec_branch = spec_result["output"]

    # Get uncommitted files that conflict with task
    uncommitted_files = []
    status_result = run_git_command(["status", "--porcelain"], cwd=project_path)
    if not status_result["success"]:
        return {"success": False, "error": "Could not get uncommitted files"}
    for line in status_result["output"].split("\n"):
        if line:
            parts = line[3:].split(" -> ")
            filename = parts[-1].strip()
            if filename:
                uncommitted_files.append(filename)

    # Get task branch files (returncode intentionally ignored — kept raw so an
    # empty/failed diff still yields the same set semantics as before)
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base_branch}...{spec_branch}"],
            cwd=project_path,
            capture_output=True,
            text=True,
        )
        task_files = set(result.stdout.strip().split('\n'))
    except subprocess.CalledProcessError:
        task_files = set()

    # Find conflicting files
    conflicting_files = list(set(uncommitted_files) & task_files)

    if not conflicting_files:
        return {"success": True, "data": {"message": "No conflicting files found", "resolved": []}}

    # Stash uncommitted changes (include untracked files)
    stash_message = f"pfactory-temp-{task_id}"
    stash_created = False
    try:
        # First try with --include-untracked to catch new files
        result = subprocess.run(
            ["git", "stash", "push", "--include-untracked", "-m", stash_message],
            cwd=project_path,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and "No local changes to save" not in result.stdout:
            stash_created = True
            logger.info(f"Stashed changes: {result.stdout.strip()}")
        elif result.returncode != 0:
            # Fallback: try without --include-untracked (for older git or if no untracked)
            result = subprocess.run(
                ["git", "stash", "push", "-m", stash_message],
                cwd=project_path,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and "No local changes to save" not in result.stdout:
                stash_created = True
                logger.info(f"Stashed changes (fallback): {result.stdout.strip()}")
            elif result.returncode != 0 and "No local changes to save" not in (result.stderr + result.stdout):
                return {"success": False, "error": f"Failed to stash changes: {result.stderr or result.stdout}"}
    except subprocess.CalledProcessError as e:
        return {"success": False, "error": f"Failed to stash changes: {e.stderr}"}

    resolved_files = []
    failed_files = []

    try:
        for file_path in conflicting_files:
            try:
                # Get base version (from base branch)
                base_content = ""
                try:
                    result = subprocess.run(
                        ["git", "show", f"{base_branch}:{file_path}"],
                        cwd=project_path,
                        capture_output=True,
                        text=True,
                    )
                    if result.returncode == 0:
                        base_content = result.stdout
                except Exception:
                    pass

                # Get local version (uncommitted changes)
                # If we stashed, get from stash; otherwise read from working directory
                local_content = ""
                try:
                    if stash_created:
                        result = subprocess.run(
                            ["git", "show", f"stash@{{0}}:{file_path}"],
                            cwd=project_path,
                            capture_output=True,
                            text=True,
                        )
                        if result.returncode == 0:
                            local_content = result.stdout
                    else:
                        # Read directly from working directory
                        working_file = project_path / file_path
                        if working_file.exists():
                            local_content = working_file.read_text()
                except Exception:
                    pass

                # Get task branch version
                task_content = ""
                try:
                    result = subprocess.run(
                        ["git", "show", f"{spec_branch}:{file_path}"],
                        cwd=project_path,
                        capture_output=True,
                        text=True,
                    )
                    if result.returncode == 0:
                        task_content = result.stdout
                except Exception:
                    pass

                # Use AI to merge the three versions
                from ..services.conflict_service import get_conflict_service

                conflict_service = get_conflict_service(project_path)
                merge_result = await conflict_service.ai_merge_three_way(
                    file_path=file_path,
                    base_content=base_content,
                    local_content=local_content,
                    task_content=task_content,
                    local_label="your uncommitted changes",
                    task_label=f"task {task_id} changes",
                )

                if merge_result.get("success"):
                    # Write merged content to working directory
                    full_path = project_path / file_path
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(merge_result.get("content", ""))
                    resolved_files.append(file_path)
                else:
                    failed_files.append({"file": file_path, "error": merge_result.get("error", "Unknown error")})

            except Exception as e:
                logger.error(f"Failed to resolve {file_path}: {e}")
                failed_files.append({"file": file_path, "error": str(e)})

    finally:
        # Drop the stash only if we created one
        if stash_created:
            try:
                run_git_command(["stash", "drop"], cwd=project_path)
                logger.info("Dropped stash after merge")
            except Exception:
                logger.warning("Failed to drop stash - may need manual cleanup")

    if failed_files:
        return {
            "success": len(resolved_files) > 0,
            "data": {
                "resolved": resolved_files,
                "failed": failed_files,
                "message": f"Resolved {len(resolved_files)} files, {len(failed_files)} failed",
            },
            "error": f"{len(failed_files)} files could not be resolved",
        }

    return {
        "success": True,
        "data": {
            "resolved": resolved_files,
            "failed": [],
            "message": f"Successfully resolved {len(resolved_files)} conflicting files",
        },
    }


@router.post("/{task_id}/worktree/resolve-git-merge")
async def resolve_git_merge_conflicts(task_id: str):
    """
    Resolve files with git merge conflict markers using AI.

    This endpoint handles the case where a git merge is in progress and files
    contain conflict markers (<<<<<<< HEAD, =======, >>>>>>> branch).

    Unlike resolve_uncommitted_conflicts (which uses stash), this works directly
    with files that already have conflict markers from an in-progress merge.

    Process:
    1. Check if merge is in progress (.git/MERGE_HEAD exists)
    2. Get list of unresolved conflicted files
    3. For each file, use AI to resolve the conflict markers
    4. Write resolved content and stage the file
    5. Return success (user can then commit the merge)
    """

    logger.info(f"Resolving git merge conflicts for task {task_id}")

    # #335 sanitize the caller-supplied task id before it is joined onto a root
    try:
        task_id = safe_spec_component(task_id, field="task_id")
    except ValueError:
        return {"success": False, "error": "invalid task id"}

    # Find the task's project
    projects_data_dir = get_data_dir()
    projects_file = projects_data_dir / "projects.json"

    if not projects_file.exists():
        return {"success": False, "error": "No projects configured"}

    projects_data = json.loads(projects_file.read_text())

    # Find the task across all projects
    project_path = None
    worktree_path = None

    if isinstance(projects_data, dict):
        projects = list(projects_data.values())
    else:
        projects = projects_data

    for project in projects:
        if isinstance(project, str):
            project_path = Path(project)
        else:
            project_path = Path(project.get("path", ""))

        spec_dir = project_path / ".pfactory" / "specs" / task_id

        if spec_dir.exists():
            worktree_path = project_path / ".pfactory" / "worktrees" / "tasks" / task_id
            break
    else:
        return {"success": False, "error": f"Task {task_id} not found"}

    # Determine which path to work with (main project or worktree)
    # Check both locations for merge in progress
    work_path = None
    merge_head_main = project_path / ".git" / "MERGE_HEAD"
    merge_head_worktree = (
        worktree_path / ".git" if worktree_path and worktree_path.exists() else None
    )

    if merge_head_main.exists():
        work_path = project_path
        logger.info(f"Found merge in progress in main project: {project_path}")
    elif merge_head_worktree and (merge_head_worktree / "MERGE_HEAD").exists():
        work_path = worktree_path
        logger.info(f"Found merge in progress in worktree: {worktree_path}")
    else:
        # No merge in progress - check if there are files with conflict markers anyway
        # This can happen if the merge state was cleared but files still have markers
        logger.info("No MERGE_HEAD found, checking for conflict markers in files...")
        work_path = project_path  # Default to main project

    # Get list of files with unresolved conflicts
    conflicted_files = []
    diff_u_result = run_git_command(["diff", "--name-only", "--diff-filter=U"], cwd=work_path)
    if diff_u_result["success"] and diff_u_result["output"]:
        conflicted_files = [f for f in diff_u_result["output"].split('\n') if f]
        logger.info(f"Found {len(conflicted_files)} conflicted files: {conflicted_files}")

    # If no conflicted files from git, scan for files with conflict markers
    if not conflicted_files:
        logger.info("No files from git diff --diff-filter=U, scanning for conflict markers...")
        status_result = run_git_command(["status", "--porcelain"], cwd=work_path)
        if status_result["success"]:
            for line in status_result["output"].split('\n'):
                if line and (line.startswith('UU') or line.startswith('AA') or
                             line.startswith('DD') or line.startswith('AU') or
                             line.startswith('UA') or line.startswith('DU') or
                             line.startswith('UD')):
                    # Status codes indicate conflicts
                    file_path = line[3:].strip()
                    if file_path:
                        conflicted_files.append(file_path)

    if not conflicted_files:
        return {
            "success": True,
            "data": {"resolved": [], "failed": [], "message": "No conflicted files found"},
        }

    # Resolve each conflicted file using AI
    resolved_files = []
    failed_files = []

    from ..services.conflict_service import get_conflict_service

    conflict_service = get_conflict_service(project_path)

    for file_path in conflicted_files:
        try:
            full_path = work_path / file_path
            if not full_path.exists():
                logger.warning(f"Conflicted file not found: {full_path}")
                failed_files.append({"file": file_path, "error": "File not found"})
                continue

            # Read file content with conflict markers
            content = full_path.read_text()

            # Check if file actually has conflict markers
            if "<<<<<<< " not in content:
                logger.info(f"File {file_path} has no conflict markers, skipping")
                # Stage it anyway since git thinks it's conflicted
                run_git_command(["add", file_path], cwd=work_path)
                resolved_files.append(file_path)
                continue

            # Use AI to resolve conflict markers
            merge_result = await conflict_service.resolve_conflict_markers(
                file_path=file_path,
                content=content,
            )

            if merge_result.get("success"):
                resolved_content = merge_result.get("content", "")

                # Verify no conflict markers remain
                if (
                    "<<<<<<< " in resolved_content
                    or "=======" in resolved_content
                    or ">>>>>>> " in resolved_content
                ):
                    logger.warning(f"AI resolution for {file_path} still contains conflict markers")
                    # Try to clean up obvious marker remnants
                    resolved_content = _clean_conflict_markers(resolved_content)

                # Write resolved content
                full_path.write_text(resolved_content)
                logger.info(f"Wrote resolved content to {full_path}")

                # Stage the file
                add_result = run_git_command(["add", file_path], cwd=work_path)
                if add_result["success"]:
                    resolved_files.append(file_path)
                    logger.info(f"Staged resolved file: {file_path}")
                else:
                    logger.warning(f"Failed to stage {file_path}: {add_result['error']}")
                    failed_files.append(
                        {"file": file_path, "error": "Failed to stage resolved file"}
                    )
            else:
                error_msg = merge_result.get("error", "AI resolution failed")
                logger.error(f"AI resolution failed for {file_path}: {error_msg}")
                failed_files.append({"file": file_path, "error": "AI conflict resolution failed"})

        except Exception:
            logger.exception(f"Failed to resolve {file_path}")
            failed_files.append({"file": file_path, "error": "Failed to resolve conflict"})

    if failed_files:
        return {
            "success": len(resolved_files) > 0,
            "data": {
                "resolved": resolved_files,
                "failed": failed_files,
                "message": f"Resolved {len(resolved_files)} files, {len(failed_files)} failed",
            },
            "error": f"{len(failed_files)} files could not be resolved",
        }

    # All conflicts resolved successfully - auto-commit the merge
    commit_result = None
    try:
        # Get the branch being merged for the commit message
        merge_head_file = work_path / ".git" / "MERGE_HEAD"
        merge_branch = "task branch"
        if merge_head_file.exists():
            merge_commit = merge_head_file.read_text().strip()[:8]
            # Try to get branch name from the merge
            name_rev_result = run_git_command(["name-rev", "--name-only", merge_commit], cwd=work_path)
            if name_rev_result["success"] and name_rev_result["output"]:
                merge_branch = name_rev_result["output"]

        # Commit the merge
        commit_msg = f"Merge {merge_branch} (AI-resolved conflicts)"
        commit_git_result = run_git_command(["commit", "-m", commit_msg], cwd=work_path)
        if commit_git_result["success"]:
            commit_result = "Merge committed successfully"
            logger.info(f"Auto-committed merge: {commit_msg}")
        else:
            commit_result = "Merge commit failed"
            logger.warning(f"Failed to auto-commit merge: {commit_git_result['error']}")
    except Exception:
        commit_result = "Merge commit failed"
        logger.exception("Error during auto-commit")

    return {
        "success": True,
        "data": {
            "resolved": resolved_files,
            "failed": [],
            "message": f"Successfully resolved {len(resolved_files)} conflicted files",
            "commit": commit_result,
        },
    }


def _clean_conflict_markers(content: str) -> str:
    """
    Clean up any remaining conflict markers from content.
    This is a fallback if AI resolution leaves some markers.
    """
    import re

    # Pattern to match conflict blocks
    # <<<<<<< ... ======= ... >>>>>>>
    pattern = r'<<<<<<<[^\n]*\n(.*?)=======\n(.*?)>>>>>>>[^\n]*\n?'

    def replace_conflict(match):
        # Prefer the second version (usually "theirs"/incoming changes)
        # This is a simple heuristic - the AI should have already merged properly
        ours = match.group(1)
        theirs = match.group(2)
        # If theirs is empty, use ours
        if not theirs.strip():
            return ours
        return theirs

    cleaned = re.sub(pattern, replace_conflict, content, flags=re.DOTALL)
    return cleaned


@router.post("/{task_id}/worktree/abort-merge")
async def abort_worktree_merge(task_id: str):
    """
    Abort a failed merge in the worktree or main project.

    This resets the git state when a merge has left the repository in an
    unmerged/conflicted state. It runs `git merge --abort` in both the
    worktree and the main project to ensure a clean state.
    """

    logger.info(f"Aborting merge for task {task_id}")

    # Parse task_id to get spec_id
    # task_id could be "project_id:spec_id" or just "spec_id"
    if ":" in task_id:
        project_id, spec_id = split_task_id(task_id)
        # Look up project path
        projects_file = get_data_file("projects.json")
        if not projects_file.exists():
            return {"success": False, "error": "Projects file not found"}

        projects_data = json.loads(projects_file.read_text())

        # Handle dict format where keys are project IDs
        if isinstance(projects_data, dict):
            project = projects_data.get(project_id)
            if not project:
                return {"success": False, "error": f"Project not found: {project_id}"}
            project_path = Path(project["path"])
        else:
            # Handle list format where each item has an "id" field
            project = None
            for p in projects_data:
                if isinstance(p, dict) and p.get("id") == project_id:
                    project = p
                    break
            if not project:
                return {"success": False, "error": f"Project not found: {project_id}"}
            project_path = Path(project["path"])
    else:
        return {"success": False, "error": "Task ID must include project ID (format: project_id:spec_id)"}

    spec_dir = project_path / ".pfactory" / "specs" / spec_id
    if not spec_dir.exists():
        return {"success": False, "error": f"Task {task_id} not found"}

    worktree_path = project_path / ".pfactory" / "worktrees" / "tasks" / spec_id

    aborted_locations = []
    errors = []

    # Try to abort merge in worktree first
    if worktree_path and worktree_path.exists():
        try:
            # Check if worktree is in a merge state
            merge_head = worktree_path / ".git" / "MERGE_HEAD"
            if merge_head.exists() or (worktree_path / "MERGE_HEAD").exists():
                result = subprocess.run(
                    ["git", "merge", "--abort"],
                    cwd=worktree_path,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode == 0:
                    aborted_locations.append("worktree")
                    logger.info(f"Aborted merge in worktree: {worktree_path}")
                else:
                    logger.warning(f"Failed to abort merge in worktree: {result.stderr}")
                    errors.append(f"Worktree: {result.stderr.strip()}")
        except subprocess.TimeoutExpired:
            errors.append("Worktree: git merge --abort timed out")
        except Exception:
            logger.exception("Error aborting merge in worktree")
            errors.append("Worktree: failed to abort merge")

    # Try to abort merge in main project
    if project_path and project_path.exists():
        try:
            # Check if main project is in a merge state
            git_dir = project_path / ".git"
            merge_head = (
                git_dir / "MERGE_HEAD" if git_dir.is_dir() else project_path / ".git" / "MERGE_HEAD"
            )
            if merge_head.exists():
                result = subprocess.run(
                    ["git", "merge", "--abort"],
                    cwd=project_path,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode == 0:
                    aborted_locations.append("main project")
                    logger.info(f"Aborted merge in main project: {project_path}")
                else:
                    logger.warning(f"Failed to abort merge in main project: {result.stderr}")
                    errors.append(f"Main project: {result.stderr.strip()}")
        except subprocess.TimeoutExpired:
            errors.append("Main project: git merge --abort timed out")
        except Exception:
            logger.exception("Error aborting merge in main project")
            errors.append("Main project: failed to abort merge")

    if aborted_locations:
        return {
            "success": True,
            "data": {
                "abortedIn": aborted_locations,
                "message": f"Merge aborted in: {', '.join(aborted_locations)}",
            },
        }
    elif errors:
        return {
            "success": False,
            "error": "; ".join(errors)
        }
    else:
        return {
            "success": True,
            "data": {"abortedIn": [], "message": "No active merge found to abort"},
        }


@router.post("/{task_id}/worktree/create-pr")
async def create_pr_from_task(task_id: str, options: CreatePRFromTaskOptions = None):
    """
    Push the worktree branch and create a GitHub Pull Request.
    Does NOT delete the worktree or branch after PR creation.
    """
    import subprocess

    if options is None:
        options = CreatePRFromTaskOptions()

    # Parse task_id to get spec_id
    # task_id could be "project_id:spec_id" or just "spec_id"
    if ":" in task_id:
        project_id, spec_id = split_task_id(task_id)
        # Look up project path
        projects_file = get_data_file("projects.json")
        if not projects_file.exists():
            return {"success": False, "error": "Projects file not found"}

        projects_data = json.loads(projects_file.read_text())

        # Handle dict format where keys are project IDs
        if isinstance(projects_data, dict):
            project = projects_data.get(project_id)
            if not project:
                return {"success": False, "error": f"Project not found: {project_id}"}
            project_path = Path(project["path"])
        else:
            # Handle list format where each item has an "id" field
            project = None
            for p in projects_data:
                if isinstance(p, dict) and p.get("id") == project_id:
                    project = p
                    break
            if not project:
                return {"success": False, "error": f"Project not found: {project_id}"}
            project_path = Path(project["path"])
    else:
        return {"success": False, "error": "Task ID must include project ID (format: project_id:spec_id)"}

    spec_dir = project_path / ".pfactory" / "specs" / spec_id
    if not spec_dir.exists():
        return {"success": False, "error": f"Task {task_id} not found"}

    # Find the worktree
    worktree_path = project_path / ".pfactory" / "worktrees" / "tasks" / spec_id

    if not worktree_path.exists():
        return {"success": False, "error": "No worktree found for this task"}

    # Get the branch name from the worktree
    branch_result = run_git_command(["rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree_path)
    if not branch_result["success"]:
        logger.warning(
            f"Could not determine worktree branch for {task_id}: {branch_result['error']}"
        )
        return {"success": False, "error": "Could not determine worktree branch"}
    worktree_branch = branch_result["output"]

    # Get the base branch (from options or detect from main project)
    base_branch = options.baseBranch
    if not base_branch:
        base_result = run_git_command(["rev-parse", "--abbrev-ref", "HEAD"], cwd=project_path)
        base_branch = base_result["output"] if base_result["success"] else "main"

    # Fetch latest base branch from remote
    run_git_command(["fetch", "origin", base_branch], cwd=worktree_path)

    # Stash any uncommitted changes before rebasing
    stashed = False
    try:
        stash_result = subprocess.run(
            ["git", "stash", "push", "-m", "pfactory-pre-rebase"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        # "No local changes to save" means nothing was stashed
        stashed = stash_result.returncode == 0 and "No local changes" not in stash_result.stdout
    except Exception:
        pass

    # Rebase onto latest base branch to minimize conflicts (best-effort)
    rebase_failed = False
    try:
        result = subprocess.run(
            ["git", "rebase", f"origin/{base_branch}"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            # Abort the failed rebase to leave worktree clean
            subprocess.run(
                ["git", "rebase", "--abort"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            rebase_failed = True
    except subprocess.TimeoutExpired:
        subprocess.run(
            ["git", "rebase", "--abort"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        rebase_failed = True
    except Exception:
        rebase_failed = True

    # Restore stashed changes
    if stashed:
        subprocess.run(
            ["git", "stash", "pop"],
            cwd=worktree_path,
            capture_output=True, text=True, timeout=10
        )

    # Push the branch to remote
    # Use --force-with-lease after successful rebase (rebase rewrites history)
    push_cmd = ["git", "push", "-u", "origin", worktree_branch]
    if not rebase_failed:
        push_cmd = ["git", "push", "--force-with-lease", "-u", "origin", worktree_branch]
    try:
        result = subprocess.run(
            push_cmd,
            cwd=worktree_path,
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode != 0:
            return {"success": False, "error": f"Failed to push branch: {result.stderr.strip()}"}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Push timed out"}
    except Exception:
        logger.exception(f"Failed to push branch {worktree_branch}")
        return {"success": False, "error": "Failed to push branch"}

    # Load task title/description for PR defaults
    pr_title = options.title
    pr_body = options.body

    if not pr_title or not pr_body:
        # Try requirements.json first
        req_file = spec_dir / "requirements.json"
        spec_file = spec_dir / "spec.md"

        if req_file.exists():
            try:
                reqs = json.loads(req_file.read_text())
                if not pr_title:
                    pr_title = reqs.get("title") or reqs.get("taskTitle") or task_id
                if not pr_body:
                    pr_body = reqs.get("description") or reqs.get("taskDescription") or ""
            except (json.JSONDecodeError, KeyError):
                pass

        if not pr_title:
            pr_title = task_id
        if not pr_body and spec_file.exists():
            try:
                pr_body = spec_file.read_text()[:2000]
            except Exception:
                pr_body = ""

    # Route PR creation through the configured git provider. When the project
    # is on GitLab or Azure DevOps the gh CLI path can't open the PR (we
    # pushed to the GitLab `origin`, not to a GitHub remote). Only fall back
    # to `gh pr create` when the project is actually a GitHub project.
    from .github import _get_project_provider, _use_provider_api, run_gh_command

    if _use_provider_api(project_id):
        try:
            provider = _get_project_provider(project_id)
            provider_type_value = getattr(provider.provider_type, "value", str(provider.provider_type))
            if provider_type_value == "github":
                # The provider abstraction picks GitHub when a custom token is
                # configured; the gh CLI path below already handles GitHub, so
                # let it run.
                pass
            else:
                created = await provider.create_pr(
                    source_branch=worktree_branch,
                    target_branch=base_branch,
                    title=pr_title,
                    body=pr_body or "",
                    draft=bool(options.draft),
                )
                return {
                    "success": True,
                    "data": {
                        "prUrl": created.get("web_url") or "",
                        "prNumber": created.get("number"),
                        "branch": worktree_branch,
                        "baseBranch": base_branch,
                        "provider": provider_type_value,
                    },
                }
        except AttributeError:
            # Provider hasn't implemented create_pr yet — surface a clear error
            # instead of silently falling through to gh CLI (which would hit
            # the wrong remote and produce GraphQL noise).
            return {
                "success": False,
                "error": f"Provider {provider_type_value!r} does not support PR creation yet",
            }
        except Exception:
            logger.exception(f"Failed to create PR via provider for {worktree_branch}")
            return {"success": False, "error": "Failed to create PR"}

    # Create the PR using gh CLI (GitHub-only path)
    head_ref = worktree_branch
    gh_args = [
        "pr", "create",
        "--head", head_ref,
        "--base", base_branch,
        "--title", pr_title,
        "--body", pr_body or "",
    ]

    if options.targetRepo:
        # Cross-fork PR: need owner:branch format for --head
        try:
            origin_url_result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=str(worktree_path),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if origin_url_result.returncode == 0:
                import re as _re
                m = _re.search(r'[:/]([^/]+)/[^/]+?(?:\.git)?$', origin_url_result.stdout.strip())
                if m:
                    fork_owner = m.group(1)
                    # Update --head to owner:branch format required by gh for cross-repo PRs
                    head_idx = gh_args.index("--head") + 1
                    gh_args[head_idx] = f"{fork_owner}:{worktree_branch}"
        except Exception:
            pass  # Fall back to plain branch name
        gh_args.extend(["--repo", options.targetRepo])

    if options.draft:
        gh_args.append("--draft")

    gh_result = run_gh_command(gh_args, cwd=str(project_path))

    if not gh_result["success"]:
        logger.warning(f"gh pr create failed: {gh_result.get('error', 'unknown error')}")
        return {"success": False, "error": "Failed to create PR"}

    # Parse PR URL from output
    pr_url = gh_result.get("output", "").strip()
    pr_number = None
    if pr_url:
        # gh pr create outputs the PR URL, extract number from it
        import re as _re
        match = _re.search(r'/pull/(\d+)', pr_url)
        if match:
            pr_number = int(match.group(1))

    return {
        "success": True,
        "data": {
            "prUrl": pr_url,
            "prNumber": pr_number,
            "branch": worktree_branch,
            "baseBranch": base_branch,
        },
    }


@router.post("/{task_id}/worktree/merge")
async def merge_worktree(task_id: str, options: WorktreeMergeOptions = None):
    """
    Merge the worktree branch into the base branch.
    """
    import subprocess

    if options is None:
        options = WorktreeMergeOptions()

    # Parse task_id to get spec_id
    # task_id could be "project_id:spec_id" or just "spec_id"
    if ":" in task_id:
        project_id, spec_id = split_task_id(task_id)
        # Look up project path
        projects_file = get_data_file("projects.json")
        if not projects_file.exists():
            return {"success": False, "error": "Projects file not found"}

        projects_data = json.loads(projects_file.read_text())

        # Handle dict format where keys are project IDs
        if isinstance(projects_data, dict):
            project = projects_data.get(project_id)
            if not project:
                return {"success": False, "error": f"Project not found: {project_id}"}
            project_path = Path(project["path"])
        else:
            # Handle list format where each item has an "id" field
            project = None
            for p in projects_data:
                if isinstance(p, dict) and p.get("id") == project_id:
                    project = p
                    break
            if not project:
                return {"success": False, "error": f"Project not found: {project_id}"}
            project_path = Path(project["path"])
    else:
        return {"success": False, "error": "Task ID must include project ID (format: project_id:spec_id)"}

    spec_dir = project_path / ".pfactory" / "specs" / spec_id
    if not spec_dir.exists():
        return {"success": False, "error": f"Task {task_id} not found"}

    # Find the worktree
    worktree_path = project_path / ".pfactory" / "worktrees" / "tasks" / spec_id

    if not worktree_path.exists():
        return {"success": False, "error": "No worktree found for this task"}

    # Get the branch name from the worktree
    branch_result = run_git_command(["rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree_path)
    if not branch_result["success"]:
        logger.warning(
            f"Could not determine worktree branch for {task_id}: {branch_result['error']}"
        )
        return {"success": False, "error": "Could not determine worktree branch"}
    worktree_branch = branch_result["output"]

    # Get the current branch in main repo
    base_result = run_git_command(["rev-parse", "--abbrev-ref", "HEAD"], cwd=project_path)
    base_branch = base_result["output"] if base_result["success"] else "develop"

    # Clean up internal auto-generated files that can block merge
    # These are untracked files created by agents in worktrees that would
    # collide with the same untracked files in the main working directory.
    _INTERNAL_MERGE_BLOCKERS = [
        ".pfactory-security.json",
        ".pfactory-status",
    ]
    for fname in _INTERNAL_MERGE_BLOCKERS:
        blocker = project_path / fname
        if blocker.exists():
            try:
                blocker.unlink()
                logger.info(f"Removed merge-blocking file: {fname}")
            except OSError:
                pass

    # Perform the merge
    try:
        merge_cmd = ["git", "merge", worktree_branch]
        if options.noCommit:
            merge_cmd.append("--no-commit")

        result = subprocess.run(
            merge_cmd,
            cwd=project_path,
            capture_output=True,
            text=True,
            check=True
        )

        # Clean up worktree after successful merge
        worktree_deleted = False
        branch_deleted = False
        try:
            # Remove git worktree
            worktree_deleted = run_git_command(
                ["worktree", "remove", str(worktree_path), "--force"],
                cwd=project_path,
            )["success"]

            # Delete the branch (it's merged now)
            branch_deleted = run_git_command(
                ["branch", "-d", worktree_branch],
                cwd=project_path,
            )["success"]
        except Exception as e:
            logger.warning(f"Failed to cleanup worktree after merge: {e}")
            # Don't fail the merge just because cleanup failed

        return {
            "success": True,
            "data": {
                "success": True,  # Frontend checks this for merge result display
                "merged": True,
                "message": f"Successfully merged {worktree_branch} into {base_branch}",
                "output": result.stdout,
                "worktreeDeleted": worktree_deleted,
                "branchDeleted": branch_deleted,
            },
        }
    except subprocess.CalledProcessError as e:
        # Check if it's a conflict
        if "CONFLICT" in e.stdout or "CONFLICT" in e.stderr:
            return {
                "success": False,
                "error": "Merge conflicts detected. Please resolve manually.",
                "conflicts": True,
                "output": e.stdout + e.stderr,
            }
        return {
            "success": False,
            "error": f"Merge failed: {e.stderr or e.stdout}",
            "output": e.stdout + e.stderr,
        }


@router.get("/{task_id}/worktree/status")
async def get_worktree_status(task_id: str):
    """
    Get the status of a task's worktree.
    Returns information about the worktree including changed files count,
    additions/deletions, and whether it exists.
    """
    # Parse task_id to get project_id and spec_id
    if ":" in task_id:
        project_id, spec_id = split_task_id(task_id)
    else:
        # task_id is just the spec_id, search for project
        # #335 barrier the bare id (the split branch is validated by split_task_id)
        try:
            spec_id = safe_spec_component(task_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid task id") from None
        project_id = None

    # Find project path
    projects_data_dir = get_data_dir()
    projects_file = projects_data_dir / "projects.json"

    if not projects_file.exists():
        return {
            "success": True,
            "data": {
                "exists": False,
            },
        }

    projects_data = json.loads(projects_file.read_text())

    # Handle both dict format (id -> project) and list format
    project_path = None
    if isinstance(projects_data, dict):
        if project_id and project_id in projects_data:
            project_path = Path(projects_data[project_id]["path"])
        else:
            # Search all projects for this spec
            for proj in projects_data.values():
                path = Path(proj["path"])
                if (path / ".pfactory" / "specs" / spec_id).exists():
                    project_path = path
                    break
    else:
        for project in projects_data:
            path = Path(project.get("path", ""))
            if project_id and project.get("id") == project_id:
                project_path = path
                break
            elif (path / ".pfactory" / "specs" / spec_id).exists():
                project_path = path
                break

    if not project_path:
        return {
            "success": True,
            "data": {
                "exists": False,
            },
        }

    # Check for worktree
    worktree_path = project_path / ".pfactory" / "worktrees" / "tasks" / spec_id

    if not worktree_path.exists():
        return {
            "success": True,
            "data": {
                "exists": False,
            },
        }

    # Get worktree branch
    branch_result = run_git_command(["rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree_path)
    worktree_branch = branch_result["output"] if branch_result["success"] else f"pfactory/{spec_id}"

    # Get base branch from main project
    base_result = run_git_command(["rev-parse", "--abbrev-ref", "HEAD"], cwd=project_path)
    base_branch = base_result["output"] if base_result["success"] else "develop"

    # Count commits ahead
    commit_count = 0
    count_result = run_git_command(["rev-list", "--count", f"{base_branch}..{worktree_branch}"], cwd=project_path)
    if count_result["success"]:
        try:
            commit_count = int(count_result["output"])
        except ValueError:
            commit_count = 0

    # Get changed files stats
    files_changed = 0
    additions = 0
    deletions = 0

    stat_result = run_git_command(["diff", "--stat", f"{base_branch}...{worktree_branch}"], cwd=project_path)
    if stat_result["success"]:
        # Parse the last line for summary (e.g., "5 files changed, 100 insertions(+), 20 deletions(-)")
        lines = stat_result["output"].split('\n')
        if lines:
            summary_line = lines[-1]
            import re
            files_match = re.search(r'(\d+) files? changed', summary_line)
            if files_match:
                files_changed = int(files_match.group(1))
            insert_match = re.search(r'(\d+) insertions?\(\+\)', summary_line)
            if insert_match:
                additions = int(insert_match.group(1))
            del_match = re.search(r'(\d+) deletions?\(-\)', summary_line)
            if del_match:
                deletions = int(del_match.group(1))

    return {
        "success": True,
        "data": {
            "exists": True,
            "worktreePath": str(worktree_path),
            "branch": worktree_branch,
            "baseBranch": base_branch,
            "commitCount": commit_count,
            "filesChanged": files_changed,
            "additions": additions,
            "deletions": deletions,
        },
    }


@router.get("/{task_id}/worktree/diff")
async def get_worktree_diff(task_id: str):
    """
    Get the diff details for a task's worktree.
    Returns detailed file-by-file changes between the worktree branch and base branch.
    """
    import subprocess

    # Parse task_id to get project_id and spec_id
    if ":" in task_id:
        project_id, spec_id = split_task_id(task_id)
    else:
        # #335 barrier the bare id (the split branch is validated by split_task_id)
        try:
            spec_id = safe_spec_component(task_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid task id") from None
        project_id = None

    # Find project path
    projects_data_dir = get_data_dir()
    projects_file = projects_data_dir / "projects.json"

    if not projects_file.exists():
        return {
            "success": False,
            "error": "No projects configured"
        }

    projects_data = json.loads(projects_file.read_text())

    # Handle both dict format (id -> project) and list format
    project_path = None
    if isinstance(projects_data, dict):
        if project_id and project_id in projects_data:
            project_path = Path(projects_data[project_id]["path"])
        else:
            for proj in projects_data.values():
                path = Path(proj["path"])
                if (path / ".pfactory" / "specs" / spec_id).exists():
                    project_path = path
                    break
    else:
        for project in projects_data:
            path = Path(project.get("path", ""))
            if project_id and project.get("id") == project_id:
                project_path = path
                break
            elif (path / ".pfactory" / "specs" / spec_id).exists():
                project_path = path
                break

    if not project_path:
        return {
            "success": False,
            "error": f"Project not found for task {task_id}"
        }

    # Check for worktree
    worktree_path = project_path / ".pfactory" / "worktrees" / "tasks" / spec_id

    if not worktree_path.exists():
        return {
            "success": False,
            "error": "No worktree found for this task"
        }

    # Get worktree branch
    branch_result = run_git_command(["rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree_path)
    worktree_branch = branch_result["output"] if branch_result["success"] else f"pfactory/{spec_id}"

    # Get base branch from main project
    base_result = run_git_command(["rev-parse", "--abbrev-ref", "HEAD"], cwd=project_path)
    base_branch = base_result["output"] if base_result["success"] else "develop"

    # Get detailed diff with numstat
    files = []
    numstat_result = run_git_command(["diff", "--numstat", f"{base_branch}...{worktree_branch}"], cwd=project_path)
    if numstat_result["success"]:
        for line in numstat_result["output"].split('\n'):
            if line:
                parts = line.split('\t')
                if len(parts) >= 3:
                    added = parts[0]
                    deleted = parts[1]
                    path = parts[2]
                    # Handle binary files (show as -)
                    additions = int(added) if added != '-' else 0
                    deletions = int(deleted) if deleted != '-' else 0
                    files.append({
                        "path": path,
                        "status": "modified",  # Will be refined below
                        "additions": additions,
                        "deletions": deletions,
                    })

    # Get file statuses (A/M/D/R)
    name_status_result = run_git_command(["diff", "--name-status", f"{base_branch}...{worktree_branch}"], cwd=project_path)
    if name_status_result["success"]:
        status_map = {}
        for line in name_status_result["output"].split('\n'):
            if line:
                parts = line.split('\t')
                if len(parts) >= 2:
                    status_code = parts[0][0]  # First char (R100 -> R)
                    filename = parts[-1]  # Last part is the filename
                    status = "modified"
                    if status_code == 'A':
                        status = "added"
                    elif status_code == 'D':
                        status = "deleted"
                    elif status_code == 'R':
                        status = "renamed"
                    elif status_code == 'M':
                        status = "modified"
                    status_map[filename] = status

        # Update files with proper status
        for f in files:
            if f["path"] in status_map:
                f["status"] = status_map[f["path"]]

    # Filter out internal pfactory files and agent artifacts (not relevant for user review)
    INTERNAL_FILES = {".pfactory-security.json", ".pfactory-status"}
    INTERNAL_PREFIXES = (".pfactory/", "VERIFICATION_REPORT", "LANGUAGE_CHOICE")
    files = [
        f for f in files
        if f["path"] not in INTERNAL_FILES
        and not any(f["path"].startswith(p) for p in INTERNAL_PREFIXES)
    ]

    # Fallback: if git diff shows no user-facing files but worktree has changes,
    # list files that exist in worktree but not in the main project
    if not files and worktree_path.exists():
        for f in worktree_path.iterdir():
            # Skip internal files, directories, and dotfiles
            if f.name.startswith('.') or f.name.startswith('__') or f.is_dir():
                continue
            if f.name in INTERNAL_FILES or any(f.name.startswith(p) for p in INTERNAL_PREFIXES):
                continue
            # Check if this file exists in the main project
            main_file = project_path / f.name
            if not main_file.exists():
                # New file created by the agent
                try:
                    content = f.read_text(errors='replace')
                    line_count = content.count('\n') + (1 if content and not content.endswith('\n') else 0)
                    # Generate a unified diff for display
                    diff_lines = ["--- /dev/null", f"+++ b/{f.name}"]
                    diff_lines.append(f"@@ -0,0 +1,{line_count} @@")
                    for line in content.splitlines():
                        diff_lines.append(f"+{line}")
                    synthetic_diff = "\n".join(diff_lines) + "\n"
                except OSError:
                    line_count = 0
                    synthetic_diff = ""
                files.append({
                    "path": f.name,
                    "status": "added",
                    "additions": line_count,
                    "deletions": 0,
                    "diff": synthetic_diff,
                })

    # Get actual diff content for each file
    for f in files:
        try:
            result = subprocess.run(
                ["git", "diff", f"{base_branch}...{worktree_branch}", "--", f["path"]],
                cwd=project_path,
                capture_output=True,
                text=True,
                check=True,
            )
            f["diff"] = result.stdout
        except subprocess.CalledProcessError:
            # If diff fails for a file, leave diff empty
            f["diff"] = ""

    # Generate summary
    total_additions = sum(f["additions"] for f in files)
    total_deletions = sum(f["deletions"] for f in files)
    summary = f"{len(files)} files changed, +{total_additions} -{total_deletions}"

    return {
        "success": True,
        "data": {
            "files": files,
            "summary": summary,
        },
    }


@router.post("/{task_id}/worktree/discard")
async def discard_worktree(task_id: str):
    """
    Discard/delete the worktree for a task.
    Removes the worktree directory and optionally the branch.
    """
    # Parse task_id to get spec_id
    # task_id could be "project_id:spec_id" or just "spec_id"
    if ":" in task_id:
        project_id, spec_id = split_task_id(task_id)
        # Look up project path
        projects_file = get_data_file("projects.json")
        if not projects_file.exists():
            return {"success": False, "error": "Projects file not found"}

        import json

        projects_data = json.loads(projects_file.read_text())

        # Handle dict format where keys are project IDs
        if isinstance(projects_data, dict):
            project = projects_data.get(project_id)
            if not project:
                return {"success": False, "error": f"Project not found: {project_id}"}
            project_path = Path(project["path"])
        else:
            # Handle list format where each item has an "id" field
            project = None
            for p in projects_data:
                if isinstance(p, dict) and p.get("id") == project_id:
                    project = p
                    break
            if not project:
                return {"success": False, "error": f"Project not found: {project_id}"}
            project_path = Path(project["path"])
    else:
        # task_id is just the spec_id, need to find project from context
        return {"success": False, "error": "Task ID must include project ID (format: project_id:spec_id)"}

    # Find the worktree
    worktree_path = project_path / ".pfactory" / "worktrees" / "tasks" / spec_id

    if not worktree_path.exists():
        return {"success": False, "error": "No worktree found for this task"}

    try:
        # Get the branch name before removing worktree
        branch_name = f"pfactory/{spec_id}"

        # Remove worktree using git command
        remove_result = run_git_command(["worktree", "remove", "--force", str(worktree_path)], cwd=project_path)

        if not remove_result["success"]:
            # Fallback: force delete directory
            if worktree_path.exists():
                shutil.rmtree(worktree_path, ignore_errors=True)

        # Prune worktrees
        run_git_command(["worktree", "prune"], cwd=project_path)

        # Delete the branch
        run_git_command(["branch", "-D", branch_name], cwd=project_path)

        return {
            "success": True,
            "data": {
                "discarded": True,
                "message": f"Successfully discarded worktree for {spec_id}",
            },
        }
    except Exception:
        logger.exception(f"Failed to discard worktree for {spec_id}")
        return {"success": False, "error": "Failed to discard worktree"}


# ============================================
# Worktree Open in IDE/Terminal Routes
# ============================================


class OpenInIDERequest(BaseModel):
    """Request body for opening a path in IDE."""

    worktreePath: str
    ide: str


class OpenInTerminalRequest(BaseModel):
    """Request body for opening a path in terminal."""

    worktreePath: str
    terminal: str


def _safe_launch_path(raw: str) -> str:
    """Resolve a caller-supplied worktree path for use in a launcher argv.

    Two separate problems, both fixed by resolving against the registry rather
    than trusting the request:

    * The value becomes a command-line argument, so an option-shaped string
      ("--user-data-dir=...") must never reach the launcher. A resolved
      absolute path always begins with a separator.
    * Without an anchor the endpoint would open *any* directory on the host.
      The path is therefore required to sit inside a registered project, which
      is the only thing this feature was ever meant to open. `resolve()` runs
      before the containment test so `..` segments and symlinks cannot escape.

    Raises:
        ValueError: with a message safe to show a caller.
    """
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise ValueError("Path must be an absolute path inside a registered project")
    resolved = candidate.resolve()

    roots = [Path(meta["path"]).resolve() for meta in load_projects().values()]
    if not any(resolved == root or root in resolved.parents for root in roots):
        raise ValueError("Path is not inside a registered project")

    if not resolved.is_dir():
        raise ValueError("Path is not a directory")
    return str(resolved)


def get_ide_command(ide: str, path: str) -> list[str]:
    """Get the command to open a path in the specified IDE.

    The executable is always chosen from the curated map below — never from the
    request. This used to honour a caller-supplied `customPath`, which made the
    endpoint an arbitrary-command sink: `[custom_path, path]` went straight to
    subprocess.Popen, so any authenticated caller could run any binary in the
    server process. (CodeQL py/command-line-injection.)
    """
    import platform

    system = platform.system()

    # IDE command mappings
    ide_commands = {
        # VS Code family
        "vscode": ["code", path],
        "cursor": ["cursor", path],
        "vscodium": ["codium", path],
        "vscode-insiders": ["code-insiders", path],
        # JetBrains IDEs
        "webstorm": ["webstorm", path] if system != "Darwin" else ["open", "-a", "WebStorm", path],
        "intellij": ["idea", path] if system != "Darwin" else ["open", "-a", "IntelliJ IDEA", path],
        "pycharm": ["pycharm", path] if system != "Darwin" else ["open", "-a", "PyCharm", path],
        "phpstorm": ["phpstorm", path] if system != "Darwin" else ["open", "-a", "PhpStorm", path],
        "goland": ["goland", path] if system != "Darwin" else ["open", "-a", "GoLand", path],
        "rider": ["rider", path] if system != "Darwin" else ["open", "-a", "Rider", path],
        "clion": ["clion", path] if system != "Darwin" else ["open", "-a", "CLion", path],
        "rubymine": ["rubymine", path] if system != "Darwin" else ["open", "-a", "RubyMine", path],
        "datagrip": ["datagrip", path] if system != "Darwin" else ["open", "-a", "DataGrip", path],
        # Sublime Text
        "sublime": ["subl", path] if system != "Darwin" else ["open", "-a", "Sublime Text", path],
        # Atom / Pulsar
        "atom": ["atom", path],
        "pulsar": ["pulsar", path],
        # Vim/Neovim (terminal-based)
        "vim": ["vim", path],
        "neovim": ["nvim", path],
        "nvim": ["nvim", path],
        # Emacs
        "emacs": ["emacs", path],
        # Zed
        "zed": ["zed", path] if system != "Darwin" else ["open", "-a", "Zed", path],
        # Nova (macOS)
        "nova": ["open", "-a", "Nova", path],
        # BBEdit (macOS)
        "bbedit": ["open", "-a", "BBEdit", path],
        # TextMate (macOS)
        "textmate": ["open", "-a", "TextMate", path],
        # Notepad++ (Windows)
        "notepadpp": ["notepad++", path],
        # Visual Studio (Windows)
        "visualstudio": ["devenv", path],
        # Fleet
        "fleet": ["fleet", path],
        # Lapce
        "lapce": ["lapce", path],
        # Helix
        "helix": ["hx", path],
        # Kate (Linux/KDE)
        "kate": ["kate", path],
        # Geany (Linux)
        "geany": ["geany", path],
    }

    return ide_commands.get(ide, ["code", path])  # Default to VS Code


def get_terminal_command(terminal: str, path: str) -> list[str]:
    """Get the command to open a terminal at the specified path.

    Same contract as :func:`get_ide_command`: the executable comes only from the
    curated map, never from the request body.
    """
    import platform

    system = platform.system()

    # Terminal command mappings by platform
    if system == "Darwin":  # macOS
        terminal_commands = {
            "system": ["open", "-a", "Terminal", path],
            "terminal": ["open", "-a", "Terminal", path],
            "iterm2": ["open", "-a", "iTerm", path],
            "iterm": ["open", "-a", "iTerm", path],
            "warp": ["open", "-a", "Warp", path],
            "hyper": ["open", "-a", "Hyper", path],
            "kitty": ["kitty", "--directory", path],
            "alacritty": ["alacritty", "--working-directory", path],
            "wezterm": ["wezterm", "start", "--cwd", path],
            "tabby": ["open", "-a", "Tabby", path],
        }
    elif system == "Windows":
        terminal_commands = {
            "system": ["cmd", "/c", "start", "cmd", "/k", f"cd /d {path}"],
            "wt": ["wt", "-d", path],
            "windows-terminal": ["wt", "-d", path],
            "cmd": ["cmd", "/c", "start", "cmd", "/k", f"cd /d {path}"],
            "powershell": ["powershell", "-NoExit", "-Command", f"cd '{path}'"],
            "pwsh": ["pwsh", "-NoExit", "-Command", f"cd '{path}'"],
            "hyper": ["hyper", path],
            "alacritty": ["alacritty", "--working-directory", path],
            "wezterm": ["wezterm", "start", "--cwd", path],
            "kitty": ["kitty", "--directory", path],
            "cmder": ["cmder", "/START", path],
            "conemu": ["conemu", "-Dir", path],
        }
    else:  # Linux and others
        terminal_commands = {
            "system": ["x-terminal-emulator", "-e", f"cd {path} && $SHELL"],
            "gnome-terminal": ["gnome-terminal", f"--working-directory={path}"],
            "konsole": ["konsole", f"--workdir={path}"],
            "xfce4-terminal": ["xfce4-terminal", f"--working-directory={path}"],
            "terminator": ["terminator", f"--working-directory={path}"],
            "tilix": ["tilix", f"--working-directory={path}"],
            "kitty": ["kitty", "--directory", path],
            "alacritty": ["alacritty", "--working-directory", path],
            "wezterm": ["wezterm", "start", "--cwd", path],
            "hyper": ["hyper", path],
            "xterm": ["xterm", "-e", f"cd {path} && $SHELL"],
            "urxvt": ["urxvt", "-cd", path],
            "st": ["st", "-d", path],
            "foot": ["foot", f"--working-directory={path}"],
            "sakura": ["sakura", f"--working-directory={path}"],
            "tabby": ["tabby", path],
        }

    return terminal_commands.get(terminal, terminal_commands.get("system", ["xterm"]))


@router.post("/worktree/open-in-ide")
async def open_worktree_in_ide(request: OpenInIDERequest):
    """
    Open a worktree path in the specified IDE.
    Used by the web UI to launch external IDE applications.
    """
    worktree_path = request.worktreePath
    ide = request.ide

    # Validate the path exists. Resolve it first: the value still lands in the
    # launcher's argv, so a relative path or one beginning with "-" would be
    # read as an option by the application we spawn rather than as a directory.
    try:
        worktree_path = _safe_launch_path(worktree_path)
    except ValueError as exc:
        logger.warning("rejected launch path %r: %s", worktree_path, exc)
        return {"success": False, "error": "Invalid worktree path"}

    # "custom" used to mean "run the executable the caller names" — an
    # arbitrary-command sink. Say so plainly rather than silently falling
    # through to the VS Code default and opening the wrong application.
    if ide == "custom":
        return {
            "success": False,
            "error": (
                "A custom IDE path is no longer supported: the server only "
                "launches applications from its known list. Pick your IDE by "
                "name instead."
            ),
        }

    try:
        cmd = get_ide_command(ide, worktree_path)

        # Launch the IDE (don't wait for it to finish)
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )

        return {
            "success": True,
            "data": {
                "opened": True
            }
        }
    except FileNotFoundError:
        return {
            "success": False,
            "error": f"IDE command not found. Make sure '{ide}' is installed and in your PATH.",
        }
    except Exception:
        logger.exception(f"Failed to open IDE {ide!r}")
        return {"success": False, "error": "Failed to open IDE"}


@router.post("/worktree/open-in-terminal")
async def open_worktree_in_terminal(request: OpenInTerminalRequest):
    """
    Open a worktree path in the specified terminal emulator.
    Used by the web UI to launch external terminal applications.
    """
    worktree_path = request.worktreePath
    terminal = request.terminal

    # Validate the path exists. Resolve it first: the value still lands in the
    # launcher's argv, so a relative path or one beginning with "-" would be
    # read as an option by the application we spawn rather than as a directory.
    try:
        worktree_path = _safe_launch_path(worktree_path)
    except ValueError as exc:
        logger.warning("rejected launch path %r: %s", worktree_path, exc)
        return {"success": False, "error": "Invalid worktree path"}

    if terminal == "custom":
        return {
            "success": False,
            "error": (
                "A custom terminal path is no longer supported: the server only "
                "launches applications from its known list. Pick your terminal "
                "by name instead."
            ),
        }

    try:
        cmd = get_terminal_command(terminal, worktree_path)

        # Launch the terminal (don't wait for it to finish)
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )

        return {
            "success": True,
            "data": {
                "opened": True
            }
        }
    except FileNotFoundError:
        return {
            "success": False,
            "error": f"Terminal command not found. Make sure '{terminal}' is installed and in your PATH.",
        }
    except Exception:
        logger.exception(f"Failed to open terminal {terminal!r}")
        return {"success": False, "error": "Failed to open terminal"}


@router.post("/worktree/detect-tools")
async def detect_worktree_tools():
    """
    Detect installed IDEs and terminal emulators on the system.
    Returns lists of available tools with their installation status.
    """
    import platform
    import shutil

    system = platform.system()

    # IDE detection
    ide_definitions = [
        {"id": "vscode", "name": "Visual Studio Code", "command": "code"},
        {"id": "cursor", "name": "Cursor", "command": "cursor"},
        {"id": "vscodium", "name": "VSCodium", "command": "codium"},
        {"id": "vscode-insiders", "name": "VS Code Insiders", "command": "code-insiders"},
        {"id": "sublime", "name": "Sublime Text", "command": "subl"},
        {"id": "webstorm", "name": "WebStorm", "command": "webstorm" if system != "Darwin" else None},
        {"id": "intellij", "name": "IntelliJ IDEA", "command": "idea" if system != "Darwin" else None},
        {"id": "pycharm", "name": "PyCharm", "command": "pycharm" if system != "Darwin" else None},
        {"id": "zed", "name": "Zed", "command": "zed"},
        {"id": "atom", "name": "Atom", "command": "atom"},
        {"id": "pulsar", "name": "Pulsar", "command": "pulsar"},
        {"id": "vim", "name": "Vim", "command": "vim"},
        {"id": "neovim", "name": "Neovim", "command": "nvim"},
        {"id": "emacs", "name": "Emacs", "command": "emacs"},
        {"id": "helix", "name": "Helix", "command": "hx"},
        {"id": "fleet", "name": "Fleet", "command": "fleet"},
        {"id": "lapce", "name": "Lapce", "command": "lapce"},
    ]

    if system == "Windows":
        ide_definitions.extend([
            {"id": "notepadpp", "name": "Notepad++", "command": "notepad++"},
            {"id": "visualstudio", "name": "Visual Studio", "command": "devenv"},
        ])
    elif system == "Linux":
        ide_definitions.extend([
            {"id": "kate", "name": "Kate", "command": "kate"},
            {"id": "geany", "name": "Geany", "command": "geany"},
        ])

    # Terminal detection
    terminal_definitions = []
    if system == "Darwin":
        terminal_definitions = [
            {"id": "terminal", "name": "Terminal", "command": None, "app": "Terminal"},
            {"id": "iterm2", "name": "iTerm2", "command": None, "app": "iTerm"},
            {"id": "warp", "name": "Warp", "command": None, "app": "Warp"},
            {"id": "hyper", "name": "Hyper", "command": None, "app": "Hyper"},
            {"id": "kitty", "name": "Kitty", "command": "kitty"},
            {"id": "alacritty", "name": "Alacritty", "command": "alacritty"},
            {"id": "wezterm", "name": "WezTerm", "command": "wezterm"},
        ]
    elif system == "Windows":
        terminal_definitions = [
            {"id": "wt", "name": "Windows Terminal", "command": "wt"},
            {"id": "cmd", "name": "Command Prompt", "command": "cmd"},
            {"id": "powershell", "name": "PowerShell", "command": "powershell"},
            {"id": "pwsh", "name": "PowerShell Core", "command": "pwsh"},
            {"id": "hyper", "name": "Hyper", "command": "hyper"},
            {"id": "alacritty", "name": "Alacritty", "command": "alacritty"},
            {"id": "wezterm", "name": "WezTerm", "command": "wezterm"},
            {"id": "kitty", "name": "Kitty", "command": "kitty"},
        ]
    else:  # Linux
        terminal_definitions = [
            {"id": "gnome-terminal", "name": "GNOME Terminal", "command": "gnome-terminal"},
            {"id": "konsole", "name": "Konsole", "command": "konsole"},
            {"id": "xfce4-terminal", "name": "Xfce Terminal", "command": "xfce4-terminal"},
            {"id": "terminator", "name": "Terminator", "command": "terminator"},
            {"id": "tilix", "name": "Tilix", "command": "tilix"},
            {"id": "kitty", "name": "Kitty", "command": "kitty"},
            {"id": "alacritty", "name": "Alacritty", "command": "alacritty"},
            {"id": "wezterm", "name": "WezTerm", "command": "wezterm"},
            {"id": "hyper", "name": "Hyper", "command": "hyper"},
            {"id": "xterm", "name": "XTerm", "command": "xterm"},
            {"id": "foot", "name": "Foot", "command": "foot"},
        ]

    # Check which tools are installed
    ides = []
    for ide_def in ide_definitions:
        installed = False
        path = ""
        if ide_def.get("command"):
            found = shutil.which(ide_def["command"])
            if found:
                installed = True
                path = found
        ides.append({
            "id": ide_def["id"],
            "name": ide_def["name"],
            "path": path,
            "installed": installed
        })

    terminals = []
    for term_def in terminal_definitions:
        installed = False
        path = ""
        if term_def.get("command"):
            found = shutil.which(term_def["command"])
            if found:
                installed = True
                path = found
        elif term_def.get("app") and system == "Darwin":
            # Check macOS applications
            app_path = f"/Applications/{term_def['app']}.app"
            if Path(app_path).exists():
                installed = True
                path = app_path
        terminals.append({
            "id": term_def["id"],
            "name": term_def["name"],
            "path": path,
            "installed": installed
        })

    return {
        "success": True,
        "data": {
            "ides": ides,
            "terminals": terminals
        }
    }
