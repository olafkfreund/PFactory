"""The IDE/terminal launchers must never run an executable the caller names.

Both endpoints hand their result straight to ``subprocess.Popen``. They used to
honour a ``customPath`` from the request body -- ``[custom_path, path]`` -- so
any authenticated caller could execute any binary in the server process
(CodeQL ``py/command-line-injection``, criticals #135/#136). The executable now
comes only from the curated map.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

_WEB = Path(__file__).resolve().parent.parent / "apps" / "web-server"
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))

pytest.importorskip("fastapi")

from server.routes.tasks import (  # noqa: E402
    OpenInIDERequest,
    OpenInTerminalRequest,
    get_ide_command,
    get_terminal_command,
)


@pytest.mark.parametrize("builder", [get_ide_command, get_terminal_command])
def test_builders_take_no_caller_supplied_executable(builder) -> None:
    """The signature itself must not accept a path to run."""
    params = set(inspect.signature(builder).parameters)
    assert "custom_path" not in params, (
        f"{builder.__name__} still accepts a caller-supplied executable"
    )


@pytest.mark.parametrize(
    ("model", "field"),
    [(OpenInIDERequest, "ide"), (OpenInTerminalRequest, "terminal")],
)
def test_request_bodies_reject_a_custom_path(model, field) -> None:
    """An extra customPath in the body must not become a field we read."""
    body = model(worktreePath="/srv/project", **{field: "vscode"}, customPath="/bin/sh")
    assert not hasattr(body, "customPath"), "customPath is still bound on the request model"


def test_attacker_controlled_ide_name_cannot_choose_the_binary() -> None:
    """An unknown IDE falls back to a known binary, never the supplied string."""
    cmd = get_ide_command("/bin/sh", "/srv/project")
    assert cmd[0] != "/bin/sh", "the IDE name must not become the executable"
    assert cmd[0] == "code"


def test_terminal_name_cannot_choose_the_binary() -> None:
    cmd = get_terminal_command("/bin/sh", "/srv/project")
    assert "/bin/sh" not in cmd, "the terminal name must not become the executable"
