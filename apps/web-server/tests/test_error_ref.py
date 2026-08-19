"""Failure detail must reach the server log, never the client (CWE-209).

CodeQL reported 31 ``py/stack-trace-exposure`` sinks, and 28 of them are one
bug seen 28 times: ``git_utils.run_gh_command`` ends with
``except Exception as e: return {"success": False, "error": str(e)}`` and every
route in ``routes/github.py`` returns that dict's ``error`` field to the browser
verbatim. So this test drives the SHARED WRAPPERS, not the routes, and asserts
on the field that becomes the response body.

What it asserts is deliberately not "the string is shorter". Truncating does not
fix this - the leak is at the FRONT of an OSError. It asserts the body carries
none of: the exception's message, its class name, the internal hostname, the
port, the on-disk path, or the word Traceback. And it asserts the detail did
reach the logger, because a fix that merely deletes the information makes the
failure undebuggable and would be quietly reverted the first time someone is
paged.

Mutation check: make ``error_reference`` return ``str(exc)`` instead of the id,
or put ``{exc}`` back in any caller's sentence, and every
``test_*_body_leaks_nothing`` here goes red.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import sys
from pathlib import Path

import pytest

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

# apps/backend must be importable before `server.routes.github` pulls in `plan`;
# the route does this insert itself at call time, so mirror it here.
_BACKEND = _WEB_SERVER.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from plan.service import SERVICE  # noqa: E402
from server.error_ref import error_message, error_reference  # noqa: E402
from server.routes import github, terminal  # noqa: E402
from server.services import git_utils, pr_data_service  # noqa: E402

# An exception shaped like the ones these wrappers actually catch: it names an
# internal host, a port, and an absolute server path.
LEAKY_HOST = "pfactory-db.internal.svc.cluster.local"
LEAKY_PORT = "5432"
LEAKY_PATH = "/home/projects/MagesticAI/workspaces/acme/.git/config"
BOOM_MSG = f"[Errno 2] connect to {LEAKY_HOST}:{LEAKY_PORT} failed reading {LEAKY_PATH}"


def raised() -> OSError:
    """An OSError with a REAL traceback attached.

    A bare ``OSError(...)`` built at module level has ``__traceback__ is None``,
    and ``logger.warning(..., exc_info=True)`` then logs "NoneType: None" instead
    of a stack. That would make this test assert against a weaker artifact than
    production produces, so the exception is raised and caught for real.
    """
    try:
        raise OSError(BOOM_MSG)
    except OSError as exc:
        return exc


REF_RE = re.compile(r"reference ([0-9a-f]{12})")


def assert_no_leak(body: str) -> str:
    """Assert a client-facing string carries no failure detail; return its ref."""
    for forbidden in (LEAKY_HOST, LEAKY_PORT, LEAKY_PATH, "Errno", "OSError", "Traceback"):
        assert forbidden not in body, f"response body leaked {forbidden!r}: {body!r}"
    match = REF_RE.search(body)
    assert match, f"no correlation id for the operator to grep: {body!r}"
    return match.group(1)


@pytest.fixture
def captured(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.WARNING)
    return caplog


def test_error_message_body_leaks_nothing(captured: pytest.LogCaptureFixture) -> None:
    body = error_message(logging.getLogger("t"), "some context", raised(), "the call failed")
    ref = assert_no_leak(body)
    # ...and the operator can still find the whole failure under that id.
    logged = captured.text
    assert ref in logged
    assert LEAKY_HOST in logged
    assert LEAKY_PATH in logged
    assert "Traceback" in logged


def test_error_reference_ids_are_unique() -> None:
    log = logging.getLogger("t")
    refs = {error_reference(log, "c", raised()) for _ in range(200)}
    assert len(refs) == 200


def test_context_is_sanitized(captured: pytest.LogCaptureFixture) -> None:
    """The context carries request-supplied values, so it can forge a record."""
    error_reference(logging.getLogger("t"), "spec\nWARNING:server.audit:forged", raised())
    assert "\\n" in captured.text
    assert not any(r.getMessage().startswith("WARNING:server.audit") for r in captured.records)


def test_run_gh_command_body_leaks_nothing(
    monkeypatch: pytest.MonkeyPatch, captured: pytest.LogCaptureFixture
) -> None:
    """The wrapper behind ~28 routes/github.py response bodies."""

    def boom(*_a: object, **_k: object) -> None:
        raise raised()

    monkeypatch.setattr(subprocess, "run", boom)
    result = git_utils.run_gh_command(["pr", "merge", "7"], cwd="/srv/ws/acme")
    assert result["success"] is False
    ref = assert_no_leak(result["error"])
    assert ref in captured.text
    assert LEAKY_PATH in captured.text


def test_run_git_command_body_leaks_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise raised()

    monkeypatch.setattr(subprocess, "run", boom)
    result = git_utils.run_git_command(["log", "-1"], cwd="/srv/ws/acme")
    assert result["success"] is False
    assert_no_leak(result["error"])


def test_pr_data_run_gh_body_leaks_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise raised()

    monkeypatch.setattr(subprocess, "run", boom)
    result = pr_data_service._run_gh(["pr", "list"], cwd="/srv/ws/acme")
    assert result["success"] is False
    assert_no_leak(result["error"])


def test_known_failures_keep_their_plain_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only UNKNOWN failures get a reference; the diagnosed ones stay readable.

    A correlation id on "GitHub CLI (gh) not installed" would be a worse user
    experience for no security gain - that string names nothing internal.
    """

    def missing(*_a: object, **_k: object) -> None:
        raise FileNotFoundError("gh")

    monkeypatch.setattr(subprocess, "run", missing)
    assert git_utils.run_gh_command(["pr", "list"])["error"] == "GitHub CLI (gh) not installed"


# ---------------------------------------------------------------------------
# The two remaining py/stack-trace-exposure sinks (#547 follow-up).
#
# Both are the SAME shape as the wrappers above, but they live in the ROUTE, not
# in a shared helper, so driving the helper would never have caught them. Each
# was an `except ValueError` arm whose comment asserted the message was a
# curated, hand-authored string. That is true of SOME of the ValueErrors those
# blocks catch and false of the rest, and the handler cannot tell at the point
# it has to produce a body which one it is holding.
# ---------------------------------------------------------------------------


def _leaky_value_error() -> ValueError:
    """The shape `confine_to_workspace` and the plan pipeline actually raise."""
    try:
        raise ValueError(BOOM_MSG)
    except ValueError as exc:
        return exc


def test_remove_terminal_worktree_body_leaks_nothing(
    monkeypatch: pytest.MonkeyPatch, captured: pytest.LogCaptureFixture
) -> None:
    """routes/terminal.py: `TerminalWorktreeService(project)` puts the path
    through `confine_to_workspace`, whose ValueError quotes the rejected path
    and names the allowed roots."""

    def boom(*_a: object, **_k: object) -> None:
        raise _leaky_value_error()

    monkeypatch.setattr(terminal, "TerminalWorktreeService", boom)
    result = asyncio.run(
        terminal.remove_terminal_worktree("wt", project="/srv/ws/acme", deleteBranch=False)
    )
    assert result["success"] is False
    ref = assert_no_leak(result["error"])
    # ...and the operator still gets the whole failure, under that same id.
    assert ref in captured.text
    assert LEAKY_PATH in captured.text


def test_plan_review_pr_body_leaks_nothing(
    monkeypatch: pytest.MonkeyPatch, captured: pytest.LogCaptureFixture
) -> None:
    """routes/github.py: the bare `ValueError` arm around the plan pipeline."""
    monkeypatch.setattr(
        github,
        "run_gh_command",
        lambda *_a, **_k: {
            "success": True,
            "output": json.dumps({"title": "t", "body": "b"}),
        },
    )

    def boom(*_a: object, **_k: object) -> None:
        raise _leaky_value_error()

    monkeypatch.setattr(SERVICE, "ingest_text", boom)

    response = asyncio.run(
        github.plan_review_pr(7, github.PlanReviewPRRequest(repo="o/n", pr_number=7))
    )
    assert response.status_code == 400
    body = json.loads(response.body)
    assert body["success"] is False
    ref = assert_no_leak(body["error"])
    assert ref in captured.text
    assert LEAKY_PATH in captured.text
