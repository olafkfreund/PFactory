"""CLI-account login: the auth event must arrive, and the token file must
never exist at readable permissions.

Two defects in ``server/routes/cli_accounts.py``:

* ``_broadcast_cli_auth_event`` ran the WebSocket broadcast on an event loop
  it created inside the polling worker thread, so the send touched
  connections owned by the ASGI server's loop. ``broadcast_event`` swallows
  send errors and unregisters the client -- a silent drop.
* ``_save_credentials`` wrote OAuth tokens with ``write_text`` and chmod'd to
  0600 afterwards, leaving the secret on disk at umask permissions in
  between (and truncating in place, so a concurrent reader sees a torn file).
  ``server/paths.py`` already had ``atomic_write_secret_json`` (#298); this
  call site was not using it.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from server.routes import cli_accounts as ca
from server.websockets import events as ws_events

# Stands in for the OAuth token the real payload carries; a neutral name keeps
# the credential linters focused on production code.
MARKER = "written-by-the-test"


# ---------------------------------------------------------------------------
# Finding A: the broadcast must reach a websocket owned by the server's loop
# ---------------------------------------------------------------------------


class LoopBoundWebSocket:
    """A stand-in for a Starlette WebSocket, which is bound to one event loop.

    A real WebSocket's transport belongs to the loop the ASGI server runs on.
    Touching it from a coroutine running on a *different* loop either raises
    ("... is attached to a different loop") or corrupts the connection. This
    fake reproduces the first, deterministic, half of that: it accepts a send
    only on the loop it was created on.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self.received: list[str] = []

    async def send_text(self, message: str) -> None:
        running = asyncio.get_running_loop()
        if running is not self._loop:
            raise RuntimeError("WebSocket transport is attached to a different loop")
        self.received.append(message)


@pytest.mark.asyncio
async def test_worker_thread_broadcast_reaches_the_connected_websocket() -> None:
    """The polling threads' cli-account-auth event must actually arrive.

    Regression test for the silent-drop bug: ``_broadcast_cli_auth_event``
    used to run ``broadcast_event`` on a brand new event loop created inside
    the worker thread. ``broadcast_event`` swallows send errors and
    unregisters the client, so the browser never heard that the CLI login
    completed and the portal showed it as still pending -- with nothing in
    the response to say so.

    Asserting "no exception escaped" would pass on the broken code: the
    exception never escaped in the first place. This asserts DELIVERY.
    """
    server_loop = asyncio.get_running_loop()
    ws = LoopBoundWebSocket(server_loop)
    # A duck-typed stand-in, not a real Starlette WebSocket: the registry
    # only stores it and calls send_text.
    ws_events._register_client(ws, None)  # type: ignore[arg-type]
    try:
        # to_thread keeps the server loop free to run the scheduled coroutine,
        # which is exactly the situation in the running app.
        await asyncio.wait_for(
            asyncio.to_thread(ca._broadcast_cli_auth_event, "codex", True, server_loop),
            timeout=10,
        )

        assert ws.received, (
            "cli-account-auth never reached the connected websocket -- the "
            "broadcast ran on a loop that does not own this connection"
        )
        message = json.loads(ws.received[-1])
        assert message["type"] == "cli-account-auth"
        assert message["payload"] == {"cli": "codex", "success": True}
        assert ws in ws_events.active_connections, "the client was unregistered by a failed send"
    finally:
        ws_events._unregister_client(ws)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_broadcast_does_not_leave_the_server_loop_behind() -> None:
    """The fix must not fall back to a private loop when the server loop is given."""
    server_loop = asyncio.get_running_loop()
    seen: list[tuple[asyncio.AbstractEventLoop, str, dict[str, object]]] = []

    async def _capture(event_type: str, payload: dict[str, object]) -> None:
        seen.append((asyncio.get_running_loop(), event_type, payload))

    with patch("server.websockets.events.broadcast_event", _capture):
        await asyncio.wait_for(
            asyncio.to_thread(ca._broadcast_cli_auth_event, "codex", False, server_loop),
            timeout=10,
        )

    assert seen == [(server_loop, "cli-account-auth", {"cli": "codex", "success": False})]


def test_pollers_take_the_server_loop() -> None:
    """The worker threads cannot find the server loop themselves.

    ``asyncio.get_running_loop()`` inside a plain thread raises, so the loop
    has to be handed in by the async endpoint that starts the thread. Guard
    the signature so a future refactor cannot quietly drop it.
    """
    for poller in (ca._poll_codex_token, ca._poll_gemini_token):
        params = list(inspect.signature(poller).parameters)
        assert params == ["mtime_before", "loop"], (
            f"{poller.__name__} must receive the server's event loop"
        )


# ---------------------------------------------------------------------------
# Finding B: the token file must be 0600 from creation, not 0600 eventually
# ---------------------------------------------------------------------------


def test_credentials_file_is_never_readable_even_for_an_instant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Assert on the mode the file is CREATED with, not the mode it ends at.

    ``write_text`` + ``chmod(0o600)`` ends at 0600 too, so an end-state
    assertion passes on the broken code and proves nothing. What separates
    the two is *how* 0600 is reached:

    * broken: create at the umask default (here 0666, umask forced to 0 to
      make the window unmistakable), write the OAuth tokens, then narrow.
      Any local process can read the secret in between.
    * fixed: ``atomic_write_secret_json`` uses ``mkstemp``, which creates at
      0600 regardless of umask, and publishes with ``os.replace`` -- so there
      is no ``chmod`` at all.

    So: force umask 0, record every ``os.chmod`` (``Path.chmod`` routes
    through it), and require the final file to be 0600 with no chmod having
    been needed to get there.
    """
    dest = tmp_path / "codex-credentials.json"
    monkeypatch.setitem(ca.CLI_CONFIG["codex"], "stored_credentials", dest)
    monkeypatch.setattr(ca, "CREDENTIALS_DIR", tmp_path)

    chmods: list[tuple[str, int]] = []
    real_chmod = os.chmod

    def recording_chmod(path: object, mode: int, *args: object, **kwargs: object) -> None:
        chmods.append((str(path), mode))
        real_chmod(path, mode, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "chmod", recording_chmod)

    old_umask = os.umask(0)
    try:
        ca._save_credentials("codex", {"source": "cli_login", "marker": MARKER})
    finally:
        os.umask(old_umask)

    assert dest.exists()
    assert json.loads(dest.read_text())["marker"] == MARKER

    mode = stat.S_IMODE(dest.stat().st_mode)
    assert mode == 0o600, f"stored credentials ended at {mode:#o}, want 0o600"
    assert not chmods, (
        "the credentials file was narrowed to 0600 by a chmod, which means it "
        "was created at the umask default (0666 here) with the token already "
        f"in it -- a readable window. chmod calls: {chmods}"
    )


def test_credentials_write_replaces_rather_than_truncates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent reader must never see a half-written credentials file.

    ``write_text`` truncates the destination in place. The atomic write goes
    to a temp file and is published with ``os.replace``, so the destination
    inode is swapped, never emptied: an open handle on the old file keeps
    reading the old, complete content.
    """
    dest = tmp_path / "codex-credentials.json"
    monkeypatch.setitem(ca.CLI_CONFIG["codex"], "stored_credentials", dest)
    monkeypatch.setattr(ca, "CREDENTIALS_DIR", tmp_path)

    ca._save_credentials("codex", {"marker": "first"})
    first_inode = dest.stat().st_ino
    with dest.open() as reader_handle:
        ca._save_credentials("codex", {"marker": "second"})
        # The reader opened before the second write still sees whole, valid
        # JSON -- the old content, not an empty or torn file.
        assert json.loads(reader_handle.read())["marker"] == "first"

    assert json.loads(dest.read_text())["marker"] == "second"
    assert dest.stat().st_ino != first_inode, (
        "the destination was written in place; a concurrent reader can see a "
        "truncated file and conclude there are no credentials"
    )
