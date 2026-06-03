"""rmux integration for PFactory (Epic #44).

v1 scope: F1 (Live Agent Console) + F7 (Playwright E2E).  rmux is bundled as
an opt-in binary (gate: ``PFACTORY_RMUX_ENABLED=true``); the bank-pilot
image ships without it.

This package contains:

- ``wrapper`` — thin async subprocess wrapper around the ``rmux`` CLI
- ``session`` — per-task lifecycle (added in R1)
- ``bridge`` — FIFO ↔ WebSocket transport (added in R1)

See ``guides/plans/2026-05-24-pfactory-rmux-integration-design.md``.
"""

from .bridge import router as console_router
from .integration import is_enabled as is_rmux_enabled
from .session import SessionRegistry, SessionState, configure, get_registry
from .wrapper import (
    RmuxDaemonError,
    RmuxError,
    RmuxNotInstalledError,
    RmuxSessionError,
    RmuxWrapper,
)

__all__ = [
    # wrapper layer
    "RmuxWrapper",
    "RmuxError",
    "RmuxDaemonError",
    "RmuxSessionError",
    "RmuxNotInstalledError",
    # session layer
    "SessionRegistry",
    "SessionState",
    "get_registry",
    "configure",
    # bridge layer
    "console_router",
    # integration shim
    "is_rmux_enabled",
]
