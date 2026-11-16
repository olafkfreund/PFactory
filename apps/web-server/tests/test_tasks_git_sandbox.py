"""PFactory#1009: route git calls run with the sandboxed env, never bare subprocess."""

# ruff: noqa: S603, S607
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_WEB_SERVER = Path(__file__).resolve().parents[1]
for _p in (_WEB_SERVER, _WEB_SERVER.parent / "backend"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from server.services import git_utils  # noqa: E402


def test_git_run_drops_server_secrets_and_pins_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    monkeypatch.setenv("APP_DATABASE_URL", "postgres://x")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    seen: dict[str, Any] = {}
    real = subprocess.run

    def spy(cmd: list[str], **kw: Any) -> Any:
        if "env" in kw and cmd[:1] == ["git"] and cmd[-1:] == ["HEAD"]:
            seen["env"] = kw["env"]
        return real(cmd, **kw)

    monkeypatch.setattr(subprocess, "run", spy)
    git_utils.git_run(["git", "show", "HEAD"], cwd=tmp_path, capture_output=True, text=True)
    env = seen["env"]
    assert "JWT_SECRET" not in env and "APP_DATABASE_URL" not in env
    assert "GIT_CONFIG_COUNT" in env  # exec-bearing config pins from git_env


def test_tasks_routes_have_no_bare_git_subprocess_run() -> None:
    src = (_WEB_SERVER / "server" / "routes" / "tasks.py").read_text()
    bare = re.findall(r'subprocess\.run\(\s*(?:\[\s*"git"|push_cmd|merge_cmd)', src)
    assert bare == []


def test_git_run_blocks_external_diff_and_textconv(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    marker = tmp_path / "ran"
    (tmp_path / ".gitattributes").write_text("*.txt diff=evil\n")
    (tmp_path / "a.txt").write_text("x\n")
    for k, v in (
        ("diff.evil.textconv", f"touch {marker}; cat"),
        ("diff.external", f"touch {marker}; true"),
    ):
        subprocess.run(["git", "-C", str(tmp_path), "config", k, v], check=True)
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "1"], check=True)
    (tmp_path / "a.txt").write_text("y\n")
    git_utils.git_run(["git", "diff"], cwd=tmp_path, capture_output=True, text=True)
    git_utils.git_run(["git", "show", "HEAD"], cwd=tmp_path, capture_output=True, text=True)
    assert not marker.exists()
