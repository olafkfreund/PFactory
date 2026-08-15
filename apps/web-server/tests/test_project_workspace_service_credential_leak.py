"""PFactory#576: a failed credentialed git operation must not disclose the
credential -- to a caller, OR to the application log.

`clone_or_update` embeds a PAT into the fetch URL for network operations
(`_inject_credential`) and hands it to `_run_git` as an argv element. Before
this fix, `GitOperationError`'s message interpolated the full argv AND git's
own stderr (which independently echoes the remote URL on some auth
failures) -- and `routes/projects.py` puts that message straight into an
HTTPException response. A wrong or revoked token is the most likely
trigger, since ANY non-zero exit reached the disclosure, not just ones where
git's stderr happens to name the URL.

Moving the argv/stderr into a log line is not itself a fix here: this
fleet's application logs are forwarded off-host (a scheduled
audit-siem-forward job), and `sanitize_log` only escapes control characters
(CWE-117) -- it does not redact secrets. So the log lines below are
asserted too, on the rendered record TEXT (`record.getMessage()`, which
applies the `%`-formatting), not on the raw `%s` arguments passed to the
logger call -- an argument-level assertion would miss a formatting layer
concatenating a scrubbed piece next to an unscrubbed one.

The test that matters throughout asserts the SECRET IS ABSENT, not that a
friendly message is present -- a friendly message can be present with the
token appended after it.
"""

from __future__ import annotations

import logging

import pytest

from server.services.project_workspace_service import GitOperationError, _run_git

# A connection to a closed local port fails immediately (no network, no DNS),
# so this is fast and deterministic without reaching out to a real host.
_UNREACHABLE = "https://oauth2:{token}@127.0.0.1:1/owner/repo.git"


@pytest.mark.asyncio
async def test_failed_clone_does_not_leak_the_embedded_token(tmp_path, caplog):
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
    # The scrub marker proves the DEBUG line ran the credentialed argv
    # through the scrubber rather than happening not to log it at all.
    assert any("***@" in line for line in rendered)


@pytest.mark.asyncio
async def test_timeout_does_not_leak_the_embedded_token(tmp_path, caplog):
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
    assert any("***@" in line for line in rendered)
