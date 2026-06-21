"""Unit tests for the RFC-0015 §3.1 constitution (PFactory #213).

Covers: markdown parsing (headings + bold bullets + enforceable markers), the
contract block builder + its degrade-never-fake behaviour, the loader against a
checkout, attach into epic_context, prompt injection into the decompose prompt,
and the constitution-grounded readiness check.

Run: apps/backend/.venv/bin/pytest tests/test_constitution.py
"""

from __future__ import annotations

from pathlib import Path

from plan.decompose.models import EpicPlan
from plan.decompose.planner import build_decompose_prompt
from plan.emit.constitution import (
    CONSTITUTION_PATH,
    attach_constitution,
    build_constitution_block,
    load_constitution,
    parse_constitution,
    render_constitution_prompt,
)
from plan.models import NormalizedPlan
from plan.plan_types import select_for
from plan.review.readiness.checks import ReadinessContext, _constitution_grounded

_CONSTITUTION = """\
# Project Constitution

## P1: Every change ships with tests (enforceable)
Tests must accompany every feature.

## P2: Prefer composition over inheritance

- **P3 (enforceable):** No plaintext secrets in source.
- **P4 (advisory):** Keep functions under 50 lines.
"""


def _plan(constitution_md: str | None = None) -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="001-x",
        title="Add a login endpoint",
        description="REST API with auth",
        source_format="markdown",
        target_kind="software",
        constitution_md=constitution_md,
    )


# ── parsing ─────────────────────────────────────────────────────────────────


def test_parse_mixes_headings_and_bullets() -> None:
    principles = parse_constitution(_CONSTITUTION)
    ids = [p["id"] for p in principles]
    assert ids == ["P1", "P2", "P3", "P4"]
    # The bare "# Project Constitution" title is not a principle.
    assert all("constitution" not in p["text"].lower() for p in principles)


def test_parse_enforceable_flags() -> None:
    principles = {p["id"]: p for p in parse_constitution(_CONSTITUTION)}
    assert principles["P1"]["enforceable"] is True
    assert principles["P2"]["enforceable"] is False
    assert principles["P3"]["enforceable"] is True
    # An explicit advisory marker wins.
    assert principles["P4"]["enforceable"] is False


def test_parse_strips_id_and_marker_from_text() -> None:
    principles = {p["id"]: p for p in parse_constitution(_CONSTITUTION)}
    assert principles["P1"]["text"] == "Tests must accompany every feature."
    assert principles["P3"]["text"] == "No plaintext secrets in source."


def test_parse_synthesizes_ids_when_missing() -> None:
    text = "## Testing policy (enforceable)\nWrite tests first.\n## Style\nUse black."
    principles = parse_constitution(text)
    assert [p["id"] for p in principles] == ["P1", "P2"]
    assert principles[0]["enforceable"] is True


def test_parse_dedupes_repeated_ids() -> None:
    text = "- **P1:** first\n- **P1:** second"
    ids = [p["id"] for p in parse_constitution(text)]
    assert ids == ["P1", "P1.1"]


# ── block builder (degrade, never fake) ──────────────────────────────────────


def test_block_available_with_enforceable_ids() -> None:
    block = build_constitution_block(_CONSTITUTION, source=CONSTITUTION_PATH)
    assert block["available"] is True
    assert block["source"] == CONSTITUTION_PATH
    assert block["enforceable_ids"] == ["P1", "P3"]
    assert len(block["principles"]) == 4


def test_block_unavailable_when_text_missing() -> None:
    block = build_constitution_block(None, source=CONSTITUTION_PATH)
    assert block == {
        "available": False,
        "source": CONSTITUTION_PATH,
        "principles": [],
        "enforceable_ids": [],
    }


def test_block_unavailable_when_unparseable() -> None:
    # Prose with no headings / bold-led principles parses to nothing.
    block = build_constitution_block("just some prose, no principles here", source="x")
    assert block["available"] is False
    assert block["principles"] == []


# ── loader ──────────────────────────────────────────────────────────────────


def test_load_constitution_from_checkout(tmp_path: Path) -> None:
    (tmp_path / ".factory").mkdir()
    (tmp_path / CONSTITUTION_PATH).write_text(_CONSTITUTION)
    block = load_constitution(tmp_path)
    assert block["available"] is True
    assert block["enforceable_ids"] == ["P1", "P3"]


def test_load_constitution_missing_file_degrades(tmp_path: Path) -> None:
    block = load_constitution(tmp_path)
    assert block["available"] is False
    assert block["principles"] == []


# ── attach into epic_context ─────────────────────────────────────────────────


def test_attach_records_block_even_when_absent() -> None:
    contract: dict = {}
    attach_constitution(contract, _plan(constitution_md=None))
    # Always records an (unavailable) block so consumers can distinguish
    # "looked and found nothing" from "predates the feature".
    assert contract["epic_context"]["constitution"]["available"] is False


def test_attach_records_available_block() -> None:
    contract: dict = {"epic_context": {"house_standards": {"available": True}}}
    attach_constitution(contract, _plan(constitution_md=_CONSTITUTION))
    block = contract["epic_context"]["constitution"]
    assert block["available"] is True
    assert block["enforceable_ids"] == ["P1", "P3"]
    # Does not clobber the sibling house_standards block.
    assert contract["epic_context"]["house_standards"]["available"] is True


def test_attach_never_raises_on_garbage() -> None:
    # A plan-like object whose attribute access explodes must not break emit.
    class Boom:
        @property
        def constitution_md(self) -> str:
            raise RuntimeError("boom")

    contract: dict = {}
    # Should swallow and leave the contract usable.
    attach_constitution(contract, Boom())


# ── prompt injection ─────────────────────────────────────────────────────────


def test_render_prompt_marks_hard_clauses() -> None:
    block = build_constitution_block(_CONSTITUTION, source="plan")
    rendered = render_constitution_prompt(block)
    assert "[HARD]" in rendered
    assert "[advisory]" in rendered
    assert "No plaintext secrets" in rendered


def test_render_prompt_empty_when_unavailable() -> None:
    assert render_constitution_prompt(None) == ""
    assert render_constitution_prompt({"available": False}) == ""


def test_decompose_prompt_injects_constitution() -> None:
    plan = _plan(constitution_md=_CONSTITUTION)
    descriptor = select_for(plan)
    prompt = build_decompose_prompt(plan, descriptor)
    assert "Project constitution" in prompt
    assert "[HARD]" in prompt
    assert "P1" in prompt


def test_decompose_prompt_unchanged_without_constitution() -> None:
    plan = _plan(constitution_md=None)
    descriptor = select_for(plan)
    prompt = build_decompose_prompt(plan, descriptor)
    assert "Project constitution" not in prompt


# ── readiness check ──────────────────────────────────────────────────────────


def _epic() -> EpicPlan:
    return EpicPlan(plan_id="001-x", epic_title="x", children=[])


def test_readiness_not_applicable_without_constitution() -> None:
    ctx = ReadinessContext(constitution=None)
    res = _constitution_grounded(_plan(), _epic(), ctx)
    assert res.status == "not_applicable"


def test_readiness_pass_with_principles() -> None:
    block = build_constitution_block(_CONSTITUTION, source="plan")
    ctx = ReadinessContext(constitution=block)
    res = _constitution_grounded(_plan(), _epic(), ctx)
    assert res.status == "pass"
    assert res.evidence["enforceable_ids"] == ["P1", "P3"]
    assert res.evidence["principle_count"] == 4


def test_readiness_hard_fails_on_malformed_constitution() -> None:
    # available True but no principles is the pathological malformed case.
    ctx = ReadinessContext(
        constitution={"available": True, "source": "plan", "principles": [], "enforceable_ids": []}
    )
    res = _constitution_grounded(_plan(), _epic(), ctx)
    assert res.status == "fail"
    assert res.hard is True
