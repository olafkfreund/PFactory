"""RFC-0007 (#84): access classification + the contract access block.

Pure classifier — given .pfactory.yml targets + spec text, produce
access.requirements classified A/B/C/D. The output is also validated against the
PFactory task-contract schema copy so the two stay in lockstep.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from plan.access_discovery import (  # noqa: E402
    classify_target,
    detect_interactive_mfa,
    discover_access,
)

_SCHEMA = (
    _BACKEND / "plan" / "emit" / "contracts" / "task-contract.schema.json"
)


def _cls(target, **kw):
    return classify_target(target, **kw)["auth_class"]


def test_machine_native_auth_types_are_class_A():
    for atype in ("none", "bearer", "oauth2_client_credentials", "serviceaccount", "mtls"):
        assert _cls({"name": "t", "type": "http", "auth": {"type": atype}}) == "A-machine-native"


def test_human_credential_types_are_class_B():
    assert _cls({"name": "web", "type": "http", "auth": {"type": "basic",
                "username_env": "U", "password_env": "P"}}) == "B-bootstrap-once"
    assert _cls({"name": "web", "type": "http", "auth": {"type": "ref",
                "ref": "store:tc_1"}}) == "B-bootstrap-once"


def test_docker_compose_target_is_class_C_regardless_of_auth():
    assert _cls({"name": "local", "type": "docker_compose", "auth": {"type": "basic",
                "username_env": "U", "password_env": "P"}}) == "C-ephemeral-target"


def test_interactive_mfa_escalates_B_to_D_but_not_A_or_C():
    spec = "Users log in via a push notification approved in the app."
    assert detect_interactive_mfa(spec)
    b = _cls({"name": "web", "type": "http", "auth": {"type": "ref", "ref": "store:x"}}, interactive_mfa=True)
    a = _cls({"name": "api", "type": "http", "auth": {"type": "bearer", "token_env": "T"}}, interactive_mfa=True)
    c = _cls({"name": "l", "type": "docker_compose"}, interactive_mfa=True)
    assert (b, a, c) == ("D-un-automatable", "A-machine-native", "C-ephemeral-target")


def test_credential_ref_is_a_broker_ref_never_a_secret():
    r = classify_target({"name": "api", "type": "http", "auth": {"type": "bearer", "token_env": "STAGING_TOKEN"}})
    assert r["credential_ref"] == "env:STAGING_TOKEN"
    assert r["env_required"] == ["STAGING_TOKEN"]
    r2 = classify_target({"name": "web", "type": "http", "auth": {"type": "ref", "ref": "store:tc_9"}})
    assert r2["credential_ref"] == "store:tc_9"


def test_bootstrap_is_none_for_A_C_and_human_for_B_D():
    a = classify_target({"name": "a", "type": "http", "auth": {"type": "serviceaccount", "token_file": "/x"}})
    b = classify_target({"name": "b", "type": "http", "auth": {"type": "basic", "username_env": "U", "password_env": "P"}})
    assert a["bootstrap"] == "none" and b["bootstrap"] == "human"


def test_no_targets_returns_none():
    assert discover_access(None) is None
    assert discover_access([]) is None


def test_discovered_block_validates_against_the_schema():
    targets = [
        {"name": "api", "type": "http", "auth": {"type": "bearer", "token_env": "T"}},
        {"name": "web", "type": "http", "auth": {"type": "ref", "ref": "store:tc_1"}},
        {"name": "local", "type": "docker_compose"},
    ]
    block = discover_access(targets, "plain login, no mfa")
    schema = json.loads(_SCHEMA.read_text())
    contract = {
        "contract_version": "2", "feature": "f", "workflow_type": "feature",
        "phases": [{"phase": 1, "name": "p", "subtasks": [{"id": "t1", "description": "d"}]}],
        "access": block,
    }
    # importorskip, not try/except ImportError: the old form left
    # `Draft202012Validator` unbound on the except branch as far as any reader
    # (and any analyzer) could tell -- it only worked because `pytest.skip`
    # happens to raise. The idiomatic call says "skip" in one place.
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft202012Validator(schema)
    errors = [e.message for e in validator.iter_errors(contract)]
    assert errors == [], errors
