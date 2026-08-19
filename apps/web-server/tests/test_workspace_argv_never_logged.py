"""A credentialed git argv must never reach the log FILES -- and since
PFactory#602, the token must not reach the child's argv at all
(PFactory#576 follow-up; CodeQL 1696-1700).

`_run_git`'s DEBUG line used to print the full argv on the `not credentialed`
branch. That made "is the PAT in the log?" a property of a boolean each of the
seven call sites sets by hand -- on two of them, three lines away from the
`fetch_url` that carries the token. Driving the REAL pipeline with that pairing
inverted wrote the whole `https://oauth2:<PAT>@host/...` URL to `server.log`:

    {"event": "[workspace] running: git clone https://oauth2:ghp_...@127.0.0.1:1/...",
     "level": "debug", "logger": "server.services.project_workspace_service", ...}

That was the true count -- exactly ONE leaking line, in `server.log`, from the
DEBUG argv line and nowhere else. `errors.log` and `agent.log` were clean, the
`GitOperationError` message was clean, and the credentialed branch was clean.
So the fix is to that one line, and this test pins the count at zero across
every file the pipeline writes.

Why FILE LINES and not `caplog`: `caplog` sees `LogRecord`s before the
handlers' formatter runs, so it cannot see anything a formatter or an
`exc_info` render adds on the way to disk (PFactory#592, fixed at the formatter
in `logging_config.py`). The sibling `test_project_workspace_service_credential_leak.py`
asserts on `record.getMessage()`, which is the right check for the exception
path it covers but is blind to the written line. This module reads the files
back.

Mutation check: restore the old `sanitize_log(" ".join(args))` DEBUG branch and
`test_argv_is_never_written_to_any_log_file` goes red with the PAT on a
`server.log` line.

PFactory#602 converged this module on TFactory's fork: the token is no longer
an argv element at all (`GIT_ASKPASS` feeds it via `GIT_PASS`), so the
`credentialed` flag and its parametrize are gone. The two properties are now
independent and both pinned here -- `test_token_is_absent_from_the_child_argv`
proves the token never reaches `/proc/<pid>/cmdline`, and the argv is STILL
never logged, because argv carries caller-controlled text (a URL, a branch)
that has no business on an off-host-forwarded log line whether or not it is
secret.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, NamedTuple
from unittest.mock import patch

import pytest

# `apps/web-server` on sys.path so `server.*` imports resolve. Explicit rather
# than inherited: pytest only auto-inserts the test file's own directory, and
# the other modules in this tree resolve `server.*` only because
# `test_task_models_phase_status.py` happens to insert it and collection is
# alphabetical. Both modules here sort before it.
_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.logging_config import setup_logging  # noqa: E402
from server.services.project_workspace_service import (  # noqa: E402
    GitOperationError,
    _run_git,
    _safe_subcommand,
    clone_or_update,
)


# A closed local port fails immediately -- no network, no DNS.
# Assembled at import time rather than written as one literal: a realistic
# 40-char PAT literal trips the repo's gitleaks gate (generic-api-key, on
# entropy), and silencing a secret scanner to land a secret-leak test would be
# the wrong trade. The repeated word keeps the entropy low while the value is
# still PAT-shaped and unmistakable in a log line.
class _Spawn(NamedTuple):
    """One recorded create_subprocess_exec call: what we asked for, what
    the kernel published, and what env the child got."""

    argv: list[str]
    cmdline: bytes
    env: dict[str, str]


_SECRET = "ghp_" + "ARGVLEAKCANARY" * 3
_URL = f"https://oauth2:{_SECRET}@127.0.0.1:1/owner/repo.git"


@pytest.fixture
def log_lines(tmp_path: Path) -> Iterator[Callable[[], dict[str, list[str]]]]:
    """Drive the real pipeline into a temp log dir; yield a file-line reader."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    log_dir = tmp_path / "logs"
    setup_logging(log_level="DEBUG", log_dir=log_dir)

    def read() -> dict[str, list[str]]:
        for handler in logging.getLogger().handlers:
            handler.flush()
        return {f.name: f.read_text().splitlines() for f in sorted(log_dir.glob("*.log"))}

    try:
        yield read
    finally:
        for handler in logging.getLogger().handlers[:]:
            handler.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


@pytest.mark.asyncio
async def test_argv_is_never_written_to_any_log_file(
    tmp_path: Path,
    log_lines: Callable[[], dict[str, list[str]]],
) -> None:
    """A PAT-shaped argv element must be absent from every emitted line.

    Passed here as a URL-embedded token even though `clone_or_update` no
    longer builds one (PFactory#602): this pins the LOGGING property against
    whatever ends up in argv, so it stays red if someone reintroduces a
    credential there.
    """
    with pytest.raises(GitOperationError) as excinfo:
        await _run_git(
            ["clone", _URL, str(tmp_path / "dest")],
            cwd=tmp_path,
            timeout=10,
        )

    assert _SECRET not in str(excinfo.value)

    files = log_lines()
    # Guard against a vacuous pass: if the pipeline wrote nothing, "the secret
    # is absent" is true and meaningless.
    assert any("project_workspace_service" in line for lines in files.values() for line in lines), (
        f"no workspace log lines were written at all: {files}"
    )

    leaks = [
        f"{name}:{i}: {line}"
        for name, lines in files.items()
        for i, line in enumerate(lines, 1)
        if _SECRET in line
    ]
    assert leaks == [], "credential written to log file(s):\n" + "\n".join(leaks)


@pytest.mark.asyncio
async def test_the_operation_is_still_identifiable_in_the_log(
    tmp_path: Path, log_lines: Callable[[], dict[str, list[str]]]
) -> None:
    """Withholding the argv must not make the log useless: the subcommand and
    the exit code still have to name what failed."""
    with pytest.raises(GitOperationError):
        await _run_git(["clone", _URL, str(tmp_path / "dest")], cwd=tmp_path, timeout=10)

    events = [
        json.loads(line)["event"]
        for lines in log_lines().values()
        for line in lines
        if "project_workspace_service" in line
    ]
    assert any("running: git clone" in e for e in events), events
    assert any("git clone failed" in e for e in events), events


def test_unrecognised_subcommand_does_not_echo_argv_text() -> None:
    """The barrier returns a module constant, so an unlisted subcommand reads
    as "unknown" rather than putting caller text on a log line."""
    assert _safe_subcommand(["clone", _URL]) == "clone"
    assert _safe_subcommand([]) == "unknown"
    assert _safe_subcommand([f"log\nCRITICAL:server.audit:forged {_SECRET}"]) == "unknown"


@pytest.mark.asyncio
async def test_token_is_absent_from_the_child_argv(tmp_path: Path) -> None:
    """PFactory#602's own property: the token must not be in the child's argv.

    This is what `GIT_ASKPASS` adds over #599. #599 stopped the credentialed
    argv reaching the LOG; the argv itself still carried the PAT and
    `/proc/<pid>/cmdline` is world-readable to every other uid on the host for
    the lifetime of the clone. Without this test the issue could be closed by
    something that only MOVES the leak.

    Both forms of the check, on ONE real child process:

    * the recorded ``create_subprocess_exec`` args (what this module asked
      for), and
    * ``/proc/<pid>/cmdline`` (what the kernel actually published), read while
      the process is still alive.

    The remote is a real socket that accepts and never speaks, so git blocks
    in the HTTP exchange and the read is not racing the child's exit.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def read_cmdline(pid: int) -> bytes:
        """Sync helper: ASYNC240 forbids pathlib inside an async def."""
        return Path(f"/proc/{pid}/cmdline").read_bytes()

    real_exec = asyncio.create_subprocess_exec
    seen: list[_Spawn] = []

    async def spy(*args: Any, **kwargs: Any) -> Any:
        proc = await real_exec(*args, **kwargs)
        seen.append(
            _Spawn(
                argv=[str(a) for a in args],
                cmdline=read_cmdline(proc.pid),
                env=dict(kwargs.get("env") or {}),
            )
        )
        return proc

    try:
        with (
            patch("asyncio.create_subprocess_exec", new=spy),
            pytest.raises(GitOperationError),
        ):
            await clone_or_update(
                git_url=f"https://127.0.0.1:{port}/owner/repo.git",
                root=tmp_path,
                slug="argv-probe",
                credential=("oauth2", _SECRET),
                timeout_seconds=3,
            )
    finally:
        listener.close()

    assert seen, "no child process was spawned"

    # Guard against a vacuous pass: the credential must actually have been in
    # play on this call, just by a route that isn't argv.
    assert any(spawn.env.get("GIT_PASS") == _SECRET for spawn in seen), (
        "the token never reached GIT_PASS -- this test would pass vacuously"
    )

    argv_leaks = [s.argv for s in seen if any(_SECRET in a for a in s.argv)]
    assert argv_leaks == [], f"token present in create_subprocess_exec args: {argv_leaks}"

    proc_leaks = [
        s.cmdline.decode("utf-8", "replace") for s in seen if _SECRET.encode() in s.cmdline
    ]
    assert proc_leaks == [], f"token present in /proc/<pid>/cmdline: {proc_leaks}"


@pytest.mark.asyncio
async def test_credentialed_failure_logs_full_stderr(
    tmp_path: Path, log_lines: Callable[[], dict[str, list[str]]]
) -> None:
    """The counterpart to `test_non_credentialed_failure_still_logs_full_detail`
    in the sibling module: a CREDENTIALED failure now logs its real stderr too.

    #599 withheld it on credentialed calls because stderr could echo the
    token-bearing URL back. With the token out of argv there is nothing to
    echo, so operators get the real git error on both paths -- which is the
    behaviour #602 was meant to restore, asserted on the written FILE LINES.
    """
    with pytest.raises(GitOperationError):
        await clone_or_update(
            git_url="https://127.0.0.1:1/owner/repo.git",
            root=tmp_path,
            slug="stderr-probe",
            credential=("oauth2", _SECRET),
            timeout_seconds=10,
        )

    events = [
        json.loads(line)["event"]
        for lines in log_lines().values()
        for line in lines
        if "project_workspace_service" in line
    ]
    failures = [e for e in events if "git clone failed" in e]
    assert failures, events
    # Real git detail, not the withheld-shape placeholder.
    assert not any("detail withheld" in e for e in events), events
    assert any("fatal" in e or "unable to" in e or "Could not" in e for e in failures), failures
    assert not any(_SECRET in e for e in events)
