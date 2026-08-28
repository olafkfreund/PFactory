"""#647: a repo-only project's ``path: ""`` must never become ``Path(".")``.

``ensure_tracked_project`` stores ``""`` to mean "no local clone yet". Every
consumer used to run that through ``Path(...)``, which yields ``Path(".")`` --
a real relative path against the server's CWD. On the read-only container root
that surfaced as ``OSError: [Errno 30]`` mentioning only ``.pfactory``; on a
writable one it would have written the project's index and ``.env`` into
whatever directory the server started in.

The tests below pin the distinction the old code lost: absent (``""``) and
"the current directory" (``"."``) are different answers. A guard that only
checked "does the path exist" would pass for both, so each case is asserted
separately.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.routes import (  # noqa: E402
    context as ctx,
    projects as proj,
)


def _registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, projects: dict[str, dict[str, str]]
) -> None:
    """Point the project registry at a throwaway file holding *projects*."""
    f = tmp_path / "projects.json"
    monkeypatch.setattr(proj, "get_projects_file", lambda: f)
    proj.save_projects(projects)


def test_repo_only_path_is_refused_not_converted(monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path, {"repo-only": {"repo": "o/r", "path": ""}})
    with pytest.raises(HTTPException) as exc:
        proj.resolve_project_path("repo-only")
    assert exc.value.status_code == 409
    # The message has to name the project and the reason -- "Failed to refresh
    # project index" with no path in it is what made #647 read as a network bug.
    assert "repo-only" in str(exc.value.detail)
    assert "no local clone" in str(exc.value.detail)


def test_missing_path_key_is_refused_too(monkeypatch, tmp_path):
    # ensure_tracked_project writes "", but a hand-edited or older record may
    # simply omit the key. Same meaning, same answer.
    _registry(monkeypatch, tmp_path, {"repo-only": {"repo": "o/r"}})
    with pytest.raises(HTTPException) as exc:
        proj.resolve_project_path("repo-only")
    assert exc.value.status_code == 409


def test_dot_is_a_real_path_and_still_resolves(monkeypatch, tmp_path):
    # The distinction the fix exists for: "" is absent, "." is the current
    # directory. Only the first is refused; a caller that genuinely means "."
    # (or any relative path) still gets it back untouched.
    _registry(monkeypatch, tmp_path, {"here": {"path": "."}})
    assert str(proj.resolve_project_path("here")) == "."


def test_unknown_project_still_404s(monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path, {})
    with pytest.raises(HTTPException) as exc:
        proj.resolve_project_path("nope")
    assert exc.value.status_code == 404


def test_real_clone_path_is_unchanged(monkeypatch, tmp_path):
    clone = tmp_path / "clone"
    clone.mkdir()
    _registry(monkeypatch, tmp_path, {"real": {"path": str(clone)}})
    assert proj.resolve_project_path("real") == clone


def test_context_routes_return_the_reason_instead_of_a_relative_path(monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path, {"repo-only": {"repo": "o/r", "path": ""}})
    project_path, error = ctx._project_path("repo-only")
    assert project_path is None
    assert error is not None
    assert error["success"] is False
    assert "no local clone" in str(error["error"])


@pytest.mark.asyncio
async def test_refresh_index_never_writes_under_cwd(monkeypatch, tmp_path):
    """The reported symptom: POST /context/refresh mkdir'd a relative
    ``.pfactory``. Assert on the filesystem, not just the response -- a fix
    that returned a nice error while still creating the directory would pass a
    response-only assertion."""
    _registry(monkeypatch, tmp_path, {"repo-only": {"repo": "o/r", "path": ""}})
    monkeypatch.chdir(tmp_path)

    result = await ctx.refresh_project_index("repo-only")

    assert result["success"] is False
    assert "no local clone" in result["error"]
    assert not (tmp_path / ".pfactory").exists()
