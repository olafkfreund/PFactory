"""Tests for the contract compliance block (emit carriage).

Without this block an obligation raised in review never reaches AIFactory or
TFactory — the difference between a flag and a control. The block must also
make "the lens never ran" (available=false) distinguishable from "the lens ran
and found nothing" (available=true, empty obligations), so a silently unwired
lens cannot masquerade as a clean pass.

Run: apps/backend/.venv/bin/pytest tests/test_compliance_contract.py
"""

from __future__ import annotations

import json
from pathlib import Path

from plan.decompose.models import ChildIssue, EpicPlan
from plan.emit.compliance_block import attach_compliance, build_compliance_block
from plan.emit.contract_emit import assemble_contract
from plan.emit.task_contract import validate_contract
from plan.models import Criterion, NormalizedPlan
from plan.review.gates import run_gates

SOCIAL_SPEC = (
    "Users create a personal profile with photos, see people nearby via "
    "location, and chat with their matches. Distributed via the App Store."
)


def _plan(
    description: str = SOCIAL_SPEC,
    *,
    title: str = "MyFriends app",
    criteria: tuple[str, ...] = ("Users can create a profile",),
) -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="001-x",
        title=title,
        description=description,
        source_format="markdown",
        target_kind="software",
        criteria=[Criterion(id=f"AC#{i}", text=t) for i, t in enumerate(criteria, 1)],
    ).with_hash()


def _epic() -> EpicPlan:
    return EpicPlan(
        plan_id="001-x",
        epic_title="MyFriends",
        children=[
            ChildIssue(
                key="C1", title="Profiles", acceptance_criteria=["Users can create a profile"]
            )
        ],
    )


def test_block_carries_the_lens_findings_with_citations() -> None:
    plan = _plan()
    review = run_gates(plan, _epic())
    block = build_compliance_block(plan, review)
    assert block["available"] is True
    assert block["obligations"], "a social spec must yield obligations on the contract"
    titles = [o["title"] for o in block["obligations"]]
    assert "No retention or deletion policy stated" in titles
    assert any(o["blocking"] for o in block["obligations"])
    for o in block["obligations"]:
        assert o["citations"], f"obligation without citations: {o['title']}"
        assert all(c["uri"].startswith("https://") for c in o["citations"])
    assert "not legal advice" in block["disclaimer"]
    assert "personal-profile" in block["data_classes"]
    assert "location" in block["data_classes"]


def test_never_ran_is_distinguishable_from_found_nothing() -> None:
    plan = _plan()
    # No review at all → the lens never ran → available=false.
    assert build_compliance_block(plan, None)["available"] is False
    # A clean, non-personal plan through the real gates → the lens RAN and
    # found nothing → available=true with empty obligations.
    clean = _plan(
        "Rotate the TLS certificates on the ingress controllers.",
        title="Rotate the TLS certificates",
        criteria=("New certs are served", "Old certs are revoked"),
    )
    review = run_gates(clean, _epic())
    block = build_compliance_block(clean, review)
    assert block["available"] is True
    assert block["obligations"] == []


def test_assemble_contract_attaches_and_still_validates() -> None:
    plan = _plan()
    review = run_gates(plan, _epic())
    contract = assemble_contract(plan, _epic(), review)
    assert "compliance" in contract, "assemble_contract must attach the compliance block"
    assert contract["compliance"]["available"] is True
    assert contract["compliance"]["obligations"]
    # Additive: the contract must still validate against the schema.
    errors = validate_contract(contract)
    assert errors == []


def test_attach_never_raises() -> None:
    contract: dict = {}
    out = attach_compliance(contract, None, None)  # type: ignore[arg-type]
    assert out is contract  # degraded, not raised


def test_schema_declares_the_compliance_def() -> None:
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "apps"
        / "backend"
        / "plan"
        / "emit"
        / "contracts"
        / "task-contract.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is True  # additive, non-breaking
    assert "compliance" in schema["$defs"]
    assert schema["properties"]["compliance"] == {"$ref": "#/$defs/compliance"}
