"""Tests for config_to_credential_specs (#107, task 4b glue).

Bridges the .pfactory.yml schema (task 3) to the resolver specs (task 2).
Backend-pure → critical lane.
"""

from __future__ import annotations

from pfactory_yml.schema import PFactoryConfig
from tools.runners.sandbox_credentials import config_to_credential_specs


def _cfg(auth: dict | None) -> PFactoryConfig:
    target = {"name": "web", "type": "http", "base_url": "https://staging.example.com"}
    if auth is not None:
        target["auth"] = auth
    return PFactoryConfig.model_validate(
        {
            "version": 1,
            "targets": [target],
            "egress": {"enabled": True},
            "test_credentials": {
                "login": {
                    "ref": "store:tc_1",
                    "as_secret": "TEST_PASSWORD",
                    "as_username": "TEST_USERNAME",
                    "username_ref": "env:TT_USER",
                }
            },
        }
    )


def test_ref_auth_target_yields_a_spec() -> None:
    specs = config_to_credential_specs(_cfg({"type": "ref", "ref": "login"}), "web")
    assert len(specs) == 1
    s = specs[0]
    assert s.name == "login"
    assert s.ref == "store:tc_1"
    assert s.as_secret == "TEST_PASSWORD"
    assert s.as_username == "TEST_USERNAME"
    assert s.username_ref == "env:TT_USER"


def test_non_ref_auth_yields_nothing() -> None:
    cfg = _cfg({"type": "bearer", "token_env": "TOK"})
    assert config_to_credential_specs(cfg, "web") == []


def test_no_auth_yields_nothing() -> None:
    assert config_to_credential_specs(_cfg(None), "web") == []


def test_unknown_target_yields_nothing() -> None:
    cfg = _cfg({"type": "ref", "ref": "login"})
    assert config_to_credential_specs(cfg, "nope") == []


def test_none_config_or_target_yields_nothing() -> None:
    assert config_to_credential_specs(None, "web") == []
    cfg = _cfg({"type": "ref", "ref": "login"})
    assert config_to_credential_specs(cfg, None) == []
