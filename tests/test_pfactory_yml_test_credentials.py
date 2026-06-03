"""Tests for the .pfactory.yml test_credentials schema + subtask requires_auth (#107, task 3)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from test_plan.subtask import Subtask
from pfactory_yml.schema import (
    TestCredentialEntry as CredEntry,  # alias: avoid pytest Test* collection
)
from pfactory_yml.schema import (
    PFactoryConfig,
)


def _http_target(auth: dict | None = None) -> dict:
    t = {"name": "web", "type": "http", "base_url": "https://staging.example.com"}
    if auth is not None:
        t["auth"] = auth
    return t


def _cfg(**over) -> dict:
    base = {
        "version": 1,
        "targets": [_http_target()],
        "egress": {"enabled": True},
        "test_credentials": {
            "login": {"ref": "store:tc_1", "as_secret": "TEST_PASSWORD"}
        },
    }
    base.update(over)
    return base


# ── happy path ──────────────────────────────────────────────────────────────


def test_valid_test_credentials_parses() -> None:
    cfg = PFactoryConfig.model_validate(_cfg())
    assert cfg.test_credentials["login"].ref == "store:tc_1"
    assert cfg.test_credentials["login"].as_secret == "TEST_PASSWORD"
    assert cfg.test_credentials["login"].kind == "form"


def test_ref_auth_target_referencing_a_known_credential_parses() -> None:
    cfg = PFactoryConfig.model_validate(
        _cfg(targets=[_http_target(auth={"type": "ref", "ref": "login"})])
    )
    auth = cfg.targets[0].auth
    assert auth.type == "ref" and auth.ref == "login"


# ── fail-closed validation ──────────────────────────────────────────────────


def test_test_credentials_without_egress_is_rejected() -> None:
    with pytest.raises(ValidationError, match="egress.enabled"):
        PFactoryConfig.model_validate(_cfg(egress={"enabled": False}))


def test_ref_auth_naming_unknown_credential_is_rejected() -> None:
    with pytest.raises(ValidationError, match="does not"):
        PFactoryConfig.model_validate(
            _cfg(targets=[_http_target(auth={"type": "ref", "ref": "nope"})])
        )


def test_bad_env_var_name_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CredEntry(ref="env:X", as_secret="1-bad name")


# ── subtask requires_auth round-trip ────────────────────────────────────────


def test_subtask_requires_auth_roundtrips() -> None:
    st = Subtask(id="s1", description="login flow", requires_auth=True)
    d = st.to_dict()
    assert d["requires_auth"] is True
    assert Subtask.from_dict(d).requires_auth is True


def test_subtask_requires_auth_defaults_false_and_is_omitted() -> None:
    st = Subtask(id="s2", description="plain")
    assert st.requires_auth is False
    assert "requires_auth" not in st.to_dict()  # terse: omit at default
    assert Subtask.from_dict({"id": "s3", "description": "legacy"}).requires_auth is False
