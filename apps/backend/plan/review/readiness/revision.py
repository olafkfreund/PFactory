"""The gate revision a readiness verdict was computed under (#450).

A :class:`~plan.review.readiness.models.ReadinessReport` is stored on the
session and read back later as if it were current state. When a check's logic is
fixed, every stored verdict keeps the old answer forever — so a plan stays
unapprovable on a defect that no longer exists in the code (#450, whose live
example was the #397 language false positive).

:func:`gate_revision` is the fingerprint that makes that knowable: a hash of the
source of the modules the checks actually decide from. A stored report carrying a
different revision was produced by different logic, so its verdict is stale.

The fingerprint is derived rather than hand-maintained on purpose. A constant
someone must remember to bump is exactly what was not bumped by #397 — the fix
lived in ``plan/recon/language_reconcile.py``, not in the check that calls it.
"""

from __future__ import annotations

import hashlib
import importlib.util
from functools import lru_cache

# The modules whose source decides a readiness verdict: the check catalog plus
# every module a check delegates its actual decision to. Editing any of them
# changes the revision, so every stored verdict recomputes on next read.
#
# ponytail: a check that grows a NEW decision dependency must add it here, or a
# fix in that dependency will not invalidate stored verdicts. Over-triggering is
# harmless (a recompute costs microseconds and needs no LLM); under-triggering is
# the bug this exists to prevent.
_SOURCE_MODULES: tuple[str, ...] = (
    "plan.review.readiness.checks",
    "plan.review.readiness.models",
    "plan.recon.language_reconcile",
    "plan.recon.delta",
    "plan.enrich.relevance",
    "plan.decompose.implicit_requirements",
    "plan.emit.constitution",
)


def _source_bytes(module: str) -> bytes:
    """Return ``module``'s source, or a stable marker when it cannot be read."""
    try:
        spec = importlib.util.find_spec(module)
    except Exception:  # noqa: BLE001 - a broken import must not break the gate
        spec = None
    origin = getattr(spec, "origin", None)
    if not origin or not origin.endswith(".py"):
        return b"<unreadable>"
    try:
        with open(origin, "rb") as fh:  # noqa: PTH123 - plain read, no Path needed
            return fh.read()
    except OSError:
        return b"<unreadable>"


@lru_cache(maxsize=1)
def gate_revision() -> str:
    """Fingerprint of the readiness logic currently loaded in this process.

    Cached: the source cannot change under a running process, and a report is
    stamped on every compute.
    """
    digest = hashlib.sha256()
    for module in _SOURCE_MODULES:
        digest.update(module.encode("utf-8"))
        digest.update(_source_bytes(module))
    return digest.hexdigest()[:12]
