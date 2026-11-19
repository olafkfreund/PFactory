"""CLI Update/Install must land where the server will find it, and say why it failed.

Owner report 2026-11-19: Update on Codex failed (EROFS: the image's
``~/.npm-global`` is read-only) and Update on Antigravity "succeeded" but the
UI kept the old version (the image copy on PATH shadowed the new install).
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import provider_runtime as pr
from server.routes import cli_accounts as ca


def _fake_cli(root: Path, binary: str, pkg_name: str, version: str) -> None:
    pkg = root / "lib" / "node_modules" / pkg_name
    (pkg / "bin").mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps({"version": version}))
    entry = pkg / "bin" / f"{binary}.js"
    entry.write_text("")
    entry.chmod(0o755)
    (root / "bin").mkdir(exist_ok=True)
    (root / "bin" / binary).symlink_to(entry)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    return tmp_path


def _record_runs(stderr: str = "", returncode: int = 0) -> tuple[list[str], Callable[..., Any]]:
    calls: list[str] = []

    def fake_run(args: list[str], **_kwargs: Any) -> SimpleNamespace:
        calls.append(args[-1] if args[:2] == ["bash", "-l"] else " ".join(args))
        is_install = "npm install" in calls[-1]
        return SimpleNamespace(
            returncode=returncode if is_install else 0,
            stdout="",
            stderr=stderr if is_install else "",
        )

    return calls, fake_run


@pytest.mark.parametrize("cli", ["codex", "gemini"])
def test_install_targets_the_writable_user_prefix(home: Path, cli: str) -> None:
    calls, fake_run = _record_runs()
    with (
        patch.object(subprocess, "run", fake_run),
        patch.object(ca, "_detect_cli_version", return_value="9.9.9"),
    ):
        out = ca.install_or_update_cli(cli)
    assert out["success"] is True
    install = next(c for c in calls if "npm install" in c)
    argv = shlex.split(install)
    assert argv[argv.index("--prefix") + 1] == str(home / ".local" / "cli-npm")


@pytest.mark.usefixtures("home")
def test_install_failure_shows_the_real_npm_error() -> None:
    _, fake_run = _record_runs(
        stderr="npm error EROFS: read-only file system, rename", returncode=1
    )
    with patch.object(subprocess, "run", fake_run):
        out = ca.install_or_update_cli("codex")
    assert out["success"] is False
    assert "EROFS" in out["error"]


def test_updated_antigravity_shadows_the_image_copy(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Install succeeded but the UI kept the old version: PATH found the image copy."""
    _fake_cli(home / "image", "antigravity", "@google/gemini-cli", "0.68.0")
    _fake_cli(home / ".local" / "cli-npm", "antigravity", "@google/gemini-cli", "0.69.0")
    monkeypatch.setenv("PATH", str(home / "image" / "bin"))
    pr.prefer_user_cli_path()
    assert ca._detect_cli_version("gemini") == "0.69.0"
