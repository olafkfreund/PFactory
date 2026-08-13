"""#335 phase 2: server-side path confinement to the workspace / registered roots."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

_WS = Path(__file__).resolve().parents[1]
if str(_WS) not in sys.path:
    sys.path.insert(0, str(_WS))

import inspect  # noqa: E402

import server.routes.projects as projects_mod  # noqa: E402
from server.services.git_utils import confine_to_workspace as _confine  # noqa: E402


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


@pytest.mark.usefixtures("workspace")
def test_confine_to_workspace_cannot_reject_an_already_registered_root(monkeypatch):
    """The reason the three open `py/path-injection-sanitized` alerts under
    `routes/github._resolve_project_path` are NOT closed by calling this
    barrier: `_allowed_roots()` is the workspace root plus every *registered*
    project root, so a registry-derived path is always inside itself.

    Wrapping `_resolve_project_path` in `confine_to_workspace` would satisfy
    the CodeQL sanitizer and change nothing at runtime. This test exists so
    that claim is checked by CI rather than asserted in a PR description - if
    someone tightens `_allowed_roots` to the workspace root alone, this goes
    red and the containment fix becomes available (and honest).
    """
    outside = Path("/etc")
    monkeypatch.setattr(projects_mod, "load_projects", lambda: {"p": {"path": str(outside)}})

    # /etc is outside the workspace by every ordinary reading, and it is
    # accepted anyway - solely because the registry named it.
    assert _confine(str(outside)) == outside.resolve()


def test_registry_paths_are_confined_by_their_writers():
    """...and this is what actually makes those three alerts false positives.

    `add_project` / `update_project` are the only writers of a project's
    `path`, and both run the request-supplied value through
    `confine_to_workspace` before it is stored. So the value
    `_resolve_project_path` hands downstream is never caller-supplied text; the
    caller only chooses WHICH already-confined project to address, via a dict
    key guarded by `not in projects`.
    """
    for fn in (projects_mod.add_project, projects_mod.update_project):
        src = inspect.getsource(fn)
        assert "confine_to_workspace(" in src, (
            f"{fn.__name__} no longer confines the registered path; the "
            "py/path-injection FP argument for routes/github.py dies with it"
        )


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
