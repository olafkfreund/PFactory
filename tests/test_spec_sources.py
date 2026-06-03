"""Tests for generic acceptance-criteria ingestion (#40)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from spec_sources import (  # noqa: E402
    SpecFormat,
    SpecSourceError,
    detect_format,
    ingest,
    ingest_file,
    parse_ears,
    parse_gherkin,
    parse_markdown,
    write_spec_markdown,
)

# ── format detection ───────────────────────────────────────────────────

def test_detect_gherkin_by_filename():
    assert detect_format("anything", filename="login.feature") is SpecFormat.GHERKIN


def test_detect_gherkin_by_content():
    text = "Feature: Login\n  Scenario: ok\n    Given x\n"
    assert detect_format(text) is SpecFormat.GHERKIN


def test_detect_ears_by_shall_majority():
    text = (
        "The system shall reject expired tokens.\n"
        "When a user logs in, the system shall create a session.\n"
    )
    assert detect_format(text) is SpecFormat.EARS


def test_detect_markdown_default():
    text = "# Feature\n\n## Acceptance Criteria\n- user can log in\n"
    assert detect_format(text) is SpecFormat.MARKDOWN


# ── markdown ───────────────────────────────────────────────────────────

def test_parse_markdown_acceptance_section():
    text = (
        "# Login feature\n\n"
        "## Acceptance Criteria\n"
        "- User can log in with valid credentials\n"
        "- Login rejects an expired token\n\n"
        "## Notes\n- not a criterion\n"
    )
    spec = parse_markdown(text)
    assert spec.title == "Login feature"
    assert [c.id for c in spec.criteria] == ["AC#1", "AC#2"]
    assert spec.criteria[0].text == "User can log in with valid credentials"
    # the Notes bullet is outside the acceptance section
    assert all("not a criterion" not in c.text for c in spec.criteria)


def test_parse_markdown_numbered_under_requirements():
    text = "# Spec\n## Requirements\n1. first\n2) second\n"
    spec = parse_markdown(text)
    assert [c.text for c in spec.criteria] == ["first", "second"]


def test_parse_markdown_inline_ac_fallback():
    text = "Some intro.\nAC#1: must do X\nAC2. must do Y\n"
    spec = parse_markdown(text)
    assert [c.text for c in spec.criteria] == ["must do X", "must do Y"]


def test_parse_markdown_no_criteria_raises():
    with pytest.raises(SpecSourceError):
        parse_markdown("# Title\n\nJust prose, no criteria.\n")


# ── gherkin ────────────────────────────────────────────────────────────

GHERKIN = """\
Feature: User login
  As a user I want to log in.

  Scenario: valid credentials
    Given a registered user
    When they submit valid credentials
    Then a session is created

  Scenario: expired token
    Given an expired token
    Then login is rejected
"""


def test_parse_gherkin_one_ac_per_scenario():
    spec = parse_gherkin(GHERKIN)
    assert spec.source_format is SpecFormat.GHERKIN
    assert spec.title == "User login"
    assert len(spec.criteria) == 2
    assert spec.criteria[0].text.startswith("valid credentials — given a registered user")
    assert "session is created" in spec.criteria[0].text
    assert spec.criteria[1].text.startswith("expired token —")


def test_parse_gherkin_no_scenarios_raises():
    with pytest.raises(SpecSourceError):
        parse_gherkin("Feature: empty\n")


# ── EARS ───────────────────────────────────────────────────────────────

EARS = """\
# Auth requirements
The system shall reject expired tokens.
When a user submits valid credentials, the system shall create a session.
While offline, the app shall queue requests.
This line has no keyword and is ignored.
"""


def test_parse_ears_collects_shall_lines():
    spec = parse_ears(EARS)
    assert spec.source_format is SpecFormat.EARS
    assert len(spec.criteria) == 3
    assert "reject expired tokens" in spec.criteria[0].text
    assert all("ignored" not in c.text for c in spec.criteria)


def test_parse_ears_strips_bullets():
    spec = parse_ears("- The system shall log audits.\n")
    assert spec.criteria[0].text == "The system shall log audits."


def test_parse_ears_no_shall_raises():
    with pytest.raises(SpecSourceError):
        parse_ears("# reqs\nnothing here.\n")


# ── ingest + render + write ────────────────────────────────────────────

def test_ingest_autodetects_and_normalises():
    spec = ingest(GHERKIN, filename="x.feature")
    assert spec.source_format is SpecFormat.GHERKIN
    md = spec.to_markdown()
    assert "## Acceptance Criteria" in md
    assert "**AC#1:**" in md
    assert "Ingested from a gherkin source" in md


def test_ingest_empty_raises():
    with pytest.raises(SpecSourceError):
        ingest("   \n  ")


def test_ingest_force_format_overrides_detection():
    # content looks like markdown but we force EARS (has a shall line)
    spec = ingest("The system shall do X.\n", fmt=SpecFormat.EARS)
    assert spec.source_format is SpecFormat.EARS


def test_to_markdown_roundtrips_through_markdown_parser():
    spec = parse_ears(EARS)
    md = spec.to_markdown()
    # the rendered canonical markdown is itself parseable
    reparsed = parse_markdown(md)
    assert len(reparsed.criteria) == len(spec.criteria)


def test_write_spec_markdown_drops_pipeline_file(tmp_path):
    spec = ingest(GHERKIN, filename="x.feature")
    ctx = tmp_path / "context"
    dst = write_spec_markdown(spec, ctx)
    assert dst == ctx / "aifactory_spec.md"
    assert dst.exists()
    assert "## Acceptance Criteria" in dst.read_text()


def test_ingest_file_uses_extension(tmp_path):
    f = tmp_path / "login.feature"
    f.write_text(GHERKIN)
    spec = ingest_file(f)
    assert spec.source_format is SpecFormat.GHERKIN
