"""#335 phase 2 / #553: server-side path confinement, in two tiers.

``confine_to_workspace`` is the permissive (browse) tier -- workspace root plus
every registered project. ``confine_to_project`` is the strict tier -- the
registered projects alone. #553 split them; the tests below pin which tier
accepts what, and, just as importantly, what each one still REJECTS.
"""

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
from server.services import git_utils  # noqa: E402
from server.services.git_utils import (  # noqa: E402
    confine_to_project,
    confine_to_workspace,
)


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
def test_neither_tier_can_reject_an_already_registered_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Why the three `py/path-injection-sanitized` alerts under
    `routes/github._resolve_project_path` are still NOT closed after #553.

    Before #553 this test asserted the same acceptance against the single
    `_allowed_roots()`, and said in its docstring that it would "go red the day
    someone tightens `_allowed_roots` to the workspace root alone". #553 is
    that day, and the answer turned out to be no: the tiers were split, but
    workspace-root-only was NOT adopted, because the live registries measured
    for #553 hold three non-empty project paths and all three sit outside the
    workspace root. Such a tier would strand every project it protects.

    So the acceptance stays, and this test now pins it for BOTH tiers rather
    than one -- a registry-derived value is inside itself under either root
    set, so wrapping `_resolve_project_path` in either helper would clear the
    three alerts while rejecting nothing.
    """
    outside = Path("/etc")
    monkeypatch.setattr(git_utils, "load_projects", lambda: {"p": {"path": str(outside)}})

    # /etc is outside the workspace by every ordinary reading, and it is
    # accepted anyway - solely because the registry named it.
    assert confine_to_workspace(str(outside)) == outside.resolve()
    assert confine_to_project(str(outside)) == outside.resolve()


def test_strict_tier_rejects_a_workspace_neighbour(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tightening #553 actually buys.

    Two clones under one workspace root, only one of them registered. The
    browse tier admits both, because the workspace root is one of its roots --
    that is what makes "add a project you have not registered yet" possible.
    The strict tier admits only the registered one, so a request cannot read
    out of, or delete inside, its neighbour.
    """
    registered = workspace / "proj"
    neighbour = workspace / "someone-elses-clone"
    neighbour.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(git_utils, "load_projects", lambda: {"p": {"path": str(registered)}})

    assert confine_to_workspace(str(neighbour)) == neighbour.resolve()
    with pytest.raises(ValueError):
        confine_to_project(str(neighbour))


def test_strict_tier_accepts_a_registered_project_outside_the_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The migration case, pinned: #553 strands nothing.

    Both live registries measured for #553 hold projects outside the workspace
    root -- /mnt/data/... locally, ~/.pfactory/projects/... in the cluster. A
    path becomes usable by being REGISTERED, never by being under the
    workspace root, so the strict tier keeps every one of them reachable.
    """
    elsewhere = Path(tempfile.mkdtemp()) / "on-another-volume"
    elsewhere.mkdir(parents=True)
    monkeypatch.setattr(git_utils, "load_projects", lambda: {"p": {"path": str(elsewhere)}})

    assert confine_to_project(str(elsewhere / "src")) == (elsewhere / "src").resolve()


def test_strict_tier_ignores_empty_registry_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """A repo-only "tracked" project stores `path: ""`, and 17 of the 18
    entries in the live cluster registry are exactly that. `Path("").resolve()`
    is the server's CWD, so resolving those entries instead of skipping them
    would quietly authorise the whole working directory."""
    monkeypatch.setattr(git_utils, "load_projects", lambda: {"repo-only": {"path": ""}})

    assert git_utils.registered_project_roots() == []
    with pytest.raises(ValueError):
        confine_to_project(str(Path.cwd() / "anything"))


def test_strict_tier_rejects_traversal_out_of_a_registered_project(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Containment is checked after resolve(), so `..` cannot climb out of a
    registered root into its parent workspace."""
    registered = workspace / "proj"
    monkeypatch.setattr(git_utils, "load_projects", lambda: {"p": {"path": str(registered)}})

    with pytest.raises(ValueError):
        confine_to_project(str(registered / "sub" / ".." / ".." / ".." / "etc"))


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
    """No workspace + no registered projects -> nothing is allowed, in either
    tier. #553 renamed `_allowed_roots` to `browse_roots` and added
    `registered_project_roots`; an empty root list must still reject rather
    than fall through to allow."""
    monkeypatch.setattr(git_utils, "browse_roots", lambda: [])
    monkeypatch.setattr(git_utils, "registered_project_roots", lambda: [])
    with pytest.raises(ValueError):
        git_utils.confine_to_workspace("/anything")
    with pytest.raises(ValueError):
        git_utils.confine_to_project("/anything")
