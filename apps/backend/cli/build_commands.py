"""
Build Commands
==============

CLI commands for building specs and handling the main build flow.
"""

import asyncio
import sys
from pathlib import Path

# Ensure parent directory is in path for imports (before other imports)
_PARENT_DIR = Path(__file__).parent.parent
if str(_PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(_PARENT_DIR))

# Import only what we need at module level
# Heavy imports are lazy-loaded in functions to avoid import errors
from progress import print_paused_banner
from review import ReviewState
from ui import (
    BuildState,
    Icons,
    MenuOption,
    StatusManager,
    bold,
    box,
    highlight,
    icon,
    muted,
    print_status,
    select_menu,
    success,
    warning,
)
from workspace import (
    WorkspaceMode,
    check_existing_build,
    choose_workspace,
    finalize_workspace,
    get_existing_build_worktree,
    handle_workspace_choice,
    setup_workspace,
)

from .input_handlers import (
    read_from_file,
    read_multiline_input,
)


class BuildNotSupportedError(RuntimeError):
    """This fork was asked to write code, and has no agent that can.

    Named rather than a bare ``RuntimeError`` so the caller can tell "PFactory
    does not do this" apart from "PFactory tried and failed". Before
    PFactory#607 the same situation surfaced as ``ImportError: cannot import
    name 'run_autonomous_agent'`` from the first statement of
    :func:`handle_build_command`, which reads as a broken install rather than
    a deliberate boundary.
    """


def handle_build_command(
    project_dir: Path,
    spec_dir: Path,
    model: str,
    max_iterations: int | None,
    verbose: bool,
    force_isolated: bool,
    force_direct: bool,
    auto_continue: bool,
    skip_qa: bool,
    force_bypass_approval: bool,
    base_branch: str | None = None,
    stop_after_planning: bool = False,
    remote_control_session: str | None = None,
) -> None:
    """
    Handle the main build command.

    Args:
        project_dir: Project root directory
        spec_dir: Spec directory path
        model: Model to use (used as default; may be overridden by task_metadata.json)
        max_iterations: Maximum number of iterations (None for unlimited)
        verbose: Enable verbose output
        force_isolated: Force isolated workspace mode
        force_direct: Force direct workspace mode
        auto_continue: Auto-continue mode (non-interactive)
        skip_qa: Skip automatic QA validation
        force_bypass_approval: Force bypass approval check
        base_branch: Base branch for worktree creation (default: current branch)
        stop_after_planning: Exit cleanly after the planner phase writes
            test_plan.json. Used by the Copilot delegation flow,
            where PFactory generates the plan locally and then hands off
            implementation to GitHub Copilot Coding Agent (#92, #94).
    """
    # Lazy imports to avoid loading heavy modules
    from agent import run_planner, sync_plan_to_source
    from debug import (
        debug,
        debug_info,
        debug_section,
        debug_success,
    )
    from phase_config import get_phase_model
    from phase_event import ExecutionPhase, emit_phase

    # NOT imported here: `qa_loop` is a TFactory/AIFactory module and has never
    # existed in this fork, so this unconditional import crashed every build --
    # including `run.py --spec <id>`, the command AIFactory invokes. All three
    # uses below are already behind `not skip_qa`, so the import belongs behind
    # the same condition rather than ahead of it. See .qa_commands._qa_loop.
    from .qa_commands import _qa_loop

    from .utils import print_banner, validate_environment

    # Get the resolved model for the planning phase (first phase of build)
    # This respects task_metadata.json phase configuration from the UI
    planning_model = get_phase_model(spec_dir, "planning", model)
    coding_model = get_phase_model(spec_dir, "coding", model)
    qa_model = get_phase_model(spec_dir, "qa", model)

    print_banner()
    print(f"\nProject directory: {project_dir}")
    print(f"Spec: {spec_dir.name}")
    # Show phase-specific models if they differ
    if planning_model != coding_model or coding_model != qa_model:
        print(
            f"Models: Planning={planning_model.split('-')[1] if '-' in planning_model else planning_model}, "
            f"Coding={coding_model.split('-')[1] if '-' in coding_model else coding_model}, "
            f"QA={qa_model.split('-')[1] if '-' in qa_model else qa_model}"
        )
    else:
        print(f"Model: {planning_model}")

    if max_iterations:
        print(f"Max iterations: {max_iterations}")
    else:
        print("Max iterations: Unlimited (runs until all subtasks complete)")

    print()

    # Validate environment
    if not validate_environment(spec_dir):
        sys.exit(1)

    # Check human review approval
    review_state = ReviewState.load(spec_dir)
    if not review_state.is_approval_valid(spec_dir):
        if force_bypass_approval:
            # User explicitly bypassed approval check
            print()
            print(warning(f"{icon(Icons.WARNING)} WARNING: Bypassing approval check with --force"))
            print(muted("This spec has not been approved for building."))
            print()
        else:
            print()
            content = [
                bold(f"{icon(Icons.WARNING)} BUILD BLOCKED - REVIEW REQUIRED"),
                "",
                "This spec requires human approval before building.",
            ]

            if review_state.approved and not review_state.is_approval_valid(spec_dir):
                # Spec changed after approval
                content.append("")
                content.append(warning("The spec has been modified since approval."))
                content.append("Please re-review and re-approve.")

            content.extend(
                [
                    "",
                    highlight("To review and approve:"),
                    f"  python pfactory/review.py --spec-dir {spec_dir}",
                    "",
                    muted("Or use --force to bypass this check (not recommended)."),
                ]
            )
            print(box(content, width=70, style="heavy"))
            print()

            # If auto_continue mode (web UI), save pending review state and exit cleanly
            if auto_continue:
                # Save review state indicating spec is waiting for approval
                review_state.save(spec_dir)
                # Exit with success code - web UI will handle the human_review transition
                sys.exit(0)
            else:
                # CLI mode - exit with error to block execution
                sys.exit(1)
    else:
        debug_success("run.py", "Review approval validated", approved_by=review_state.approved_by)

    # Check for existing build
    if get_existing_build_worktree(project_dir, spec_dir.name):
        if auto_continue:
            # Non-interactive mode: auto-continue with existing build
            debug("run.py", "Auto-continue mode: continuing with existing build")
            print("Auto-continue: Resuming existing build...")
        else:
            continue_existing = check_existing_build(project_dir, spec_dir.name)
            if continue_existing:
                # Continue with existing worktree
                pass
            else:
                # User chose to start fresh or merged existing
                pass

    # Choose workspace (skip for parallel mode - it always uses worktrees)
    working_dir = project_dir
    worktree_manager = None
    source_spec_dir = None  # Track original spec dir for syncing back from worktree

    # Let user choose workspace mode (or auto-select if --auto-continue)
    workspace_mode = choose_workspace(
        project_dir,
        spec_dir.name,
        force_isolated=force_isolated,
        force_direct=force_direct,
        auto_continue=auto_continue,
    )

    if workspace_mode == WorkspaceMode.ISOLATED:
        # Keep reference to original spec directory for syncing progress back
        source_spec_dir = spec_dir

        working_dir, worktree_manager, localized_spec_dir = setup_workspace(
            project_dir,
            spec_dir.name,
            workspace_mode,
            source_spec_dir=spec_dir,
            base_branch=base_branch,
        )
        # Use the localized spec directory (inside worktree) for AI access
        if localized_spec_dir:
            spec_dir = localized_spec_dir

    # Run the autonomous agent
    debug_section("run.py", "Starting Build Execution")
    debug(
        "run.py",
        "Build configuration",
        model=model,
        workspace_mode=str(workspace_mode),
        working_dir=str(working_dir),
        spec_dir=str(spec_dir),
    )

    try:
        debug("run.py", "Starting agent execution")

        # This fork has no coder agent. `core/agent.py` says so outright --
        # "This fork removed the coder agent (run_autonomous_agent,
        # run_followup_planner)" -- and `agents/__init__.py` records that its
        # replacement (Planner, Generators, Executor, Evaluator, Triager) is
        # "scheduled in Tasks 5-8". The CLI was never pruned to match, so every
        # build reached `from agent import run_autonomous_agent` and died on
        # ImportError -- INCLUDING the delegation flow, because the old call
        # took `stop_after_planning` and ran the planner INSIDE the coder loop,
        # so planning went down with coding (PFactory#607).
        #
        # The planner is the half this fork does own, and it ships: `run_planner`
        # is in `core.agent.__all__` and emits the same `test_plan.json` the
        # delegation caller below waits for.
        # `--remote-control` steered the coder loop's turns. `run_planner` has
        # no such hook, so honouring the flag is impossible -- and accepting it
        # silently would mean the flag reads as applied while doing nothing,
        # which is the failure mode this whole issue is made of.
        if remote_control_session:
            raise BuildNotSupportedError(
                "--remote-control steered the coder agent, which this fork "
                "removed. The planner has no equivalent hook, so the flag "
                "cannot be honoured. Re-run without it. PFactory#607."
            )

        if not stop_after_planning:
            raise BuildNotSupportedError(
                "PFactory plans; it does not implement. This fork removed the "
                "coder agent (see apps/backend/agents/__init__.py), so a build "
                "that must write code has no runner here. Use the delegation "
                "flow (--stop-after-planning) and hand implementation to the "
                "coding agent, or run the build in AIFactory. PFactory#607."
            )

        asyncio.run(
            run_planner(
                spec_dir=spec_dir,
                project_dir=working_dir,  # worktree if isolated
                verbose=verbose,
            )
        )
        debug_success("run.py", "Agent execution completed")

        # Delegation mode: planner has written test_plan.json and
        # we hand off to the vendor agent (Copilot) from auto_fix_service.
        # No QA, no finalization — auto_fix_service drives the rest.
        if stop_after_planning:
            debug_info(
                "run.py",
                "Stop-after-planning: planner done, returning to delegation caller",
            )
            return

        # Run QA validation BEFORE finalization (while worktree still exists)
        # QA must sign off before the build is considered complete
        qa_approved = True  # Default to approved if QA is skipped
        if not skip_qa and _qa_loop().should_run_qa(spec_dir):
            print("\n" + "=" * 70)
            print("  SUBTASKS COMPLETE - STARTING QA VALIDATION")
            print("=" * 70)
            print("\nAll subtasks completed. Now running QA validation loop...")
            print("This ensures production-quality output before sign-off.\n")

            try:
                qa_approved = asyncio.run(
                    _qa_loop().run_qa_validation_loop(
                        project_dir=working_dir,
                        spec_dir=spec_dir,
                        model=model,
                        verbose=verbose,
                    )
                )

                if qa_approved:
                    print("\n" + "=" * 70)
                    print("  ✅ QA VALIDATION PASSED")
                    print("=" * 70)
                    print("\nAll acceptance criteria verified.")
                    print("The implementation is production-ready.\n")
                else:
                    print("\n" + "=" * 70)
                    print("  ⚠️  QA VALIDATION INCOMPLETE")
                    print("=" * 70)
                    print("\nSome issues require manual attention.")
                    print(f"See: {spec_dir / 'qa_report.md'}")
                    print(f"Or:  {spec_dir / 'QA_FIX_REQUEST.md'}")
                    print(f"\nResume QA: python pfactory/run.py --spec {spec_dir.name} --qa\n")

                # Sync implementation plan to main project after QA
                # This ensures the main project has the latest status (human_review)
                if sync_plan_to_source(spec_dir, source_spec_dir):
                    debug_info("run.py", "Implementation plan synced to main project after QA")
            except KeyboardInterrupt:
                print("\n\nQA validation paused.")
                print(f"Resume: python pfactory/run.py --spec {spec_dir.name} --qa")
                qa_approved = False

        elif not skip_qa and _qa_loop().is_qa_approved(spec_dir):
            # QA was pre-approved by coder agent - emit phase events for proper logging
            emit_phase(ExecutionPhase.QA_REVIEW, "QA pre-approved by coder agent", progress=100)

            print("\n" + "=" * 70)
            print("  QA PRE-APPROVED BY CODER")
            print("=" * 70)
            print("\nThe coder agent has validated all acceptance criteria.")
            print("Implementation meets requirements - no additional QA review needed.\n")

            emit_phase(ExecutionPhase.COMPLETE, "QA validation passed (pre-approved)")

            # Sync implementation plan to main project
            if sync_plan_to_source(spec_dir, source_spec_dir):
                debug_info(
                    "run.py", "Implementation plan synced to main project after pre-approved QA"
                )

        # Post-build finalization (only for isolated sequential mode)
        # This happens AFTER QA validation so the worktree still exists
        if worktree_manager:
            choice = finalize_workspace(
                project_dir,
                spec_dir.name,
                worktree_manager,
                auto_continue=auto_continue,
            )
            handle_workspace_choice(choice, project_dir, spec_dir.name, worktree_manager)

    except KeyboardInterrupt:
        _handle_build_interrupt(
            spec_dir=spec_dir,
            project_dir=project_dir,
            worktree_manager=worktree_manager,
        )
    except Exception as e:
        print(f"\nFatal error: {e}")
        if verbose:
            import traceback

            traceback.print_exc()
        sys.exit(1)


def _handle_build_interrupt(
    spec_dir: Path,
    project_dir: Path,
    worktree_manager,
) -> None:
    """
    Handle keyboard interrupt during build.

    Args:
        spec_dir: Spec directory path
        project_dir: Project root directory
        worktree_manager: Worktree manager instance (if using isolated mode)

    ``working_dir``, ``model``, ``max_iterations`` and ``verbose`` used to be
    parameters here. They existed only to feed the resume call, which reached
    the coder agent this fork removed (PFactory#607). Keeping them would mean
    accepting four arguments and ignoring all four.
    """

    # Print paused banner
    print_paused_banner(spec_dir, spec_dir.name, has_worktree=bool(worktree_manager))

    # Update status file
    status_manager = StatusManager(project_dir)
    status_manager.update(state=BuildState.PAUSED)

    # Offer to add human input with enhanced menu
    try:
        options = [
            MenuOption(
                key="type",
                label="Type instructions",
                icon=Icons.EDIT,
                description="Enter guidance for the agent's next session",
            ),
            MenuOption(
                key="paste",
                label="Paste from clipboard",
                icon=Icons.CLIPBOARD,
                description="Paste text you've copied (Cmd+V / Ctrl+Shift+V)",
            ),
            MenuOption(
                key="file",
                label="Read from file",
                icon=Icons.DOCUMENT,
                description="Load instructions from a text file",
            ),
            MenuOption(
                key="skip",
                label="Continue without instructions",
                icon=Icons.SKIP,
                # Was "Resume the build as-is". Resuming means finishing a
                # build, and this fork has no coder agent to finish one
                # (PFactory#607), so offering it promised something the code
                # could never do -- it reached `run_autonomous_agent` and died
                # on ImportError.
                description="Save state and exit; PFactory cannot resume a build",
            ),
            MenuOption(
                key="quit",
                label="Quit",
                icon=Icons.DOOR,
                description="Exit without resuming",
            ),
        ]

        choice = select_menu(
            title="What would you like to do?",
            options=options,
            subtitle="Progress saved. You can add instructions for the agent.",
            allow_quit=False,  # We have explicit quit option
        )

        if choice == "quit" or choice is None:
            print()
            print_status("Exiting...", "info")
            status_manager.set_inactive()
            sys.exit(0)

        human_input = ""

        if choice == "file":
            # Read from file
            human_input = read_from_file()
            if human_input is None:
                human_input = ""

        elif choice in ["type", "paste"]:
            human_input = read_multiline_input("Enter/paste your instructions below.")
            if human_input is None:
                print()
                print_status("Exiting without saving instructions...", "warning")
                status_manager.set_inactive()
                sys.exit(0)

        if human_input:
            # Save to HUMAN_INPUT.md
            input_file = spec_dir / "HUMAN_INPUT.md"
            input_file.write_text(human_input)

            content = [
                success(f"{icon(Icons.SUCCESS)} INSTRUCTIONS SAVED"),
                "",
                f"Saved to: {highlight(str(input_file.name))}",
                "",
                muted("The agent will read and follow these instructions when you resume."),
            ]
            print()
            print(box(content, width=70, style="heavy"))
        elif choice != "skip":
            print()
            print_status("No instructions provided.", "info")

        # If 'skip' was selected, actually resume the build
        if choice == "skip":
            print()
            print_status("Resuming build...", "info")
            status_manager.update(state=BuildState.RUNNING)
            # Same reason as the primary path: resuming means finishing a
            # build, and this fork has no coder agent to finish it with
            # (PFactory#607). Raised rather than printed so the caller sees a
            # named failure instead of an ImportError traceback.
            raise BuildNotSupportedError(
                "PFactory cannot resume a build: this fork has no coder agent. "
                "Re-run with --stop-after-planning to regenerate the plan and "
                "delegate implementation. PFactory#607."
            )
            # Build completed or was interrupted again - exit
            sys.exit(0)

    except KeyboardInterrupt:
        # User pressed Ctrl+C again during input prompt - exit immediately
        print()
        print_status("Exiting...", "warning")
        status_manager = StatusManager(project_dir)
        status_manager.set_inactive()
        sys.exit(0)
    except EOFError:
        # stdin closed
        pass

    # Resume instructions (shown when user provided instructions or chose file/type/paste)
    print()
    content = [
        bold(f"{icon(Icons.PLAY)} TO RESUME"),
        "",
        f"Run: {highlight(f'python pfactory/run.py --spec {spec_dir.name}')}",
    ]
    if worktree_manager:
        content.append("")
        content.append(muted("Your build is in a separate workspace and is safe."))
    print(box(content, width=70, style="light"))
    print()
