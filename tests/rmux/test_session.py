"""Tests for ``apps/web-server/server/rmux/session.py``.

Two layers, matching ``test_wrapper.py``:

1. **Unit tests** — registry mutations, idempotent reap, duplicate-spec
   guard, configure/reset machinery.  Uses a mocked wrapper.

2. **Integration tests** (``@pytest.mark.rmux``) — drive a real rmux
   daemon: ``create_for_task`` actually spins up a session, the FIFO
   exists, and ``reap_for_task`` cleans up.
"""

from __future__ import annotations

import asyncio
import os
import stat
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from server.rmux.session import (
    SessionRegistry,
    SessionState,
    configure,
    get_registry,
    reset_for_tests,
)
from server.rmux.wrapper import RmuxError, RmuxWrapper

# ---------------------------------------------------------------------------
# Unit — no rmux required
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_wrapper() -> AsyncMock:
    """Wrapper double — every method is a coroutine that returns None."""
    w = AsyncMock(spec=RmuxWrapper)
    return w


@pytest.fixture
def registry(mock_wrapper, tmp_path) -> SessionRegistry:
    """Fresh registry per test pointing at tmp panes dir."""
    return SessionRegistry(wrapper=mock_wrapper, panes_dir=tmp_path / "panes")


class TestSessionState:
    """``SessionState`` is a dataclass — sanity-check its defaults."""

    def test_defaults(self, tmp_path) -> None:
        s = SessionState(
            spec_id="001",
            session_name="pfactory-task-001",
            fifo_path=tmp_path / "001.fifo",
        )
        assert s.attached_connection_id is None
        assert isinstance(s.lock, asyncio.Lock)


class TestCreateForTask:
    """Happy + edge paths of ``create_for_task``."""

    @pytest.mark.asyncio
    async def test_creates_fifo_at_expected_path(
        self, registry: SessionRegistry, tmp_path: Path
    ) -> None:
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        fifo = await registry.create_for_task(
            spec_id="001-feature",
            worktree_path=worktree,
            agent_cmd="bash -c 'echo hi'",
        )
        assert fifo == tmp_path / "panes" / "001-feature.fifo"
        assert fifo.exists()
        # MUST be a FIFO, not a regular file (bridge layer reads from it
        # as a stream — a regular file wouldn't block on empty read).
        assert stat.S_ISFIFO(fifo.stat().st_mode)

    @pytest.mark.asyncio
    async def test_records_session_in_registry(
        self, registry: SessionRegistry, tmp_path: Path
    ) -> None:
        await registry.create_for_task("001", tmp_path, "true")
        state = registry.get_state("001")
        assert state is not None
        assert state.session_name == "pfactory-task-001"
        assert state.attached_connection_id is None  # read-only on create

    @pytest.mark.asyncio
    async def test_calls_wrapper_in_correct_order(
        self, registry: SessionRegistry, mock_wrapper: AsyncMock, tmp_path: Path
    ) -> None:
        """ensure_daemon → new_session → pipe_pane.

        Per R0a gotcha #2: pipe-pane must attach BEFORE the agent has
        a chance to emit; we enforce that ordering inside create_for_task
        so callers can't get it wrong.
        """
        await registry.create_for_task("001", tmp_path, "true")

        method_order = [c[0] for c in mock_wrapper.method_calls]
        assert method_order == ["ensure_daemon", "new_session", "pipe_pane"]

    @pytest.mark.asyncio
    async def test_duplicate_spec_id_raises(
        self, registry: SessionRegistry, tmp_path: Path
    ) -> None:
        await registry.create_for_task("001", tmp_path, "true")
        with pytest.raises(ValueError, match="already exists"):
            await registry.create_for_task("001", tmp_path, "true")

    @pytest.mark.asyncio
    async def test_recovers_from_stale_fifo(
        self, registry: SessionRegistry, tmp_path: Path
    ) -> None:
        """A leftover FIFO from a previous (crashed) run is unlinked
        before the new one is created — ``mkfifo`` would otherwise EEXIST.
        """
        panes_dir = tmp_path / "panes"
        panes_dir.mkdir(parents=True)
        stale = panes_dir / "001.fifo"
        os.mkfifo(str(stale))
        assert stale.exists()
        # New create should succeed and replace the FIFO without raising
        await registry.create_for_task("001", tmp_path, "true")
        assert stale.exists()  # new FIFO at same path


class TestReapForTask:
    """``reap_for_task`` is the task-shutdown half — must never raise."""

    @pytest.mark.asyncio
    async def test_unlinks_fifo_and_clears_registry(
        self, registry: SessionRegistry, tmp_path: Path
    ) -> None:
        await registry.create_for_task("001", tmp_path, "true")
        fifo = registry.get_state("001").fifo_path
        assert fifo.exists()

        await registry.reap_for_task("001")
        assert registry.get_state("001") is None
        assert not fifo.exists()

    @pytest.mark.asyncio
    async def test_idempotent_when_not_registered(
        self, registry: SessionRegistry
    ) -> None:
        # Should not raise
        await registry.reap_for_task("never-created")

    @pytest.mark.asyncio
    async def test_swallows_kill_session_error(
        self, registry: SessionRegistry, mock_wrapper: AsyncMock, tmp_path: Path
    ) -> None:
        """If rmux's kill-session blows up (rare — daemon already
        gone, file perms changed, etc.), reap must still finish so the
        task-shutdown path doesn't hang."""
        await registry.create_for_task("001", tmp_path, "true")
        mock_wrapper.kill_session.side_effect = RmuxError("kaboom")
        # Must not raise:
        await registry.reap_for_task("001")
        # Registry must still be cleaned regardless:
        assert registry.get_state("001") is None


class TestModuleSingleton:
    """``get_registry`` / ``configure`` / ``reset_for_tests`` machinery."""

    def setup_method(self) -> None:
        reset_for_tests()

    def teardown_method(self) -> None:
        reset_for_tests()

    def test_get_registry_lazy_creates(self) -> None:
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2  # same singleton

    def test_configure_replaces_singleton(self, tmp_path) -> None:
        configure(panes_dir=tmp_path / "panes")
        r = get_registry()
        # Internal field check — verifies configure took effect.
        assert r._panes_dir == tmp_path / "panes"

    def test_reset_drops_singleton(self) -> None:
        r1 = get_registry()
        reset_for_tests()
        r2 = get_registry()
        assert r1 is not r2


# ---------------------------------------------------------------------------
# Integration — real rmux daemon
# ---------------------------------------------------------------------------


@pytest.fixture
def real_registry(tmp_path) -> SessionRegistry:
    """Integration fixture — wrapper bound to user-owned tmp socket dir.

    NB: ``tmp_path`` is under ``/tmp/pytest-of-USER/`` which is user-owned
    (the parent ``/tmp`` would be rejected — see R0a gotcha #1).
    """
    sock_dir = tmp_path / "rmux-sock"
    panes_dir = tmp_path / "panes"
    wrapper = RmuxWrapper(socket_dir=sock_dir)
    return SessionRegistry(wrapper=wrapper, panes_dir=panes_dir)


@pytest.mark.rmux
@pytest.mark.asyncio
class TestIntegrationCreateAndReap:
    """End-to-end against a real rmux daemon."""

    async def test_real_create_then_reap(
        self, real_registry: SessionRegistry, tmp_path: Path
    ) -> None:
        spec_id = "rsess-test-create-reap"
        worktree = tmp_path / "wt"
        worktree.mkdir()

        fifo = await real_registry.create_for_task(
            spec_id=spec_id,
            worktree_path=worktree,
            cmd_or_session="bash -c 'sleep 30'",
        ) if False else await real_registry.create_for_task(
            spec_id=spec_id,
            worktree_path=worktree,
            agent_cmd="bash -c 'sleep 30'",
        )

        # FIFO exists + is a FIFO
        assert fifo.exists()
        assert stat.S_ISFIFO(fifo.stat().st_mode)

        # rmux actually sees the session
        sessions = await real_registry.wrapper.list_sessions()
        assert f"pfactory-task-{spec_id}" in sessions

        # Reap cleans it up
        await real_registry.reap_for_task(spec_id)
        sessions_after = await real_registry.wrapper.list_sessions()
        assert f"pfactory-task-{spec_id}" not in sessions_after
        assert not fifo.exists()
