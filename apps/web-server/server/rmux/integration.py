"""Tiny shim between ``agent_service`` and the rmux session/bridge layer.

Keeps the integration footprint in ``agent_service.py`` minimal — one
import, three callsites (create, stop-reap, monitor-reap) — and
centralises the ``PFACTORY_RMUX_ENABLED`` flag check + the worktree
path convention.

When the flag is unset or false:
  - ``create_if_enabled`` returns ``None`` and does nothing
  - ``reap_if_enabled`` is a no-op
  - existing behaviour is byte-for-byte unchanged
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .session import get_registry

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    """Return ``True`` iff ``PFACTORY_RMUX_ENABLED=true`` in the env.

    Case-insensitive truthy parsing — ``true`` / ``1`` / ``yes`` all flip
    it on so operators don't trip over case sensitivity.
    """
    raw = os.environ.get("PFACTORY_RMUX_ENABLED", "").strip().lower()
    return raw in {"true", "1", "yes", "on"}


def _worktree_path(project_path: Path | str, spec_id: str) -> Path:
    """The convention the agent uses for per-task worktrees.

    Mirrors ``apps/backend/cli/worktree.py`` — kept here as a local
    constant rather than imported so this shim doesn't take a new
    cross-package dependency.
    """
    return Path(project_path) / ".pfactory" / "worktrees" / "tasks" / spec_id


async def create_if_enabled(
    spec_id: str,
    project_path: Path | str,
    agent_cmd: str | list[str],
) -> Path | None:
    """Spin up the rmux session for a task — no-op when feature flag off.

    Returns the FIFO path on success (so the WebSocket bridge can find
    it), ``None`` when disabled.  All rmux errors are caught + logged
    so the task can still proceed via the existing PTY path (design §6
    failure-mode policy).
    """
    if not is_enabled():
        return None

    worktree = _worktree_path(project_path, spec_id)
    try:
        registry = get_registry()
        return await registry.create_for_task(
            spec_id=spec_id,
            worktree_path=worktree,
            agent_cmd=agent_cmd,
        )
    except Exception:
        logger.warning(
            "rmux create_for_task failed (falling back to PTY); spec_id=%s",
            spec_id,
            exc_info=True,
        )
        return None


async def reap_if_enabled(spec_id: str) -> None:
    """Tear down the rmux session — no-op when feature flag off.

    Idempotent.  Designed to be safe to call from multiple cleanup
    paths in ``agent_service`` (``stop_task`` AND ``_monitor_process``);
    only the first call does work, subsequent calls return immediately.
    """
    if not is_enabled():
        return
    try:
        registry = get_registry()
        await registry.reap_for_task(spec_id)
    except Exception:
        logger.warning(
            "rmux reap_for_task failed (ignored); spec_id=%s",
            spec_id,
            exc_info=True,
        )
