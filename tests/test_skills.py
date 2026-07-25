"""Structural test suite for the ``.claude/skills/`` bundles (#29, #349).

These tests do NOT execute the skills. They assert every SKILL.md is
well-formed (valid YAML front-matter, required fields, non-stub body) and
— since #349 — that no skill re-advertises TFactory's test-generation
pipeline as a PFactory capability.

Why that guard exists: ``.claude/skills/`` is served verbatim to the portal
by ``server/routes/pfactory_skills.py`` and feeds the RFC-0019 agent-skills
manifest, so a wrong skill here is a public, machine-readable claim that
PFactory can do something it cannot. PFactory plans and governs; it does
not generate or run tests.

Run with::

    PYTHONPATH=apps/backend pytest tests/test_skills.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# Repository root: this file lives at tests/test_skills.py, so two parents up.
REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"

# The PFactory-specific skills that must exist and stay accurate. Imported
# third-party bundles (Backstage migrations, etc.) are validated structurally
# by the parametrised tests below but are not required to be present.
EXPECTED_SKILLS: tuple[str, ...] = (
    "handover-to-pfactory",
    "pfactory-watch",
    "cloud-discover",
)

# Identifiers that belong exclusively to TFactory's test-generation pipeline.
# A PFactory skill naming any of these is describing another product's
# behaviour — the #349 defect.
TFACTORY_ONLY_MARKERS: tuple[str, ...] = (
    "Gen-Functional",
    "tests-catalog.json",
    "Triager",
    "triage_report",
    "task_create_and_run",
)


def _skill_dirs() -> list[str]:
    """Every skill bundle directory name (a dir containing SKILL.md)."""
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(d.name for d in SKILLS_DIR.iterdir() if (d / "SKILL.md").is_file())


ALL_SKILLS = _skill_dirs()


def _read_skill(name: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_text) for the named skill.

    ``allowed-tools:`` (hyphenated) is normalised to ``allowed_tools`` so the
    tests can assert one canon — the portal route accepts both.
    """
    path = SKILLS_DIR / name / "SKILL.md"
    text = path.read_text()
    assert text.startswith("---\n"), f"{path}: must start with '---\\n'"
    parts = text.split("\n---\n", 1)
    assert len(parts) == 2, f"{path}: missing closing '---' delimiter"
    fm = yaml.safe_load(parts[0][4:])  # strip leading '---\n'
    assert isinstance(fm, dict), f"{path}: front-matter must be a YAML mapping"
    if "allowed-tools" in fm and "allowed_tools" not in fm:
        fm["allowed_tools"] = fm["allowed-tools"]
    return fm, parts[1].lstrip("\n")


# ---------------------------------------------------------------------------
# Structural assertions over every bundle in the directory
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("skill", ALL_SKILLS)
def test_frontmatter_names_the_directory(skill: str) -> None:
    """Front-matter ``name:`` must match the skill directory name."""
    fm, _body = _read_skill(skill)
    assert fm.get("name") == skill, (
        f"{skill}: front-matter name {fm.get('name')!r} doesn't match directory"
    )


@pytest.mark.parametrize("skill", ALL_SKILLS)
def test_description_is_non_trivial(skill: str) -> None:
    """Front-matter ``description:`` must be present and non-trivial."""
    fm, _body = _read_skill(skill)
    desc = fm.get("description", "")
    assert isinstance(desc, str) and len(desc.strip()) >= 20, (
        f"{skill}: description is empty or too short (got {desc!r})"
    )


@pytest.mark.parametrize("skill", ALL_SKILLS)
def test_body_is_not_a_stub(skill: str) -> None:
    """Skill body (after front-matter) must be at least 500 chars."""
    _fm, body = _read_skill(skill)
    assert len(body) >= 500, f"{skill}: body is only {len(body)} chars (a stub)"


# ---------------------------------------------------------------------------
# The PFactory skills specifically
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("skill", EXPECTED_SKILLS)
def test_expected_pfactory_skill_exists(skill: str) -> None:
    """The PFactory-specific bundles must be present."""
    assert (SKILLS_DIR / skill / "SKILL.md").is_file(), f"missing bundle: {skill}"


@pytest.mark.parametrize("skill", EXPECTED_SKILLS)
def test_expected_skill_declares_when_to_use_and_tools(skill: str) -> None:
    """Each PFactory skill must declare triggers and a non-empty tool list."""
    fm, _body = _read_skill(skill)
    when = fm.get("when_to_use")
    if isinstance(when, list):
        assert when and all(isinstance(i, str) and i.strip() for i in when), (
            f"{skill}: when_to_use list is empty or has blank entries"
        )
    else:
        assert isinstance(when, str) and len(when.strip()) >= 20, (
            f"{skill}: when_to_use must be a list[str] or a non-trivial string"
        )
    tools = fm.get("allowed_tools")
    assert isinstance(tools, list) and tools, f"{skill}: allowed_tools is missing/empty"


@pytest.mark.parametrize("skill", ALL_SKILLS)
def test_no_skill_advertises_tfactory_test_generation(skill: str) -> None:
    """#349: no skill may describe TFactory's test-generation pipeline.

    ``.claude/skills/`` is published as PFactory's capability manifest, so a
    skill naming Gen-Functional / the Triager / the tests catalog claims a
    capability PFactory does not have.
    """
    fm, body = _read_skill(skill)
    haystack = f"{fm.get('description', '')}\n{fm.get('when_to_use', '')}\n{body}"
    hits = [m for m in TFACTORY_ONLY_MARKERS if m in haystack]
    assert not hits, (
        f"{skill}: names TFactory-only identifiers {hits} — PFactory plans and "
        f"governs, it does not generate or run tests (#349)"
    )


def test_handover_skill_describes_the_planning_pipeline() -> None:
    """The handover skill must drive the plan_* surface, not a test pipeline."""
    fm, body = _read_skill("handover-to-pfactory")
    for tool in ("plan_ingest", "plan_process", "plan_approve"):
        assert tool in body, f"handover-to-pfactory body must reference {tool}"
    assert any("plan_" in t for t in fm["allowed_tools"]), (
        "handover-to-pfactory must declare the mcp__pfactory__plan_* tools"
    )
