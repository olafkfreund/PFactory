#!/usr/bin/env python3
"""A ref read back out of a config file is asserted before it reaches git argv.

PFactory#505. Every other ref reaching a git argv in
``terminal_worktree_service`` is safe by construction: ``branch_name`` is
``f"terminal/{name}"`` over a ``safe_spec_component`` name, and ``base_branch``
is passed through ``assert_safe_git_ref`` before use. ``branch`` in
``remove_worktree`` is the exception -- it comes from the worktree config file,
so nothing in that call path establishes it, and its safety rests on the file
staying trustworthy.

That is exactly the shape the module's own comment warns about above
``base_branch``: relying on an incidental property of a different method is how
these holes reopen. The guard is defence in depth rather than a live exploit
fix -- the config is only written by ``create_worktree``, which sanitizes -- but
a tampered or older-format config must not be able to hand git an option.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.services import terminal_worktree_service as mod  # noqa: E402
from server.services.terminal_worktree_service import (  # noqa: E402
    TerminalWorktreeService,
)


def _service_with_config(tmp_path: Path, branch: str, monkeypatch):
    """A service whose config already carries ``branch`` for one worktree.

    ``confine_to_workspace`` is neutralised because a tmp_path is not inside a
    registered project root. That barrier is a separate control with its own
    tests; what is under test here is the ref that reaches git argv.
    """
    monkeypatch.setattr(mod, "confine_to_workspace", lambda p, **_k: Path(p))
    svc = TerminalWorktreeService(str(tmp_path))
    wt_dir = tmp_path / "wt"
    wt_dir.mkdir(parents=True, exist_ok=True)
    svc.config_file.parent.mkdir(parents=True, exist_ok=True)
    svc.config_file.write_text(
        json.dumps({"worktrees": [{"name": "demo", "path": str(wt_dir), "branch": branch}]})
    )
    return svc


def _record_git_calls(svc, monkeypatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def _fake(cmd, check=True):  # noqa: ARG001 - test double
        calls.append(list(cmd))

        class _R:
            returncode = 0
            stdout = ""

        return _R()

    monkeypatch.setattr(svc, "_run_git_command", _fake)
    monkeypatch.setattr(svc, "_is_git_repo", lambda: True)
    return calls


def test_a_hostile_branch_in_the_config_never_reaches_git_argv(tmp_path, monkeypatch):
    """A leading dash is an option to git, not a branch name.

    `git branch -D --output=/etc/cron.d/x` is a file-write primitive, which is
    why the validator rejects anything that cannot begin a ref.
    """
    svc = _service_with_config(tmp_path, "--output=/tmp/pwned", monkeypatch)
    calls = _record_git_calls(svc, monkeypatch)

    svc.remove_worktree("demo", delete_branch=True)

    branch_deletes = [c for c in calls if c[:3] == ["git", "branch", "-D"]]
    assert branch_deletes == [], calls
    # The removal itself must still have happened — the guard protects the ref,
    # it does not abandon the operation.
    assert any(c[:3] == ["git", "worktree", "remove"] for c in calls), calls


def test_a_legitimate_branch_is_still_deleted(tmp_path, monkeypatch):
    """The control with teeth: a guard that refused everything would also pass
    the test above while silently breaking branch deletion."""
    svc = _service_with_config(tmp_path, "terminal/demo", monkeypatch)
    calls = _record_git_calls(svc, monkeypatch)

    svc.remove_worktree("demo", delete_branch=True)

    assert ["git", "branch", "-D", "terminal/demo"] in calls, calls


@pytest.mark.parametrize(
    "hostile",
    [
        "--upload-pack=touch /tmp/x",
        "-D",
        "main..evil",  # an embedded range would rewrite the range it lands in
    ],
)
def test_other_option_shaped_refs_are_rejected_too(tmp_path, monkeypatch, hostile):
    svc = _service_with_config(tmp_path, hostile, monkeypatch)
    calls = _record_git_calls(svc, monkeypatch)

    svc.remove_worktree("demo", delete_branch=True)

    assert [c for c in calls if c[:3] == ["git", "branch", "-D"]] == [], calls
