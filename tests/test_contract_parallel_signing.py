"""Task Contract parallelism (#65 child 4) + signing (#65 child 8)."""

from __future__ import annotations

import hashlib
import hmac

from plan.decompose.models import ChildIssue, EpicPlan
from plan.emit.contract_builder import build_phases, build_task_contract
from plan.emit.execution_profile import (
    attach_execution,
    build_execution,
    derive_parallelism,
)
from plan.emit.signing import (
    APPROVAL_KEY,
    _canonical,
    _signing_bytes,
    attach_signature,
    key_from_env,
    sign_contract,
)
from plan.emit.task_contract import validate_contract
from plan.models import Criterion, NormalizedPlan

TS = "2026-06-06T12:00:00Z"
KEY = "k-pfactory"


def _plan(**kw) -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="001-widget", title="Widget", source_format="markdown",
        criteria=[Criterion(id="AC#1", text="works")], **kw,
    ).with_hash()


def _epic(children: list[ChildIssue]) -> EpicPlan:
    return EpicPlan(plan_id="001-widget", epic_title="Widget", children=children)


# ---- parallelism (#69) ----------------------------------------------------

def test_fanout_epic_is_parallel():
    epic = _epic([
        ChildIssue(key="C1", title="a"),
        ChildIssue(key="C2", title="b"),
        ChildIssue(key="C3", title="c"),
    ])
    parallel, workers = derive_parallelism(epic)
    assert parallel is True and workers == 3
    # phase is marked parallel_safe
    phases = build_phases(epic)
    assert len(phases) == 1 and phases[0]["parallel_safe"] is True


def test_chain_epic_is_serial():
    epic = _epic([
        ChildIssue(key="C1", title="a"),
        ChildIssue(key="C2", title="b", depends_on=["C1"]),
        ChildIssue(key="C3", title="c", depends_on=["C2"]),
    ])
    parallel, workers = derive_parallelism(epic)
    assert parallel is False and workers == 1
    assert all(ph["parallel_safe"] is False for ph in build_phases(epic))


def test_workers_capped_at_four():
    epic = _epic([ChildIssue(key=f"C{i}", title=str(i)) for i in range(6)])
    _, workers = derive_parallelism(epic)
    assert workers == 4  # capped


def test_execution_block_carries_parallel_fields():
    epic = _epic([ChildIssue(key="C1", title="a"), ChildIssue(key="C2", title="b")])
    ex = build_execution(_plan(), epic)
    assert ex["parallel"] is True and ex["workers"] == 2


# ---- signing (#73) --------------------------------------------------------

def _contract():
    epic = _epic([
        ChildIssue(key="C1", title="Scaffold", kind="infra"),
        ChildIssue(key="C2", title="Build", kind="feature", depends_on=["C1"]),
    ])
    return attach_execution(build_task_contract(_plan(), epic), _plan(), epic)


def test_sign_contract_matches_manual_hmac():
    contract = _contract()
    env = sign_contract(contract, key=KEY, approval_timestamp=TS)
    expected = hmac.new(
        KEY.encode(), _signing_bytes(contract, "pfactory", TS, "2"), hashlib.sha256
    ).hexdigest()
    assert env["signature"] == expected
    assert env["approved_by"] == "pfactory"
    assert env["plan_contract_version"] == "2"


def test_attach_signature_embeds_and_stays_valid():
    contract = _contract()
    signed = attach_signature(contract, key=KEY, approval_timestamp=TS)
    assert APPROVAL_KEY in signed
    assert validate_contract(signed) == []  # still schema-valid with approval


def test_tampering_breaks_signature():
    contract = _contract()
    env = sign_contract(contract, key=KEY, approval_timestamp=TS)
    contract["execution"]["workers"] = 99  # tamper after signing
    expected = hmac.new(
        KEY.encode(), _signing_bytes(contract, "pfactory", TS, "2"), hashlib.sha256
    ).hexdigest()
    assert env["signature"] != expected  # signature no longer matches the bytes


def test_canonical_is_deterministic_and_compact():
    # Locks the canonical form so it cannot drift from AIFactory's verifier.
    assert _canonical({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_key_from_env():
    assert key_from_env("pfactory", {"AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY": "x"}) == "x"
    assert key_from_env("pfactory", {}) is None
