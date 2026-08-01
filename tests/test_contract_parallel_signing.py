"""Task Contract parallelism (#65 child 4) + signing (#65 child 8)."""

from __future__ import annotations

import hashlib
import hmac

import pytest

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
    legacy = {"AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY": "x"}
    assert key_from_env("pfactory", legacy) == ("x", None)
    assert key_from_env("pfactory", {}) == (None, None)


# ---- key ids / rotation (#401) --------------------------------------------


def test_keyed_env_var_yields_the_kid_alongside_the_key():
    # A keyed var is what makes the signature revocable: AIFactory's
    # AIFACTORY_TRUSTED_PLAN_RETIRED_KIDS can only name a kid it can see.
    env = {"AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY__2026Q3": "rotated"}
    assert key_from_env("pfactory", env) == ("rotated", "2026q3")


def test_keyed_var_wins_over_the_legacy_var():
    env = {
        "AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY": "legacy",
        "AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY__2026Q3": "rotated",
    }
    assert key_from_env("pfactory", env) == ("rotated", "2026q3")


def test_pinned_kid_selects_among_several_keyed_vars():
    env = {
        "AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY__2026Q2": "old",
        "AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY__2026Q3": "new",
        "PFACTORY_TRUSTED_PLAN_KID": "2026Q2",
    }
    assert key_from_env("pfactory", env) == ("old", "2026q2")


def test_ambiguous_key_config_raises_rather_than_guessing():
    env = {
        "AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY__2026Q2": "old",
        "AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY__2026Q3": "new",
    }
    with pytest.raises(ValueError, match="PFACTORY_TRUSTED_PLAN_KID"):
        key_from_env("pfactory", env)


def test_pinned_kid_without_its_key_raises():
    # Falling back to the legacy key here would silently emit the unrevocable
    # contract that #401 exists to prevent.
    env = {
        "AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY": "legacy",
        "PFACTORY_TRUSTED_PLAN_KID": "2026Q3",
    }
    with pytest.raises(ValueError, match="2026Q3.*is not set"):
        key_from_env("pfactory", env)


def test_sign_with_kid_stamps_and_binds_it():
    contract = _contract()
    env = sign_contract(contract, key=KEY, approval_timestamp=TS, kid="2026q3")
    assert env["kid"] == "2026q3"
    # AIFactory appends the kid to the signed parts; recompute it that way.
    expected = hmac.new(
        KEY.encode(),
        _signing_bytes(contract, "pfactory", TS, "2", "2026q3"),
        hashlib.sha256,
    ).hexdigest()
    assert env["signature"] == expected


def test_relabelling_the_kid_breaks_the_signature():
    # The kid is bound into the bytes, so a captured envelope cannot be
    # re-pointed at a key that has not been retired.
    contract = _contract()
    env = sign_contract(contract, key=KEY, approval_timestamp=TS, kid="2026q3")
    relabelled = hmac.new(
        KEY.encode(),
        _signing_bytes(contract, "pfactory", TS, "2", "2026q4"),
        hashlib.sha256,
    ).hexdigest()
    assert env["signature"] != relabelled


def test_no_kid_envelope_is_byte_identical_to_the_legacy_one():
    # Back-compat: in-flight contracts and unkeyed deployments must not move.
    contract = _contract()
    legacy_bytes = "|".join(
        (_canonical({k: v for k, v in contract.items() if k != APPROVAL_KEY}),
         "pfactory", TS, "2")
    ).encode("utf-8")
    assert _signing_bytes(contract, "pfactory", TS, "2") == legacy_bytes
    assert _signing_bytes(contract, "pfactory", TS, "2", None) == legacy_bytes
    assert "kid" not in sign_contract(contract, key=KEY, approval_timestamp=TS)
