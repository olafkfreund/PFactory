"""Git metadata baseline at worktree creation and refusal of remote program keys (#3737)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from core.child_env import merge_driver_pins
from worktree import WorktreeManager


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_tampered_git_metadata_is_detected_after_create_worktree(temp_git_repo: Path) -> None:
    info = WorktreeManager(temp_git_repo).create_worktree("001-baseline")
    assert "merge.x.driver=" not in merge_driver_pins(info.path, baseline=True)
    _git(temp_git_repo, "config", "merge.x.driver", "touch /tmp/pwned")
    with pytest.raises(RuntimeError, match="git metadata changed"):
        merge_driver_pins(info.path, baseline=True)
    # The web-server helpers on a user's own repo do not check the baseline.
    assert "merge.x.driver=" in merge_driver_pins(info.path)


def test_tampered_worktree_attributes_are_detected(temp_git_repo: Path) -> None:
    WorktreeManager(temp_git_repo).create_worktree("002-attrs")
    (temp_git_repo / ".git" / "info").mkdir(exist_ok=True)
    (temp_git_repo / ".git" / "info" / "attributes").write_text("* merge=x\n")
    with pytest.raises(RuntimeError, match="info/attributes"):
        merge_driver_pins(temp_git_repo, baseline=True)


@pytest.mark.parametrize("key", ["remote.evil.uploadpack", "remote.origin.receivepack"])
def test_unpinned_remote_program_is_rejected(temp_git_repo: Path, key: str) -> None:
    _git(temp_git_repo, "config", key, "/tmp/x")
    with pytest.raises(RuntimeError, match="cannot be pinned"):
        merge_driver_pins(temp_git_repo)
