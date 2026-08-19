"""A credentialed git argv must never reach the log FILES, whatever
`credentialed=` says (PFactory#576 follow-up; CodeQL 1696-1700).

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
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

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
)

# A closed local port fails immediately -- no network, no DNS.
# Assembled at import time rather than written as one literal: a realistic
# 40-char PAT literal trips the repo's gitleaks gate (generic-api-key, on
# entropy), and silencing a secret scanner to land a secret-leak test would be
# the wrong trade. The repeated word keeps the entropy low while the value is
# still PAT-shaped and unmistakable in a log line.
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
@pytest.mark.parametrize("credentialed", [True, False])
async def test_argv_is_never_written_to_any_log_file(
    tmp_path: Path,
    log_lines: Callable[[], dict[str, list[str]]],
    credentialed: bool,
) -> None:
    """The PAT must be absent from every emitted line -- on BOTH settings of
    `credentialed`, because that flag is caller-supplied and can be wrong."""
    with pytest.raises(GitOperationError) as excinfo:
        await _run_git(
            ["clone", _URL, str(tmp_path / "dest")],
            cwd=tmp_path,
            timeout=10,
            credentialed=credentialed,
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
        await _run_git(
            ["clone", _URL, str(tmp_path / "dest")], cwd=tmp_path, timeout=10, credentialed=True
        )

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
