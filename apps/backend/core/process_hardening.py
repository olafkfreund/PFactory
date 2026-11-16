"""Hide the control plane's /proc files from same-uid agents (Factory#3737, as AIFactory#1680)."""

from __future__ import annotations

import ctypes
import sys

_PR_SET_DUMPABLE = 4


def make_non_dumpable() -> bool:
    """Hide this process's environ and memory from same-uid processes.

    A non-dumpable process's /proc files are root-owned, so a same-uid agent
    cannot read its secrets; children become dumpable again on execve and keep
    the env they were given. Linux only; elsewhere a no-op returning True.
    Returns False when the prctl call fails.
    """
    if not sys.platform.startswith("linux"):
        return True
    libc = ctypes.CDLL(None, use_errno=True)
    return bool(libc.prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0) == 0)


def require_non_dumpable() -> None:
    """Fail closed: a process that cannot hide its secrets must not run."""
    if not make_non_dumpable():
        raise SystemExit("prctl(PR_SET_DUMPABLE, 0) failed")
