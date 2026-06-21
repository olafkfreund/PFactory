"""Unit tests for spec-kit artifact ingest (RFC-0015 §3.2 — PFactory #214).

Covers: workspace discovery across the .specify/ + specs/<feature>/ + flat
layouts, spec.md→NormalizedPlan, tasks.md→EpicPlan children, plan.md folding,
constitution→epic_context.constitution, and clean degradation when files are
absent.

Run: apps/backend/.venv/bin/pytest tests/test_speckit_ingest.py
"""

from __future__ import annotations

from pathlib import Path

import pytest
from plan.ingest.speckit import (
    SpecKitError,
    discover_workspace,
    ingest_speckit,
    parse_spec,
    parse_tasks,
)

_SPEC = """\
# Feature Specification: User login

Add a secure login flow so registered users can authenticate.

## Acceptance Criteria
- Users can log in with email + password
- A failed login shows an error
- Sessions expire after 30 minutes
"""

_PLAN = """\
# Technical Plan

Use FastAPI + JWT. Hash passwords with argon2. Store sessions in Redis.
"""

_TASKS = """\
# Tasks
- [ ] T1: Build the login endpoint
- [ ] T2: Add the session store
- [ ] Add integration tests
"""

_CONSTITUTION = """\
# Constitution

## P1: Every feature ships with tests (enforceable)
## P2: Prefer small, reviewable changes
"""


def _write_specify(tmp_path: Path, *, with_spec=True, with_tasks=True, with_const=True) -> Path:
    """Create a .specify/ workspace with specs/<feature>/ + memory/."""
    root = tmp_path / ".specify"
    feat = root / "specs" / "001-user-login"
    feat.mkdir(parents=True)
    if with_spec:
        (feat / "spec.md").write_text(_SPEC)
    (feat / "plan.md").write_text(_PLAN)
    if with_tasks:
        (feat / "tasks.md").write_text(_TASKS)
    if with_const:
        (root / "memory").mkdir()
        (root / "memory" / "constitution.md").write_text(_CONSTITUTION)
    return tmp_path


# ── parsing units ─────────────────────────────────────────────────────────────


def test_parse_spec_extracts_title_and_criteria() -> None:
    spec = parse_spec(_SPEC, feature="001-user-login")
    assert spec.title == "User login"
    assert "secure login flow" in spec.description
    assert len(spec.criteria) == 3
    assert spec.criteria[0].text.startswith("Users can log in")


def test_parse_spec_falls_back_to_feature_when_no_title() -> None:
    spec = parse_spec("Just some prose without a heading or ACs.", feature="my-feature")
    assert spec.title == "my-feature"
    assert spec.criteria == ()


def test_parse_tasks_captures_ids_and_synthesizes() -> None:
    tasks = parse_tasks(_TASKS)
    assert tasks[0] == ("T1", "Build the login endpoint")
    assert tasks[1] == ("T2", "Add the session store")
    # The third item has no explicit id → synthesized T3.
    assert tasks[2][0] == "T3"
    assert "integration tests" in tasks[2][1]


# ── discovery ─────────────────────────────────────────────────────────────────


def test_discover_specify_layout(tmp_path: Path) -> None:
    root = _write_specify(tmp_path)
    ws = discover_workspace(root)
    assert ws.feature == "001-user-login"
    assert ws.spec is not None and ws.spec.name == "spec.md"
    assert ws.tasks is not None
    assert ws.constitution is not None and ws.constitution.name == "constitution.md"


def test_discover_flat_layout(tmp_path: Path) -> None:
    (tmp_path / "spec.md").write_text(_SPEC)
    (tmp_path / "tasks.md").write_text(_TASKS)
    ws = discover_workspace(tmp_path)
    assert ws.spec is not None
    assert ws.tasks is not None
    assert ws.constitution is None


def test_discover_raises_when_no_artifacts(tmp_path: Path) -> None:
    (tmp_path / "readme.txt").write_text("nothing here")
    with pytest.raises(SpecKitError):
        discover_workspace(tmp_path)


# ── end-to-end ingest ─────────────────────────────────────────────────────────


def test_ingest_maps_all_artifacts(tmp_path: Path) -> None:
    root = _write_specify(tmp_path)
    plan, epic, constitution = ingest_speckit(root)

    # spec → plan
    assert plan.title == "User login"
    assert plan.source_format == "spec-kit"
    assert plan.target_kind == "software"
    assert len(plan.criteria) == 3
    assert plan.content_hash  # hashed

    # tasks → epic children (one per task)
    assert len(epic.children) == 3
    assert epic.children[0].title == "Build the login endpoint"
    assert epic.children[0].kind == "task"
    assert "speckit:T1" in epic.children[0].labels

    # plan.md folded into the epic body
    assert "Technical plan" in epic.epic_body
    assert "FastAPI + JWT" in epic.epic_body

    # constitution → epic_context.constitution block
    assert constitution["available"] is True
    assert constitution["enforceable_ids"] == ["P1"]
    assert plan.constitution_md is not None


def test_ingest_degrades_without_tasks(tmp_path: Path) -> None:
    root = _write_specify(tmp_path, with_tasks=False)
    plan, epic, _ = ingest_speckit(root)
    # No tasks → no children; the normal decomposer fills them from criteria.
    assert epic.children == []
    assert "no explicit tasks" in epic.summary
    assert len(plan.criteria) == 3


def test_ingest_degrades_without_constitution(tmp_path: Path) -> None:
    root = _write_specify(tmp_path, with_const=False)
    plan, _, constitution = ingest_speckit(root)
    assert constitution["available"] is False
    assert constitution["principles"] == []
    assert plan.constitution_md is None


def test_ingest_spec_only_workspace(tmp_path: Path) -> None:
    feat = tmp_path / "specs" / "feat-x"
    feat.mkdir(parents=True)
    (feat / "spec.md").write_text(_SPEC)
    plan, epic, constitution = ingest_speckit(tmp_path)
    assert plan.title == "User login"
    assert epic.children == []
    assert constitution["available"] is False


def test_ingest_no_spec_uses_feature_name(tmp_path: Path) -> None:
    root = _write_specify(tmp_path, with_spec=False)
    plan, epic, constitution = ingest_speckit(root)
    # Falls back to the feature directory name as the title.
    assert plan.title == "001-user-login"
    # plan.md + tasks.md + constitution still flow.
    assert len(epic.children) == 3
    assert constitution["available"] is True
