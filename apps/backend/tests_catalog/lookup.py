"""AC-match lookup algorithm for the tests catalog — Task 3 (#19).

Implements the deterministic 3-step ``lookup_by_ac`` algorithm defined in the
design document (``docs/plans/2026-05-28-enterprise-test-frameworks-design.md``,
section "AC-match algorithm").

The three steps, in priority order:

1. **Exact match** — candidate AC string appears verbatim in
   ``entry.covers_acs``.  Returns immediately if any hits are found.
2. **AC-ID prefix match** — the prefix before the first ``:`` is extracted
   (e.g. ``"AC#1: login expiry"`` → ``"AC#1"``).  A catalog entry matches if
   any of its ``covers_acs`` strings *starts with* that prefix.  This handles
   the common case where the AC text changes between runs but the AC-ID is
   stable.
3. **Empty** — no match; returns ``[]``.

Embedding-similarity matching is explicitly out of scope for v0.2 (see design
doc).  The exact + prefix algorithm covers > 95 % of real-world cases; the
operator handles the rest via ``operator_locked`` flags or manual edits.

Usage::

    from tests_catalog.lookup import lookup_by_ac
    from tests_catalog.schema import TestsCatalog

    matches = lookup_by_ac(catalog, "AC#1: User can log in with valid credentials")
    if matches and matches[0].operator_locked:
        ...  # skip regeneration
"""

from __future__ import annotations

from .schema import CatalogEntry, TestsCatalog


def lookup_by_ac(
    catalog: TestsCatalog,
    candidate_ac: str,
) -> list[CatalogEntry]:
    """Find catalog entries that cover *candidate_ac*.

    Implements the 3-step deterministic algorithm from the v0.2 design doc.
    Insertion order of ``catalog.tests`` is preserved within each result tier.

    Args:
        catalog: The catalog to search.
        candidate_ac: The acceptance-criterion string emitted by the Planner
            for a new subtask, e.g. ``"AC#1: User can log in"``.

    Returns:
        A (possibly empty) list of ``CatalogEntry`` objects.  At most one tier
        is returned per call:

        * If exact matches exist, only exact matches are returned.
        * Otherwise, if AC-ID prefix matches exist, only those are returned.
        * Otherwise, ``[]``.
    """
    # ------------------------------------------------------------------
    # Step 1: Exact match — best signal
    # ------------------------------------------------------------------
    exact = [e for e in catalog.tests if candidate_ac in e.covers_acs]
    if exact:
        return exact

    # ------------------------------------------------------------------
    # Step 2: AC-ID prefix match
    # candidate_ac="AC#1: login expiry" matches stored "AC#1: login flow"
    # The prefix is the part before the first ':' (stripped of whitespace).
    # ------------------------------------------------------------------
    ac_id = candidate_ac.split(":", 1)[0].strip()
    if ac_id:
        prefix = [
            e for e in catalog.tests if any(s.startswith(ac_id) for s in e.covers_acs)
        ]
        if prefix:
            return prefix

    # ------------------------------------------------------------------
    # Step 3: No match
    # ------------------------------------------------------------------
    return []
