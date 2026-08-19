"""`start_spec_creation` must invoke a script that EXISTS on disk.

The deployed pipeline failed every task at spec creation::

    can't open file '.../apps/backend/runners/spec_runner.py':
    [Errno 2] No such file or directory

`spec_runner.py` has never existed anywhere in this repository's history --
0 matches on `main`, 0 on `dev`, nothing under
`git log --all --diff-filter=D`. It is named only in design docs that
describe it as though it shipped. So the fix is not to write the runner:
`run.py` already drives the pipeline, it just needs a spec to exist first
(its docstring: "Spec created via: `claude /spec`"). `start_spec_creation`
now authors spec.md + test_plan.json in-process and calls `run.py --spec`,
the approach TFactory took under its #779.

The assertion below is deliberately about the FILESYSTEM, not about the
argv shape: the bug was "the referenced script is not there", and only a
check that resolves the path catches that class of defect. A test that
asserted `"run.py" in argv` would pass against a run.py that had been
deleted.

Mutation check: point `cmd[1]` in `start_spec_creation` at any path that
does not exist (e.g. `self.backend_path / "runners" / "spec_runner.py"`,
which is exactly what `dev` does today) and
`test_spec_creation_invokes_a_script_that_exists` goes red with
"agent_service spawned a script that does not exist on disk".
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# `apps/web-server` on sys.path so `server.*` imports resolve. Explicit rather
# than inherited: pytest only auto-inserts the test file's own directory, and
# relying on another test module in this tree to do the insert would make this
# file depend on collection order.
_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.services.agent_service import AgentService  # noqa: E402


def _assert_is_file(path: Path | str, why: str) -> None:
    """Blocking stat, deliberately outside any `async def`.

    ruff's ASYNC240 bans filesystem calls inside a coroutine, and this one
    genuinely blocks -- so it lives in a sync helper rather than behind a
    `noqa`, which would suppress a true finding.
    """
    assert Path(path).is_file(), why


def _patches(spawned: list[list[str]]) -> tuple[Any, ...]:
    async def fake_create_subprocess_exec(*args: Any, **_kwargs: Any) -> MagicMock:
        spawned.append([str(a) for a in args])
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

    return (
        patch("asyncio.create_subprocess_exec", new=fake_create_subprocess_exec),
        patch.object(AgentService, "_resolve_claude_token", return_value=(None, None, None)),
        patch.object(AgentService, "_process_output", new=noop),
        patch.object(AgentService, "_emit_progress", new=noop),
        patch.object(asyncio, "create_task", new=drop_task),
    )


async def _run(tmp_path: Path, complexity: str | None) -> tuple[list[str], Path]:
    spec_dir = tmp_path / ".pfactory" / "specs" / "pending-abcd1234"
    spec_dir.mkdir(parents=True)
    service = AgentService()
    spawned: list[list[str]] = []
    a, b, c, d, e = _patches(spawned)
    with a, b, c, d, e:
        await service.start_spec_creation(
            task_id="proj1:pending-abcd1234",
            project_path=tmp_path,
            title="Add a health endpoint",
            description="Return 200 from /healthz.",
            complexity=complexity,
        )
    assert spawned, "the agent subprocess was never spawned"
    return spawned[0], spec_dir


@pytest.mark.asyncio
@pytest.mark.parametrize("complexity", ["simple", "standard", None])
async def test_spec_creation_invokes_a_script_that_exists(
    tmp_path: Path, complexity: str | None
) -> None:
    """The regression itself: argv[1] must resolve to a real file."""
    argv, _ = await _run(tmp_path, complexity)
    _assert_is_file(
        argv[1], f"agent_service spawned a script that does not exist on disk: {argv[1]}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("complexity", ["simple", "standard"])
async def test_spec_creation_authors_a_spec_run_py_can_consume(
    tmp_path: Path, complexity: str
) -> None:
    """run.py only ever operates on an EXISTING spec, so both files must be on
    disk before it is spawned, and the plan must have real subtasks -- an empty
    plan silently no-ops the coding phase."""
    argv, spec_dir = await _run(tmp_path, complexity)

    _assert_is_file(spec_dir / "spec.md", "run.py was handed a spec with no spec.md")
    plan = json.loads((spec_dir / "test_plan.json").read_text())
    assert plan["phases"], f"empty plan for complexity={complexity}"
    assert any(p["subtasks"] for p in plan["phases"]), plan

    # Auto-approved (no requireReviewBeforeCoding metadata), so the review gate
    # must be pre-satisfied and bypassed -- nothing here can reach a human.
    assert json.loads((spec_dir / "review_state.json").read_text())["approved"] is True
    assert "--force" in argv
    assert argv[argv.index("--spec") + 1] == "pending-abcd1234"


@pytest.mark.asyncio
async def test_manual_review_task_is_not_auto_approved(tmp_path: Path) -> None:
    """`requireReviewBeforeCoding` still holds the gate shut."""
    spec_dir = tmp_path / ".pfactory" / "specs" / "pending-abcd1234"
    spec_dir.mkdir(parents=True)
    (spec_dir / "task_metadata.json").write_text(json.dumps({"requireReviewBeforeCoding": True}))
    service = AgentService()
    spawned: list[list[str]] = []
    a, b, c, d, e = _patches(spawned)
    with a, b, c, d, e:
        await service.start_spec_creation(
            task_id="proj1:pending-abcd1234",
            project_path=tmp_path,
            title="Gated",
            description="",
        )
    assert "--force" not in spawned[0]
    assert not (spec_dir / "review_state.json").exists()
