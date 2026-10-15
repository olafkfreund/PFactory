"""Runtime invariants in production -- companion loader, timer and readiness (RFC-0024 section 4).

Non-critical checks run on an in-app timer: each violation is logged naming the
owning top-level package and counted in ``factory_invariant_violations_total``.
Critical checks back ``GET /api/ready`` through :func:`readiness`, cached for a
few seconds so a probe cannot hammer them. Both are no-ops while the registry is
disabled (``FACTORY_INVARIANTS=0``).
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
import time
from pathlib import Path

from prometheus_client import Counter

# Same pattern as liveness_sweep: apps/backend is not reliably on PYTHONPATH.
_APPS = Path(__file__).resolve().parents[3]
_ROOTS = (_APPS / "backend", _APPS / "web-server")
for _root in _ROOTS:
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from factory_invariants import Violation, registry  # noqa: E402  (after sys.path insert)

logger = logging.getLogger(__name__)

VIOLATIONS = Counter(
    "factory_invariant_violations_total",
    "Runtime invariant violations, by owning package (RFC-0024).",
    ["service", "package"],
)

READY_CACHE_SECONDS = 10.0
_ready_cache: tuple[float, list[str]] | None = None


def load_companions() -> list[str]:
    """Import every ``*/_invariants.py`` and root ``*_invariants.py`` so they register.

    A companion that fails to import is logged and skipped, never fatal.
    """
    names: list[str] = []
    for root in _ROOTS:
        names += [f"{p.parent.name}._invariants" for p in sorted(root.glob("*/_invariants.py"))]
        names += [p.stem for p in sorted(root.glob("*_invariants.py"))]
    loaded: list[str] = []
    for name in names:
        try:
            importlib.import_module(name)
        except Exception:  # one bad companion must not stop startup
            logger.exception("invariant companion %s failed to import", name)
        else:
            loaded.append(name)
    return loaded


def _package(v: Violation) -> str:
    return v.module.split(".")[0]


def run_once() -> list[Violation]:
    """Run the non-critical checks, log and count each violation. Sync, testable."""
    violations = registry.check_all(critical=False)
    for v in violations:
        logger.error("invariant violated in %s: %s", _package(v), v.detail)
        VIOLATIONS.labels(service="pfactory", package=_package(v)).inc()
    return violations


async def invariant_loop(period_seconds: float) -> None:
    """Run :func:`run_once` every ``period_seconds`` until cancelled."""
    while True:
        await asyncio.sleep(period_seconds)
        try:
            await asyncio.to_thread(run_once)
        except Exception:  # a bad iteration must not kill the loop
            logger.exception("invariant iteration failed")


def readiness() -> list[str]:
    """Top-level packages with a failing critical check; empty means ready."""
    global _ready_cache  # noqa: PLW0603  (module-level 10 s cache)
    now = time.monotonic()
    if _ready_cache is not None and now - _ready_cache[0] < READY_CACHE_SECONDS:
        return _ready_cache[1]
    failing = sorted({_package(v) for v in registry.check_all(critical=True)})
    _ready_cache = (now, failing)
    return failing
