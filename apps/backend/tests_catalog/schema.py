"""Tests-catalog schema dataclasses — Task 3 (#19).

Defines the in-memory representation of `.pfactory/tests-catalog.json`, the
persistent cross-run catalog that the Triager (Task 11 / #29) consults to
decide UPDATE-in-place vs CREATE-new for each accepted test.

The catalog lives in the AIFactory repo (not the PFactory workspace), so it
is checked in alongside the generated tests.  Its schema is intentionally
loose in two areas:

* ``framework`` stores the framework name (e.g. ``"playwright"``, ``"pytest"``)
  but is NOT validated against the framework registry at catalog-write time.
  Validation happens at Triager time so the catalog survives framework renames
  in the registry without requiring a catalog migration.
* ``covers_acs`` is a tuple of free-form strings, not a foreign key to any
  spec.  The 3-step ``lookup_by_ac`` algorithm in ``lookup.py`` resolves
  matches deterministically without needing a normalised AC store.

Both ``CatalogEntry`` and ``TestsCatalog`` are *frozen* dataclasses so they
are hashable and safe to use as dict keys or in sets.

Usage::

    from tests_catalog.schema import CatalogEntry, TestsCatalog

    entry = CatalogEntry(
        test_id="ac1-login-flow",
        test_file="tests/e2e/login-flow.spec.ts",
        framework="playwright",
        lane="browser",
        language="typescript",
        covers_acs=("AC#1: User can log in with valid credentials",),
        generated_at="2026-05-28T10:30:00Z",
        generated_by_task="042-session-expiry",
        last_verdict="accept",
        browsers_tested=("chromium",),
        target_ref="web-staging",
    )

    catalog = TestsCatalog(
        version=1,
        updated_at="2026-05-28T12:00:00Z",
        tests=(entry,),
    )

    d = catalog.to_dict()
    assert TestsCatalog.from_dict(d) == catalog
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from test_plan.enums import Lane

# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class CatalogError(Exception):
    """Raised for invalid catalog data or IO errors.

    Attributes:
        field: The field or category that caused the error (e.g. ``"lane"``,
            ``"file"``).
        reason: Human-readable explanation of what went wrong.
    """

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"CatalogError({field!r}): {reason}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# The 5 valid v0.2 lane values as strings (mirrors Lane enum values).
_VALID_LANES: frozenset[str] = frozenset(lane.value for lane in Lane)

_VALID_VERDICTS: frozenset[str] = frozenset({"accept", "reject", "flag", "skip"})


def validate_lane(s: str) -> str:
    """Return *s* unchanged if it is a valid v0.2 lane; raise ``CatalogError`` if not.

    Valid values are the five ``Lane`` enum values: ``"unit"``, ``"browser"``,
    ``"api"``, ``"integration"``, ``"mutation"``.

    Args:
        s: The lane string to validate.

    Returns:
        The original string *s* (unchanged).

    Raises:
        CatalogError: If *s* is not one of the five v0.2 lane values.
    """
    if s not in _VALID_LANES:
        sorted_valid = sorted(_VALID_LANES)
        raise CatalogError(
            "lane",
            f"{s!r} is not a valid v0.2 lane value. Valid values: {sorted_valid}",
        )
    return s


# ---------------------------------------------------------------------------
# CatalogEntry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CatalogEntry:
    """A single test registered in the tests catalog.

    All fields map 1-to-1 to the JSON schema.  Tuple fields (``covers_acs``,
    ``browsers_tested``) are frozen so instances are hashable.

    Attributes:
        test_id: Unique identifier within the catalog.
            Derived from the test filename stem during Gen-Functional, e.g.
            ``"ac1-login-flow"``.
        test_file: Repo-relative path to the test file,
            e.g. ``"tests/e2e/login-flow.spec.ts"``.
        framework: Framework name, e.g. ``"playwright"``, ``"pytest"``,
            ``"jest"``.  Not validated against the registry here — validation
            happens at Triager time so the catalog survives registry renames.
        lane: One of the five v0.2 lane values: ``"unit"``, ``"browser"``,
            ``"api"``, ``"integration"``, ``"mutation"``.
        language: Programming language, e.g. ``"typescript"``, ``"python"``.
        covers_acs: Tuple of acceptance-criterion strings this test covers,
            e.g. ``("AC#1: User can log in with valid credentials",)``.
        generated_at: ISO-8601 timestamp (UTC, ``Z`` suffix) when the test
            was first generated, e.g. ``"2026-05-28T10:30:00Z"``.
        generated_by_task: Spec-ID that generated this test,
            e.g. ``"042-session-expiry"``.
        last_verdict: Most recent Evaluator verdict — one of
            ``"accept"``, ``"reject"``, ``"flag"``, ``"skip"``.
        browsers_tested: For ``lane="browser"`` tests, the browsers exercised
            (e.g. ``("chromium", "firefox")``).  Empty tuple for other lanes.
        target_ref: The target name from ``.pfactory.yml`` this test was
            generated for (e.g. ``"web-staging"``).  ``None`` if absent.
        operator_locked: When ``True`` the Triager skips regeneration of this
            test even when a new AC match is found.  Operators set this to pin
            a hand-crafted test.
        generation_version: Starts at 1; the Triager increments it on each
            UPDATE-in-place cycle so the catalog history is auditable.
    """

    test_id: str
    test_file: str
    framework: str
    lane: str
    language: str
    covers_acs: tuple[str, ...]
    generated_at: str
    generated_by_task: str
    last_verdict: str
    # Optional / defaulted fields
    browsers_tested: tuple[str, ...] = ()
    target_ref: str | None = None
    operator_locked: bool = False
    generation_version: int = 1
    # Evidence capture fields (Task 16 / #32) — backward-compatible defaults
    last_evidence_run_id: str | None = None
    # Stored as a tuple of (key, value) pairs where list values become tuples.
    # This keeps CatalogEntry hashable (frozen dataclass constraint).
    # Use .evidence_urls property for a plain dict view, or to_dict() for JSON.
    evidence_urls_raw: tuple[tuple[str, str | tuple[str, ...]], ...] = ()

    def __post_init__(self) -> None:
        validate_lane(self.lane)
        if self.last_verdict not in _VALID_VERDICTS:
            raise CatalogError(
                "last_verdict",
                f"{self.last_verdict!r} is not a valid verdict. "
                f"Valid values: {sorted(_VALID_VERDICTS)}",
            )

    @property
    def evidence_urls(self) -> dict[str, str | list[str]]:
        """Return evidence artifact URLs as a plain dict.

        Converts the internal hashable tuple representation back to
        ``dict[str, str | list[str]]`` for callers that need a plain dict.
        """
        result: dict[str, str | list[str]] = {}
        for key, value in self.evidence_urls_raw:
            if isinstance(value, tuple):
                result[key] = list(value)
            else:
                result[key] = value
        return result

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-stable dictionary.

        Keys are sorted and values use JSON-primitive types so that
        ``json.dumps(entry.to_dict(), sort_keys=True)`` produces a
        deterministic byte string.

        Evidence fields (``last_evidence_run_id``, ``evidence_urls``) are
        omitted when they hold their default values to keep the JSON compact
        and backward-compatible with readers that predate Task 16.
        """
        d: dict[str, Any] = {
            "browsers_tested": list(self.browsers_tested),
            "covers_acs": list(self.covers_acs),
            "framework": self.framework,
            "generated_at": self.generated_at,
            "generated_by_task": self.generated_by_task,
            "generation_version": self.generation_version,
            "lane": self.lane,
            "language": self.language,
            "last_verdict": self.last_verdict,
            "operator_locked": self.operator_locked,
            "target_ref": self.target_ref,
            "test_file": self.test_file,
            "test_id": self.test_id,
        }
        # Emit evidence fields only when non-default (omit-when-empty contract)
        if self.last_evidence_run_id is not None:
            d["last_evidence_run_id"] = self.last_evidence_run_id
        if self.evidence_urls_raw:
            d["evidence_urls"] = self.evidence_urls  # property returns plain dict
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CatalogEntry:
        """Deserialise from a dictionary (e.g. parsed from JSON).

        Unknown keys are silently ignored so forward-compatible catalog files
        round-trip cleanly on older PFactory installs.

        Raises:
            CatalogError: If a required field is missing or a value is invalid.
        """
        try:
            # Defensively load evidence_urls — may be absent in pre-Task-16 catalogs
            raw_evidence_urls_dict = d.get("evidence_urls") or {}
            if not isinstance(raw_evidence_urls_dict, dict):
                raw_evidence_urls_dict = {}

            # Convert dict[str, str|list] → tuple[(str, str|tuple[str,...])]
            # so the frozen dataclass stays hashable.
            evidence_urls_raw: tuple[tuple[str, str | tuple[str, ...]], ...] = tuple(
                (k, tuple(v) if isinstance(v, list) else v)
                for k, v in raw_evidence_urls_dict.items()
            )

            return cls(
                test_id=d["test_id"],
                test_file=d["test_file"],
                framework=d["framework"],
                lane=d["lane"],
                language=d["language"],
                covers_acs=tuple(d.get("covers_acs", [])),
                generated_at=d["generated_at"],
                generated_by_task=d["generated_by_task"],
                last_verdict=d["last_verdict"],
                browsers_tested=tuple(d.get("browsers_tested", [])),
                target_ref=d.get("target_ref"),
                operator_locked=bool(d.get("operator_locked", False)),
                generation_version=int(d.get("generation_version", 1)),
                last_evidence_run_id=d.get("last_evidence_run_id") or None,
                evidence_urls_raw=evidence_urls_raw,
            )
        except KeyError as exc:
            raise CatalogError("field", f"missing required field: {exc}") from exc


# ---------------------------------------------------------------------------
# TestsCatalog
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TestsCatalog:
    """Root catalog object — the full contents of ``.pfactory/tests-catalog.json``.

    Attributes:
        version: Schema version (always ``1`` for v0.2; future migrations
            increment this).
        updated_at: ISO-8601 UTC timestamp of the last write,
            e.g. ``"2026-05-28T12:00:00Z"``.
        tests: Tuple of ``CatalogEntry`` objects, one per registered test.
            Insertion order is preserved; lookup routines respect it.
    """

    version: int
    updated_at: str
    tests: tuple[CatalogEntry, ...]

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-stable dictionary.

        Produces the canonical ``.pfactory/tests-catalog.json`` structure.
        ``json.dumps(catalog.to_dict(), sort_keys=True, indent=2)`` is
        byte-identical on repeated calls for the same catalog.
        """
        return {
            "tests": [e.to_dict() for e in self.tests],
            "updated_at": self.updated_at,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TestsCatalog:
        """Deserialise from a dictionary (e.g. parsed from JSON).

        Raises:
            CatalogError: If the structure is invalid.
        """
        try:
            version = int(d["version"])
            updated_at = d["updated_at"]
            tests_raw = d.get("tests", [])
        except KeyError as exc:
            raise CatalogError("field", f"missing required field: {exc}") from exc

        tests = tuple(CatalogEntry.from_dict(e) for e in tests_raw)
        return cls(version=version, updated_at=updated_at, tests=tests)
