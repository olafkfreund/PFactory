"""#655: the repo-only ``path: ""`` sentinel at the sites #647 did not reach.

#647 fixed ``resolve_project_path`` and routed ``routes/context`` and two
``routes/projects`` helpers through it. The same open-coded conversion survived
in ``changelog``, ``github``, ``execution``, ``terminal``, ``mcp`` and
``services/auto_fix_service``.

Two things every test here has to do, because a guard can fail in either
direction:

* ``""`` must NOT become the server's CWD -- and the proof is the filesystem,
  not the response. A route that returns a clean error while still mkdir'ing
  under the CWD is still the #647 bug.
* ``"."`` must still resolve. It is a real, legitimate relative path; a guard
  that rejects it too has replaced one wrong answer with another.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException

_WEB_SERVER = Path(__file__).resolve().parents[1]
# routes/changelog imports client_errors, which lives in apps/backend.
_BACKEND = _WEB_SERVER.parent / "backend"
for _p in (_WEB_SERVER, _BACKEND):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from client_errors import InputRejectedError  # noqa: E402  (apps/backend, added above)
from server import paths as server_paths  # noqa: E402
from server.routes import (  # noqa: E402
    changelog as changelog_routes,
    github as github_routes,
    mcp as mcp_routes,
    projects as proj,
    terminal as terminal_routes,
)
from server.services import auto_fix_service  # noqa: E402

REPO_ONLY = {"repo-only": {"repo": "o/r", "path": ""}}


def _registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, projects: dict[str, dict[str, str]]
) -> None:
    """Point the project registry at a throwaway file holding *projects*."""
    f = tmp_path / "projects.json"
    monkeypatch.setattr(proj, "get_projects_file", lambda: f)
    proj.save_projects(projects)


# --------------------------------------------------------------------------
# The shared envelope form of the resolver (routes that answer 200)
# --------------------------------------------------------------------------


def test_envelope_resolver_refuses_the_sentinel(monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path, REPO_ONLY)
    path, error = proj.resolve_project_path_or_error("repo-only")
    assert path is None
    assert error == {
        "success": False,
        "error": proj.NO_LOCAL_CLONE_DETAIL.format(project_id="repo-only"),
    }


def test_envelope_resolver_keeps_the_404_wording_for_an_unknown_project(monkeypatch, tmp_path):
    # The routes that adopted this helper used to answer with exactly this
    # string; the switch must not change what the portal renders.
    _registry(monkeypatch, tmp_path, {})
    path, error = proj.resolve_project_path_or_error("nope")
    assert path is None
    assert error == {"success": False, "error": "Project nope not found"}


def test_envelope_resolver_passes_a_dot_through(monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path, {"here": {"path": "."}})
    path, error = proj.resolve_project_path_or_error("here")
    assert error is None
    assert str(path) == "."


# --------------------------------------------------------------------------
# github.py -- the local chokepoint fronting ~20 call sites
# --------------------------------------------------------------------------


def test_github_chokepoint_returns_none_for_a_repo_only_project(monkeypatch, tmp_path):
    # None is what its ~20 callers already handle for "no usable path", so the
    # sentinel joins the case they were written for rather than a fake path.
    _registry(monkeypatch, tmp_path, REPO_ONLY)
    assert github_routes._resolve_project_path("repo-only") is None


def test_github_chokepoint_still_returns_a_dot(monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path, {"here": {"path": "."}})
    assert github_routes._resolve_project_path("here") == Path()


def test_github_token_persist_writes_nothing_under_cwd(monkeypatch, tmp_path):
    """``.pfactory/.env`` under the server's CWD is a credential written to the
    wrong directory, so assert on the filesystem."""
    _registry(monkeypatch, tmp_path, REPO_ONLY)

    def _gh_token(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"success": True, "output": "gho_x"}

    monkeypatch.setattr(github_routes, "run_gh_command", _gh_token)
    monkeypatch.chdir(tmp_path)

    assert github_routes._persist_cli_token_to_project("repo-only") is False
    assert not (tmp_path / ".pfactory").exists()


# --------------------------------------------------------------------------
# changelog.py
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_changelog_writes_no_changelog_under_cwd(monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path, REPO_ONLY)
    monkeypatch.chdir(tmp_path)

    result = await changelog_routes.save_changelog(
        projectId="repo-only",
        request=changelog_routes.ChangelogSaveRequest(content="# 1.0.0", version="1.0.0"),
    )

    assert result["success"] is False
    assert "no local clone" in result["error"]
    assert not (tmp_path / "CHANGELOG.md").exists()


@pytest.mark.asyncio
async def test_save_image_writes_no_assets_dir_under_cwd(monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path, REPO_ONLY)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(HTTPException) as exc:
        await changelog_routes.save_changelog_image(
            projectId="repo-only",
            request=changelog_routes.SaveImageRequest(imageData="Zm9v", filename="a.png"),
        )

    assert exc.value.status_code == 409
    assert not (tmp_path / ".pfactory").exists()


# --------------------------------------------------------------------------
# terminal.py -- a terminal buffer saved into the server's CWD
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminal_buffer_falls_back_to_home_not_cwd(monkeypatch, tmp_path):
    """A repo-only project has no clone, so its buffer belongs in the same
    default location an unknown project already uses -- not in ``./.pfactory``,
    which ``Path("")`` would have produced."""
    home = tmp_path / "home"
    home.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    _registry(monkeypatch, tmp_path, REPO_ONLY)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.chdir(cwd)

    class _Session:
        cwd = "/"
        shell = "/bin/sh"
        created_at = datetime(2026, 1, 1, tzinfo=UTC)

    class _Manager:
        def get_session(self, _terminal_id):
            return _Session()

    monkeypatch.setattr(terminal_routes, "get_pty_manager", _Manager)

    result = await terminal_routes.save_terminal_buffer(
        "t1", {"buffer": "hello", "projectId": "repo-only"}
    )

    assert result["success"] is True
    assert not (cwd / ".pfactory").exists()
    assert list((home / ".pfactory" / "terminal-sessions").glob("terminal_t1_*.json"))


@pytest.mark.asyncio
async def test_terminal_clear_skips_the_sentinel_but_not_a_dot(monkeypatch, tmp_path):
    """The sweep collects ``<path>/.pfactory/terminal-sessions`` for every
    registered project and deletes the session files in it. Under the sentinel
    that directory was the CWD's, so run it from a directory that has one and
    check the file survives -- then flip the registry to a genuine ``"."`` and
    check the same file is deleted, so the guard is not simply switched off."""
    home = tmp_path / "home"
    (home / ".pfactory" / "terminal-sessions").mkdir(parents=True)
    cwd = tmp_path / "cwd"
    sessions = cwd / ".pfactory" / "terminal-sessions"
    sessions.mkdir(parents=True)
    victim = sessions / "terminal_x.json"

    registry: Path = tmp_path / "projects.json"

    def _data_file(_name: str) -> Path:
        return registry

    def _register(entry: dict[str, dict[str, str]]) -> None:
        registry.write_text(json.dumps(entry))

    monkeypatch.setattr(server_paths, "get_data_file", _data_file)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.chdir(cwd)

    _register({"repo-only": {"path": ""}})
    victim.write_text("{}")
    result = await terminal_routes.clear_terminal_sessions()
    assert result["data"]["cleared"] == 0
    assert victim.exists()

    _register({"here": {"path": "."}})
    result = await terminal_routes.clear_terminal_sessions()
    assert result["data"]["cleared"] == 1
    assert not victim.exists()


# --------------------------------------------------------------------------
# mcp.py -- .expanduser() does not rescue Path("")
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_status_refuses_the_sentinel(monkeypatch, tmp_path):
    _registry(monkeypatch, tmp_path, REPO_ONLY)
    monkeypatch.setattr(mcp_routes, "_load_projects", proj.load_projects)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(HTTPException) as exc:
        await mcp_routes.get_mcp_status("repo-only")

    assert exc.value.status_code == 409
    assert "no local clone" in str(exc.value.detail)


def test_expanduser_does_not_rescue_the_empty_path():
    # The reason mcp.py needed its own guard rather than looking safe already.
    # Held in a variable because that is how the value arrives -- read out of
    # the registry, never written as a literal.
    sentinel = ""
    assert Path(sentinel).expanduser() == Path()


# --------------------------------------------------------------------------
# services/auto_fix_service.py
# --------------------------------------------------------------------------


def test_auto_fix_refuses_the_sentinel_but_accepts_a_dot():
    with pytest.raises(InputRejectedError):
        auto_fix_service._project_path({"repo-only": {"path": ""}}, "repo-only")
    with pytest.raises(InputRejectedError):
        auto_fix_service._project_path({}, "missing")
    assert auto_fix_service._project_path({"here": {"path": "."}}, "here") == Path()
