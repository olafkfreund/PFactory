"""Tests for ``apps/web-server/server/rmux/wrapper.py``.

Two layers:

1. **Unit tests** (no rmux required) — error-class hierarchy, default
   socket-dir resolution, stderr classification logic.  Run on every
   developer machine, no external dependency.

2. **Integration round-trip** (``@pytest.mark.rmux``) — drives a real
   ``rmux 0.3.0`` daemon: new-session → send-text → capture-pane →
   kill-session.  Auto-skipped when the binary isn't on PATH (see
   ``conftest.py``).  This is the canonical acceptance test for issue
   #45's R0b deliverable.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Web-server import path is set up by ``tests/rmux/conftest.py``.
from server.rmux import (
    RmuxDaemonError,
    RmuxError,
    RmuxNotInstalledError,
    RmuxSessionError,
    RmuxWrapper,
)
from server.rmux.wrapper import _default_socket_dir

# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class TestErrorHierarchy:
    """All four typed errors inherit from ``RmuxError`` so callers can
    catch broadly when they don't need to branch on the cause."""

    def test_not_installed_is_rmux_error(self) -> None:
        assert issubclass(RmuxNotInstalledError, RmuxError)

    def test_daemon_error_is_rmux_error(self) -> None:
        assert issubclass(RmuxDaemonError, RmuxError)

    def test_session_error_is_rmux_error(self) -> None:
        assert issubclass(RmuxSessionError, RmuxError)

    def test_distinct_classes(self) -> None:
        """Each error class is a distinct branch, not aliases."""
        assert RmuxNotInstalledError is not RmuxDaemonError
        assert RmuxDaemonError is not RmuxSessionError


# ---------------------------------------------------------------------------
# Socket-dir defaults
# ---------------------------------------------------------------------------


class TestDefaultSocketDir:
    """The socket dir must be user-owned to satisfy rmux v0.3.0's startup
    check.  See R0a verdict on issue #45 — gotcha #1."""

    def test_uses_xdg_runtime_dir_when_set(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        sd = _default_socket_dir()
        assert sd == tmp_path / "pfactory-rmux"

    def test_falls_back_to_cache_when_xdg_unset(self, monkeypatch) -> None:
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        sd = _default_socket_dir()
        assert sd == Path.home() / ".cache" / "pfactory" / "rmux"

    def test_never_returns_slash_tmp(self, monkeypatch) -> None:
        """Regression guard for R0a gotcha: rmux refuses /tmp."""
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        sd = _default_socket_dir()
        assert not str(sd).startswith("/tmp")


# ---------------------------------------------------------------------------
# ensure_daemon — unit-level
# ---------------------------------------------------------------------------


class TestEnsureDaemon:
    """``ensure_daemon`` verifies the binary is on PATH and the socket
    dir exists.  Doesn't actually fork a server — rmux auto-starts on
    first command."""

    @pytest.mark.asyncio
    async def test_raises_when_binary_missing(self, tmp_path) -> None:
        wrapper = RmuxWrapper(
            rmux_bin="rmux-nonexistent-binary-12345",
            socket_dir=tmp_path / "sd",
        )
        with pytest.raises(RmuxNotInstalledError):
            await wrapper.ensure_daemon()

    @pytest.mark.asyncio
    async def test_creates_socket_dir(self, tmp_path) -> None:
        """First call should mkdir the socket dir with 0o700."""
        socket_dir = tmp_path / "rmux-socket"
        assert not socket_dir.exists()
        wrapper = RmuxWrapper(rmux_bin="rmux", socket_dir=socket_dir)
        # Patch the subprocess so we don't actually run rmux here.
        async def _fake_communicate():
            return (b"", b"no server running on socket")
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
            mock_proc.return_value.communicate = _fake_communicate
            mock_proc.return_value.returncode = 1  # rmux returns non-zero when no server
            try:
                await wrapper.ensure_daemon()
            except RmuxError:
                # ensure_daemon swallows "no server" — but if the mock
                # doesn't trigger our swallow path we still want to
                # verify the mkdir happened.
                pass
        assert socket_dir.exists()
        # mode check: dir was created at 0o700 (note: umask may strip
        # other bits but we should not have group/other write perms).
        mode = oct(socket_dir.stat().st_mode)[-3:]
        assert mode in {"700", "750", "755"}, f"unexpected mode {mode}"


# ---------------------------------------------------------------------------
# Stderr classification — unit-level
# ---------------------------------------------------------------------------


class TestStderrClassification:
    """``_run`` maps rmux's stderr text into the typed exception hierarchy
    so callers can branch without parsing strings themselves."""

    @pytest.mark.asyncio
    async def test_no_server_raises_daemon_error(self, tmp_path) -> None:
        wrapper = RmuxWrapper(socket_dir=tmp_path)
        async def fake_comm():
            return (b"", b"no server running on /tmp/sock")
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_proc:
            mock_proc.return_value.communicate = fake_comm
            mock_proc.return_value.returncode = 1
            with pytest.raises(RmuxDaemonError):
                await wrapper._run("list-sessions")

    @pytest.mark.asyncio
    async def test_missing_session_raises_session_error(self, tmp_path) -> None:
        wrapper = RmuxWrapper(socket_dir=tmp_path)
        async def fake_comm():
            return (b"", b"can't find session: ghost-session")
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_proc:
            mock_proc.return_value.communicate = fake_comm
            mock_proc.return_value.returncode = 1
            with pytest.raises(RmuxSessionError):
                await wrapper._run("kill-session", "-t", "ghost-session")

    @pytest.mark.asyncio
    async def test_swallow_no_server_returns_empty(self, tmp_path) -> None:
        """list_sessions wants "no server" to mean [] not raise."""
        wrapper = RmuxWrapper(socket_dir=tmp_path)
        async def fake_comm():
            return (b"", b"no server running")
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_proc:
            mock_proc.return_value.communicate = fake_comm
            mock_proc.return_value.returncode = 1
            result = await wrapper._run(
                "list-sessions", swallow_no_server=True, capture=True
            )
            assert result == ""

    @pytest.mark.asyncio
    async def test_unknown_error_raises_generic_rmuxerror(self, tmp_path) -> None:
        """Unrecognised stderr → plain RmuxError (still typed enough to
        catch broadly, but doesn't lie about being a daemon/session error)."""
        wrapper = RmuxWrapper(socket_dir=tmp_path)
        async def fake_comm():
            return (b"", b"some other weird rmux error")
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_proc:
            mock_proc.return_value.communicate = fake_comm
            mock_proc.return_value.returncode = 2
            with pytest.raises(RmuxError) as exc_info:
                await wrapper._run("new-session")
            # Must be plain RmuxError, not the more specific subclasses
            assert type(exc_info.value) is RmuxError


# ---------------------------------------------------------------------------
# Integration round-trip — requires real rmux
# ---------------------------------------------------------------------------


@pytest.fixture
def integration_wrapper(tmp_path):
    """Build an RmuxWrapper pointing at an ephemeral socket in tmp_path.

    The fixture yields the wrapper.  Teardown best-effort kills any
    sessions and the server itself so concurrent test runs don't collide
    on a stale daemon.
    """
    wrapper = RmuxWrapper(socket_dir=tmp_path)
    yield wrapper
    # Best-effort teardown — never raise from a fixture cleanup.
    import asyncio
    async def _cleanup():
        try:
            for name in await wrapper.list_sessions():
                await wrapper.kill_session(name, ignore_missing=True)
        except RmuxError:
            pass
    try:
        asyncio.run(_cleanup())
    except Exception:
        pass


@pytest.mark.rmux
@pytest.mark.asyncio
class TestIntegrationRoundtrip:
    """End-to-end test driving a real rmux daemon.

    Acceptance for issue #45: "Round-trip pytest passes against a real
    rmux daemon: new-session → send-text → capture-pane → kill-session."
    """

    async def test_ensure_daemon_succeeds_when_rmux_present(self, integration_wrapper) -> None:
        await integration_wrapper.ensure_daemon()
        assert integration_wrapper.socket_path.parent.exists()

    async def test_full_lifecycle_round_trip(
        self, integration_wrapper, tmp_path
    ) -> None:
        """The canonical R0b acceptance test (5-step round-trip)."""
        await integration_wrapper.ensure_daemon()

        # 1. new-session: start a long-lived shell so capture-pane has
        #    something to inspect.  ``sleep`` keeps it alive past
        #    send-text below.
        session = "rwrapper-roundtrip-test"
        await integration_wrapper.new_session(
            name=session,
            cwd=str(tmp_path),
            cmd="bash -c 'while true; do read line; echo got: \"$line\"; done'",
        )

        # 2. list-sessions includes our new session
        sessions = await integration_wrapper.list_sessions()
        assert session in sessions, f"new session missing from {sessions}"

        # 3. send-text — type a probe string + Enter via send_keys
        probe = "ALPHA-BETA-GAMMA"
        await integration_wrapper.send_text(session, probe)
        await integration_wrapper.send_keys(session, "Enter")

        # rmux is async — give the shell a moment to echo and the
        # pane to settle.  100 ms is plenty per R0a's ~2 ms latency.
        import asyncio
        await asyncio.sleep(0.3)

        # 4. capture-pane shows the probe AND the echo
        snapshot = await integration_wrapper.capture_pane(session)
        assert probe in snapshot, (
            f"probe {probe!r} not in capture-pane output:\n{snapshot}"
        )

        # 5. kill-session
        await integration_wrapper.kill_session(session)
        sessions_after = await integration_wrapper.list_sessions()
        assert session not in sessions_after, (
            f"session {session} survived kill: {sessions_after}"
        )

    async def test_kill_missing_session_with_ignore_flag(
        self, integration_wrapper
    ) -> None:
        """``kill_session(ignore_missing=True)`` is the task-shutdown path."""
        await integration_wrapper.ensure_daemon()
        # No exception should propagate
        await integration_wrapper.kill_session(
            "nonexistent-session-zxcvbn",
            ignore_missing=True,
        )

    async def test_kill_missing_session_without_ignore_raises(
        self, integration_wrapper, tmp_path
    ) -> None:
        """When the server IS running, killing a missing session must
        raise the precise ``RmuxSessionError`` so callers can tell
        "wrong target" apart from "daemon down".

        Bring the server up by starting a placeholder session first.
        """
        await integration_wrapper.ensure_daemon()
        # Start a placeholder session so the rmux server is actually
        # up — kill-session against a server-less socket would raise
        # RmuxDaemonError, which is a different (correct) failure mode.
        placeholder = "rwrapper-placeholder"
        await integration_wrapper.new_session(
            name=placeholder,
            cwd=str(tmp_path),
            cmd="bash -c 'sleep 30'",
        )
        try:
            with pytest.raises(RmuxSessionError):
                await integration_wrapper.kill_session("nonexistent-session-zxcvbn")
        finally:
            await integration_wrapper.kill_session(placeholder, ignore_missing=True)

    async def test_list_sessions_no_server_returns_empty(self, tmp_path) -> None:
        """Fresh socket dir, no server started yet → ``list_sessions``
        returns ``[]`` rather than raising (graceful for the "task
        service just booted" case)."""
        wrapper = RmuxWrapper(socket_dir=tmp_path / "fresh")
        result = await wrapper.list_sessions()
        assert result == []

    async def test_pipe_pane_streams_bytes_to_fifo(
        self, integration_wrapper, tmp_path
    ) -> None:
        """The actual R0a behaviour, now as a permanent regression test.

        new-session → pipe-pane → external writes to pane → bytes
        arrive in the FIFO within ~100 ms.
        """
        import asyncio

        await integration_wrapper.ensure_daemon()

        fifo = tmp_path / "stream.fifo"
        os.mkfifo(fifo)

        # Background FIFO reader writing arrivals to a capture file.
        capture = tmp_path / "capture.out"
        reader_proc = await asyncio.create_subprocess_exec(
            "bash", "-c",
            f"while IFS= read -r line; do echo \"$line\" >> {capture}; done < {fifo}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        session = "rwrapper-pipe-test"
        try:
            await integration_wrapper.new_session(
                name=session,
                cwd=str(tmp_path),
                cmd="bash -c 'while true; do read line; echo got: \"$line\"; done'",
            )
            await integration_wrapper.pipe_pane(session, fifo)
            # Drive a couple of probe lines through the pane.
            await integration_wrapper.send_text(session, "PROBE-1")
            await integration_wrapper.send_keys(session, "Enter")
            await asyncio.sleep(0.3)
            await integration_wrapper.send_text(session, "PROBE-2")
            await integration_wrapper.send_keys(session, "Enter")
            await asyncio.sleep(0.5)

            content = capture.read_text() if capture.exists() else ""
            assert "PROBE-1" in content, f"PROBE-1 missing from FIFO capture:\n{content}"
            assert "PROBE-2" in content, f"PROBE-2 missing from FIFO capture:\n{content}"
        finally:
            await integration_wrapper.kill_session(session, ignore_missing=True)
            # The FIFO reader may exit on its own (EOF when the writer
            # detaches), so .terminate() can race with natural exit.
            try:
                reader_proc.terminate()
                await asyncio.wait_for(reader_proc.wait(), timeout=2)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    reader_proc.kill()
                except ProcessLookupError:
                    pass
