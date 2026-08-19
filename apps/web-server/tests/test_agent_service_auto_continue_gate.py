"""`start_task_execution(auto_continue=False)` must not crash (CodeQL 1572).

The human-review gate -- `spec_dir`, `requirements_file`, `task_metadata_file`
and `require_review` -- was indented one level too deep, inside
`if auto_continue:`. Nothing about reading `task_metadata.json` to decide
whether to pass `--force` depends on auto-continue; the block just drifted
under the two-line `if` above it.

The result was a hard failure, not a subtle one. `spec_dir` is read again
outside the block, ~230 lines later::

    self._spec_dirs[task_id] = spec_dir

so every execution with `auto_continue=False` raised, AFTER the agent
subprocess had already been spawned:

    UnboundLocalError: cannot access local variable 'spec_dir'
    where it is not associated with a value

`--force` was also never decided on that path, and `_write_skill_context` never
ran, so the task's selected skills were silently dropped too.

The alert named `spec_dir` only, because that is the one use that escapes the
block. `require_review` and `_write_skill_context(spec_dir)` sit inside it and
were invisible to the analyzer -- the fix is the dedent, not a guard on the one
flagged name.

Mutation check: re-indent lines 693-755 of `agent_service.py` back under
`if auto_continue:` and this test goes red with that UnboundLocalError.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# `apps/web-server` on sys.path so `server.*` imports resolve. Explicit rather
# than inherited: pytest only auto-inserts the test file's own directory, and
# the other modules in this tree resolve `server.*` only because
# `test_task_models_phase_status.py` happens to insert it and collection is
# alphabetical. Both modules here sort before it.
_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.services.agent_service import AgentService  # noqa: E402


@pytest.mark.asyncio
@pytest.mark.parametrize("auto_continue", [True, False])
async def test_start_task_execution_reaches_the_spawn_on_both_auto_continue_settings(
    tmp_path: Path, auto_continue: bool
) -> None:
    (tmp_path / ".pfactory" / "specs" / "spec1").mkdir(parents=True)
    service = AgentService()
    spawned: list[list[str]] = []

    async def fake_create_subprocess_exec(*args: Any, **_kwargs: Any) -> MagicMock:
        spawned.append(list(args))
        proc = MagicMock()
        proc.returncode = None
        proc.stdout = proc.stderr = None
        return proc

    async def noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    def drop_task(coro: Any, *_args: Any, **_kwargs: Any) -> MagicMock:
        # Close rather than schedule: the background monitor is out of scope
        # here, and an un-awaited coroutine emits a RuntimeWarning at GC.
        coro.close()
        return MagicMock()

    with (
        patch("asyncio.create_subprocess_exec", new=fake_create_subprocess_exec),
        patch.object(AgentService, "_resolve_claude_token", return_value=(None, None, None)),
        patch.object(AgentService, "_process_output", new=noop),
        patch.object(AgentService, "_write_skill_context", new=MagicMock()),
        patch.object(asyncio, "create_task", new=drop_task),
    ):
        # The assertion is simply that this returns. Before the fix, the
        # auto_continue=False parametrization raised UnboundLocalError.
        await service.start_task_execution(
            task_id="t1",
            project_path=tmp_path,
            spec_id="spec1",
            auto_continue=auto_continue,
        )

    assert spawned, "the agent subprocess was never spawned"
    # The gate ran on both settings: spec_dir was recorded either way.
    assert service._spec_dirs["t1"] == tmp_path / ".pfactory" / "specs" / "spec1"
    # ...and --auto-continue is still what actually varies with the flag.
    argv = spawned[0]
    assert ("--auto-continue" in argv) is auto_continue, argv


@pytest.mark.asyncio
async def test_skill_context_is_written_regardless_of_auto_continue(tmp_path: Path) -> None:
    """The second casualty of the mis-indent: selected skills were silently
    dropped on the auto_continue=False path because `_write_skill_context`
    lived inside the block too."""
    (tmp_path / ".pfactory" / "specs" / "spec1").mkdir(parents=True)
    service = AgentService()

    async def fake_create_subprocess_exec(*_args: Any, **_kwargs: Any) -> MagicMock:
        proc = MagicMock()
        proc.returncode = None
        proc.stdout = proc.stderr = None
        return proc

    async def noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    def drop_task(coro: Any, *_args: Any, **_kwargs: Any) -> MagicMock:
        # Close rather than schedule: the background monitor is out of scope
        # here, and an un-awaited coroutine emits a RuntimeWarning at GC.
        coro.close()
        return MagicMock()

    write_skill_context = MagicMock()
    with (
        patch("asyncio.create_subprocess_exec", new=fake_create_subprocess_exec),
        patch.object(AgentService, "_resolve_claude_token", return_value=(None, None, None)),
        patch.object(AgentService, "_process_output", new=noop),
        patch.object(AgentService, "_write_skill_context", new=write_skill_context),
        patch.object(asyncio, "create_task", new=drop_task),
    ):
        await service.start_task_execution(
            task_id="t1", project_path=tmp_path, spec_id="spec1", auto_continue=False
        )

    write_skill_context.assert_called_once_with(tmp_path / ".pfactory" / "specs" / "spec1")
