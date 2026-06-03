"""Operator credential config — ~/.pfactory/credentials.json (#71).

Covers the formalised schema + loader and the broker's delegation to it:
  - typed schema: cloud + named credentials, `as` alias, extra-forbid
  - loader: missing / malformed / non-object → empty; valid → populated
  - loose file mode → warning (still loads)
  - broker._cloud_config / _operator_credentials read through the loader
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from pfactory_secrets.operator_config import (
    OperatorCredentialsConfig,
    load_operator_config,
)


def _write(path: Path, data: dict, mode: int = 0o600) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(mode)
    return path


def test_schema_parses_cloud_and_named() -> None:
    cfg = OperatorCredentialsConfig.model_validate(
        {
            "cloud": {
                "gcp": {"ref": "gcp-sm://p/sa", "as": "GOOGLE_APPLICATION_CREDENTIALS", "kind": "file"},
            },
            "credentials": {
                "staging-db": {"ref": "vault:secret/data/db#url", "as": "DATABASE_URL"},
            },
        }
    )
    assert cfg.cloud["gcp"].ref == "gcp-sm://p/sa"
    assert cfg.cloud["gcp"].as_ == "GOOGLE_APPLICATION_CREDENTIALS"  # `as` alias
    assert cfg.cloud["gcp"].kind == "file"
    assert cfg.credentials["staging-db"].kind == "env"  # default


def test_entry_rejects_unknown_field() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        OperatorCredentialsConfig.model_validate(
            {"credentials": {"x": {"ref": "env:TOKEN", "bogus": 1}}}
        )


def test_load_missing_file_is_empty(tmp_path: Path) -> None:
    cfg = load_operator_config(tmp_path / "nope.json")
    assert cfg.cloud == {} and cfg.credentials == {}


def test_load_malformed_json_is_empty(tmp_path: Path) -> None:
    p = tmp_path / "credentials.json"
    p.write_text("{not json", encoding="utf-8")
    p.chmod(0o600)
    assert load_operator_config(p).credentials == {}


def test_load_non_object_is_empty(tmp_path: Path) -> None:
    p = _write(tmp_path / "credentials.json", {})
    p.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_operator_config(p).cloud == {}


def test_load_valid_populates(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "credentials.json",
        {"credentials": {"db": {"ref": "vault:secret/data/db#url", "as": "DATABASE_URL"}}},
    )
    cfg = load_operator_config(p)
    assert cfg.credentials["db"].ref == "vault:secret/data/db#url"
    assert cfg.credentials["db"].as_ == "DATABASE_URL"


def test_loose_mode_warns_but_loads(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    p = _write(
        tmp_path / "credentials.json",
        {"cloud": {"aws": {"ref": "aws-sm://s/api#token", "as": "AWS_TOKEN"}}},
        mode=0o644,  # group/world-readable
    )
    with caplog.at_level(logging.WARNING):
        cfg = load_operator_config(p)
    assert cfg.cloud["aws"].ref == "aws-sm://s/api#token"  # still loads
    assert any("recommend chmod 600" in r.message for r in caplog.records)


def test_broker_reads_operator_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pfactory_secrets import broker

    p = _write(
        tmp_path / "credentials.json",
        {
            "cloud": {"gcp": {"ref": "gcp-sm://p/sa", "as": "GOOGLE_APPLICATION_CREDENTIALS", "kind": "file"}},
            "credentials": {"db": {"ref": "env:DATABASE_URL", "as": "DATABASE_URL"}},
        },
    )
    monkeypatch.setattr(broker, "CREDENTIALS_CONFIG_PATH", p)
    broker.reset_config_cache()
    try:
        cloud = broker._cloud_config()
        named = broker._operator_credentials()
        assert cloud["gcp"]["ref"] == "gcp-sm://p/sa"
        assert cloud["gcp"]["as"] == "GOOGLE_APPLICATION_CREDENTIALS"  # dict shape, aliased
        assert named["db"]["ref"] == "env:DATABASE_URL"
    finally:
        broker.reset_config_cache()
