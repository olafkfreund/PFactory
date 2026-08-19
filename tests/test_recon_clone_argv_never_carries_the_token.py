"""The recon clone must never put the token in the child's argv (PFactory#615).

The same property, and deliberately the same shape, as
``apps/web-server/tests/test_workspace_argv_never_logged.py::test_token_is_absent_from_the_child_argv``
(PFactory#602) -- extended to the OTHER site in this repo that built a
credential-bearing clone URL: ``plan.recon.clone._git_url``.

``/proc/<pid>/cmdline`` is world-readable to every uid on the host for the
lifetime of the clone, so a token embedded in a URL that is handed to
``subprocess.run`` as an argv element is disclosed to every process on the box.
That is upstream of everything the module already defended: the hardened env,
and the ``CalledProcessError`` handler that refuses to surface git's stderr.
Neither can reach argv. ``GIT_ASKPASS`` removes the cause -- the URL carries
the username only and the password is read from ``GIT_PASS``, and
``/proc/<pid>/environ`` is owner-only.

Checked on ONE real git child, both ways:

* the argv this module asked for, and
* ``/proc/<pid>/cmdline`` -- what the kernel actually published -- read while
  the process is still alive. The remote is a real socket that accepts and
  never speaks, so git blocks in the HTTP exchange rather than racing us to
  exit.

Mutation check: restore ``f"https://x-access-token:{token}@{host}/..."`` in
``_git_url`` and ``test_the_token_is_absent_from_the_recon_child_argv`` goes red
on both assertions (recorded argv and /proc/<pid>/cmdline).
"""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from plan.recon import clone as clone_mod  # noqa: E402

# Assembled rather than written as one literal: a realistic PAT literal trips
# the repo's gitleaks gate on entropy, and silencing a secret scanner to land a
# secret-leak test would be the wrong trade. The repeated word keeps entropy low
# while the value stays PAT-shaped and unmistakable in a command line.
_SECRET = "ghp_" + "RECONARGVCANARY" * 2


def test_the_recon_clone_url_carries_no_token(monkeypatch):
    """The URL itself -- the thing that becomes an argv element."""
    monkeypatch.setenv("PFACTORY_RECON_TOKEN", _SECRET)
    monkeypatch.delenv("PFACTORY_RECON_GIT_HOST", raising=False)
    url = clone_mod._git_url("owner/repo")
    assert _SECRET not in url
    # Username still present: that is what makes git ask askpass for a password.
    assert url == "https://x-access-token@github.com/owner/repo.git"


def test_the_token_is_absent_from_the_recon_child_argv(monkeypatch):
    """The property the issue is about, on a real spawned git process."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    monkeypatch.setenv("PFACTORY_RECON_TOKEN", _SECRET)
    monkeypatch.setenv("PFACTORY_RECON_GIT_HOST", f"127.0.0.1:{port}")

    seen: list[tuple[list[str], bytes, dict[str, str]]] = []

    def spy(argv, **kwargs):
        """Spawn the real child so the kernel publishes a real cmdline, read
        it while the process is alive, then stop it -- reconnaissance itself
        is not what is under test here. Only the clone is spawned for real;
        the follow-up ``rev-parse`` would run in a checkout that never
        appeared."""
        if "clone" not in argv:
            return subprocess.CompletedProcess(argv, 0, "", "")
        proc = subprocess.Popen(  # noqa: S603 - argv is the module's own
            argv,
            cwd=kwargs.get("cwd"),
            env=kwargs.get("env"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            cmdline = Path(f"/proc/{proc.pid}/cmdline").read_bytes()
        finally:
            proc.kill()
            proc.communicate()
        seen.append(([str(a) for a in argv], cmdline, dict(kwargs.get("env") or {})))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(clone_mod.subprocess, "run", spy)
    try:
        with clone_mod.clone_for_recon("owner/repo", "main"):
            pass
    finally:
        listener.close()

    assert seen, "no child process was spawned"

    # Not vacuous: the token WAS in play on this call, just not via argv.
    assert any(env.get("GIT_PASS") == _SECRET for _, _, env in seen), (
        "the token never reached GIT_PASS -- this test would pass vacuously"
    )
    assert any(env.get("GIT_ASKPASS") for _, _, env in seen)
    # And the kernel really did publish a command line for us to inspect: an
    # empty /proc read looks exactly like a clean one.
    assert any(b"clone" in cmdline for _, cmdline, _ in seen), [c for _, c, _ in seen]

    argv_leaks = [argv for argv, _, _ in seen if any(_SECRET in a for a in argv)]
    assert argv_leaks == [], f"token present in the git argv: {argv_leaks}"

    proc_leaks = [
        cmdline.decode("utf-8", "replace") for _, cmdline, _ in seen if _SECRET.encode() in cmdline
    ]
    assert proc_leaks == [], f"token present in /proc/<pid>/cmdline: {proc_leaks}"


def test_a_caller_supplied_clone_url_gets_no_token_in_the_env(monkeypatch):
    """The env is not a free pass to hand the token to an arbitrary host.

    ``_git_url`` passes a full clone URL through untouched and never injected a
    token into it. Moving the credential into the environment could have made
    it unconditional, which would offer the token to whatever host that URL
    names -- so the askpass vars are set only for a URL this module built.
    """
    monkeypatch.setenv("PFACTORY_RECON_TOKEN", _SECRET)
    envs: list[dict[str, str]] = []

    def spy(argv, **kwargs):
        envs.append(dict(kwargs.get("env") or {}))
        raise subprocess.CalledProcessError(128, argv)

    monkeypatch.setattr(clone_mod.subprocess, "run", spy)
    with clone_mod.clone_for_recon("https://evil.example/owner/repo.git") as result:
        assert result.ok is False

    assert envs
    assert not any("GIT_PASS" in env or "GIT_ASKPASS" in env for env in envs), envs


def test_the_askpass_helper_answers_from_the_environment_only(tmp_path):
    """The helper is what actually feeds git the password. Run it."""
    env = clone_mod._hardened_env(home=str(tmp_path), token=_SECRET)
    for prompt, expected in (
        ("Username for 'https://github.com': ", "x-access-token"),
        ("Password for 'https://x-access-token@github.com': ", _SECRET),
    ):
        out = subprocess.run(  # noqa: S603 - the helper this module just wrote
            [env["GIT_ASKPASS"], prompt], env=env, capture_output=True, text=True, check=True
        )
        assert out.stdout == expected

    # Owner-only: the token is readable through this script, so nobody else on
    # the host may execute it.
    assert Path(env["GIT_ASKPASS"]).stat().st_mode & 0o077 == 0
