"""The assembled contract carries the skills the work actually needs (#687).

End-to-end over `assemble_contract`, against the REAL catalogue: the registry's
skill rows were inert, so a plan that raised a privacy obligation reached the
coder with no pointer to `skills/engineering/privacy-and-regulatory.md`. These
prove the rows are load-bearing — delete a row or its capabilities and a test
here goes red.

Run: apps/backend/.venv/bin/pytest tests/test_skills_contract.py
"""

from __future__ import annotations

import json
from pathlib import Path

from plan.decompose.models import ChildIssue, EpicPlan
from plan.emit.contract_emit import assemble_contract
from plan.emit.task_contract import validate_contract
from plan.models import Criterion, NormalizedPlan
from plan.review.gates import run_gates

SOCIAL_SPEC = (
    "Users create a personal profile with photos, see people nearby via "
    "location, and chat with their matches. Distributed via the App Store."
)
PLAIN_SPEC = "A batch job reads a CSV from disk and writes a summary report to disk."


def _plan(description: str, *, plan_type: str | None = None, title: str = "T") -> NormalizedPlan:
    plan = NormalizedPlan(
        plan_id="001-x",
        title=title,
        description=description,
        source_format="markdown",
        target_kind="software",
        criteria=[Criterion(id="AC#1", text="It works")],
    ).with_hash()
    if plan_type is not None:
        plan.plan_type = plan_type
    return plan


def _epic() -> EpicPlan:
    return EpicPlan(
        plan_id="001-x",
        epic_title="T",
        children=[ChildIssue(key="C1", title="C1", acceptance_criteria=["It works"])],
    )


def _skills_block(plan: NormalizedPlan) -> dict:
    epic = _epic()
    contract = assemble_contract(plan, epic, run_gates(plan, epic))
    assert validate_contract(contract) == [], "the block must not break schema validation"
    return contract["epic_context"]["skills"]


def test_a_personal_data_plan_is_pointed_at_the_privacy_skill():
    block = _skills_block(_plan(SOCIAL_SPEC))
    assert block["available"] is True
    ids = [s["id"] for s in block["skills"]]
    assert "skill:privacy-and-regulatory" in ids
    entry = next(s for s in block["skills"] if s["id"] == "skill:privacy-and-regulatory")
    assert entry["path"] == "skills/engineering/privacy-and-regulatory.md"
    assert entry["title"]


def test_a_mobile_plan_is_pointed_at_the_mobile_skill():
    block = _skills_block(_plan(SOCIAL_SPEC, plan_type="mobile-app"))
    assert "skill:mobile-native" in [s["id"] for s in block["skills"]]
    assert "mobile" in block["matched_on"]


def test_a_plain_plan_looked_and_found_nothing():
    """`available: true` + empty skills is NOT the same as the block being
    absent: it says the matcher ran and no skill applies."""
    block = _skills_block(_plan(PLAIN_SPEC, plan_type="software-service"))
    assert block["available"] is True
    assert block["skills"] == []
    assert block["matched_on"] == []


def test_the_attached_paths_resolve_to_real_skill_files():
    repo_root = Path(__file__).resolve().parents[1]
    for entry in _skills_block(_plan(SOCIAL_SPEC))["skills"]:
        assert (repo_root / entry["path"]).is_file(), entry


def test_schema_declares_the_skills_def():
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "apps/backend/plan/emit/contracts/task-contract.schema.json"
    )
    schema = json.loads(schema_path.read_text())
    assert "skills" in schema["$defs"]
    assert schema["properties"]["epic_context"]["properties"]["skills"]["$ref"] == (
        "#/$defs/skills"
    )
