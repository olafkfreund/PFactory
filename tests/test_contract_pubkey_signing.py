"""Ed25519 pubkey_signature emission (#1432). Golden vector shared with TFactory."""

from __future__ import annotations

import base64
import json
import logging
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from plan.decompose.models import ChildIssue, EpicPlan
from plan.emit.contract_emit import emit_contract
from plan.emit.signing import _signing_bytes, key_from_env, sign_pubkey
from plan.models import Criterion, NormalizedPlan
from plan.review.models import LensScore, PlanReview

GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "contract_signature_golden.json").read_text()
)
SEED = GOLDEN["test_only_seed"]
KEY_VAR = "PFACTORY_CONTRACT_SIGNING_KEY__GOLDEN"
TS = "2026-06-06T12:00:00Z"


def _plan() -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="001-widget",
        title="Widget",
        source_format="markdown",
        description="fastapi service tested with pytest",
        criteria=[Criterion(id="AC#1", text="exposes an API")],
    ).with_hash()


def _epic() -> EpicPlan:
    return EpicPlan(
        plan_id="001-widget",
        epic_title="Widget",
        children=[ChildIssue(key="C1", title="Scaffold", kind="infra")],
    )


def _review() -> PlanReview:
    return PlanReview(
        plan_id="001-widget",
        lenses=[LensScore(lens="architecture", score=0.95)],
        aggregate_score=0.95,
        gates_passed=True,
    )


def _emit(env: dict, monkeypatch) -> dict:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return emit_contract(
        _plan(),
        _epic(),
        _review(),
        base_url="http://ai:3101",
        project_id="p1",
        key="hmac-secret",
        approval_timestamp=TS,
    )["contract"]


def test_golden_vector_reproduces_sig():
    env = sign_pubkey(
        GOLDEN["contract"],
        seed_b64=SEED,
        kid=GOLDEN["kid"],
        approved_by=GOLDEN["approved_by"],
        approval_timestamp=GOLDEN["approval_timestamp"],
        contract_version=GOLDEN["contract_version"],
    )
    assert env == {"alg": "ed25519", "kid": "golden", "sig": GOLDEN["sig"]}


def test_golden_vector_verifies_with_public_key():
    msg = _signing_bytes(
        GOLDEN["contract"],
        GOLDEN["approved_by"],
        GOLDEN["approval_timestamp"],
        GOLDEN["contract_version"],
        kid=GOLDEN["kid"],
    )
    Ed25519PublicKey.from_public_bytes(base64.b64decode(GOLDEN["public_key"])).verify(
        base64.b64decode(GOLDEN["sig"]), msg
    )


def test_emit_adds_pubkey_signature_and_keeps_hmac(monkeypatch):
    plain = _emit({}, monkeypatch)
    assert "pubkey_signature" not in plain["approval"]
    signed = _emit({KEY_VAR: SEED}, monkeypatch)
    ps = signed["approval"]["pubkey_signature"]
    assert ps["alg"] == "ed25519" and ps["kid"] == "golden"
    assert signed["approval"]["signature"] == plain["approval"]["signature"]


def test_kidless_key_adds_nothing(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING):
        c = _emit({"PFACTORY_CONTRACT_SIGNING_KEY": SEED}, monkeypatch)
    assert "pubkey_signature" not in c["approval"]
    assert "no kid" in caplog.text and SEED not in caplog.text


@pytest.mark.parametrize(
    "env",
    [
        {KEY_VAR: "not base64!!"},
        {KEY_VAR: base64.b64encode(b"short").decode()},
        {KEY_VAR: SEED, "PFACTORY_CONTRACT_SIGNING_KEY__OTHER": SEED},  # ambiguous kid
    ],
)
def test_bad_config_warns_and_keeps_hmac(env, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING):
        c = _emit(env, monkeypatch)
    assert "pubkey_signature" not in c["approval"]
    assert c["approval"]["signature"]
    assert "Ed25519 contract signing skipped" in caplog.text
    assert all(v not in caplog.text for v in env.values() if v != "not base64!!")


def test_missing_cryptography_warns_and_keeps_hmac(monkeypatch, caplog):
    # A None entry makes the lazy import in sign_pubkey raise ImportError.
    monkeypatch.setitem(sys.modules, "cryptography.hazmat.primitives.asymmetric.ed25519", None)
    with caplog.at_level(logging.WARNING):
        c = _emit({KEY_VAR: SEED}, monkeypatch)
    assert "pubkey_signature" not in c["approval"]
    assert c["approval"]["signature"]
    assert "Ed25519 contract signing skipped" in caplog.text and SEED not in caplog.text


def test_key_from_env_defaults_unchanged():
    env = {"AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY__K1": "x"}
    assert key_from_env("pfactory", env) == ("x", "k1")
    assert key_from_env("", {KEY_VAR: "s"}, prefix="PFACTORY_CONTRACT_SIGNING_KEY") == (
        "s",
        "golden",
    )
