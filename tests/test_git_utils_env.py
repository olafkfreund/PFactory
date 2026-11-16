"""Repository Git configuration cannot execute local filters or merge drivers."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from core.child_env import git_env, merge_driver_pins
from tools.git_writer import GitWriteRequest, write_tests_to_branch


def test_local_filter_and_merge_driver_do_not_run(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)

    git("init", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    (repo / ".gitattributes").write_text("*.txt filter=x merge=x\n")
    (repo / "file.txt").write_text("base\n")
    git("add", ".gitattributes", "file.txt")
    git("commit", "-m", "base")
    git("branch", "side")
    (repo / "file.txt").write_text("main\n")
    git("commit", "-am", "main")
    git("checkout", "side")
    (repo / "file.txt").write_text("side\n")
    git("commit", "-am", "side")
    git("checkout", "main")

    marker = tmp_path / "executed"
    for key in ("filter.x.clean", "filter.x.smudge", "merge.x.driver"):
        git("config", key, f"touch {marker}")

    request = GitWriteRequest(repo, "main", (("new.txt", "content\n"),), "generated")
    result = write_tests_to_branch(request, dry_run=False)
    assert result.ok, result.error
    assert not marker.exists()

    pins = merge_driver_pins(repo)
    merged = subprocess.run(
        ["git", *pins, "merge", "side"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=git_env(),
    )
    assert merged.returncode != 0
    assert not marker.exists()


def test_git_utils_calls_carry_pinned_env_and_no_ext_diff(tmp_path: Path, monkeypatch) -> None:
    from core.workspace import git_utils as ws
    from merge import git_utils as merge_git

    calls: list[tuple[list[str], dict[str, str] | None]] = []

    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs.get("env")))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(ws, "merge_driver_pins", lambda _cwd: ["-c", "merge.x.driver="])
    monkeypatch.setattr(ws.subprocess, "run", fake_run)
    monkeypatch.setattr(merge_git.subprocess, "run", fake_run)

    ws.get_file_content_from_ref(tmp_path, "HEAD", "a.txt")
    ws.get_changed_files_from_branch(tmp_path, "main", "side")
    ws.detect_file_renames(tmp_path, "main", "side")
    merge_git.get_file_from_branch(tmp_path, "a.txt", "main")

    assert len(calls) == 4
    for argv, env in calls:
        assert env is not None and env["GIT_CONFIG_KEY_0"] == "core.hooksPath"
        if "diff" in argv or "show" in argv or "log" in argv:
            assert "--no-ext-diff" in argv and "--no-textconv" in argv
    diff_argv = next(a for a, _ in calls if "diff" in a)
    assert diff_argv[:3] == ["git", "-c", "merge.x.driver="]


def test_web_run_git_command_keeps_token_and_pins_remote_programs(
    tmp_path: Path, monkeypatch
) -> None:
    sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "web-server"))
    from server.services import git_utils

    monkeypatch.setenv("GH_TOKEN", "sentinel-token")
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"], seen["env"] = list(argv), kwargs["env"]
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(git_utils.subprocess, "run", fake_run)
    assert git_utils.run_git_command(["fetch", "origin"], tmp_path)["success"]
    assert seen["env"]["GH_TOKEN"] == "sentinel-token"
    assert seen["env"]["GIT_CONFIG_KEY_0"] == "core.hooksPath"
    assert "core.gitProxy=" in seen["argv"]
    git_utils.run_git_command(["status"], tmp_path)
    assert "core.gitProxy=" not in seen["argv"]
