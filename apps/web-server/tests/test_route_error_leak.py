"""CWE-209 on the broad-catch handlers (#557, batch 2).

28 handlers across 10 modules ended a broad ``except`` -- ``Exception``,
``OSError``, ``CalledProcessError`` -- by putting the caught exception straight
into a response field. Those are the ones that render third-party and stdlib
text: an ``OSError`` names the absolute path it failed on, and
``CalledProcessError.stderr`` is git's own output about our worktree.

The other 36 ``str(exc)`` sites in this repo are NOT converted and are not
leaks: they catch repo-owned typed exceptions (``PlanServiceError``,
``WaiverError``, ``SpecSourceError``) whose messages are developer-written
about the caller's own input, and whose raise sites were checked for the
laundering shape -- ``PlanServiceError(f"...: {inner}")`` -- and do not have it.
Converting those would hide a fixable 400 behind a correlation id.

Coverage stated plainly: these drive the ``logs`` routes through a real
TestClient, which is 2 of the 28. The rest are the same single-expression
conversion onto ``error_message``, whose behaviour is exercised here and whose
correlation id is asserted. This file should grow if more of these routes get
harnesses.

Every case pins the status code, so a request rejected before the handler runs
cannot make the leak assertion pass while testing nothing.

Mutation check: make ``server.error_ref.error_reference`` return ``str(exc)``
and both leak cases go red naming the fragment that escaped.
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.routes import logs as logs_routes

REF = re.compile(r"\b[0-9a-f]{12}\b")

CREDENTIALS_FILE = "/etc/pfactory/credentials.yaml"
INTERNAL_HOST = "pfactory-db.internal"
BOOM = OSError(f"[Errno 13] Permission denied: {CREDENTIALS_FILE} (host {INTERNAL_HOST})")
LEAKS = (CREDENTIALS_FILE, INTERNAL_HOST, "Errno 13", "Permission denied")


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(logs_routes.router, prefix="/api/logs")
    return TestClient(app, raise_server_exceptions=False)


def _assert_no_leak(text: str) -> None:
    for fragment in LEAKS:
        assert fragment not in text, f"leaked {fragment!r} in response body: {text!r}"


def test_a_frontend_log_write_failure_does_not_leak_the_path(
    client: TestClient,
) -> None:
    """OSError renders the absolute path it failed on.

    The body field is ``entries``, not ``logs``: the first draft of this test
    sent the wrong shape, got a 422 before the handler ran, and the status-code
    pin below is what caught it.
    """
    payload: dict[str, Any] = {
        "entries": [
            {
                "level": "info",
                "message": "hi",
                "timestamp": "2026-01-01T00:00:00Z",
                "category": "ui",
            }
        ]
    }
    with patch("builtins.open", side_effect=BOOM):
        response = client.post("/api/logs/frontend", json=payload)

    assert response.status_code == 500, (
        f"handler not reached, so this proves nothing: {response.text!r}"
    )
    _assert_no_leak(response.text)
    assert REF.search(response.text), "no correlation id for the caller to quote"


def test_a_log_read_failure_does_not_leak_the_path(client: TestClient, tmp_path: Any) -> None:
    """The file must EXIST and ``open`` must fail.

    Patching ``get_log_files`` to raise makes it blow up before the ``try``,
    so the handler is bypassed and the server returns a bare 500 with no
    correlation id -- which is how the first draft of this test failed.
    """
    real = tmp_path / "server.log"
    real.write_text("hello\n")
    with (
        patch.object(logs_routes, "get_log_files", return_value={"server": real}),
        patch("builtins.open", side_effect=BOOM),
    ):
        response = client.get("/api/logs/server/raw")

    assert response.status_code == 500, (
        f"handler not reached, so this proves nothing: {response.text!r}"
    )
    _assert_no_leak(response.text)
    assert REF.search(response.text), "no correlation id for the caller to quote"
