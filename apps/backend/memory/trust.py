"""Untrusted-content window (RFC-0021 Phase 4, trust gate).

Signals observed shortly after an external-content tool ran are marked
lower-trust. A time window rather than a per-session flag: tools do not know
the session, and a window another session opens can only make this one promote
less, never more.
"""

import time

TRUST_WINDOW = 600  # seconds after the last external-content tool call
UNVERIFIED = "\n\n_Source: unverified (external content read in this session)_"

_state: dict[str, float] = {}


def taint(now: float | None = None) -> None:
    _state["at"] = time.monotonic() if now is None else now


def tainted(now: float | None = None) -> bool:
    at = _state.get("at")
    if at is None:
        return False
    return (time.monotonic() if now is None else now) - at < TRUST_WINDOW


def strip_marker(body: str) -> str:
    return body.removesuffix(UNVERIFIED)
