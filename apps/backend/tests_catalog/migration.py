"""v0.1 workspace migration helper — Task 3 (#19).

Walks a v0.1 Gen-Functional workspace (``spec_dir/tests/``) and synthesises
``CatalogEntry`` objects for every previously-generated pytest file.

This is the primitive that the future ``pfactory migrate`` CLI command (Task 15)
will call.  It does NOT mutate ``spec_dir``, does NOT write to disk, and does
NOT call any LLM.  The caller owns persistence.

v0.1 workspace layout::

    spec_dir/
      tests/
        test_login_expiry.py
        test_refresh_session.py
      test_plan.json          ← Planner's output; used to map files → AC strings
      findings/
        verdicts.json         ← optional Evaluator output; used for last_verdict

Migration rules
---------------
* ``test_id``: derived from the filename stem by replacing ``_`` with ``-``
  (e.g. ``test_login_expiry.py`` → ``"test-login-expiry"``).
* ``test_file``: repo-relative path ``"tests/<filename>"``.
* ``framework``: always ``"pytest"`` (v0.1 was pytest-only).
* ``lane``: always ``"unit"`` (v0.1 FUNCTIONAL lane = v0.2 UNIT).
* ``language``: always ``"python"``.
* ``covers_acs``: resolved from ``test_plan.json`` by finding the ``Subtask``
  whose ``files_to_create`` list includes this test file; the subtask's
  ``rationale`` field is the AC string.  If no match is found, ``()`` is used.
* ``generated_at``: the file's mtime formatted as ISO-8601 UTC.
* ``generated_by_task``: ``spec_dir.name``.
* ``last_verdict``: looked up in ``findings/verdicts.json`` (if present) by
  ``test_id``; defaults to ``"accept"`` if the file is absent or the entry is
  not found.
* ``target_ref``: ``None``.
* ``operator_locked``: ``False``.
* ``generation_version``: ``1``.

Deduplication is by ``test_id``: if the incoming *catalog* already has an
entry with the same ``test_id``, the existing entry is kept (migration is
additive-only, never overwrites).

Usage::

    from pathlib import Path
    from tests_catalog.migration import migrate_v0_1_workspace
    from tests_catalog.schema import TestsCatalog

    empty_catalog = TestsCatalog(version=1, updated_at="2026-05-28T12:00:00Z", tests=())
    new_catalog = migrate_v0_1_workspace(Path("/path/to/spec_dir"), empty_catalog)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .schema import CatalogEntry, TestsCatalog

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mtime_iso(path: Path) -> str:
    """Return the file's modification time as an ISO-8601 UTC string."""
    mtime = path.stat().st_mtime
    dt = datetime.fromtimestamp(mtime, tz=UTC)
    # Format as "2026-05-28T10:30:00Z" (strip microseconds, replace +00:00 with Z)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json_safe(path: Path) -> Any:
    """Return parsed JSON or None if the file does not exist / is unreadable."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _derive_test_id(stem: str) -> str:
    """Convert a filename stem to a catalog test-id.

    ``test_login_expiry`` → ``"test-login-expiry"``
    """
    return stem.replace("_", "-")


def _resolve_covers_acs(test_file_rel: str, plan: Any) -> tuple[str, ...]:
    """Find the AC string from test_plan.json for *test_file_rel*.

    Searches every subtask in every phase for a ``files_to_create`` entry
    that matches *test_file_rel*.  Returns the subtask's ``rationale`` if
    found, or an empty tuple if not.

    Args:
        test_file_rel: Repo-relative test file path (e.g. ``"tests/test_login.py"``).
        plan: Parsed ``test_plan.json`` dict, or ``None``.
    """
    if not plan:
        return ()

    phases = plan.get("phases", [])
    for phase in phases:
        for subtask in phase.get("subtasks", []):
            files_to_create = subtask.get("files_to_create", [])
            if test_file_rel in files_to_create:
                rationale = subtask.get("rationale")
                if rationale:
                    return (rationale,)
    return ()


def _resolve_last_verdict(test_id: str, verdicts: Any) -> str:
    """Look up the last verdict for *test_id* in the verdicts JSON.

    Args:
        test_id: The catalog test-id (e.g. ``"test-login-expiry"``).
        verdicts: Parsed ``findings/verdicts.json`` dict/list, or ``None``.

    Returns:
        The verdict string, or ``"accept"`` if not found.
    """
    if not verdicts:
        return "accept"

    # verdicts.json is either a list of {test_id, verdict, ...} objects
    # or a dict keyed by test_id.
    if isinstance(verdicts, list):
        for item in verdicts:
            if isinstance(item, dict) and item.get("test_id") == test_id:
                verdict = item.get("verdict", "accept")
                # Normalise to catalog-valid verdicts; unknown → "accept"
                if verdict in {"accept", "reject", "flag", "skip"}:
                    return verdict
                return "accept"
    elif isinstance(verdicts, dict):
        item = verdicts.get(test_id)
        if isinstance(item, dict):
            verdict = item.get("verdict", "accept")
            if verdict in {"accept", "reject", "flag", "skip"}:
                return verdict
        elif isinstance(item, str) and item in {"accept", "reject", "flag", "skip"}:
            return item

    return "accept"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def migrate_v0_1_workspace(
    spec_dir: Path,
    catalog: TestsCatalog,
) -> TestsCatalog:
    """Synthesise ``CatalogEntry`` objects from a v0.1 spec workspace.

    Walks ``spec_dir/tests/`` for ``test_*.py`` files and builds a new
    ``TestsCatalog`` that contains the original *catalog* entries plus the
    newly synthesised entries (deduplicated by ``test_id``).

    This function is **pure** — it does not write to disk and does not mutate
    *spec_dir* or *catalog*.

    Args:
        spec_dir: The v0.1 spec directory, e.g.
            ``~/.pfactory/workspaces/<project>/specs/042-session-expiry``.
        catalog: The existing catalog to append to (may be empty).

    Returns:
        A new ``TestsCatalog`` with the migrated entries appended.  The
        ``updated_at`` timestamp is left unchanged from the input *catalog* —
        callers should set it before saving.
    """
    tests_dir = spec_dir / "tests"
    plan = _load_json_safe(spec_dir / "test_plan.json")
    verdicts = _load_json_safe(spec_dir / "findings" / "verdicts.json")

    # Build a set of existing test_ids so we can skip duplicates.
    existing_ids: set[str] = {e.test_id for e in catalog.tests}

    new_entries: list[CatalogEntry] = []

    if tests_dir.exists():
        # Sort for deterministic ordering across runs.
        for test_file_path in sorted(tests_dir.glob("test_*.py")):
            stem = test_file_path.stem  # e.g. "test_login_expiry"
            test_id = _derive_test_id(stem)

            # Skip if already in catalog — existing entries win.
            if test_id in existing_ids:
                continue

            test_file_rel = f"tests/{test_file_path.name}"

            covers_acs = _resolve_covers_acs(test_file_rel, plan)
            last_verdict = _resolve_last_verdict(test_id, verdicts)
            generated_at = _mtime_iso(test_file_path)

            entry = CatalogEntry(
                test_id=test_id,
                test_file=test_file_rel,
                framework="pytest",
                lane="unit",
                language="python",
                covers_acs=covers_acs,
                generated_at=generated_at,
                generated_by_task=spec_dir.name,
                last_verdict=last_verdict,
                browsers_tested=(),
                target_ref=None,
                operator_locked=False,
                generation_version=1,
            )
            new_entries.append(entry)
            existing_ids.add(test_id)

    # Append migrated entries after the existing ones.
    merged_tests = tuple(catalog.tests) + tuple(new_entries)

    return TestsCatalog(
        version=catalog.version,
        updated_at=catalog.updated_at,
        tests=merged_tests,
    )
