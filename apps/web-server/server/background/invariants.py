"""Runtime invariants in production -- companion loader, timer and readiness (RFC-0024 section 4).

Non-critical checks run on an in-app timer: each violation is logged naming the
owning top-level package and counted in ``factory_invariant_violations_total``.
Critical checks back ``GET /api/ready`` through :func:`readiness`, cached for a
few seconds so a probe cannot hammer them, with at most one run in flight and a
timeout that counts as a violation so a hung check cannot pile up threads. Both
are no-ops while the registry is disabled (``FACTORY_INVARIANTS=0``).
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
READY_TIMEOUT_SECONDS = 3.0  # well below the 5 s kubelet probe timeout
_ready_cache: tuple[float, list[str]] | None = None
_ready_run: asyncio.Future[list[str]] | None = None


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


def _critical_failing() -> list[str]:
    return sorted({_package(v) for v in registry.check_all(critical=True)})


async def readiness() -> list[str]:
    """Top-level packages with a failing critical check; empty means ready.

    Runs on the event loop thread only, so the globals need no lock.
    """
    global _ready_cache, _ready_run  # noqa: PLW0603  (module-level cache and in-flight run)
    now = time.monotonic()
    if _ready_cache is not None and now - _ready_cache[0] < READY_CACHE_SECONDS:
        return _ready_cache[1]
    # Reuse a run still in flight (even one that already timed out) rather than start another.
    if _ready_run is None or _ready_run.done():
        _ready_run = asyncio.ensure_future(asyncio.to_thread(_critical_failing))
    try:
        failing = await asyncio.wait_for(asyncio.shield(_ready_run), READY_TIMEOUT_SECONDS)
    except TimeoutError:
        # The run is sequential, so any of its checks may be the stuck one.
        failing = sorted({m.split(".")[0] for m in registry.selected(critical=True)})
        for package in failing:
            logger.error(
                "invariant violated in %s: critical checks did not finish within %ss",
                package,
                READY_TIMEOUT_SECONDS,
            )
            VIOLATIONS.labels(service="pfactory", package=package).inc()
    _ready_cache = (now, failing)
    return failing
