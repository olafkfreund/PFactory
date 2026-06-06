"""Tests for the WS2 enrichment sanitizer (issue #80).

Covers redaction of every identifier type, that raw/load/target are dropped,
policy categorization, size caps, never-raises on malformed input, and that
``attach_constraints`` keeps the contract schema-valid.
"""

from __future__ import annotations

import json

from plan.decompose.models import ChildIssue, EpicPlan
from plan.emit.contract_builder import build_task_contract
from plan.emit.handoff_sanitize import (
    attach_constraints,
    redact,
    sanitize_constraints,
    sanitize_knowledge_links,
)
from plan.emit.task_contract import validate_contract
from plan.models import Criterion, Enrichment, NormalizedPlan

# ── redaction ───────────────────────────────────────────────────────────────


def test_redacts_aws_account_id() -> None:
    assert redact("account 123456789012 owns it") == "account [redacted] owns it"


def test_redacts_arn() -> None:
    out = redact("role arn:aws:iam::123456789012:role/admin here")
    assert "arn:aws" not in out
    assert "[redacted]" in out


def test_redacts_instance_and_sg_ids() -> None:
    assert "i-0abc" not in redact("box i-0abc123def456")
    assert "sg-0abc" not in redact("group sg-0abc123def456")
    assert redact("box i-0abc123def456").count("[redacted]") == 1


def test_redacts_ipv4_and_cidr() -> None:
    assert redact("10.0.0.1") == "[redacted]"
    assert redact("0.0.0.0/0") == "[redacted]"


def test_redacts_ipv6() -> None:
    assert "[redacted]" in redact("addr 2001:db8::ff00:42:8329 end")


def test_redacts_akia_key() -> None:
    assert redact("AKIAIOSFODNN7EXAMPLE") == "[redacted]"


def test_redacts_long_hex_and_base64() -> None:
    assert redact("abcdef0123456789abcdef0123456789abcd") == "[redacted]"
    b64 = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU2Nzg5"
    assert "[redacted]" in redact(b64)


def test_redact_never_raises_on_non_string() -> None:
    assert isinstance(redact(12345678901234567890), str)
    assert isinstance(redact(None), str)
    assert isinstance(redact({"a": 1}), str)


# ── snapshot reduction ───────────────────────────────────────────────────────


def _snapshot(**kw) -> dict:
    base = {
        "adapter": "aws",
        "target": "123456789012",  # account id → must be dropped
        "available": True,
        "workloads": [{"name": "web"}],
        "resources": {
            "regions": ["us-east-1", "eu-west-1"],
            "instance_types": {"t3.micro": 4, "m5.large": 2},
            "vpc_count": 3,
            "bucket_count": 12,
        },
        "policies": [
            {"id": "sg-0abc123def", "rule": "0.0.0.0/0 ingress allow"},
            "public read on bucket",
        ],
        "findings": ["open ingress from 0.0.0.0/0", "bucket public read"],
        "load": {"cpu": 0.9, "secret": "AKIAIOSFODNN7EXAMPLE"},
        "raw": {"everything": "123456789012 arn:aws:s3:::x"},
        "error": None,
    }
    base.update(kw)
    return base


def test_drops_raw_load_target() -> None:
    [c] = sanitize_constraints([_snapshot()])
    assert "raw" not in c
    assert "load" not in c
    assert "target" not in c
    assert c["adapter"] == "aws"
    assert c["available"] is True


def test_keeps_resources_regions_instance_types_counts() -> None:
    [c] = sanitize_constraints([_snapshot()])
    res = c["resources"]
    assert res["regions"] == ["eu-west-1", "us-east-1"]
    assert set(res["instance_types"]) == {"t3.micro", "m5.large"}
    assert res["vpc_count"] == 3
    assert res["bucket_count"] == 12


def test_policy_flags_categorized_and_deduped() -> None:
    [c] = sanitize_constraints([_snapshot()])
    flags = c["policy_flags"]
    assert flags == sorted(set(flags))  # deduped + sorted
    assert "public-access" in flags
    assert "open-ingress" in flags
    # raw rule text / ids never leak
    assert all("sg-" not in f and "0.0.0.0" not in f for f in flags)


def test_notes_redacted_from_findings() -> None:
    [c] = sanitize_constraints([_snapshot()])
    notes = c["notes"]
    assert all("0.0.0.0" not in n for n in notes)
    assert any("[redacted]" in n for n in notes)


def test_error_kept_and_clipped() -> None:
    long_err = "boom " * 100  # >200 chars
    [c] = sanitize_constraints([_snapshot(error=long_err)])
    assert "error" in c
    assert len(c["error"]) <= 200


# ── caps ─────────────────────────────────────────────────────────────────────


def test_constraints_capped_at_eight() -> None:
    out = sanitize_constraints([_snapshot() for _ in range(20)])
    assert len(out) == 8


def test_instance_types_capped_at_twenty() -> None:
    big = {f"t{i}.x": 1 for i in range(50)}
    snap = _snapshot(resources={"instance_types": big})
    [c] = sanitize_constraints([snap])
    assert len(c["resources"]["instance_types"]) == 20


def test_notes_capped_at_ten() -> None:
    snap = _snapshot(findings=[f"finding {i}" for i in range(30)])
    [c] = sanitize_constraints([snap])
    assert len(c["notes"]) == 10


def test_32kb_budget_drops_notes_then_entries() -> None:
    # Make notes the dominant payload, then inflate non-note content so that even
    # after notes are dropped we may still need to trim tail entries. 8 snapshots
    # × 10 notes × 200 redact-free chars ≈ 16KB; bulk up instance_types so the
    # post-notes residue plus notes blows past 32KB and forces the drop path.
    huge_findings = ["word " * 60 for _ in range(10)]  # clipped to 200 each
    snaps = []
    for _ in range(8):
        s = _snapshot(findings=list(huge_findings))
        # regions aren't count-capped: many long region names per snapshot push
        # the non-note residue well past 32KB on its own.
        s["resources"] = {
            "regions": [f"region-{i}-{'q' * 200}" for i in range(120)],
        }
        snaps.append(s)
    out = sanitize_constraints(snaps)
    blob = json.dumps(out).encode("utf-8")
    assert len(blob) <= 32 * 1024
    # notes were the first thing dropped under pressure
    assert all("notes" not in c for c in out) or len(out) < 8


# ── malformed / degraded ─────────────────────────────────────────────────────


def test_malformed_infra_never_raises() -> None:
    assert sanitize_constraints(None) == []
    assert sanitize_constraints("not a list") == []
    assert sanitize_constraints([None, 42, "x", {}]) == []
    assert sanitize_constraints([{"adapter": "aws"}]) == [{"adapter": "aws"}]


def test_knowledge_links_deduped_and_capped() -> None:
    entries = [{"uri": "u", "title": "t"}, {"uri": "u", "title": "t"}]
    entries += [{"uri": f"u{i}", "title": f"t{i}"} for i in range(40)]
    links = sanitize_knowledge_links(entries)
    assert len(links) <= 25
    # the duplicate (u,t) collapsed to one
    assert links.count({"uri": "u", "title": "t"}) == 1


def test_knowledge_links_malformed_never_raises() -> None:
    assert sanitize_knowledge_links(None) == []
    assert sanitize_knowledge_links([1, "x", None]) == []


# ── attach_constraints keeps contract schema-valid ───────────────────────────


def _plan_with_enrichment() -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="001-widget",
        title="Widget",
        source_format="markdown",
        criteria=[Criterion(id="AC#1", text="exposes an API")],
        enrichment=Enrichment(
            infra=[_snapshot()],
            knowledge=[{"uri": "https://wiki/x", "title": "Runbook"}],
        ),
    ).with_hash()


def _epic() -> EpicPlan:
    return EpicPlan(
        plan_id="001-widget",
        epic_title="Widget",
        children=[ChildIssue(key="C1", title="Build", kind="feature")],
    )


def test_attach_constraints_keeps_contract_valid() -> None:
    contract = build_task_contract(_plan_with_enrichment(), _epic())
    result = attach_constraints(contract, _plan_with_enrichment())
    assert result is contract  # composable: returns the same contract
    assert validate_contract(contract) == []
    ec = contract["epic_context"]
    assert ec["constraints"][0]["adapter"] == "aws"
    assert ec["knowledge_links"] == [{"uri": "https://wiki/x", "title": "Runbook"}]


def test_attach_constraints_creates_epic_context_when_absent() -> None:
    contract = build_task_contract(_plan_with_enrichment(), _epic())
    assert "epic_context" not in contract  # builder doesn't create it
    attach_constraints(contract, _plan_with_enrichment())
    assert "epic_context" in contract


def test_attach_constraints_preserves_existing_epic_context() -> None:
    contract = build_task_contract(_plan_with_enrichment(), _epic())
    contract["epic_context"] = {"summary": "keep me"}
    attach_constraints(contract, _plan_with_enrichment())
    assert contract["epic_context"]["summary"] == "keep me"
    assert "constraints" in contract["epic_context"]


def test_attach_constraints_marks_truncated_when_capped() -> None:
    plan = NormalizedPlan(
        plan_id="001-widget", title="Widget", source_format="markdown",
        criteria=[Criterion(id="AC#1", text="x")],
        enrichment=Enrichment(infra=[_snapshot() for _ in range(20)]),
    ).with_hash()
    contract = build_task_contract(plan, _epic())
    attach_constraints(contract, plan)
    assert contract["epic_context"]["truncated"] is True


def test_attach_constraints_never_raises_on_bad_plan() -> None:
    contract = build_task_contract(_plan_with_enrichment(), _epic())

    class Bad:
        enrichment = "not an enrichment"

    # must not raise; degrades to empty constraints
    attach_constraints(contract, Bad())
    assert contract["epic_context"]["constraints"] == []
