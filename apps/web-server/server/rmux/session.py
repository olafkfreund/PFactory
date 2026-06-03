"""Per-task rmux session lifecycle (Epic #44, issue #46).

Maps an PFactory ``spec_id`` to:

  - an rmux session named ``pfactory-task-<spec_id>``
  - a Unix FIFO at ``<panes_dir>/<spec_id>.fifo`` that pipe-pane writes
    bytes to as the agent produces output
  - per-session mutable state needed by the WebSocket bridge:
    an ``asyncio.Lock`` to serialise attach mode flips, and the
    currently-attached ``connection_id`` (or ``None`` when read-only).

Module-level singleton.  ``agent_service`` calls ``create_for_task``
when a task starts (only when ``PFACTORY_RMUX_ENABLED=true``) and
``reap_for_task`` when it ends.

Threading model
---------------

Everything runs on a single asyncio event loop in the web-server
process.  No threads, no multi-process — multi-replica rmux is
explicitly out of scope for v1 (design §3.4 pins ``replicas: 1`` in
the Helm chart when the feature is enabled).  The ``asyncio.Lock``s
serialise (a) registry mutations against concurrent create/reap calls
for the same ``spec_id``, and (b) attach-mode flips so a 1000-concurrent
``POST /attach`` race resolves to exactly one 200 + 999 409s
(acceptance criterion in design §7).
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from .wrapper import RmuxError, RmuxWrapper

logger = logging.getLogger(__name__)

# Default panes directory in the runtime container.  Overridden for
# tests via ``configure(panes_dir=...)`` so they can use a tmp_path.
_DEFAULT_PANES_DIR = Path("/var/run/pfactory/panes")


@dataclass
class SessionState:
    """Per-task mutable state held in the registry.

    The ``lock`` here protects ``attached_connection_id`` against a
    1000-concurrent ``POST /attach`` race.  Any handler that wants to
    flip attach mode MUST acquire this lock first.
    """

    spec_id: str
    session_name: str
    fifo_path: Path
    attached_connection_id: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SessionRegistry:
    """Module-singleton registry mapping ``spec_id`` → ``SessionState``.

    Constructor parameters are exposed for tests (point at a tmp_path
    panes dir, inject a wrapper bound to a tmp-path socket).  Production
    uses defaults.
    """

    def __init__(
        self,
        wrapper: RmuxWrapper | None = None,
        panes_dir: Path | str | None = None,
    ) -> None:
        self._wrapper = wrapper or RmuxWrapper()
        self._panes_dir = Path(panes_dir) if panes_dir else _DEFAULT_PANES_DIR
        self._states: dict[str, SessionState] = {}
        # Serialises mutations to ``_states`` itself.  Per-session
        # ``attached_connection_id`` flips use the per-state lock.
        self._registry_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def create_for_task(
        self,
        spec_id: str,
        worktree_path: str | Path,
        agent_cmd: str | list[str],
    ) -> Path:
        """Spin up the rmux session + FIFO + pipe-pane for ``spec_id``.

        Returns the FIFO path the bridge layer reads bytes from.

        Raises:
            ValueError: if a session for ``spec_id`` already exists
                (caller must reap before re-create).
            RmuxError: rmux subprocess failures bubble up unchanged so
                ``agent_service`` can fall back to the existing PTY
                path and surface a banner per design §6.

        Note on ordering: ``new_session`` actually starts the agent
        command, and rmux returns immediately (it's ``-d`` detached).
        ``pipe-pane`` runs right after; in practice the agent's first
        bytes don't land until after this returns because subprocess
        startup is slower than the wrapper round-trip.  But the
        contract is "pipe-pane attaches eagerly" — see R0a gotcha #2.
        """
        session_name = f"pfactory-task-{spec_id}"
        fifo_path = self._panes_dir / f"{spec_id}.fifo"

        async with self._registry_lock:
            if spec_id in self._states:
                raise ValueError(
                    f"rmux session already exists for spec_id={spec_id!r}"
                )

            # Create panes dir + FIFO.  mkfifo blows up if the path
            # already exists, so unlink first (idempotent recovery
            # from a half-cleaned previous run).
            self._panes_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            if fifo_path.exists():
                fifo_path.unlink()
            os.mkfifo(str(fifo_path), mode=0o600)

            # Bring up rmux + session + pipe-pane in one shot.
            await self._wrapper.ensure_daemon()
            await self._wrapper.new_session(
                session_name, worktree_path, agent_cmd
            )
            await self._wrapper.pipe_pane(session_name, fifo_path)

            self._states[spec_id] = SessionState(
                spec_id=spec_id,
                session_name=session_name,
                fifo_path=fifo_path,
            )
            logger.info(
                "rmux session created: spec_id=%s session=%s fifo=%s",
                spec_id, session_name, fifo_path,
            )
            return fifo_path

    async def reap_for_task(self, spec_id: str) -> None:
        """Kill the session + remove the FIFO.  Idempotent.

        Called from ``agent_service`` on task completion/failure/discard.
        Logs but never raises — reaping must not block task shutdown.
        """
        async with self._registry_lock:
            state = self._states.pop(spec_id, None)
            if state is None:
                return  # nothing to reap

        # Outside the registry lock — these are slow-ish subprocess ops
        # and other callers don't need to wait on them.
        try:
            await self._wrapper.kill_session(
                state.session_name, ignore_missing=True
            )
        except RmuxError:
            logger.warning(
                "rmux kill-session failed during reap (ignored): %s",
                state.session_name,
                exc_info=True,
            )
        try:
            state.fifo_path.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "fifo unlink failed during reap (ignored): %s",
                state.fifo_path,
                exc_info=True,
            )

        logger.info(
            "rmux session reaped: spec_id=%s session=%s",
            spec_id, state.session_name,
        )

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_state(self, spec_id: str) -> SessionState | None:
        """Return the state for ``spec_id`` or ``None`` if not registered."""
        return self._states.get(spec_id)

    @property
    def wrapper(self) -> RmuxWrapper:
        """Expose the wrapper for the bridge layer (send-keys forwarding)."""
        return self._wrapper

    def __iter__(self) -> Iterator[str]:
        """Iterate over currently-registered spec_ids (for diagnostics)."""
        return iter(self._states.keys())


# ---------------------------------------------------------------------------
# Module-level singleton + configuration hook
# ---------------------------------------------------------------------------


_registry: SessionRegistry | None = None


def get_registry() -> SessionRegistry:
    """Return the module-level singleton, lazily creating it on first call."""
    global _registry
    if _registry is None:
        _registry = SessionRegistry()
    return _registry


def configure(
    *,
    wrapper: RmuxWrapper | None = None,
    panes_dir: Path | str | None = None,
) -> SessionRegistry:
    """Replace the singleton with one bound to test/container settings.

    Production should call this exactly once at web-server startup
    (e.g. from a FastAPI startup hook gated by
    ``PFACTORY_RMUX_ENABLED``).  Tests call it in fixtures to point
    at a tmp_path FIFO directory + a wrapper using a tmp-path socket.
    """
    global _registry
    _registry = SessionRegistry(wrapper=wrapper, panes_dir=panes_dir)
    return _registry


def reset_for_tests() -> None:
    """Drop the singleton — convenience for test teardown.

    The next ``get_registry()`` call will create a fresh default
    registry.  Tests that use ``configure()`` should call this in
    teardown so they don't leak state across the suite.
    """
    global _registry
    _registry = None
