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

from server.services import git_utils  # noqa: E402
from server.services.terminal_worktree_service import (  # noqa: E402
    TerminalWorktreeService,
)


def _declare_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``tmp_path`` a registered project so the #553 strict barrier passes.

    The service confines its ``project_path`` through ``confine_to_project``,
    and a bare tmp_path is inside no registered project, so without this the
    403 arrives before the ref this file is actually about ever reaches git.
    Declaring the root lets the barrier RUN and succeed rather than stubbing it
    out. TWO entries, because with one root "confines to the registered set"
    and "confines to the first root" are the same observation.
    """
    other = tmp_path.parent / "another-registered-project"
    other.mkdir(exist_ok=True)
    monkeypatch.setattr(
        git_utils, "registered_project_roots", lambda: [other.resolve(), tmp_path.resolve()]
    )


def _service_with_config(tmp_path: Path, branch: str, monkeypatch):
    """A service whose config already carries ``branch`` for one worktree."""
    _declare_roots(tmp_path, monkeypatch)
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


def test_a_project_path_outside_every_registered_root_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The barrier the helper above satisfies is real, not stubbed (#553).

    Without this, `_declare_roots` would be indistinguishable from neutralising
    the guard: every test in this file would pass just as happily if
    `confine_to_project` accepted anything.
    """
    _declare_roots(tmp_path, monkeypatch)
    stranger = tmp_path.parent / "not-a-registered-project"
    stranger.mkdir(exist_ok=True)

    with pytest.raises(ValueError):
        TerminalWorktreeService(str(stranger))
