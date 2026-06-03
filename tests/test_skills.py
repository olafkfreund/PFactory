"""Structural test suite for the Task 13 (#29) skill bundles.

These tests do NOT execute the skills — they assert the SKILL.md files
are well-formed (valid YAML front-matter, required fields, required
allowed_tools, non-stub body content) and that the corresponding slash
commands exist and reference their skill. They also assert the
``handover-to-pfactory`` skill has been updated to use the v0.2 mental
model (5-lane spine, ``.pfactory.yml``, ``.pfactory/tests-catalog.json``,
polyglot ``(language, framework)`` per subtask).

Run with::

    PYTHONPATH=apps/backend pytest tests/test_skills.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

# Repository root: this file lives at tests/test_skills.py, so two parents up.
REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"
COMMANDS_DIR = REPO_ROOT / ".claude" / "commands"

# The four skill bundles covered by Task 13 — three new + one updated.
EXPECTED_SKILLS: tuple[str, ...] = (
    "pfactory-init",
    "pfactory-add-test",
    "pfactory-from-template",
    "handover-to-pfactory",
)

# Three slash commands wrapping the three new skills (handover already
# had its own command historically; Task 13 does not introduce a new
# slash command for it).
EXPECTED_COMMANDS: tuple[str, ...] = (
    "pfactory-init",
    "pfactory-add-test",
    "pfactory-from-template",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_skill(name: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_text) for the named skill.

    Parses the YAML front-matter delimited by ``---`` lines at the top of
    ``SKILL.md`` and returns the body text after the closing delimiter.
    Skills with the ``allowed-tools:`` (hyphenated) key — the v0.1 form —
    are normalised to ``allowed_tools`` so the tests can assert one canon.
    """
    path = SKILLS_DIR / name / "SKILL.md"
    text = path.read_text()
    assert text.startswith("---\n"), f"{path}: must start with '---\\n'"
    parts = text.split("\n---\n", 1)
    assert len(parts) == 2, f"{path}: missing closing '---' delimiter"
    front_raw = parts[0][4:]  # strip leading '---\n'
    body = parts[1].lstrip("\n")
    fm = yaml.safe_load(front_raw)
    assert isinstance(fm, dict), f"{path}: front-matter must be a YAML mapping"
    # Accept both `allowed_tools:` (new, Task 13) and `allowed-tools:`
    # (legacy v0.1 form used by the existing handover skill). Normalise
    # so downstream tests can rely on one key.
    if "allowed-tools" in fm and "allowed_tools" not in fm:
        fm["allowed_tools"] = fm["allowed-tools"]
    return fm, body


def _skill_body(name: str) -> str:
    """Return ONLY the body of the named skill (post-front-matter)."""
    _, body = _read_skill(name)
    return body


# ---------------------------------------------------------------------------
# Per-skill structural assertions (parametrised over the four skills)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("skill", EXPECTED_SKILLS)
def test_each_skill_has_valid_yaml_frontmatter(skill: str) -> None:
    """Front-matter must be delimited by '---' lines and parse as a dict."""
    fm, _body = _read_skill(skill)
    assert isinstance(fm, dict)


@pytest.mark.parametrize("skill", EXPECTED_SKILLS)
def test_each_skill_declares_name(skill: str) -> None:
    """Front-matter ``name:`` must match the skill directory name."""
    fm, _body = _read_skill(skill)
    assert "name" in fm, f"{skill}: missing 'name' in front-matter"
    assert fm["name"] == skill, (
        f"{skill}: front-matter name {fm['name']!r} doesn't match directory"
    )


@pytest.mark.parametrize("skill", EXPECTED_SKILLS)
def test_each_skill_declares_description(skill: str) -> None:
    """Front-matter ``description:`` must be present and non-trivial."""
    fm, _body = _read_skill(skill)
    assert "description" in fm, f"{skill}: missing 'description'"
    desc = fm["description"]
    assert isinstance(desc, str) and len(desc.strip()) >= 20, (
        f"{skill}: description is empty or too short (got {desc!r})"
    )


@pytest.mark.parametrize("skill", EXPECTED_SKILLS)
def test_each_skill_declares_when_to_use(skill: str) -> None:
    """Front-matter ``when_to_use:`` must list at least one trigger.

    Two acceptable shapes for backward-compat with the v0.1 handover
    skill:

    - list[str] — the new Task 13 shape (preferred)
    - str       — the v0.1 form: a single prose sentence describing when
                  to use the skill. Must still be non-trivial.
    """
    fm, _body = _read_skill(skill)
    assert "when_to_use" in fm, f"{skill}: missing 'when_to_use'"
    val = fm["when_to_use"]
    if isinstance(val, list):
        assert len(val) >= 1, f"{skill}: when_to_use list is empty"
        assert all(isinstance(item, str) and item.strip() for item in val), (
            f"{skill}: when_to_use items must be non-empty strings"
        )
    else:
        assert isinstance(val, str) and len(val.strip()) >= 20, (
            f"{skill}: when_to_use must be a list[str] or a non-trivial string"
        )


@pytest.mark.parametrize("skill", EXPECTED_SKILLS)
def test_each_skill_declares_allowed_tools(skill: str) -> None:
    """Front-matter ``allowed_tools:`` must be a list naming at least Bash."""
    fm, _body = _read_skill(skill)
    assert "allowed_tools" in fm, (
        f"{skill}: missing 'allowed_tools' (or legacy 'allowed-tools')"
    )
    tools = fm["allowed_tools"]
    assert isinstance(tools, list), f"{skill}: allowed_tools must be a list"
    assert len(tools) >= 1, f"{skill}: allowed_tools is empty"
    # Bash is the lowest common denominator — every PFactory skill needs it
    # to invoke python/git/gh/etc.  We don't require Read/Write because the
    # handover skill is MCP-tool-driven and the new skills allow both.
    assert "Bash" in tools, f"{skill}: allowed_tools missing 'Bash' (got {tools})"


@pytest.mark.parametrize("skill", EXPECTED_SKILLS)
def test_each_skill_body_is_nonempty(skill: str) -> None:
    """Skill body (after front-matter) must be at least 500 chars — not a stub."""
    body = _skill_body(skill)
    assert len(body) >= 500, (
        f"{skill}: body is only {len(body)} chars (looks like a stub)"
    )


# Per-new-skill: allowed_tools must include both Read AND Write — the new
# Task 13 skills file-write to the project.  The handover skill is exempt
# because it is MCP-tool-driven and never writes a project file itself.
NEW_SKILLS_REQUIRING_READ_WRITE: tuple[str, ...] = (
    "pfactory-init",
    "pfactory-add-test",
    "pfactory-from-template",
)


@pytest.mark.parametrize("skill", NEW_SKILLS_REQUIRING_READ_WRITE)
def test_new_skills_allow_read_and_write(skill: str) -> None:
    """The three new file-writing skills must declare Read + Write tools."""
    fm, _body = _read_skill(skill)
    tools = fm["allowed_tools"]
    assert "Read" in tools, f"{skill}: allowed_tools missing 'Read' (got {tools})"
    assert "Write" in tools, f"{skill}: allowed_tools missing 'Write' (got {tools})"


# ---------------------------------------------------------------------------
# Slash command files
# ---------------------------------------------------------------------------


def test_three_slash_commands_exist() -> None:
    """All three Task 13 slash commands must exist as .md files."""
    for name in EXPECTED_COMMANDS:
        path = COMMANDS_DIR / f"{name}.md"
        assert path.is_file(), f"missing slash command file: {path}"


@pytest.mark.parametrize("name", EXPECTED_COMMANDS)
def test_each_slash_command_references_its_skill(name: str) -> None:
    """Each slash command body must reference its skill by name."""
    path = COMMANDS_DIR / f"{name}.md"
    text = path.read_text()
    assert name in text, (
        f"{path}: command body does not reference skill name {name!r}"
    )


# ---------------------------------------------------------------------------
# handover-to-pfactory v0.2 vocabulary update
# ---------------------------------------------------------------------------


def test_handover_skill_mentions_v02_lane_spine() -> None:
    """Body of handover-to-pfactory/SKILL.md must mention at least one of
    the v0.2 lane names (unit / browser / api / integration) — confirming
    the update landed — and MUST NOT mention the deprecated v0.1 lane
    names (``sast`` / ``dast`` / ``fuzz``) as standalone words.

    The lowercase standalone word ``functional`` is also forbidden, but
    we allow ``Gen-Functional`` (the hyphenated agent name) since the
    Generator agent is literally called Gen-Functional in the code.
    """
    body = _skill_body("handover-to-pfactory")
    # Must contain at least one v0.2 lane word.
    v02_words = ("unit", "browser", "api", "integration")
    assert any(re.search(rf"\b{w}\b", body) for w in v02_words), (
        f"handover-to-pfactory body mentions none of {v02_words}"
    )
    # Forbidden v0.1 lane vocabulary.
    forbidden = ("sast", "dast", "fuzz")
    for word in forbidden:
        assert not re.search(rf"\b{word}\b", body, re.IGNORECASE), (
            f"handover-to-pfactory body still mentions v0.1 lane {word!r}"
        )
    # Lowercase standalone 'functional' is forbidden, but 'Gen-Functional'
    # (the agent name) is fine. We assert on a standalone lowercase match.
    assert not re.search(r"(?<![A-Za-z-])functional(?![A-Za-z-])", body), (
        "handover-to-pfactory body has a standalone 'functional' (v0.1 lane name)"
    )


def test_handover_skill_mentions_pfactory_yml() -> None:
    """Body must reference .pfactory.yml — the v0.2 config file."""
    body = _skill_body("handover-to-pfactory")
    assert ".pfactory.yml" in body, (
        "handover-to-pfactory body must reference '.pfactory.yml'"
    )


def test_handover_skill_mentions_tests_catalog() -> None:
    """Body must reference the tests-catalog.json — the v0.2 catalog file."""
    body = _skill_body("handover-to-pfactory")
    assert "tests-catalog.json" in body, (
        "handover-to-pfactory body must reference 'tests-catalog.json'"
    )


def test_handover_skill_mentions_polyglot_or_language_framework() -> None:
    """Body must communicate the polyglot mental model — either by
    using the word ``polyglot`` or the ``(language, framework)`` phrasing.

    This is what distinguishes v0.2 from v0.1 — the Planner picks a
    per-subtask ``(language, framework)`` tuple instead of v0.1's
    one-size-fits-all pytest assumption.
    """
    body = _skill_body("handover-to-pfactory").lower()
    has_polyglot = "polyglot" in body
    has_language_framework = "language, framework" in body or (
        "language" in body and "framework" in body
    )
    assert has_polyglot or has_language_framework, (
        "handover-to-pfactory body must mention 'polyglot' or '(language, framework)' "
        "to communicate the v0.2 mental model"
    )


# ---------------------------------------------------------------------------
# Cross-cutting: the four skills exist as expected directory entries
# ---------------------------------------------------------------------------


def test_skills_directory_contains_all_four_bundles() -> None:
    """``.claude/skills/`` must contain all four Task 13 skill directories."""
    for name in EXPECTED_SKILLS:
        path = SKILLS_DIR / name / "SKILL.md"
        assert path.is_file(), f"missing skill bundle: {path}"
