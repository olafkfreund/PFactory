"""#335 phase 2: server-side path confinement to the workspace / registered roots."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

_WS = Path(__file__).resolve().parents[1]
if str(_WS) not in sys.path:
    sys.path.insert(0, str(_WS))


@pytest.fixture()
def workspace(monkeypatch):
    ws = Path(tempfile.mkdtemp())
    monkeypatch.setenv("PROJECT_WORKSPACE_ROOT", str(ws))
    (ws / "proj" / "sub").mkdir(parents=True, exist_ok=True)
    return ws


def test_allows_paths_inside_workspace_unchanged(workspace):
    from server.services.git_utils import confine_to_workspace

    inside = workspace / "proj" / "sub"
    assert confine_to_workspace(str(inside)) == inside.resolve()


@pytest.mark.parametrize(
    "outside",
    ["/etc/passwd", "/", "/tmp", "/root/.ssh/id_rsa"],
)
def test_rejects_paths_outside_workspace(workspace, outside):
    from server.services.git_utils import confine_to_workspace

    with pytest.raises(ValueError):
        confine_to_workspace(outside)


def test_rejects_traversal_escape(workspace):
    from server.services.git_utils import confine_to_workspace

    # a value that resolves OUT of the workspace via .. must be refused
    escape = str(workspace / "proj" / ".." / ".." / ".." / "etc")
    with pytest.raises(ValueError):
        confine_to_workspace(escape)


def test_fail_closed_when_no_roots(monkeypatch):
    """No workspace + no registered projects -> nothing is allowed."""
    from server.services import git_utils

    monkeypatch.setattr(git_utils, "_allowed_roots", lambda: [])
    with pytest.raises(ValueError):
        git_utils.confine_to_workspace("/anything")
