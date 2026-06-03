"""Tests for the per-language per-lane tool registry — Task 4 (#5),
restructured for v0.2 Task 0 (#16).

v0.2 registry covers Python (UNIT=pytest, MVP) + TypeScript (UNIT=jest +
BROWSER=playwright, both v0.2 MVP). Other languages (Java, .NET, Go, Rust,
Ruby) have placeholder rows with None across all lanes (v0.3+ ramp).
"""

from __future__ import annotations

import pytest
from tools.runners.lang_registry import (
    ToolSpec,
    UnsupportedLanguageError,
    get_tool_for_lane,
    languages_supporting_lane,
)

# ── Python entries ─────────────────────────────────────────────────────


def test_python_unit_is_pytest_and_mvp_available():
    spec = get_tool_for_lane("python", "unit")
    assert spec is not None
    assert spec.binary == "pytest"
    assert spec.available_at_mvp is True


def test_python_browser_is_playwright_python():
    spec = get_tool_for_lane("python", "browser")
    assert spec is not None
    assert "playwright" in spec.binary.lower()


def test_python_mutation_is_mutmut():
    spec = get_tool_for_lane("python", "mutation")
    assert spec is not None
    assert spec.binary == "mutmut"


def test_python_api_lane_present():
    spec = get_tool_for_lane("python", "api")
    assert spec is not None


def test_python_integration_lane_present():
    spec = get_tool_for_lane("python", "integration")
    assert spec is not None


# ── TypeScript entries (v0.2 ramp) ─────────────────────────────────────


def test_typescript_unit_is_jest_and_mvp_available():
    spec = get_tool_for_lane("typescript", "unit")
    assert spec is not None
    assert spec.binary == "jest"
    assert spec.available_at_mvp is True


def test_typescript_browser_is_playwright_and_mvp_available():
    spec = get_tool_for_lane("typescript", "browser")
    assert spec is not None
    assert "playwright" in spec.binary.lower()
    assert spec.available_at_mvp is True


def test_typescript_api_lane_present_but_not_mvp():
    spec = get_tool_for_lane("typescript", "api")
    assert spec is not None
    assert spec.available_at_mvp is False  # v0.3+ ramp


def test_typescript_mutation_is_stryker():
    spec = get_tool_for_lane("typescript", "mutation")
    assert spec is not None
    assert "stryker" in spec.binary.lower()


# ── Phase-2/3+ language placeholders ───────────────────────────────────


def test_java_csharp_have_no_entries_yet():
    """v0.3 ramp — placeholders only at v0.2."""
    for lang in ("java", "csharp"):
        for lane in ("unit", "browser", "api", "integration", "mutation"):
            assert get_tool_for_lane(lang, lane) is None, f"{lang}/{lane}"


def test_go_rust_ruby_have_no_entries_yet():
    """v0.4+ ramp — placeholders only at v0.2."""
    for lang in ("go", "rust", "ruby"):
        for lane in ("unit", "browser", "api", "integration", "mutation"):
            assert get_tool_for_lane(lang, lane) is None, f"{lang}/{lane}"


# ── Error paths ────────────────────────────────────────────────────────


def test_unsupported_language_raises():
    with pytest.raises(UnsupportedLanguageError):
        get_tool_for_lane("brainfuck", "unit")


def test_unknown_lane_returns_none_for_known_language():
    """Unknown lane on known language is a soft None, not an exception."""
    assert get_tool_for_lane("python", "telepathy") is None


def test_deprecated_v01_lane_returns_none():
    """Old lane names (functional/sast/dast/fuzz) are not registry keys.
    Callers use _parse_lane_str or _DEPRECATED_V01_ALIASES to remap upstream."""
    assert get_tool_for_lane("python", "functional") is None
    assert get_tool_for_lane("python", "sast") is None
    assert get_tool_for_lane("python", "dast") is None
    assert get_tool_for_lane("python", "fuzz") is None


# ── languages_supporting_lane ─────────────────────────────────────────


def test_languages_supporting_lane_mvp_only_for_unit():
    """v0.2 MVP: Python + TypeScript both have unit lane available."""
    assert set(languages_supporting_lane("unit", mvp_only=True)) == {
        "python", "typescript",
    }


def test_languages_supporting_lane_mvp_only_for_browser():
    """v0.2 MVP: only TypeScript has the browser lane (Playwright TS)."""
    assert languages_supporting_lane("browser", mvp_only=True) == ["typescript"]


def test_languages_supporting_lane_unfiltered_for_unit():
    """Unfiltered: Python + TypeScript both have unit registrations."""
    assert set(languages_supporting_lane("unit", mvp_only=False)) == {
        "python", "typescript",
    }


def test_languages_supporting_lane_mvp_only_empty_for_v03_lanes():
    """API, integration, mutation are v0.3+ — not MVP available in v0.2."""
    for lane in ("api", "integration", "mutation"):
        # MVP-only filter excludes non-MVP entries
        assert languages_supporting_lane(lane, mvp_only=True) == [], (
            f"{lane!r} should have no MVP-available languages in v0.2"
        )


# ── ToolSpec immutability ───────────────────────────────────────────────


def test_tool_spec_is_frozen():
    """Registry entries are immutable — accidental mutation should error."""
    spec = get_tool_for_lane("python", "unit")
    with pytest.raises((AttributeError, Exception)):
        spec.binary = "nope"  # type: ignore[misc]
