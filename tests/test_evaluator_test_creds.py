"""Tests for the Evaluator → test-target credential wiring (#107).

Covers `_test_credential_specs`: turning a subtask's `auth: {type: ref}` target
into a TargetCredentialSpec the sandbox resolver injects as login env. No broker,
no Docker — just the snapshot→spec bridge.
"""

from __future__ import annotations

import json
from pathlib import Path

from agents.evaluator import _test_credential_specs


def _snapshot(spec_dir: Path, targets: list[dict], test_credentials: dict) -> None:
    ctx = spec_dir / "context"
    ctx.mkdir(parents=True, exist_ok=True)
    (ctx / "pfactory_yml.json").write_text(
        json.dumps({"targets": targets, "test_credentials": test_credentials})
    )


_REF_TARGET = {
    "name": "app",
    "type": "http",
    "base_url": "https://app.example.com",
    "auth": {"type": "ref", "ref": "app-login"},
}
_TEST_CREDS = {
    "app-login": {
        "ref": "env:APP_PASSWORD",
        "as_secret": "TEST_PASSWORD",
        "as_username": "TEST_USERNAME",
        "username_ref": "env:APP_USERNAME",
    }
}


def test_specs_built_for_ref_auth_target(tmp_path) -> None:
    _snapshot(tmp_path, [_REF_TARGET], _TEST_CREDS)
    specs = _test_credential_specs(tmp_path, {"target_name": "app"})
    assert len(specs) == 1
    s = specs[0]
    assert s.name == "app-login"
    assert s.ref == "env:APP_PASSWORD" and s.as_secret == "TEST_PASSWORD"
    assert s.as_username == "TEST_USERNAME" and s.username_ref == "env:APP_USERNAME"


def test_no_specs_for_non_ref_auth(tmp_path) -> None:
    http = {"name": "api", "type": "http", "base_url": "x",
            "auth": {"type": "bearer", "token_env": "T"}}
    _snapshot(tmp_path, [http], _TEST_CREDS)
    assert _test_credential_specs(tmp_path, {"target_name": "api"}) == []


def test_no_specs_when_credential_missing(tmp_path) -> None:
    # ref-auth points at a credential name not present in test_credentials
    t = {"name": "app", "type": "http", "base_url": "x",
         "auth": {"type": "ref", "ref": "does-not-exist"}}
    _snapshot(tmp_path, [t], _TEST_CREDS)
    assert _test_credential_specs(tmp_path, {"target_name": "app"}) == []


def test_no_specs_without_subtask_or_snapshot(tmp_path) -> None:
    assert _test_credential_specs(tmp_path, None) == []  # no subtask
    assert _test_credential_specs(tmp_path, {"target_name": "app"}) == []  # no snapshot


def test_specs_resolve_through_sandbox_resolver(tmp_path, monkeypatch) -> None:
    # end-to-end through resolve_test_target_credentials with env: refs + egress on
    from tools.runners import sandbox_credentials as sc

    _snapshot(tmp_path, [_REF_TARGET], _TEST_CREDS)
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    monkeypatch.setenv("APP_USERNAME", "alice")
    # resolver imports egress_enabled from the source module at call time
    monkeypatch.setattr("pfactory_secrets.egress.egress_enabled", lambda *_a, **_k: True)

    specs = _test_credential_specs(tmp_path, {"target_name": "app"})
    creds = sc.resolve_test_target_credentials(specs, tmp_path, tmp_path, "host")
    assert creds.env["TEST_PASSWORD"] == "s3cret"
    assert creds.env["TEST_USERNAME"] == "alice"
    creds.wipe()
