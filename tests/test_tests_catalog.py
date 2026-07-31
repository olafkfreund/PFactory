"""Test suite for tests_catalog package — Task 3 (#19 commit 5).

Covers:
- CatalogEntry round-trip (minimal and fully-populated)
- validate_lane accepts each v0.2 lane; rejects unknown with CatalogError
- TestsCatalog round-trip including empty tests tuple
- lookup_by_ac — exact match (single hit, multiple hits), prefix match,
  no-match, candidate without ':' falls through to empty
- load_catalog returns None for missing file
- load_catalog raises CatalogError("file", ...) for malformed JSON
- load_catalog parses a real catalog
- save_catalog creates .pfactory/ dir if absent
- Atomic write: no .json.tmp left on disk after save_catalog
- Byte-identical round-trip: save same catalog twice → bytes match
- migrate_v0_1_workspace end-to-end with synthetic spec_dir
- Migration with existing entries: dedup by test_id
- to_dict() emits sorted keys

25+ test functions total.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from tests_catalog import (
    CatalogEntry,
    CatalogError,
    TestsCatalog,
    load_catalog,
    lookup_by_ac,
    migrate_v0_1_workspace,
    save_catalog,
)
from tests_catalog.schema import validate_lane

# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------


def _minimal_entry(**overrides) -> CatalogEntry:
    """Build the smallest valid CatalogEntry, applying any overrides."""
    defaults = {
        "test_id": "t1",
        "test_file": "tests/test_auth.py",
        "framework": "pytest",
        "lane": "unit",
        "language": "python",
        "covers_acs": (),
        "generated_at": "2026-05-28T10:00:00Z",
        "generated_by_task": "042-auth",
        "last_verdict": "accept",
    }
    defaults.update(overrides)
    return CatalogEntry(**defaults)


def _full_entry(**overrides) -> CatalogEntry:
    """Build a fully-populated CatalogEntry."""
    defaults = {
        "test_id": "ac1-login-flow",
        "test_file": "tests/e2e/login-flow.spec.ts",
        "framework": "playwright",
        "lane": "browser",
        "language": "typescript",
        "covers_acs": ("AC#1: User can log in with valid credentials",),
        "generated_at": "2026-05-28T10:30:00Z",
        "generated_by_task": "042-session-expiry",
        "last_verdict": "accept",
        "browsers_tested": ("chromium", "firefox"),
        "target_ref": "web-staging",
        "operator_locked": False,
        "generation_version": 3,
    }
    defaults.update(overrides)
    return CatalogEntry(**defaults)


def _empty_catalog() -> TestsCatalog:
    return TestsCatalog(version=1, updated_at="2026-05-28T12:00:00Z", tests=())


def _catalog_with(*entries: CatalogEntry) -> TestsCatalog:
    return TestsCatalog(version=1, updated_at="2026-05-28T12:00:00Z", tests=tuple(entries))


# ---------------------------------------------------------------------------
# 1. CatalogEntry round-trip
# ---------------------------------------------------------------------------


def test_catalog_entry_minimal_round_trip():
    """Minimal CatalogEntry survives to_dict → from_dict."""
    e = _minimal_entry()
    assert CatalogEntry.from_dict(e.to_dict()) == e


def test_catalog_entry_fully_populated_round_trip():
    """Fully-populated CatalogEntry survives to_dict → from_dict."""
    e = _full_entry()
    assert CatalogEntry.from_dict(e.to_dict()) == e


def test_catalog_entry_to_dict_has_all_fields():
    """to_dict() emits every field including defaults."""
    e = _minimal_entry()
    d = e.to_dict()
    for field in (
        "test_id",
        "test_file",
        "framework",
        "lane",
        "language",
        "covers_acs",
        "generated_at",
        "generated_by_task",
        "last_verdict",
        "browsers_tested",
        "target_ref",
        "operator_locked",
        "generation_version",
    ):
        assert field in d, f"Missing field: {field}"


def test_catalog_entry_covers_acs_as_list_in_dict():
    """covers_acs is serialised as a list (not a tuple) in the dict."""
    e = _minimal_entry(covers_acs=("AC#1: foo", "AC#2: bar"))
    d = e.to_dict()
    assert isinstance(d["covers_acs"], list)
    assert d["covers_acs"] == ["AC#1: foo", "AC#2: bar"]


def test_catalog_entry_from_dict_ignores_unknown_keys():
    """Extra keys in a dict don't cause from_dict to raise."""
    d = _minimal_entry().to_dict()
    d["future_field"] = "future_value"
    # Should not raise
    e = CatalogEntry.from_dict(d)
    assert e.test_id == "t1"


def test_catalog_entry_from_dict_missing_required_raises():
    """from_dict raises CatalogError when a required field is absent."""
    d = _minimal_entry().to_dict()
    del d["test_id"]
    with pytest.raises(CatalogError) as exc_info:
        CatalogEntry.from_dict(d)
    assert exc_info.value.field == "field"


def test_catalog_entry_is_frozen():
    """CatalogEntry is frozen (immutable)."""
    e = _minimal_entry()
    with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
        e.test_id = "new_id"  # type: ignore[misc]


def test_catalog_entry_is_hashable():
    """Frozen CatalogEntry can be used as a dict key / in a set."""
    e = _minimal_entry()
    s = {e}
    assert e in s


# ---------------------------------------------------------------------------
# 2. validate_lane
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lane", ["unit", "browser", "api", "integration", "mutation"])
def test_validate_lane_accepts_all_v02_lanes(lane: str):
    """validate_lane accepts each of the 5 valid v0.2 lane values."""
    assert validate_lane(lane) == lane


def test_validate_lane_rejects_unknown():
    """validate_lane raises CatalogError for an unknown lane value."""
    with pytest.raises(CatalogError) as exc_info:
        validate_lane("functional")
    assert exc_info.value.field == "lane"
    assert "functional" in exc_info.value.reason


def test_validate_lane_rejects_empty_string():
    """validate_lane raises CatalogError for an empty string."""
    with pytest.raises(CatalogError):
        validate_lane("")


def test_catalog_entry_rejects_invalid_lane():
    """CatalogEntry.__post_init__ raises CatalogError for an invalid lane."""
    with pytest.raises(CatalogError) as exc_info:
        _minimal_entry(lane="invalid")
    assert exc_info.value.field == "lane"


def test_catalog_entry_rejects_invalid_verdict():
    """CatalogEntry.__post_init__ raises CatalogError for an invalid verdict."""
    with pytest.raises(CatalogError) as exc_info:
        _minimal_entry(last_verdict="unknown_verdict")
    assert exc_info.value.field == "last_verdict"


# ---------------------------------------------------------------------------
# 3. TestsCatalog round-trip
# ---------------------------------------------------------------------------


def test_tests_catalog_empty_round_trip():
    """TestsCatalog with no entries round-trips cleanly."""
    cat = _empty_catalog()
    assert TestsCatalog.from_dict(cat.to_dict()) == cat


def test_tests_catalog_with_entries_round_trip():
    """TestsCatalog with entries round-trips cleanly."""
    cat = _catalog_with(_minimal_entry(), _full_entry())
    assert TestsCatalog.from_dict(cat.to_dict()) == cat


def test_tests_catalog_is_frozen():
    """TestsCatalog is frozen (immutable)."""
    cat = _empty_catalog()
    with pytest.raises(Exception):
        cat.version = 2  # type: ignore[misc]


def test_to_dict_emits_sorted_keys():
    """to_dict() produces dicts whose keys are in sorted order.

    This guarantees that json.dumps(cat.to_dict(), sort_keys=True) is
    byte-identical for the same catalog across two calls.
    """
    e = _full_entry()
    entry_dict = e.to_dict()
    entry_keys = list(entry_dict.keys())
    assert entry_keys == sorted(entry_keys), f"to_dict() keys are not sorted: {entry_keys}"

    cat = _catalog_with(e)
    cat_dict = cat.to_dict()
    cat_keys = list(cat_dict.keys())
    assert cat_keys == sorted(cat_keys)


# ---------------------------------------------------------------------------
# 4. load_catalog
# ---------------------------------------------------------------------------


def test_load_catalog_returns_none_for_missing_file(tmp_path: Path):
    """load_catalog returns None when the catalog file does not exist."""
    assert load_catalog(tmp_path) is None


def test_load_catalog_raises_for_malformed_json(tmp_path: Path):
    """load_catalog raises CatalogError("file", ...) for malformed JSON."""
    pfactory_dir = tmp_path / ".pfactory"
    pfactory_dir.mkdir()
    (pfactory_dir / "tests-catalog.json").write_text("{not valid json", encoding="utf-8")

    with pytest.raises(CatalogError) as exc_info:
        load_catalog(tmp_path)
    assert exc_info.value.field == "file"
    assert "malformed" in exc_info.value.reason.lower()


def test_load_catalog_parses_real_catalog(tmp_path: Path):
    """load_catalog parses a catalog written by save_catalog."""
    cat = _catalog_with(_minimal_entry(), _full_entry())
    save_catalog(tmp_path, cat)
    loaded = load_catalog(tmp_path)
    assert loaded == cat


# ---------------------------------------------------------------------------
# 5. save_catalog — atomic write + dir creation
# ---------------------------------------------------------------------------


def test_save_catalog_creates_pfactory_dir(tmp_path: Path):
    """save_catalog creates .pfactory/ if it does not exist."""
    cat = _empty_catalog()
    pfactory_dir = tmp_path / ".pfactory"
    assert not pfactory_dir.exists()
    save_catalog(tmp_path, cat)
    assert pfactory_dir.exists()
    assert (pfactory_dir / "tests-catalog.json").exists()


def test_save_catalog_returns_final_path(tmp_path: Path):
    """save_catalog returns the Path of the written file."""
    path = save_catalog(tmp_path, _empty_catalog())
    assert path == tmp_path / ".pfactory" / "tests-catalog.json"
    assert path.exists()


def test_save_catalog_no_tmp_file_left_on_disk(tmp_path: Path):
    """After save_catalog, the .json.tmp temp file is cleaned up."""
    save_catalog(tmp_path, _empty_catalog())
    tmp_file = tmp_path / ".pfactory" / "tests-catalog.json.tmp"
    assert not tmp_file.exists(), ".json.tmp was not cleaned up after save"


def test_save_catalog_byte_identical_round_trip(tmp_path: Path):
    """Saving the same catalog twice produces byte-identical files."""
    cat = _catalog_with(_minimal_entry(), _full_entry())
    p = save_catalog(tmp_path, cat)
    bytes_a = p.read_bytes()
    save_catalog(tmp_path, cat)
    bytes_b = p.read_bytes()
    assert bytes_a == bytes_b, "Second save produced different bytes"


# ---------------------------------------------------------------------------
# 6. lookup_by_ac
# ---------------------------------------------------------------------------


def test_lookup_exact_match_single():
    """Exact match returns the single matching entry."""
    e = _minimal_entry(covers_acs=("AC#1: User can log in",))
    cat = _catalog_with(e)
    result = lookup_by_ac(cat, "AC#1: User can log in")
    assert len(result) == 1
    assert result[0] is e


def test_lookup_exact_match_multiple_entries_same_ac():
    """When two entries share an exact AC, both are returned."""
    e1 = _minimal_entry(test_id="t1", covers_acs=("AC#1: foo",))
    e2 = _minimal_entry(test_id="t2", covers_acs=("AC#1: foo",))
    cat = _catalog_with(e1, e2)
    result = lookup_by_ac(cat, "AC#1: foo")
    assert len(result) == 2
    assert result[0] is e1
    assert result[1] is e2


def test_lookup_exact_wins_over_prefix():
    """Exact match tier returns before prefix is checked."""
    # e1 has exact AC string; e2 only has a prefix match
    e1 = _minimal_entry(test_id="t1", covers_acs=("AC#1: login expiry",))
    e2 = _minimal_entry(test_id="t2", covers_acs=("AC#1: session refresh",))
    cat = _catalog_with(e1, e2)
    result = lookup_by_ac(cat, "AC#1: login expiry")
    # Only exact match returned — e2 would match on prefix but we stop at step 1
    assert len(result) == 1
    assert result[0] is e1


def test_lookup_prefix_match():
    """AC-ID prefix match finds entries with same AC-ID but different text."""
    e = _minimal_entry(covers_acs=("AC#1: login flow",))
    cat = _catalog_with(e)
    # Different text after the colon, same AC-ID
    result = lookup_by_ac(cat, "AC#1: login expiry")
    assert len(result) == 1
    assert result[0] is e


def test_lookup_prefix_match_multiple():
    """Prefix match returns all entries that share the AC-ID."""
    e1 = _minimal_entry(test_id="t1", covers_acs=("AC#1: login flow",))
    e2 = _minimal_entry(test_id="t2", covers_acs=("AC#1: session token",))
    cat = _catalog_with(e1, e2)
    result = lookup_by_ac(cat, "AC#1: something new")
    assert len(result) == 2


def test_lookup_no_match_returns_empty():
    """Returns [] when no entry matches at any tier."""
    e = _minimal_entry(covers_acs=("AC#1: login flow",))
    cat = _catalog_with(e)
    result = lookup_by_ac(cat, "AC#99: completely different")
    assert result == []


def test_lookup_empty_catalog_returns_empty():
    """Empty catalog always returns []."""
    result = lookup_by_ac(_empty_catalog(), "AC#1: anything")
    assert result == []


def test_lookup_candidate_without_colon_falls_to_no_match():
    """A candidate with no ':' has an empty ac_id; prefix step is skipped."""
    e = _minimal_entry(covers_acs=("no colon here",))
    cat = _catalog_with(e)
    # candidate has no colon — ac_id is "no colon here" stripped, which is
    # non-empty, but won't match the stored entry's covers_acs exactly at
    # step 1, and the startswith check in step 2 won't match either unless
    # the stored value starts with the full candidate text.
    result = lookup_by_ac(cat, "unrelated text no colon")
    assert result == []


def test_lookup_candidate_colon_only_ac_id():
    """Candidate 'AC#1:' with nothing after colon uses 'AC#1' as prefix."""
    e = _minimal_entry(covers_acs=("AC#1: something",))
    cat = _catalog_with(e)
    result = lookup_by_ac(cat, "AC#1: ")
    # This is an exact match check first — "AC#1: " is not in covers_acs.
    # Then prefix: ac_id = "AC#1", which matches the stored "AC#1: something".
    assert len(result) == 1
    assert result[0] is e


def test_lookup_preserves_insertion_order():
    """Results respect catalog.tests insertion order, not a sorted order."""
    e1 = _minimal_entry(test_id="zzz", covers_acs=("AC#5: foo",))
    e2 = _minimal_entry(test_id="aaa", covers_acs=("AC#5: bar",))
    cat = _catalog_with(e1, e2)
    result = lookup_by_ac(cat, "AC#5: baz")  # prefix match
    assert result[0] is e1
    assert result[1] is e2


# ---------------------------------------------------------------------------
# 7. migrate_v0_1_workspace
# ---------------------------------------------------------------------------


def _make_spec_dir(
    tmp_path: Path,
    *,
    test_files: list[str],
    plan: dict | None = None,
    verdicts: dict | None = None,
) -> Path:
    """Create a synthetic v0.1 spec_dir under tmp_path."""
    spec_dir = tmp_path / "042-session-expiry"
    spec_dir.mkdir()
    tests_dir = spec_dir / "tests"
    tests_dir.mkdir()
    for fname in test_files:
        (tests_dir / fname).write_text(
            f"# pytest test file: {fname}\ndef test_placeholder(): pass\n",
            encoding="utf-8",
        )
    if plan:
        (spec_dir / "test_plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    if verdicts:
        findings_dir = spec_dir / "findings"
        findings_dir.mkdir()
        (findings_dir / "verdicts.json").write_text(
            json.dumps(verdicts, indent=2), encoding="utf-8"
        )
    return spec_dir


def test_migration_basic(tmp_path: Path):
    """migrate_v0_1_workspace produces one entry per test file."""
    spec_dir = _make_spec_dir(
        tmp_path,
        test_files=["test_login_expiry.py", "test_refresh_session.py"],
    )
    cat = migrate_v0_1_workspace(spec_dir, _empty_catalog())
    assert len(cat.tests) == 2
    ids = {e.test_id for e in cat.tests}
    assert "test-login-expiry" in ids
    assert "test-refresh-session" in ids


def test_migration_entry_fields(tmp_path: Path):
    """Migrated entries have the correct v0.1 constant fields."""
    spec_dir = _make_spec_dir(
        tmp_path,
        test_files=["test_session_expiry.py"],
    )
    cat = migrate_v0_1_workspace(spec_dir, _empty_catalog())
    e = cat.tests[0]
    assert e.framework == "pytest"
    assert e.lane == "unit"
    assert e.language == "python"
    assert e.test_file == "tests/test_session_expiry.py"
    assert e.generated_by_task == "042-session-expiry"
    assert e.target_ref is None
    assert e.operator_locked is False
    assert e.generation_version == 1


def test_migration_covers_acs_from_plan(tmp_path: Path):
    """covers_acs is resolved from the subtask rationale in test_plan.json."""
    plan = {
        "phases": [
            {
                "subtasks": [
                    {
                        "id": "s1",
                        "rationale": "AC#1: Session expires after timeout",
                        "files_to_create": ["tests/test_expiry.py"],
                    }
                ]
            }
        ]
    }
    spec_dir = _make_spec_dir(
        tmp_path,
        test_files=["test_expiry.py"],
        plan=plan,
    )
    cat = migrate_v0_1_workspace(spec_dir, _empty_catalog())
    assert cat.tests[0].covers_acs == ("AC#1: Session expires after timeout",)


def test_migration_covers_acs_empty_when_no_plan(tmp_path: Path):
    """covers_acs is () when test_plan.json is absent."""
    spec_dir = _make_spec_dir(tmp_path, test_files=["test_foo.py"])
    cat = migrate_v0_1_workspace(spec_dir, _empty_catalog())
    assert cat.tests[0].covers_acs == ()


def test_migration_last_verdict_from_verdicts_json(tmp_path: Path):
    """last_verdict is resolved from findings/verdicts.json."""
    verdicts = [
        {"test_id": "test-expiry", "verdict": "flag"},
    ]
    spec_dir = _make_spec_dir(
        tmp_path,
        test_files=["test_expiry.py"],
        verdicts=verdicts,
    )
    cat = migrate_v0_1_workspace(spec_dir, _empty_catalog())
    assert cat.tests[0].last_verdict == "flag"


def test_migration_last_verdict_is_flag_when_unknown(tmp_path: Path):
    """An unknowable verdict is 'flag', never 'accept' (Factory#431).

    This test previously asserted 'accept' -- it encoded the defect. `last_verdict`
    means "most recent EVALUATOR verdict", so defaulting to accept recorded the
    Evaluator as having passed a test it never saw: an unknown turned into a
    confident positive, which is the shape Factory#431 exists to remove.

    'flag' needs no schema change and is the existing "a human should look" token.
    The Triager groups ("accept", "flag") for regeneration, so nothing is dropped.
    """
    spec_dir = _make_spec_dir(tmp_path, test_files=["test_foo.py"])
    cat = migrate_v0_1_workspace(spec_dir, _empty_catalog())
    assert cat.tests[0].last_verdict == "flag"


def test_migration_unrecognised_verdict_is_flag(tmp_path: Path):
    """A verdict the catalog does not know is also not a pass."""
    spec_dir = _make_spec_dir(
        tmp_path,
        test_files=["test_expiry.py"],
        verdicts=[{"test_id": "test-expiry", "verdict": "banana"}],
    )
    cat = migrate_v0_1_workspace(spec_dir, _empty_catalog())
    assert cat.tests[0].last_verdict == "flag"


def test_migration_dedup_by_test_id(tmp_path: Path):
    """Existing entries with the same test_id win over migrated ones."""
    existing_entry = _minimal_entry(
        test_id="test-expiry",
        test_file="tests/test_expiry.py",
        framework="pytest",
        covers_acs=("existing AC",),
    )
    existing_catalog = _catalog_with(existing_entry)

    spec_dir = _make_spec_dir(tmp_path, test_files=["test_expiry.py"])
    cat = migrate_v0_1_workspace(spec_dir, existing_catalog)

    # Only the existing entry should be there — no duplicate
    expiry_entries = [e for e in cat.tests if e.test_id == "test-expiry"]
    assert len(expiry_entries) == 1
    assert expiry_entries[0].covers_acs == ("existing AC",)


def test_migration_appends_to_existing_entries(tmp_path: Path):
    """Migrated entries are appended after existing catalog entries."""
    existing = _minimal_entry(test_id="existing-test")
    existing_catalog = _catalog_with(existing)

    spec_dir = _make_spec_dir(tmp_path, test_files=["test_new_feature.py"])
    cat = migrate_v0_1_workspace(spec_dir, existing_catalog)

    assert len(cat.tests) == 2
    assert cat.tests[0] is existing  # existing entry first
    assert cat.tests[1].test_id == "test-new-feature"  # migrated entry appended


def test_migration_empty_tests_dir(tmp_path: Path):
    """Migration on a spec_dir with no test files returns the catalog unchanged."""
    spec_dir = _make_spec_dir(tmp_path, test_files=[])
    cat = migrate_v0_1_workspace(spec_dir, _empty_catalog())
    assert len(cat.tests) == 0


def test_migration_missing_tests_dir(tmp_path: Path):
    """Migration on a spec_dir with no tests/ subdirectory returns unchanged catalog."""
    spec_dir = tmp_path / "spec-no-tests"
    spec_dir.mkdir()
    # No tests/ subdirectory at all
    cat = migrate_v0_1_workspace(spec_dir, _empty_catalog())
    assert len(cat.tests) == 0


def test_migration_does_not_write_to_disk(tmp_path: Path):
    """migrate_v0_1_workspace is pure — no files written to spec_dir."""
    spec_dir = _make_spec_dir(tmp_path, test_files=["test_x.py"])
    files_before = set(spec_dir.rglob("*"))
    migrate_v0_1_workspace(spec_dir, _empty_catalog())
    files_after = set(spec_dir.rglob("*"))
    assert files_before == files_after, "Migration wrote to spec_dir"


def test_migration_preserves_catalog_version(tmp_path: Path):
    """Migrated catalog keeps the same version as the input catalog."""
    existing = TestsCatalog(version=1, updated_at="2026-05-01T00:00:00Z", tests=())
    spec_dir = _make_spec_dir(tmp_path, test_files=["test_x.py"])
    cat = migrate_v0_1_workspace(spec_dir, existing)
    assert cat.version == 1
    assert cat.updated_at == "2026-05-01T00:00:00Z"
