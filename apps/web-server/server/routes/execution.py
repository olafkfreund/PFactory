"""
Task execution routes.

Handles starting, stopping, and monitoring task execution.
"""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..services.agent_service import get_agent_service
from ..websockets.events import emit_task_status
from .projects import load_projects
from .tasks import split_task_id, sync_worktree_to_main_spec

logger = logging.getLogger(__name__)

router = APIRouter()


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


class StartTaskRequest(BaseModel):
    """Request to start task execution."""

    auto_continue: bool = Field(True, description="Auto-continue to next phase")
    complexity: str | None = Field(None, description="Complexity override for spec creation")
    # Task execution options (matches frontend TaskStartOptions)
    parallel: bool | None = Field(None, description="Enable parallel execution")
    workers: int | None = Field(None, description="Number of parallel workers")
    model: str | None = Field(None, description="Model override for execution")
    baseBranch: str | None = Field(None, description="Base branch for worktree creation")
    mode: str | None = Field("full", description="Execution mode: 'quick' for simplified prompts, 'full' for comprehensive")


class RecoverTaskRequest(BaseModel):
    """Request to recover a stuck task."""

    targetStatus: str | None = Field("backlog", description="Target status after recovery")
    autoRestart: bool = Field(False, description="Auto-restart the task after recovery")


class TaskExecutionStatus(BaseModel):
    """Task execution status response."""

    task_id: str
    is_running: bool
    phase: str | None = None
    message: str | None = None


class RunningTasksResponse(BaseModel):
    """Response listing all running tasks."""

    tasks: list[str]
    count: int


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.get("/running", response_model=RunningTasksResponse)
async def get_running_tasks():
    """Get list of all currently running tasks."""
    agent_service = get_agent_service()
    running = agent_service.get_running_tasks()
    return RunningTasksResponse(tasks=running, count=len(running))


@router.get("/{task_id}/status", response_model=TaskExecutionStatus)
async def get_task_status(task_id: str):
    """Get execution status for a specific task."""
    agent_service = get_agent_service()
    is_running = agent_service.is_running(task_id)

    return TaskExecutionStatus(
        task_id=task_id,
        is_running=is_running,
    )


@router.get("/{task_id}/running")
async def is_task_running(task_id: str):
    """Check if a specific task is currently running."""
    agent_service = get_agent_service()
    is_running = agent_service.is_running(task_id)

    return {
        "task_id": task_id,
        "is_running": is_running,
    }


@router.post("/{task_id}/start")
async def start_task(task_id: str, request: StartTaskRequest, raw_request: Request):
    """Start execution of a task.

    The task must already exist (have a spec directory).
    This will run the planner, coder, and QA agents.
    """
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"[StartTask] ===== START ENDPOINT CALLED ===== task_id: {task_id}")

    # Parse task ID
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
            detail="Task/spec not found",
        )

    # Fix 3: Check if a VALID test_plan.json exists - if not, run spec creation first
    # This handles the case where projects.py created the spec directory but spec_runner.py hasn't run yet
    # A valid plan MUST have "phases" array - minimal plans with just {"status": "..."} are invalid
    import logging

    logger = logging.getLogger(__name__)
    test_plan = spec_dir / "test_plan.json"
    logger.info(f"[StartTask] Checking for test_plan.json at {test_plan}")
    logger.info(f"[StartTask] test_plan.json exists: {test_plan.exists()}")

    # Check if plan is valid (has phases/subtasks structure)
    plan_is_valid = False
    if test_plan.exists():
        try:
            import json

            plan_data = json.loads(test_plan.read_text())
            # Valid plan must have "phases" key (even if empty array)
            plan_is_valid = "phases" in plan_data and isinstance(plan_data.get("phases"), (list, dict))
            logger.info(f"[StartTask] Plan validity check: has_phases={plan_is_valid}, keys={list(plan_data.keys())}")

            # Guard against re-starting a completed task
            if plan_data.get("status") == "done":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot start a completed task. Reset the task status first.",
                )
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[StartTask] Failed to parse test_plan.json: {e}")
            plan_is_valid = False

    if not test_plan.exists() or not plan_is_valid:
        # Need to run spec creation first - read title/description from requirements.json
        import json
        from datetime import datetime
        logger.info(f"[StartTask] No valid implementation plan found, will run spec creation for {task_id}")
        requirements_file = spec_dir / "requirements.json"
        if not requirements_file.exists():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Task has no implementation plan and no requirements.json for spec creation",
            )

        try:
            requirements = json.loads(requirements_file.read_text())
            title = requirements.get("title", spec_id)
            description = requirements.get("description", "")
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid requirements.json format",
            )

        # Read complexity from request, or fall back to task metadata
        complexity = request.complexity
        if not complexity:
            metadata = requirements.get("metadata", {})
            meta_complexity = metadata.get("complexity")
            if meta_complexity in ("simple", "standard", "complex"):
                complexity = meta_complexity

        # === FAST PATH: Simple tasks skip spec creation entirely ===
        if complexity == "simple":
            logger.info(f"[StartTask] Simple task fast path: generating spec + plan programmatically for {task_id}")

            # 1. Generate minimal spec.md
            spec_file = spec_dir / "spec.md"
            if not spec_file.exists():
                spec_content = f"# {title}\n\n{description}\n"
                spec_file.write_text(spec_content)

            # 2. Generate test_plan.json with 1 subtask
            plan_data = {
                "feature": title,
                "workflow_type": "feature",
                "status": "in_progress",
                "current_phase": "coding",
                "phases": [
                    {
                        "phase": 1,
                        "name": "Implementation",
                        "subtasks": [
                            {
                                "id": "1.1",
                                "description": f"{title}: {description}" if description else title,
                                "status": "pending",
                            }
                        ],
                    }
                ],
                "last_updated": datetime.now().isoformat(),
            }
            test_plan.write_text(json.dumps(plan_data, indent=2))

            # 3. Pre-approve (skip review gate)
            review_state_file = spec_dir / "review_state.json"
            review_state_file.write_text(json.dumps({
                "approved": True,
                "approved_by": "auto-simple",
                "approved_at": datetime.now().isoformat(),
            }, indent=2))

            # 4. Set task_metadata for quick mode + reduced thinking
            task_metadata_file = spec_dir / "task_metadata.json"
            task_metadata = {}
            if task_metadata_file.exists():
                try:
                    task_metadata = json.loads(task_metadata_file.read_text())
                except (json.JSONDecodeError, OSError):
                    pass
            task_metadata["complexity"] = "simple"
            task_metadata["mode"] = "quick"
            task_metadata["isAutoProfile"] = True
            task_metadata["phaseThinking"] = {
                "spec": "low",
                "planning": "low",
                "coding": "medium",
                "qa": "low",
            }
            task_metadata_file.write_text(json.dumps(task_metadata, indent=2))

            # Mark plan as valid so we fall through to the execution path below
            plan_is_valid = True
            logger.info("[StartTask] Fast path: spec + plan generated, proceeding to execution")

        else:
            # === STANDARD PATH: Run full spec creation ===
            agent_service = get_agent_service()

            try:
                await agent_service.start_spec_creation(
                    task_id=task_id,
                    project_path=project_path,
                    title=title,
                    description=description,
                    complexity=complexity,
                    auto_continue=request.auto_continue,
                )

                # Persist status to test_plan.json for page refresh survival
                # Create minimal plan file if it doesn't exist
                try:
                    if test_plan.exists():
                        plan = json.loads(test_plan.read_text())
                    else:
                        plan = {}
                    plan["status"] = "in_progress"
                    plan["phase"] = "spec_creation"
                    test_plan.write_text(json.dumps(plan, indent=2))
                    logger.info(f"[StartTask] Persisted status=in_progress (spec creation) to {test_plan}")
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning(f"[StartTask] Failed to persist spec creation status: {e}")

                # Emit status to show spec creation in progress
                await emit_task_status(task_id, "in_progress")
                return {
                    "success": True,
                    "task_id": task_id,
                    "message": "Spec creation started (no implementation plan found)",
                }
            except Exception as e:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to start spec creation: {str(e)}",
                )

    # Sync runtime options to task_metadata.json for backend to read
    # This ensures model/thinking/baseBranch overrides are available to run.py
    import json

    task_metadata_file = spec_dir / "task_metadata.json"
    task_metadata = {}
    if task_metadata_file.exists():
        try:
            task_metadata = json.loads(task_metadata_file.read_text())
        except json.JSONDecodeError:
            pass

    # Apply runtime overrides
    if request.model:
        task_metadata["model"] = request.model
    if request.baseBranch:
        task_metadata["baseBranch"] = request.baseBranch

    # Write updated task_metadata.json if we have any settings
    if task_metadata:
        task_metadata_file.write_text(json.dumps(task_metadata, indent=2))

    # Determine mode: use request mode if provided, otherwise fall back to task_metadata
    effective_mode = request.mode
    if not effective_mode or effective_mode == "full":
        # Check if mode was set during task creation
        effective_mode = task_metadata.get("mode", "full")

    # Auto-derive quick mode from simple complexity
    if effective_mode == "full":
        task_complexity = task_metadata.get("complexity")
        if task_complexity == "simple":
            effective_mode = "quick"
            logger.info("[StartTask] Auto-derived quick mode from simple complexity")

    agent_service = get_agent_service()

    # Check if plan was manually approved - if so, use --force to bypass review check
    force_execution = False
    review_state_file = spec_dir / "review_state.json"
    if review_state_file.exists():
        try:
            review_data = json.loads(review_state_file.read_text())
            if review_data.get("approved", False):
                force_execution = True
                logger.info(f"[StartTask] Plan was manually approved for {task_id}, using --force")
        except (json.JSONDecodeError, OSError):
            pass

    if agent_service.is_running(task_id):
        if force_execution:
            # Plan was approved — clean up stale spec creation process before starting execution
            logger.info(f"[StartTask] Cleaning up stale spec creation process for approved task {task_id}")
            try:
                await agent_service.stop_task(task_id)
            except Exception as stop_err:
                logger.warning(f"[StartTask] Failed to stop stale process: {stop_err}")
                agent_service.running_tasks.pop(task_id, None)
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Task is already running",
            )

    # If review is required but not yet approved, set human_review status
    # and return early WITHOUT starting the subprocess (which would just exit)
    if not force_execution:
        task_metadata_file = spec_dir / "task_metadata.json"
        require_review = False
        if task_metadata_file.exists():
            try:
                tm = json.loads(task_metadata_file.read_text())
                require_review = tm.get("requireReviewBeforeCoding", False)
            except (json.JSONDecodeError, OSError):
                pass

        if require_review:
            try:
                if test_plan.exists():
                    plan = json.loads(test_plan.read_text())
                    plan["status"] = "human_review"
                    plan["reviewReason"] = "plan_review"
                    test_plan.write_text(json.dumps(plan, indent=2))
                    logger.info(f"[StartTask] Plan requires approval for {task_id}, set human_review")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[StartTask] Failed to persist human_review status: {e}")

            await emit_task_status(task_id, "human_review", "plan_review")

            return {
                "success": True,
                "task_id": task_id,
                "message": "Task requires plan approval before coding can begin",
                "status": "human_review",
                "reviewReason": "plan_review",
            }

    # Extract user_id from auth context for email notifications
    _user = getattr(raw_request.state, "user", None)
    _user_id = _user["id"] if isinstance(_user, dict) and _user.get("id") else ""

    # Delegation branch (gap #1 from #144) — when the task asks for
    # Copilot delegation AND the project's git provider is GitHub AND we
    # know which issue this task came from, hand off to the shared
    # delegation runner instead of running the local coder/QA pipeline.
    settings = projects[project_id].get("settings") or {}
    provider_type = (settings.get("gitProvider") or "github").lower()
    wants_delegation = bool(task_metadata.get("enableDelegation"))
    issue_number = task_metadata.get("githubIssueNumber")
    if isinstance(issue_number, str) and issue_number.isdigit():
        issue_number = int(issue_number)

    if wants_delegation and provider_type in ("github", "gitlab") and isinstance(issue_number, int):
        from ..services.auto_fix_service import _provider_for
        from ..services.delegation_runner import run_delegation

        try:
            provider = _provider_for(project_id)
            result = await run_delegation(
                project_id=project_id,
                project_path=project_path,
                spec_id=spec_id,
                issue_number=int(issue_number),
                provider=provider,
            )
        except Exception as e:
            logger.exception(f"[StartTask] Delegation failed for {task_id}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Delegation failed: {e}",
            )
        return {
            "success": True,
            "task_id": task_id,
            "status": "delegated",
            "delegatedAt": result["delegatedAt"],
            "commentPosted": result["commentPosted"],
            "commentSkippedAsDuplicate": result["commentSkippedAsDuplicate"],
            "copilotAssigned": result["copilotAssigned"],
        }

    if wants_delegation and not isinstance(issue_number, int):
        logger.warning(
            "[StartTask] Task %s has enableDelegation=true but no githubIssueNumber "
            "in metadata — falling through to local execution",
            task_id,
        )

    try:
        await agent_service.start_task_execution(
            task_id=task_id,
            project_path=project_path,
            spec_id=spec_id,
            auto_continue=request.auto_continue,
            base_branch=request.baseBranch,
            mode=effective_mode,
            force=force_execution,
            user_id=_user_id,
        )

        # Persist status to test_plan.json for page refresh survival
        # This ensures the task shows as "in_progress" even after browser refresh
        try:
            if test_plan.exists():
                plan = json.loads(test_plan.read_text())
                plan["status"] = "in_progress"
                test_plan.write_text(json.dumps(plan, indent=2))
                logger.info(f"[StartTask] Persisted status=in_progress to {test_plan}")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[StartTask] Failed to persist status: {e}")

        # Emit status change for real-time frontend update
        await emit_task_status(task_id, "in_progress")
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start task: {str(e)}",
        )

    return {
        "success": True,
        "task_id": task_id,
        "message": "Task execution started",
    }


@router.post("/{task_id}/stop")
async def stop_task(task_id: str):
    """Stop a running task."""
    agent_service = get_agent_service()

    if not agent_service.is_running(task_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task is not running",
        )

    success = await agent_service.stop_task(task_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to stop task",
        )

    # Emit status change for real-time frontend update
    await emit_task_status(task_id, "backlog")

    return {
        "success": True,
        "task_id": task_id,
        "message": "Task stopped",
    }


@router.post("/{task_id}/recover")
async def recover_task(task_id: str, request: RecoverTaskRequest = RecoverTaskRequest()):
    """Recover a stuck task by resetting its status.

    Use this when a task shows as running but the process has died.
    Optionally auto-restart the task after recovery.
    """

    # Parse task ID
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
            detail="Task/spec not found",
        )

    # Clean up from running_tasks if present
    agent_service = get_agent_service()
    if task_id in agent_service.running_tasks:
        try:
            proc = agent_service.running_tasks[task_id]
            proc.terminate()
            await proc.wait()
        except Exception:
            pass
        # Only delete if still present (might have been cleaned up by monitor)
        if task_id in agent_service.running_tasks:
            del agent_service.running_tasks[task_id]

    # Sync from worktree to main spec first to preserve progress
    sync_worktree_to_main_spec(project_path, spec_id)

    # Reset status in test_plan.json
    plan_file = spec_dir / "test_plan.json"
    plan = {}
    if plan_file.exists():
        try:
            plan = json.loads(plan_file.read_text())
        except json.JSONDecodeError:
            pass

    # Reset status from request body or default to backlog
    reset_status = request.targetStatus or "backlog"
    auto_restart = request.autoRestart
    auto_restarted = False
    auto_restart_error = None

    # Reset any reviewReason when moving out of human review states
    if reset_status in ("backlog", "in_progress", "ai_review", "done"):
        plan.pop("reviewReason", None)

    plan["status"] = reset_status
    plan_file.write_text(json.dumps(plan, indent=2))

    # Auto-restart if requested
    if auto_restart:
        try:
            await agent_service.start_task_execution(
                task_id=task_id,
                project_path=project_path,
                spec_id=spec_id,
                auto_continue=True,
            )
            auto_restarted = True
            reset_status = "in_progress"

            # Persist updated status so UI doesn't immediately revert to backlog on refresh
            plan["status"] = reset_status
            plan.pop("reviewReason", None)
            plan_file.write_text(json.dumps(plan, indent=2))
        except Exception:
            # If auto-restart fails, still return success for recovery
            logger.exception("Auto-restart failed for %s", task_id)
            auto_restart_error = "Auto-restart failed; task remains in reset state"

    # Emit status change via WebSocket (single final status to avoid UI flicker)
    await emit_task_status(task_id, reset_status)

    # Return wrapped response to match frontend expectations
    return {
        "success": True,
        "data": {
            "task_id": task_id,
            "message": "Task recovered" + (" and restarted" if auto_restarted else f" and reset to {reset_status}"),
            "newStatus": reset_status,
            "autoRestarted": auto_restarted,
            "autoRestartError": auto_restart_error,
            "recovered": True,
        },
    }


@router.post("/create-and-run")
async def create_and_run_task(
    project_id: str,
    title: str,
    description: str,
    request: StartTaskRequest,
):
    """Create a new task and immediately start execution.

    This is a convenience endpoint that combines task creation
    with spec creation and execution.
    """
    projects = load_projects()

    if project_id not in projects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    project_path = Path(projects[project_id]["path"])
    agent_service = get_agent_service()

    # Generate a temporary task ID for spec creation
    import uuid

    temp_task_id = f"{project_id}:pending-{uuid.uuid4().hex[:8]}"

    try:
        await agent_service.start_spec_creation(
            task_id=temp_task_id,
            project_path=project_path,
            title=title,
            description=description,
            complexity=request.complexity,
            auto_continue=request.auto_continue,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start task creation: {str(e)}",
        )

    return {
        "success": True,
        "task_id": temp_task_id,
        "message": "Task creation started. Connect to WebSocket for progress updates.",
    }
