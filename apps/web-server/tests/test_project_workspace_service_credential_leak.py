"""PFactory#576: a failed credentialed git operation must not disclose the
credential -- to a caller, OR to the application log.

`clone_or_update` embeds a PAT into the fetch URL for network operations
(`_inject_credential`) and hands it to `_run_git` as an argv element. Before
this fix, `GitOperationError`'s message interpolated the full argv verbatim
(`' '.join(args)`) -- and `routes/projects.py` puts that message straight
into an HTTPException response. A wrong or revoked token is the most likely
trigger, since it always exits non-zero.

stderr used to be withheld on credentialed calls too, as defence in depth
against git echoing the token-bearing URL back. PFactory#602 removed the
cause instead: `_inject_credential` now puts only the USERNAME in the URL and
the token is fed to git via `GIT_ASKPASS`, so it is not in argv, not in the
URL, and not in anything git can echo. stderr is therefore logged in full on
every failure again, credentialed or not -- withholding it cost operators the
real git error and now buys nothing.

Moving the argv/stderr into a log line was tried first and was not itself a
fix: this fleet's application logs are forwarded off-host (a scheduled
audit-siem-forward job), and `sanitize_log` only escapes control characters
(CWE-117) -- it does not redact secrets. A follow-up ran the logged text
through a regex scrubber instead of omitting it; CodeQL correctly flagged
that too (clear-text-logging-sensitive-data), because a hand-written regex
pattern can miss a credential shape it wasn't written for and CodeQL does
not treat `re.sub` as a recognized sanitizer. The fix that stuck: a
credentialed `_run_git` call never logs the argv or stderr at all, only the
safe subcommand/exit-code shape -- so the log lines below are asserted on
the RENDERED record TEXT (`record.getMessage()`, which applies the
`%`-formatting), not on the raw `%s` arguments passed to the logger call.

The test that matters throughout asserts the SECRET IS ABSENT, not that a
friendly message is present -- a friendly message can be present with the
token appended after it.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from server.services.project_workspace_service import GitOperationError, _run_git, clone_or_update

# A connection to a closed local port fails immediately (no network, no DNS),
# so this is fast and deterministic without reaching out to a real host.
_UNREACHABLE = "https://oauth2:{token}@127.0.0.1:1/owner/repo.git"


@pytest.mark.asyncio
async def test_failed_credentialed_clone_does_not_leak_the_embedded_token(tmp_path, caplog):
    secret = "ghp_SUPERSECRETTOKENDONOTLEAK1234567890"  # noqa: S105 (test fixture, not real)
    fetch_url = _UNREACHABLE.format(token=secret)
    dest = tmp_path / "dest"

    with (
        caplog.at_level(logging.DEBUG, logger="server.services.project_workspace_service"),
        pytest.raises(GitOperationError) as excinfo,
    ):
        await _run_git(["clone", fetch_url, str(dest)], cwd=tmp_path, timeout=10)

    message = str(excinfo.value)
    assert secret not in message
    assert fetch_url not in message
    # The failure is still identifiable -- subcommand + exit code, not opaque.
    assert "clone" in message
    assert "failed" in message

    # The credential must not have moved from the response into the log
    # either -- this fleet forwards application logs off-host. Asserted on
    # the RENDERED record text (getMessage() applies the %-formatting), not
    # on the raw arguments passed to the logger call.
    rendered = [r.getMessage() for r in caplog.records]
    assert not any(secret in line for line in rendered)
    assert not any(fetch_url in line for line in rendered)
    # Proves the failure was actually logged rather than nothing being
    # emitted at all -- "the secret is absent" is vacuous otherwise. Since
    # PFactory#602 the stderr is logged in FULL on this path too, because
    # there is no credential in the argv for git to echo back.
    assert any("git clone failed" in line for line in rendered)


@pytest.mark.asyncio
async def test_timeout_on_credentialed_clone_does_not_leak_the_embedded_token(tmp_path, caplog):
    """Same property on the timeout path, which builds its message separately."""
    secret = "ghp_ANOTHERSECRETTOKEN9876543210"  # noqa: S105 (test fixture, not real)
    fetch_url = _UNREACHABLE.format(token=secret)
    dest = tmp_path / "dest"

    with (
        caplog.at_level(logging.DEBUG, logger="server.services.project_workspace_service"),
        pytest.raises(GitOperationError) as excinfo,
    ):
        # A near-zero timeout forces the TimeoutError branch rather than the
        # (also-covered) non-zero-exit branch.
        await _run_git(["clone", fetch_url, str(dest)], cwd=tmp_path, timeout=0.001)

    message = str(excinfo.value)
    assert secret not in message
    assert fetch_url not in message

    rendered = [r.getMessage() for r in caplog.records]
    assert not any(secret in line for line in rendered)
    assert not any(fetch_url in line for line in rendered)


@pytest.mark.asyncio
async def test_non_credentialed_failure_still_logs_full_detail(tmp_path, caplog):
    """The withholding is specific to credentialed calls -- a plain public-URL
    failure should still log its real stderr for operators, unchanged."""
    dest = tmp_path / "dest"

    with (
        caplog.at_level(logging.DEBUG, logger="server.services.project_workspace_service"),
        pytest.raises(GitOperationError),
    ):
        await _run_git(
            ["clone", "https://127.0.0.1:1/owner/repo.git", str(dest)],
            cwd=tmp_path,
            timeout=10,
        )

    rendered = [r.getMessage() for r in caplog.records]
    assert any("git clone failed" in line for line in rendered), rendered
    assert not any("detail withheld" in line for line in rendered)


@pytest.mark.asyncio
async def test_credentialed_pull_path_keeps_the_token_out_of_every_argv(tmp_path, caplog):
    """The exact gap review found: `remote set-url` points origin at the
    credentialed URL, then `fetch` (clean argv) runs against THAT origin.

    Before PFactory#602 the `set-url` argv carried `https://oauth2:TOKEN@...`
    and the fetch's stderr was withheld to compensate. Now the URL carries the
    username only and the token rides in `GIT_PASS`, so this asserts the
    property directly: NO argv on the whole pull path contains the token, and
    the fetch failure logs its real stderr.
    """
    secret = "ghp_PULLPATHSECRETTOKEN0123456789"  # noqa: S105 (test fixture, not real)
    workspace = tmp_path / "existing-repo"
    (workspace / ".git").mkdir(parents=True)

    calls: list[list[str]] = []
    envs: list[dict[str, str]] = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        proc = MagicMock()
        git_args = list(args[1:])  # drop the "git" binary itself
        calls.append(git_args)
        envs.append(dict(kwargs.get("env") or {}))
        if git_args[:2] == ["fetch", "--prune"]:
            proc.returncode = 128
            stderr = b"fatal: Authentication failed for 'https://127.0.0.1:1/owner/repo.git'"

            async def _communicate():
                return (b"", stderr)
        else:
            proc.returncode = 0

            async def _communicate():
                return (b"", b"")

        proc.communicate = _communicate
        proc.kill = MagicMock()
        return proc

    with (
        caplog.at_level(logging.DEBUG, logger="server.services.project_workspace_service"),
        patch("asyncio.create_subprocess_exec", new=fake_create_subprocess_exec),
        pytest.raises(GitOperationError) as excinfo,
    ):
        await clone_or_update(
            git_url="https://127.0.0.1:1/owner/repo.git",
            root=tmp_path,
            slug="existing-repo",
            credential=("oauth2", secret),
        )

    # Confirms the test actually exercised the fetch-failure path, not some
    # other branch, and that the origin really was pointed at a credentialed
    # URL first -- the site the flag used to guard.
    assert any(c[:2] == ["fetch", "--prune"] for c in calls)
    set_url = [c for c in calls if c[:3] == ["remote", "set-url", "origin"]]
    assert set_url, calls

    # The username IS in the URL (that is what makes git ask askpass for a
    # password); the token is not, anywhere in any argv.
    assert any("oauth2@127.0.0.1" in c[3] for c in set_url), set_url
    leaks = [c for c in calls if any(secret in a for a in c)]
    assert leaks == [], f"token present in a git argv: {leaks}"

    # Not vacuous: the token was genuinely in play, via the environment.
    assert any(env.get("GIT_PASS") == secret for env in envs)

    message = str(excinfo.value)
    assert secret not in message

    rendered = [r.getMessage() for r in caplog.records]
    assert not any(secret in line for line in rendered)
    # Full operator detail on the credentialed path too, no longer withheld.
    assert any("Authentication failed" in line for line in rendered), rendered
    assert not any("detail withheld" in line for line in rendered)
