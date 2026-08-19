#!/usr/bin/env python3
"""Tests for #82 PR-A — portal-managed project workspaces.

Covers:
- ProjectCreate schema: requires exactly one of path/gitUrl, rejects both
- slug_from_git_url: SSH + HTTPS forms; .git suffix stripping
- workspace_root: honors PROJECT_WORKSPACE_ROOT env, falls back to default
- clone_or_update: invokes git correctly for fresh clones and existing dirs
- _run_git: surfaces non-zero exit codes as GitOperationError
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))


# ---------------------------------------------------------------------------
# ProjectCreate schema validation
# ---------------------------------------------------------------------------


def test_project_create_requires_path_or_gitUrl():
    import pydantic
    from server.routes.projects import ProjectCreate
    with pytest.raises(pydantic.ValidationError):
        ProjectCreate()
    with pytest.raises(pydantic.ValidationError):
        ProjectCreate(name="just-a-name")


def test_project_create_rejects_both_path_and_gitUrl():
    import pydantic
    from server.routes.projects import ProjectCreate
    with pytest.raises(pydantic.ValidationError):
        ProjectCreate(path="/x", gitUrl="https://example.com/r")


def test_project_create_accepts_path_only():
    from server.routes.projects import ProjectCreate
    pc = ProjectCreate(path="/tmp/x")
    assert pc.path == "/tmp/x"
    assert pc.gitUrl is None
    assert pc.branch is None


def test_project_create_accepts_gitUrl_only():
    from server.routes.projects import ProjectCreate
    pc = ProjectCreate(gitUrl="https://example.com/foo.git", branch="main")
    assert pc.gitUrl == "https://example.com/foo.git"
    assert pc.branch == "main"
    assert pc.path is None


def test_project_create_accepts_snake_case_aliases():
    """Frontend may send `git_url` / `git_credential_id` rather than camelCase."""
    from server.routes.projects import ProjectCreate
    pc = ProjectCreate.model_validate(
        {"git_url": "https://example.com/r", "git_credential_id": "cred-1"}
    )
    assert pc.gitUrl == "https://example.com/r"
    assert pc.gitCredentialId == "cred-1"


def test_project_create_treats_empty_strings_as_missing():
    """Frontend sometimes sends '' instead of omitting the field."""
    from server.routes.projects import ProjectCreate
    pc = ProjectCreate(path="/x", gitUrl="")
    assert pc.path == "/x"
    assert pc.gitUrl is None


# ---------------------------------------------------------------------------
# slug_from_git_url
# ---------------------------------------------------------------------------


def test_slug_handles_ssh_form():
    from server.services.project_workspace_service import slug_from_git_url
    assert slug_from_git_url("git@github.com:olaf/PFactory.git") == "olaf-PFactory"


def test_slug_handles_https_form():
    from server.services.project_workspace_service import slug_from_git_url
    assert (
        slug_from_git_url("https://github.com/olaf/PFactory.git") == "olaf-PFactory"
    )


def test_slug_handles_nested_groups():
    from server.services.project_workspace_service import slug_from_git_url
    assert (
        slug_from_git_url("https://gitlab.com/group/sub/repo.git")
        == "group-sub-repo"
    )


def test_slug_drops_dot_git_suffix():
    from server.services.project_workspace_service import slug_from_git_url
    assert slug_from_git_url("https://example.test/me/x.git") == "me-x"
    # already-no-suffix should work too
    assert slug_from_git_url("https://example.test/me/x") == "me-x"


def test_slug_empty_input_does_not_crash():
    from server.services.project_workspace_service import slug_from_git_url
    # Pathological URL with no path component → falls back to "workspace"
    assert slug_from_git_url("https://example.test") == "workspace"


# ---------------------------------------------------------------------------
# workspace_root
# ---------------------------------------------------------------------------


def test_workspace_root_uses_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PROJECT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    from server.services import project_workspace_service as svc
    assert svc.workspace_root() == tmp_path / "ws"


def test_workspace_root_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("PROJECT_WORKSPACE_ROOT", raising=False)
    from server.services import project_workspace_service as svc
    assert svc.workspace_root() == Path.home() / ".pfactory" / "workspaces"


# ---------------------------------------------------------------------------
# clone_or_update — mock the subprocess, assert the right git invocations
# ---------------------------------------------------------------------------


def _mock_proc(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    """Return an awaitable mock subprocess + a future for communicate()."""
    proc = MagicMock()
    proc.returncode = returncode

    async def _communicate():
        return (stdout, stderr)

    proc.communicate = _communicate
    proc.kill = MagicMock()
    return proc


@pytest.mark.asyncio
async def test_clone_or_update_fresh_clones_when_no_dir(tmp_path):
    """First call with a non-existent dir → `git clone`."""
    from server.services import project_workspace_service as svc

    captured: list[list[str]] = []

    async def fake_create_subprocess_exec(*args, **kw):
        captured.append(list(args))
        return _mock_proc(returncode=0)

    with patch("asyncio.create_subprocess_exec", new=fake_create_subprocess_exec):
        result = await svc.clone_or_update(
            git_url="https://example.test/me/repo.git",
            branch="main",
            root=tmp_path,
        )

    assert result == tmp_path / "me-repo"
    # First invocation must be `git clone --branch main https://... <dest>`
    assert captured[0][0] == "git"
    assert captured[0][1] == "clone"
    assert "--branch" in captured[0]
    assert "main" in captured[0]
    assert captured[0][-1] == str(tmp_path / "me-repo")


@pytest.mark.asyncio
async def test_clone_or_update_updates_when_dir_exists(tmp_path):
    """When .git dir already exists → fetch+(checkout?)+pull, no fresh clone."""
    from server.services import project_workspace_service as svc

    # Pre-create the workspace + a fake .git
    ws = tmp_path / "me-repo"
    (ws / ".git").mkdir(parents=True)

    captured: list[list[str]] = []

    async def fake_create_subprocess_exec(*args, **kw):
        captured.append(list(args))
        return _mock_proc(returncode=0)

    with patch("asyncio.create_subprocess_exec", new=fake_create_subprocess_exec):
        result = await svc.clone_or_update(
            git_url="https://example.test/me/repo.git",
            branch="feat/x",
            root=tmp_path,
        )

    assert result == ws
    cmd_names = [c[1] for c in captured]
    assert "clone" not in cmd_names, "should not re-clone an existing workspace"
    assert "fetch" in cmd_names
    assert "checkout" in cmd_names
    assert "pull" in cmd_names


@pytest.mark.asyncio
async def test_clone_or_update_no_branch_skips_checkout(tmp_path):
    from server.services import project_workspace_service as svc

    ws = tmp_path / "me-repo"
    (ws / ".git").mkdir(parents=True)

    captured: list[list[str]] = []

    async def fake_create_subprocess_exec(*args, **kw):
        captured.append(list(args))
        return _mock_proc(returncode=0)

    with patch("asyncio.create_subprocess_exec", new=fake_create_subprocess_exec):
        await svc.clone_or_update(
            git_url="https://example.test/me/repo.git",
            branch=None,
            root=tmp_path,
        )

    cmd_names = [c[1] for c in captured]
    assert "fetch" in cmd_names
    assert "pull" in cmd_names
    assert "checkout" not in cmd_names


@pytest.mark.asyncio
async def test_clone_or_update_raises_on_git_failure(tmp_path):
    """PFactory#576: git's stderr is no longer embedded in the exception
    message. For a credentialed clone this call's own argv carries the token
    (`fetch_url`), and a caller (routes/projects.py) puts this message
    straight into a client response -- the demonstrated leak; withholding
    stderr here too is defence in depth (see `_run_git`'s docstring). Only
    the subcommand + exit code are surfaced regardless."""
    from server.services.project_workspace_service import (
        GitOperationError,
        clone_or_update,
    )

    async def fake_create_subprocess_exec(*args, **kw):
        return _mock_proc(
            returncode=128, stderr=b"fatal: repository not found"
        )

    with patch("asyncio.create_subprocess_exec", new=fake_create_subprocess_exec):
        with pytest.raises(GitOperationError) as exc:
            await clone_or_update(
                git_url="https://example.test/missing/repo.git",
                root=tmp_path,
            )

    assert "exit 128" in str(exc.value)
    assert "clone" in str(exc.value)
    assert "repository not found" not in str(exc.value)


@pytest.mark.asyncio
async def test_clone_or_update_raises_on_missing_git(tmp_path):
    from server.services.project_workspace_service import (
        GitOperationError,
        clone_or_update,
    )

    async def fake_create_subprocess_exec(*args, **kw):
        raise FileNotFoundError("git: not found")

    with patch("asyncio.create_subprocess_exec", new=fake_create_subprocess_exec):
        with pytest.raises(GitOperationError) as exc:
            await clone_or_update(
                git_url="https://example.test/me/r.git",
                root=tmp_path,
            )
    assert "git executable not found" in str(exc.value)
