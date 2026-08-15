"""PFactory#576: a failed credentialed git operation must not disclose the
credential to a caller.

`clone_or_update` embeds a PAT into the fetch URL for network operations
(`_inject_credential`) and hands it to `_run_git` as an argv element. Before
this fix, `GitOperationError`'s message interpolated the full argv AND git's
own stderr (which independently echoes the remote URL on some auth
failures) -- and `routes/projects.py` puts that message straight into an
HTTPException response. A wrong or revoked token is the most likely
trigger, since ANY non-zero exit reached the disclosure, not just ones where
git's stderr happens to name the URL.

The test that matters asserts the SECRET IS ABSENT from the raised
exception's message, not that a friendly message is present -- a friendly
message can be present with the token appended after it.
"""

from __future__ import annotations

import pytest

from server.services.project_workspace_service import GitOperationError, _run_git

# A connection to a closed local port fails immediately (no network, no DNS),
# so this is fast and deterministic without reaching out to a real host.
_UNREACHABLE = "https://oauth2:{token}@127.0.0.1:1/owner/repo.git"


@pytest.mark.asyncio
async def test_failed_clone_does_not_leak_the_embedded_token(tmp_path):
    secret = "ghp_SUPERSECRETTOKENDONOTLEAK1234567890"  # noqa: S105 (test fixture, not real)
    fetch_url = _UNREACHABLE.format(token=secret)
    dest = tmp_path / "dest"

    with pytest.raises(GitOperationError) as excinfo:
        await _run_git(["clone", fetch_url, str(dest)], cwd=tmp_path, timeout=10)

    message = str(excinfo.value)
    assert secret not in message
    assert fetch_url not in message
    # The failure is still identifiable -- subcommand + exit code, not opaque.
    assert "clone" in message
    assert "failed" in message


@pytest.mark.asyncio
async def test_timeout_does_not_leak_the_embedded_token(tmp_path):
    """Same property on the timeout path, which builds its message separately."""
    secret = "ghp_ANOTHERSECRETTOKEN9876543210"  # noqa: S105 (test fixture, not real)
    fetch_url = _UNREACHABLE.format(token=secret)
    dest = tmp_path / "dest"

    with pytest.raises(GitOperationError) as excinfo:
        # A near-zero timeout forces the TimeoutError branch rather than the
        # (also-covered) non-zero-exit branch.
        await _run_git(["clone", fetch_url, str(dest)], cwd=tmp_path, timeout=0.001)

    message = str(excinfo.value)
    assert secret not in message
    assert fetch_url not in message
