"""
Planner Agent Module
====================

Handles follow-up planner sessions for adding new subtasks to completed specs.
"""

import logging
from pathlib import Path

from core.client import create_client
from phase_config import (
    get_phase_model,
    get_phase_thinking_budget,
    get_provider_extra_kwargs,
    infer_provider_from_model,
)
from phase_event import ExecutionPhase, emit_phase
from providers.factory import get_provider
from task_logger import (
    LogPhase,
    get_task_logger,
)
from ui import (
    BuildState,
    Icons,
    StatusManager,
    bold,
    box,
    highlight,
    icon,
    muted,
    print_status,
)

from .auth_tagging import apply_requires_auth_from_config
from .session import run_agent_session

logger = logging.getLogger(__name__)


async def run_followup_planner(
    project_dir: Path,
    spec_dir: Path,
    model: str,
    verbose: bool = False,
) -> bool:
    """
    Run the follow-up planner to add new subtasks to a completed spec.

    This is a simplified version of run_autonomous_agent that:
    1. Creates a client
    2. Loads the followup planner prompt
    3. Runs a single planning session
    4. Returns after the plan is updated (doesn't enter coding loop)

    The planner agent will:
    - Read FOLLOWUP_REQUEST.md for the new task
    - Read the existing test_plan.json
    - Add new phase(s) with pending subtasks
    - Update the plan status back to in_progress

    Args:
        project_dir: Root directory for the project
        spec_dir: Directory containing the completed spec
        model: Claude model to use
        verbose: Whether to show detailed output

    Returns:
        bool: True if planning completed successfully
    """
    from prompts import get_followup_planner_prompt
    from test_plan import ImplementationPlan

    # Initialize status manager for ccstatusline
    status_manager = StatusManager(project_dir)
    status_manager.set_active(spec_dir.name, BuildState.PLANNING)
    emit_phase(ExecutionPhase.PLANNING, "Follow-up planning")

    # Initialize task logger for persistent logging
    task_logger = get_task_logger(spec_dir)

    # Show header
    content = [
        bold(f"{icon(Icons.GEAR)} FOLLOW-UP PLANNER SESSION"),
        "",
        f"Spec: {highlight(spec_dir.name)}",
        muted("Adding follow-up work to completed spec."),
        "",
        muted("The agent will read your FOLLOWUP_REQUEST.md and add new subtasks."),
    ]
    print()
    print(box(content, width=70, style="heavy"))
    print()

    # Start planning phase in task logger
    if task_logger:
        task_logger.start_phase(LogPhase.PLANNING, "Starting follow-up planning...")
        task_logger.set_session(1)

    # Create client with phase-specific model and thinking budget
    # Respects task_metadata.json configuration when no CLI override
    planning_model = get_phase_model(spec_dir, "planning", model)
    planning_thinking_budget = get_phase_thinking_budget(spec_dir, "planning")
    provider_name = infer_provider_from_model(planning_model)
    if provider_name == "claude":
        client = create_client(
            project_dir,
            spec_dir,
            planning_model,
            max_thinking_tokens=planning_thinking_budget,
        )
    else:
        provider_kwargs = {
            "model": planning_model,
            "working_dir": project_dir,
            **get_provider_extra_kwargs(provider_name, planning_model),
        }
        client = get_provider(
            provider_name,
            phase="planning",
            **provider_kwargs,
        )

    # Generate follow-up planner prompt
    prompt = get_followup_planner_prompt(spec_dir)

    print_status("Running follow-up planner...", "progress")
    print()

    try:
        # Run single planning session
        async with client:
            status, response, _error_info = await run_agent_session(
                client, prompt, spec_dir, verbose, phase=LogPhase.PLANNING
            )

        # End planning phase in task logger
        if task_logger:
            task_logger.end_phase(
                LogPhase.PLANNING,
                success=(status != "error"),
                message="Follow-up planning session completed",
            )

        if status == "error":
            print()
            print_status("Follow-up planning failed", "error")
            status_manager.update(state=BuildState.ERROR)
            return False

        # Verify the plan was updated (should have pending subtasks now)
        plan_file = spec_dir / "test_plan.json"
        if plan_file.exists():
            plan = ImplementationPlan.load(plan_file)

            # Check if there are any pending subtasks
            all_subtasks = [c for p in plan.phases for c in p.subtasks]
            pending_subtasks = [c for c in all_subtasks if c.status.value == "pending"]

            if pending_subtasks:
                # Reset the plan status to in_progress (in case planner didn't)
                plan.reset_for_followup()
                plan.save(plan_file)

                print()
                content = [
                    bold(f"{icon(Icons.SUCCESS)} FOLLOW-UP PLANNING COMPLETE"),
                    "",
                    f"New pending subtasks: {highlight(str(len(pending_subtasks)))}",
                    f"Total subtasks: {len(all_subtasks)}",
                    "",
                    muted("Next steps:"),
                    f"  Run: {highlight(f'python pfactory/run.py --spec {spec_dir.name}')}",
                ]
                print(box(content, width=70, style="heavy"))
                print()
                status_manager.update(state=BuildState.PAUSED)
                return True
            else:
                print()
                print_status(
                    "Warning: No pending subtasks found after planning", "warning"
                )
                print(muted("The planner may not have added new subtasks."))
                print(muted("Check test_plan.json manually."))
                status_manager.update(state=BuildState.PAUSED)
                return False
        else:
            print()
            print_status("Error: test_plan.json not found after planning", "error")
            status_manager.update(state=BuildState.ERROR)
            return False

    except Exception as e:
        print()
        print_status(f"Follow-up planning error: {e}", "error")
        if task_logger:
            task_logger.log_error(f"Follow-up planning error: {e}", LogPhase.PLANNING)
        status_manager.update(state=BuildState.ERROR)
        return False


# ---------------------------------------------------------------------------
# PFactory Planner (Tasks 5-6, #6 / Task 5 v0.2 polyglot extension #21)
#
# v0.1 (Task 5 #6): initial SDK wiring, lane-tagged subtasks, retry logic,
#   replan mode, auto-fire scheduling.
# v0.2 (Task 5 #21): polyglot schema — each subtask carries
#   (language, framework, lane, target_name, intent).
#   _validate_emitted_plan now enforces (language, framework, lane) against
#   the framework registry (error_kind="invalid_framework").
#   get_pfactory_planner_prompt injects FRAMEWORK REGISTRY + TESTS CATALOG
#   context blocks so the agent picks the right framework per subtask.
# ---------------------------------------------------------------------------

import asyncio
import json
import logging as _logging
import os
import traceback
from datetime import datetime, timezone
from typing import Literal

_planner_log = _logging.getLogger(__name__ + ".pfactory")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_status(spec_dir: Path) -> dict:
    """Read status.json or return an empty dict if missing/corrupt."""
    status_path = spec_dir / "status.json"
    if not status_path.exists():
        return {}
    try:
        return json.loads(status_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_status_patch(spec_dir: Path, **fields: object) -> None:
    """Merge ``fields`` into status.json (atomic-ish single-file write)."""
    status = _read_status(spec_dir)
    status.update(fields)
    status["updated_at"] = _now_iso()
    (spec_dir / "status.json").write_text(json.dumps(status, indent=2))
    # Best-effort push-based progress event (#95); no-op unless opted in.
    from agents.stage_events import emit_stage_event

    emit_stage_event(spec_dir, status, stage="planner")


# Subtask cap — anything above is truncated post-emit with a warning.
_HARD_SUBTASK_CAP = 30
_SOFT_SUBTASK_WARN = 15


def _count_subtasks(plan) -> int:
    """Total subtasks across all phases."""
    return sum(len(p.subtasks) for p in plan.phases)


def _truncate_subtasks(plan, cap: int) -> int:
    """Drop subtasks past ``cap`` (keeping phase ordering).

    Returns the number of subtasks dropped.
    """
    dropped = 0
    keep = cap
    for phase in plan.phases:
        if keep <= 0:
            dropped += len(phase.subtasks)
            phase.subtasks = []
            continue
        if len(phase.subtasks) > keep:
            dropped += len(phase.subtasks) - keep
            phase.subtasks = phase.subtasks[:keep]
            keep = 0
        else:
            keep -= len(phase.subtasks)
    return dropped


async def _resolve_planner_client(spec_dir: Path, project_dir: Path):
    """Resolve the Claude Agent SDK client for the planning phase.

    Wraps the inherited `create_client` / `get_provider` factories so
    tests can monkey-patch this one function instead of two.
    """
    # Heavy imports deferred to runtime so test_planner_stub.py can
    # mock the SDK surface without forcing the full backend chain
    # at module import time.
    from core.client import create_client
    from phase_config import (
        get_phase_model,
        get_phase_thinking_budget,
        get_provider_extra_kwargs,
        infer_provider_from_model,
    )
    from providers.factory import get_provider

    planning_model = get_phase_model(spec_dir, "planning", None)
    provider_name = infer_provider_from_model(planning_model)
    if provider_name == "claude":
        thinking_budget = get_phase_thinking_budget(spec_dir, "planning")
        return create_client(
            project_dir,
            spec_dir,
            planning_model,
            max_thinking_tokens=thinking_budget,
        )
    # get_provider_extra_kwargs may itself return a "model" (e.g. the stripped
    # name for openai-compatible / studio endpoints) — let it win and avoid
    # passing model= twice (TypeError: multiple values for 'model').
    extra = get_provider_extra_kwargs(provider_name, planning_model)
    # The Ollama provider runs file ops through PFactory's ToolExecutor, which
    # is sandboxed to working_dir (the SUT project). The Planner writes
    # test_plan.json into the spec/workspace dir, outside that boundary — so
    # allow it explicitly. Other agentic providers (codex/copilot/gemini) use
    # their own sandboxes and don't take this kwarg.
    if provider_name == "ollama":
        extra["extra_roots"] = [spec_dir]
    return get_provider(
        provider_name,
        phase="planning",
        working_dir=project_dir,
        model=extra.pop("model", planning_model),
        **extra,
    )


async def _invoke_session(
    client,
    prompt: str,
    spec_dir: Path,
    verbose: bool,
) -> tuple[str, str, dict]:
    """Thin wrapper around run_agent_session so tests can patch one symbol.

    Returns the (status, response, error_info) triple that
    run_agent_session yields.
    """
    from agents.session import run_agent_session
    from task_logger import LogPhase

    async with client:
        return await run_agent_session(
            client, prompt, spec_dir, verbose, phase=LogPhase.PLANNING
        )


def _validate_emitted_plan(spec_dir: Path) -> tuple[bool, str, object | None]:
    """Load + validate test_plan.json the agent just wrote.

    Returns ``(ok, error_kind, plan)``:
      - ok=True, error_kind="", plan=ImplementationPlan → valid
      - ok=False, error_kind="missing"          → file not written
      - ok=False, error_kind="json"             → invalid JSON
      - ok=False, error_kind="schema"           → JSON valid but not a plan
      - ok=False, error_kind="invalid_framework" → (language, framework, lane)
          not consistent with the framework registry (v0.2 polyglot check).
          v0.1 subtasks that have neither language nor framework set pass
          through this check unchanged — backward-compat is preserved.
    """
    from test_plan import ImplementationPlan  # local: avoid SDK cost on import

    plan_file = spec_dir / "test_plan.json"
    if not plan_file.exists():
        return False, "missing", None
    try:
        # ImplementationPlan.load reads + parses + builds the dataclass.
        plan = ImplementationPlan.load(plan_file)
    except json.JSONDecodeError as exc:
        return False, "json", str(exc)
    except (KeyError, TypeError, ValueError) as exc:
        return False, "schema", str(exc)

    # v0.2: validate every subtask's (language, framework, lane) against
    # the framework registry.  v0.1-style subtasks (no language / framework
    # fields) skip this check so legacy plans round-trip cleanly.
    try:
        from framework_registry import load_registry  # deferred: not on hot path

        registry = load_registry()
    except Exception as exc:
        # Registry unavailable (e.g. frameworks/ dir missing in tests).
        # Log and skip the check — a missing registry is not a plan error.
        _planner_log.warning(
            "planner: framework registry unavailable, skipping (language, framework)"
            " validation: %s",
            exc,
        )
        return True, "", plan

    for phase in plan.phases:
        for st in phase.subtasks:
            # Both None → v0.1 subtask; skip.
            if st.framework is None and st.language is None:
                continue
            # Exactly one set → malformed.
            if st.framework is None or st.language is None:
                return (
                    False,
                    "invalid_framework",
                    (
                        f"subtask {st.id!r}: must set both 'language' AND 'framework', "
                        f"or neither (got language={st.language!r}, "
                        f"framework={st.framework!r})"
                    ),
                )
            # Framework not in registry.
            if st.framework not in registry:
                return (
                    False,
                    "invalid_framework",
                    (
                        f"subtask {st.id!r}: framework {st.framework!r} is not in the "
                        f"registry. Known frameworks: {sorted(registry.keys())}"
                    ),
                )
            descriptor = registry[st.framework]
            # Language mismatch.
            if descriptor.language != st.language:
                return (
                    False,
                    "invalid_framework",
                    (
                        f"subtask {st.id!r}: framework {st.framework!r} targets language "
                        f"{descriptor.language!r}, but subtask declared "
                        f"language={st.language!r}"
                    ),
                )
            # Lane not supported by this framework.
            lane_str = st.lane.value if hasattr(st.lane, "value") else str(st.lane)
            supported = [
                ln.value if hasattr(ln, "value") else str(ln) for ln in descriptor.lanes
            ]
            if lane_str not in supported:
                return (
                    False,
                    "invalid_framework",
                    (
                        f"subtask {st.id!r}: framework {st.framework!r} supports lanes "
                        f"{supported}, but subtask declared lane {lane_str!r}"
                    ),
                )

    return True, "", plan


# ─── Replan helpers (commit 5 of 6) ────────────────────────────────────────

# After this many replans on a single subtask, mark it stuck. The Triager
# omits stuck subtasks from the commit phase but they remain in the plan
# for the report.
_STUCK_AFTER_REPLANS = 2


def _load_replan_request(spec_dir: Path) -> tuple[bool, str, dict | None]:
    """Read + validate context/replan_request.json.

    Returns ``(ok, error, payload)``. On success, ``payload`` has at
    minimum the ``subtask_id`` field (other fields optional).
    """
    rr_path = spec_dir / "context" / "replan_request.json"
    if not rr_path.exists():
        return (
            False,
            "replan_request.json missing — Gen-Functional should write this",
            None,
        )
    try:
        data = json.loads(rr_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return False, f"replan_request.json unreadable: {exc}", None
    if not isinstance(data, dict) or "subtask_id" not in data:
        return False, "replan_request.json missing required 'subtask_id'", None
    return True, "", data


def _find_subtask_by_id(plan, subtask_id: str):
    """Locate a Subtask by ID across all phases. Returns (phase, subtask) or (None, None)."""
    for phase in plan.phases:
        for subtask in phase.subtasks:
            if subtask.id == subtask_id:
                return phase, subtask
    return None, None


def _bump_replan_count_and_maybe_stuck(plan, subtask_id: str) -> tuple[int, bool]:
    """Bump replan_count on the original subtask; mark stuck at ≥ 2.

    Returns ``(new_count, became_stuck)``. Caller is responsible for
    saving the plan back.
    """
    # Need the dataclass-level enum to set status. Imported here to keep
    # the module top-level light.
    from test_plan import SubtaskStatus

    _phase, subtask = _find_subtask_by_id(plan, subtask_id)
    if subtask is None:
        # The original subtask no longer exists — defensive no-op.
        return 0, False
    subtask.replan_count = (subtask.replan_count or 0) + 1
    became_stuck = subtask.replan_count >= _STUCK_AFTER_REPLANS
    if became_stuck:
        # Use the enum value (string) — SubtaskStatus members serialise
        # as their .value via to_dict.
        # "stuck" isn't in the base SubtaskStatus enum, but the from_dict
        # tolerates the literal so we set it via the raw string-enum
        # access. If "stuck" ever lands as a proper enum member, this
        # keeps working.
        try:
            subtask.status = SubtaskStatus("stuck")
        except ValueError:
            # SubtaskStatus enum doesn't include "stuck" yet — use
            # FAILED as the closest existing semantic equivalent and
            # rely on the planner_warnings to convey "stuck-via-replan".
            subtask.status = SubtaskStatus.FAILED
    return subtask.replan_count, became_stuck


def _existing_phase_ids(plan) -> set[int]:
    """Return the set of phase numbers in the plan (for the preserve check)."""
    return {p.phase for p in plan.phases}


def _check_existing_phases_preserved(
    plan_before,
    plan_after,
) -> tuple[bool, str]:
    """Verify the agent didn't drop / renumber existing phases on replan.

    Returns ``(ok, error)``. The replan prompt says "preserve every
    existing phase verbatim, append exactly one new replan-N phase".
    Soft check: phases present BEFORE must still be present AFTER (by
    .phase number). We don't deep-compare subtasks; the agent may bump
    a replan_count which is legitimate.
    """
    before_ids = _existing_phase_ids(plan_before)
    after_ids = _existing_phase_ids(plan_after)
    missing = before_ids - after_ids
    if missing:
        return False, f"agent dropped existing phases: {sorted(missing)}"
    # Allow exactly one new phase (the replan-N). If more, flag.
    new_phases = after_ids - before_ids
    if len(new_phases) > 1:
        return False, f"agent added {len(new_phases)} new phases; expected 1 replan-N"
    return True, ""


async def run_planner(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "replan"] = "initial",
    verbose: bool = False,
) -> bool:
    """Run the PFactory Planner agent.

    Builds the test-oriented system prompt via ``get_pfactory_planner_prompt``,
    invokes the Claude Agent SDK session via the inherited
    ``run_agent_session`` machinery, then post-validates the emitted
    ``test_plan.json``. Retries once on missing/malformed output with a
    reminder turn before giving up.

    Replan mode (commit 5) is currently a stub that surfaces the
    deferred status and returns False — it'll wire up when the replan
    path lands.

    Args:
        spec_dir: PFactory workspace spec dir
            (``~/.pfactory/workspaces/<project_id>/specs/<spec_id>/``).
        project_dir: AIFactory project root_path. Used by the SDK
            client for Glob/Grep over the diffed code surface.
        mode: 'initial' for first plan, 'replan' for follow-up after
            Gen-Functional rejection (commit 5).
        verbose: forwarded to ``run_agent_session``.

    Returns:
        ``True`` on a valid plan (including ``planned_empty`` — that's
        a warning state, not a failure). ``False`` on hard failure
        (session error, missing file after retry, parse failure after
        retry, malformed workspace).

    Side effects:
        - Updates ``spec_dir/status.json`` (status, phase, planner_*).
        - The SDK agent writes ``spec_dir/test_plan.json`` via its
          Write tool. This function may also write to status.json's
          ``planner_warnings`` list with truncation / soft-fail notes.
    """
    if not spec_dir.is_dir():
        _planner_log.error("planner: spec_dir %s does not exist", spec_dir)
        return False

    if mode == "replan":
        return await _run_planner_replan(spec_dir, project_dir, verbose)

    try:
        _write_status_patch(
            spec_dir, status="planning", phase="planner_initial_started"
        )

        # Build the system prompt (loads planner.md + prepends SPEC CONTEXT)
        from prompts_pkg.prompts import get_pfactory_planner_prompt

        prompt = get_pfactory_planner_prompt(spec_dir, project_dir)

        # Resolve the SDK client
        client = await _resolve_planner_client(spec_dir, project_dir)

        # Run the agent session — agent's Write tool emits test_plan.json
        session_status, _response, _err = await _invoke_session(
            client, prompt, spec_dir, verbose
        )
        if session_status == "error":
            _write_status_patch(
                spec_dir,
                status="planner_failed",
                phase="planner_session_error",
                planner_error="run_agent_session returned status=error",
            )
            return False

        # Validate the emitted plan; retry once on missing/malformed.
        ok, err_kind, plan = _validate_emitted_plan(spec_dir)
        if not ok:
            _planner_log.warning(
                "planner: first session produced %s (%s); retrying once",
                err_kind,
                plan,
            )
            retry_prompt = _build_retry_prompt(prompt, err_kind, str(plan or "")[:300])
            client_retry = await _resolve_planner_client(spec_dir, project_dir)
            retry_status, _r, _re = await _invoke_session(
                client_retry, retry_prompt, spec_dir, verbose
            )
            if retry_status == "error":
                _write_status_patch(
                    spec_dir,
                    status="planner_failed",
                    phase="planner_session_error",
                    planner_error="retry session returned status=error",
                )
                return False
            ok, err_kind, plan = _validate_emitted_plan(spec_dir)
            if not ok:
                _write_status_patch(
                    spec_dir,
                    status="planner_failed",
                    phase=f"planner_invalid_{err_kind}_after_retry",
                    planner_error=f"after retry: {err_kind} — {str(plan or '')[:200]}",
                )
                return False

        # plan is now a valid ImplementationPlan instance.
        subtask_count = _count_subtasks(plan)
        warnings: list[str] = []

        # #107 task 6: deterministically tag subtasks whose target uses
        # ref-auth in .pfactory.yml so the storageState login path is used.
        # Best-effort — a missing/malformed config tags nothing.
        auth_tagged = apply_requires_auth_from_config(plan, project_dir)
        if auth_tagged:
            plan.save(spec_dir / "test_plan.json")
            warnings.append(
                f"tagged {auth_tagged} subtask(s) requires_auth from "
                ".pfactory.yml ref-auth targets (#107)"
            )

        if subtask_count > _HARD_SUBTASK_CAP:
            dropped = _truncate_subtasks(plan, _HARD_SUBTASK_CAP)
            warnings.append(
                f"emitted {subtask_count} subtasks; truncated to "
                f"{_HARD_SUBTASK_CAP} (dropped {dropped})"
            )
            plan.save(spec_dir / "test_plan.json")
            subtask_count = _HARD_SUBTASK_CAP
        elif subtask_count > _SOFT_SUBTASK_WARN:
            warnings.append(
                f"emitted {subtask_count} subtasks "
                f"(soft warning above {_SOFT_SUBTASK_WARN})"
            )

        if subtask_count == 0:
            _write_status_patch(
                spec_dir,
                status="planned_empty",
                phase="planner_initial_complete",
                planner_warnings=warnings
                + [
                    "agent emitted 0 subtasks — downstream pipeline will have nothing to do"
                ],
                subtask_count=0,
            )
            _advance_to_gen_functional(spec_dir, project_dir)
            return True

        _write_status_patch(
            spec_dir,
            status="planned",
            phase="planner_initial_complete",
            planner_warnings=warnings,
            subtask_count=subtask_count,
        )
        _advance_to_gen_functional(spec_dir, project_dir)
        return True

    except Exception as exc:
        _planner_log.error("planner failed: %s\n%s", exc, traceback.format_exc())
        _write_status_patch(
            spec_dir,
            status="planner_failed",
            phase=f"planner_{mode}_exception",
            planner_error=str(exc)[:500],
        )
        return False


async def _run_planner_replan(
    spec_dir: Path,
    project_dir: Path,
    verbose: bool,
) -> bool:
    """Replan-mode body. Mirrors initial-mode structure with the additional
    post-session bookkeeping that distinguishes replan from initial:

      - Reads context/replan_request.json for the rejection details
      - Verifies the existing test_plan.json before the session
      - Verifies the agent preserved existing phases after the session
      - Bumps replan_count on the original (rejected) subtask
      - Marks the original subtask stuck if replan_count >= 2

    Returns True on success, False on hard failure. Failures leave
    status.json with a descriptive phase + planner_error.
    """
    try:
        _write_status_patch(
            spec_dir,
            status="planning",
            phase="planner_replan_started",
        )

        # 1. Load + validate replan_request.json
        rr_ok, rr_err, replan_request = _load_replan_request(spec_dir)
        if not rr_ok:
            _write_status_patch(
                spec_dir,
                status="planner_failed",
                phase="planner_replan_missing_request",
                planner_error=rr_err,
            )
            return False

        # 2. Load the existing test_plan.json — replan MUST have an
        #    existing plan to amend; if it's missing, the caller should
        #    invoke initial mode instead.
        ok_before, kind_before, plan_before = _validate_emitted_plan(spec_dir)
        if not ok_before:
            _write_status_patch(
                spec_dir,
                status="planner_failed",
                phase="planner_replan_no_existing_plan",
                planner_error=(
                    f"replan requires an existing valid test_plan.json — got {kind_before}"
                ),
            )
            return False

        # 3. Build the replan prompt + invoke session.
        from prompts_pkg.prompts import get_pfactory_planner_replan_prompt

        prompt = get_pfactory_planner_replan_prompt(spec_dir, project_dir)
        client = await _resolve_planner_client(spec_dir, project_dir)
        session_status, _response, _err = await _invoke_session(
            client, prompt, spec_dir, verbose
        )
        if session_status == "error":
            _write_status_patch(
                spec_dir,
                status="planner_failed",
                phase="planner_replan_session_error",
                planner_error="run_agent_session returned status=error",
            )
            return False

        # 4. Validate the updated plan. Retry once on missing/malformed
        #    (same pattern as initial mode).
        ok_after, err_kind, plan_after = _validate_emitted_plan(spec_dir)
        if not ok_after:
            _planner_log.warning(
                "replan: first session produced %s (%s); retrying once",
                err_kind,
                plan_after,
            )
            retry_prompt = _build_retry_prompt(
                prompt, err_kind, str(plan_after or "")[:300]
            )
            client_retry = await _resolve_planner_client(spec_dir, project_dir)
            retry_status, _r, _re = await _invoke_session(
                client_retry, retry_prompt, spec_dir, verbose
            )
            if retry_status == "error":
                _write_status_patch(
                    spec_dir,
                    status="planner_failed",
                    phase="planner_replan_session_error",
                    planner_error="retry session returned status=error",
                )
                return False
            ok_after, err_kind, plan_after = _validate_emitted_plan(spec_dir)
            if not ok_after:
                _write_status_patch(
                    spec_dir,
                    status="planner_failed",
                    phase=f"planner_replan_invalid_{err_kind}_after_retry",
                    planner_error=(
                        f"after retry: {err_kind} — {str(plan_after or '')[:200]}"
                    ),
                )
                return False

        # 5. Verify the agent preserved existing phases (didn't rewrite the plan).
        preserve_ok, preserve_err = _check_existing_phases_preserved(
            plan_before, plan_after
        )
        if not preserve_ok:
            _write_status_patch(
                spec_dir,
                status="planner_failed",
                phase="planner_replan_phases_lost",
                planner_error=preserve_err,
            )
            return False

        # 6. Bump replan_count on the original subtask + check stuck.
        original_subtask_id = replan_request["subtask_id"]
        new_count, became_stuck = _bump_replan_count_and_maybe_stuck(
            plan_after, original_subtask_id
        )

        warnings: list[str] = []
        if new_count == 0:
            warnings.append(
                f"replan_request.subtask_id={original_subtask_id!r} not found "
                f"in plan — replan_count NOT bumped"
            )
        elif became_stuck:
            warnings.append(
                f"subtask {original_subtask_id!r} hit stuck at replan_count="
                f"{new_count} — Triager will omit from commit phase"
            )

        # 7. Truncate if over budget (replan can push total over 30).
        subtask_count = _count_subtasks(plan_after)
        if subtask_count > _HARD_SUBTASK_CAP:
            dropped = _truncate_subtasks(plan_after, _HARD_SUBTASK_CAP)
            warnings.append(
                f"plan grew to {subtask_count} subtasks post-replan; truncated "
                f"to {_HARD_SUBTASK_CAP} (dropped {dropped})"
            )
            subtask_count = _HARD_SUBTASK_CAP

        # 8. Persist the updated plan + status.
        plan_after.save(spec_dir / "test_plan.json")

        _write_status_patch(
            spec_dir,
            status="planned",
            phase="planner_replan_complete",
            planner_warnings=warnings,
            subtask_count=subtask_count,
            last_replan_for=original_subtask_id,
            last_replan_count=new_count,
            last_replan_stuck=became_stuck,
        )
        # Replan succeeded — schedule Gen-Functional to pick up the new
        # replan-N subtask (it'll skip already-generated ones from prior phases).
        _advance_to_gen_functional(spec_dir, project_dir)
        return True

    except Exception as exc:
        _planner_log.error("planner replan failed: %s\n%s", exc, traceback.format_exc())
        _write_status_patch(
            spec_dir,
            status="planner_failed",
            phase="planner_replan_exception",
            planner_error=str(exc)[:500],
        )
        return False


_RETRY_REMINDERS = {
    "missing": (
        "Your previous turn did not emit `test_plan.json`. "
        "You MUST use the Write tool to create the file at "
        "`{spec_dir}/test_plan.json`. Re-emit the full plan now."
    ),
    "json": (
        "Your previous turn produced `test_plan.json` but it failed to "
        "parse as JSON: {detail}. Re-emit the full plan, double-check "
        "JSON syntax (commas, quotes, brackets) before calling Write."
    ),
    "schema": (
        "Your previous turn produced `test_plan.json` but it didn't "
        "match the ImplementationPlan schema: {detail}. "
        "Re-emit the full plan; pay attention to required Subtask keys: "
        "id, description, status, lane, target, rationale, "
        "files_to_create, verification."
    ),
    # v0.2 polyglot: (language, framework, lane) must be consistent with
    # the framework registry that was injected into your CONTEXT block.
    "invalid_framework": (
        "Your previous turn produced `test_plan.json` but one or more "
        "subtasks declared an invalid (language, framework, lane) "
        "combination: {detail}. "
        "Rules: (1) 'framework' must be a key in the FRAMEWORK REGISTRY "
        "block in your CONTEXT. (2) 'language' must match the registry "
        "entry's language exactly. (3) 'lane' must be listed in the "
        "registry entry's lanes. (4) v0.1 subtasks with neither "
        "'language' nor 'framework' set are allowed — omit both fields "
        "if you don't need polyglot metadata. "
        "Re-emit the corrected plan now."
    ),
}


def _build_retry_prompt(original_prompt: str, err_kind: str, detail: str) -> str:
    """Build a retry-turn prompt that re-presents the original system
    prompt + a short corrective note describing what went wrong.
    """
    reminder = _RETRY_REMINDERS.get(
        err_kind,
        "Your previous turn did not produce a valid test_plan.json. Re-emit.",
    ).format(spec_dir="<workspace>", detail=detail)
    return f"## RETRY ({err_kind})\n\n{reminder}\n\n---\n\n{original_prompt}"


def _advance_to_gen_functional(spec_dir: Path, project_dir: Path) -> None:
    """Schedule Gen-Functional after planner success.

    Lazy-imports schedule_gen_functional so a circular import between
    planner.py ↔ gen_functional.py is impossible. ImportError is non-
    fatal — the workspace is in a valid `planned` state regardless;
    the operator can drive Gen-Functional manually if the auto-fire
    path is unavailable.
    """
    try:
        from agents.gen_functional import schedule_gen_functional

        schedule_gen_functional(spec_dir, project_dir, mode="initial")
    except ImportError as exc:
        _planner_log.warning(
            "could not auto-schedule gen_functional: %s — manual invocation required",
            exc,
        )


# Module-level set so asyncio.create_task'd planner runs aren't GC'd while
# the scheduling caller returns. Each completed task is removed via the
# `done_callback`. Auto-fire path in task_control.py uses this directly.
_BG_PLANNER_TASKS: set[asyncio.Task] = set()


def schedule_planner(
    spec_dir: Path,
    project_dir: Path,
    mode: Literal["initial", "replan"] = "initial",
) -> asyncio.Task | None:
    """Fire-and-forget the planner, gated by PFACTORY_AUTO_PLAN env var.

    Returns the asyncio.Task that was scheduled, or None if auto-plan is
    disabled. Caller doesn't need to await; the task is GC-anchored in
    ``_BG_PLANNER_TASKS`` until it completes.
    """
    if os.environ.get("PFACTORY_AUTO_PLAN", "1") == "0":
        return None
    task = asyncio.create_task(run_planner(spec_dir, project_dir, mode=mode))
    _BG_PLANNER_TASKS.add(task)
    task.add_done_callback(_BG_PLANNER_TASKS.discard)
    return task
