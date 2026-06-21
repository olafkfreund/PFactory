"""Unit tests for spec-kit-shaped doc emit (RFC-0015 §3.3 — PFactory #215).

Renders spec.md / plan.md / tasks.md from a real Task Contract (the canonical
Markdown mirror) and asserts the structure + defensive degradation.

Run: apps/backend/.venv/bin/pytest tests/test_speckit_render.py
"""

from __future__ import annotations

from typing import Any

from plan.decompose.models import ChildIssue, EpicPlan
from plan.emit.contract_builder import build_task_contract
from plan.emit.docs.speckit_render import (
    PLAN_DOC,
    SPEC_DOC,
    TASKS_DOC,
    render_plan_md,
    render_spec_md,
    render_speckit_bundle,
    render_tasks_md,
)
from plan.models import Criterion, NormalizedPlan


def _contract() -> dict[str, Any]:
    plan = NormalizedPlan(
        plan_id="001-login",
        title="User login",
        description="A secure login flow for registered users.",
        source_format="markdown",
        target_kind="software",
        plan_type="feature",
        criteria=[
            Criterion(id="AC#1", text="Users can log in with email + password"),
            Criterion(id="AC#2", text="A failed login shows an error"),
        ],
    )
    epic = EpicPlan(
        plan_id="001-login",
        epic_title="User login",
        epic_body="Use FastAPI + JWT; argon2 hashing.",
        summary="2 children",
        children=[
            ChildIssue(
                key="C1",
                title="Build the login endpoint",
                body="do it",
                kind="feature",
                acceptance_criteria=["Users can log in"],
            ),
            ChildIssue(key="C2", title="Add tests", body="test", kind="testing", depends_on=["C1"]),
        ],
    )
    return build_task_contract(plan, epic, repo="o/r", correlation_key="42")


def _with_constitution(contract: dict[str, Any]) -> dict[str, Any]:
    contract.setdefault("epic_context", {})["constitution"] = {
        "available": True,
        "source": ".factory/constitution.md",
        "principles": [
            {"id": "P1", "text": "Ship with tests", "enforceable": True},
            {"id": "P2", "text": "Small changes", "enforceable": False},
        ],
        "enforceable_ids": ["P1"],
    }
    return contract


def _with_routing(contract: dict[str, Any]) -> dict[str, Any]:
    contract["execution"] = {
        "routing": {
            "difficulty": "medium",
            "risk": "low",
            "autonomy": {"verdict": "review", "reason": "medium difficulty"},
        }
    }
    return contract


# ── spec.md ──────────────────────────────────────────────────────────────────


def test_spec_md_has_title_overview_and_criteria() -> None:
    md = render_spec_md(_contract(), description="A secure login flow.")
    assert md.startswith("---")
    assert f"doc: {SPEC_DOC}" in md
    assert "# User login" in md
    assert "A secure login flow." in md
    assert "## Acceptance Criteria" in md
    assert "- Users can log in with email + password" in md
    assert "- A failed login shows an error" in md


def test_spec_md_renders_constitution_when_present() -> None:
    md = render_spec_md(_with_constitution(_contract()))
    assert "## Governing principles (constitution)" in md
    assert "**P1** **[enforced]**: Ship with tests" in md
    assert "**P2**: Small changes" in md


def test_spec_md_degrades_without_criteria() -> None:
    md = render_spec_md({"feature": "Bare plan"})
    assert "# Bare plan" in md
    assert "_No explicit acceptance criteria recorded._" in md


# ── plan.md ──────────────────────────────────────────────────────────────────


def test_plan_md_lists_phases_and_services() -> None:
    md = render_plan_md(_contract(), technical_notes="Use FastAPI + JWT.")
    assert f"doc: {PLAN_DOC}" in md
    assert "# Plan — User login" in md
    assert "## Technical approach" in md
    assert "Use FastAPI + JWT." in md
    assert "## Phases" in md
    assert "### Phase 1" in md
    assert "`C1`" in md


def test_plan_md_renders_routing_verdict() -> None:
    md = render_plan_md(_with_routing(_contract()))
    assert "## Difficulty, risk & autonomy" in md
    assert "**Difficulty:** medium" in md
    assert "**Autonomy:** review — medium difficulty" in md


# ── tasks.md ─────────────────────────────────────────────────────────────────


def test_tasks_md_is_a_checklist_with_deps() -> None:
    md = render_tasks_md(_contract())
    assert f"doc: {TASKS_DOC}" in md
    assert "# Tasks — User login" in md
    assert "- [ ] **C1** — Build the login endpoint" in md
    assert "- [ ] **C2** — Add tests" in md
    assert "depends on: C1" in md


def test_tasks_md_degrades_without_subtasks() -> None:
    md = render_tasks_md({"feature": "Empty"})
    assert "_No tasks decomposed yet._" in md


# ── bundle ───────────────────────────────────────────────────────────────────


def test_bundle_renders_all_three() -> None:
    bundle = render_speckit_bundle(
        _with_constitution(_contract()),
        description="overview",
        technical_notes="notes",
    )
    assert set(bundle) == {SPEC_DOC, PLAN_DOC, TASKS_DOC}
    assert all(v.strip() for v in bundle.values())
    # Every doc carries front matter so the cockpit can route/parse them.
    assert all(v.startswith("---") for v in bundle.values())


def test_bundle_never_raises_on_garbage() -> None:
    # A non-dict contract must not raise — it degrades to placeholder docs
    # (defensive reads), never an exception.
    bundle = render_speckit_bundle(None)  # type: ignore[arg-type]
    assert set(bundle) == {SPEC_DOC, PLAN_DOC, TASKS_DOC}
    assert "Untitled plan" in bundle[SPEC_DOC]
