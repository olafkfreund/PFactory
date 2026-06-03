#!/usr/bin/env python3
"""Tests for #82 PR-B — pull-on-poll hook in auto_fix_service.

When Auto-Fix polls a project that was registered via gitUrl
(epic #82 PR-A), the service should ``git pull --ff-only`` before
reading the issue list so the agent always sees the latest commits.

Local-path projects (no ``clonedFrom`` in projects.json) must be
unaffected — the hook is a no-op there.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))


@pytest.mark.asyncio
async def test_pull_on_poll_noop_for_local_path_projects(tmp_path):
    """A project without ``clonedFrom`` in projects.json is local-mode —
    the pull hook must do nothing (no clone_or_update call)."""
    from server.services import auto_fix_service

    projects_fixture = {
        "proj-local": {
            "path": str(tmp_path),
            "name": "local",
            # No clonedFrom — this is a local-mode project.
        }
    }

    with patch(
        "server.routes.projects.load_projects", return_value=projects_fixture
    ), patch(
        "server.services.project_workspace_service.clone_or_update",
        new=AsyncMock(),
    ) as mock_clone:
        await auto_fix_service._pull_clone_if_any("proj-local")

    mock_clone.assert_not_awaited()


@pytest.mark.asyncio
async def test_pull_on_poll_fast_forwards_cloned_projects(tmp_path):
    """A project with ``clonedFrom`` set triggers clone_or_update."""
    from server.services import auto_fix_service

    # The on-disk project_path must look like an existing clone for the
    # hook to fire the pull (it checks .is_dir()).
    project_path = tmp_path / "olaf-PFactory"
    project_path.mkdir()

    projects_fixture = {
        "proj-cloned": {
            "path": str(project_path),
            "name": "PFactory",
            "clonedFrom": "https://github.com/olaf/PFactory.git",
            "clonedBranch": "main",
        }
    }

    with patch(
        "server.routes.projects.load_projects", return_value=projects_fixture
    ), patch(
        "server.services.project_workspace_service.clone_or_update",
        new=AsyncMock(return_value=project_path),
    ) as mock_clone:
        await auto_fix_service._pull_clone_if_any("proj-cloned")

    mock_clone.assert_awaited_once()
    kwargs = mock_clone.await_args.kwargs
    assert kwargs["git_url"] == "https://github.com/olaf/PFactory.git"
    assert kwargs["branch"] == "main"
    assert kwargs["slug"] == "olaf-PFactory"
    # root is the parent directory of the project's on-disk path,
    # which lets clone_or_update reproduce the same slug→dir mapping
    # the original add_project used.
    assert kwargs["root"] == project_path.parent


@pytest.mark.asyncio
async def test_pull_on_poll_swallows_git_errors(tmp_path):
    """Pull-on-poll must not abort the poll cycle on a transient git
    failure (network blip, lock contention, etc.). It should log and
    continue with whatever's on disk."""
    from server.services import auto_fix_service
    from server.services.project_workspace_service import GitOperationError

    project_path = tmp_path / "x"
    project_path.mkdir()

    projects_fixture = {
        "proj-flaky": {
            "path": str(project_path),
            "name": "x",
            "clonedFrom": "https://github.com/me/x.git",
        }
    }

    with patch(
        "server.routes.projects.load_projects", return_value=projects_fixture
    ), patch(
        "server.services.project_workspace_service.clone_or_update",
        new=AsyncMock(side_effect=GitOperationError("network unreachable")),
    ):
        # Must not raise.
        await auto_fix_service._pull_clone_if_any("proj-flaky")


@pytest.mark.asyncio
async def test_pull_on_poll_skips_when_path_missing(tmp_path):
    """If the recorded path doesn't exist on disk (operator deleted the
    workspace), skip without calling clone_or_update — let the next
    operator action handle the situation."""
    from server.services import auto_fix_service

    projects_fixture = {
        "proj-gone": {
            "path": str(tmp_path / "deleted-dir"),
            "clonedFrom": "https://github.com/me/x.git",
        }
    }

    with patch(
        "server.routes.projects.load_projects", return_value=projects_fixture
    ), patch(
        "server.services.project_workspace_service.clone_or_update",
        new=AsyncMock(),
    ) as mock_clone:
        await auto_fix_service._pull_clone_if_any("proj-gone")

    mock_clone.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_project_persists_cloned_from(tmp_path, monkeypatch):
    """The add_project route, when called with gitUrl, must persist
    ``clonedFrom`` (+ optional ``clonedBranch``) on the project entry
    so the pull-on-poll hook can find it later."""
    from server.routes.projects import ProjectCreate, add_project

    captured: dict = {}

    def fake_load_projects():
        return {}

    def fake_save_projects(data):
        captured["projects"] = data

    async def fake_clone(**kwargs):
        # Pretend the clone landed under tmp_path
        path = tmp_path / "olaf-PFactory"
        path.mkdir(exist_ok=True)
        return path

    monkeypatch.setattr(
        "server.routes.projects.load_projects", fake_load_projects
    )
    monkeypatch.setattr(
        "server.routes.projects.save_projects", fake_save_projects
    )
    monkeypatch.setattr(
        "server.services.project_workspace_service.clone_or_update", fake_clone
    )

    project = ProjectCreate(
        gitUrl="https://github.com/olaf/PFactory.git", branch="dev"
    )
    await add_project(project)

    # Exactly one project should have been saved with clonedFrom set.
    saved = captured["projects"]
    assert len(saved) == 1
    only_project = next(iter(saved.values()))
    assert only_project["clonedFrom"] == "https://github.com/olaf/PFactory.git"
    assert only_project["clonedBranch"] == "dev"
