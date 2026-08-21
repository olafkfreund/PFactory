"""
QA Commands
===========

CLI commands for QA validation (run QA, check status)
"""

import asyncio
import sys
from pathlib import Path
from typing import Protocol, cast

# Ensure parent directory is in path for imports (before other imports)
_PARENT_DIR = Path(__file__).parent.parent
if str(_PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(_PARENT_DIR))

from progress import count_subtasks

# PFactory is the PLANNER and ships no QA loop -- `qa_loop` exists in TFactory
# (apps/backend/qa_loop.py) and AIFactory (apps/backend/qa/qa_loop.py) but has
# never existed here. Importing it at module scope made `cli.main` unimportable,
# which took `run.py` -- the primary entry point, and the one AIFactory invokes
# as `run.py --spec <id>` -- down with it: `run.py --help` raised
# ModuleNotFoundError in the repo AND in the deployed image (PFactory#621).
#
# Imported lazily so the other eleven subcommands work. The three QA
# subcommands cannot work in this fork, and each now says so when invoked
# instead of crashing the whole CLI on import. Deleting them outright was the
# alternative; a clear message is better than a command that vanishes without
# explaining where it went.
_QA_UNAVAILABLE = (
    "This build of PFactory has no QA loop. `qa_loop` is a TFactory/AIFactory "
    "module and has never been part of this fork, so `--qa`, `--qa-status` and "
    "`--review-status` cannot run here. Run QA through TFactory instead. "
    "See PFactory#621."
)


class _QaLoopModule(Protocol):
    """The four names this CLI uses from ``qa_loop``.

    Typed as a Protocol rather than left as the untyped module object: mypy
    cannot resolve ``qa_loop`` (it genuinely is not here), so an untyped
    accessor makes every call site ``Any`` and the ratchet counts each one as a
    net-new strict error. Signatures mirror TFactory's real module
    (``apps/backend/qa_loop.py``), which is the implementation this would bind
    to if the fork ever gained one.
    """

    def is_qa_approved(self, spec_dir: Path) -> bool: ...

    def should_run_qa(self, spec_dir: Path) -> bool: ...

    def print_qa_status(self, spec_dir: Path) -> None: ...

    async def run_qa_validation_loop(
        self,
        *,
        project_dir: Path,
        spec_dir: Path,
        model: str,
        verbose: bool = False,
    ) -> bool: ...


def _qa_loop() -> _QaLoopModule:
    """Import ``qa_loop`` on demand, with a clear error when it is absent."""
    try:
        # type: ignore[import-not-found] -- mypy is right that the module is
        # absent; that is the condition this function exists to handle. The
        # cast below binds it to the Protocol above so call sites stay typed
        # rather than degrading to Any and tripping the strict ratchet.
        import qa_loop  # type: ignore[import-not-found]  # noqa: PLC0415
    except ModuleNotFoundError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(_QA_UNAVAILABLE) from exc
    return cast("_QaLoopModule", qa_loop)


from review import ReviewState, display_review_status
from ui import (
    Icons,
    icon,
    info,
    success,
    warning,
)

from .utils import print_banner, validate_environment


def handle_qa_status_command(spec_dir: Path) -> None:
    """
    Handle the --qa-status command.

    Args:
        spec_dir: Spec directory path
    """
    print_banner()
    print(f"\nSpec: {spec_dir.name}\n")
    _qa_loop().print_qa_status(spec_dir)


def handle_review_status_command(spec_dir: Path) -> None:
    """
    Handle the --review-status command.

    Args:
        spec_dir: Spec directory path
    """
    print_banner()
    print(f"\nSpec: {spec_dir.name}\n")
    display_review_status(spec_dir)
    # Also show if approval is valid for build
    review_state = ReviewState.load(spec_dir)
    print()
    if review_state.is_approval_valid(spec_dir):
        print(success(f"{icon(Icons.SUCCESS)} Ready to build - approval is valid."))
    elif review_state.approved:
        print(warning(f"{icon(Icons.WARNING)} Spec changed since approval - re-review required."))
    else:
        print(info(f"{icon(Icons.INFO)} Review required before building."))
    print()


def handle_qa_command(
    project_dir: Path,
    spec_dir: Path,
    model: str,
    verbose: bool = False,
) -> None:
    """
    Handle the --qa command (run QA validation loop).

    Args:
        project_dir: Project root directory
        spec_dir: Spec directory path
        model: Model to use for QA
        verbose: Enable verbose output
    """
    print_banner()
    print(f"\nRunning QA validation for: {spec_dir.name}")
    if not validate_environment(spec_dir):
        sys.exit(1)

    # Check if there's pending human feedback that needs to be processed
    # Human feedback takes priority over "already approved" status
    fix_request_file = spec_dir / "QA_FIX_REQUEST.md"
    has_human_feedback = fix_request_file.exists()

    if not _qa_loop().should_run_qa(spec_dir) and not has_human_feedback:
        if _qa_loop().is_qa_approved(spec_dir):
            print("\n✅ Build already approved by QA.")
        else:
            completed, total = count_subtasks(spec_dir)
            print(f"\n❌ Build not complete ({completed}/{total} subtasks).")
            print("Complete all subtasks before running QA validation.")
        return

    if has_human_feedback:
        print("\n📝 Human feedback detected - processing fix request...")

    try:
        approved = asyncio.run(
            _qa_loop().run_qa_validation_loop(
                project_dir=project_dir,
                spec_dir=spec_dir,
                model=model,
                verbose=verbose,
            )
        )
        if approved:
            print("\n✅ QA validation passed. Ready for merge.")
        else:
            print("\n❌ QA validation incomplete. See reports for details.")
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n\nQA validation paused.")
        print(f"Resume with: python pfactory/run.py --spec {spec_dir.name} --qa")
