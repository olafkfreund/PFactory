"""Tests for the test-target credential resolver (#107, spec task 2).

Pure backend (no web-server deps): exercises the gating + broker ref
resolution. ``store:`` refs are materialised web-server-side, so the backend
resolver only ever sees broker schemes (``env:`` here).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tools.runners.sandbox_credentials import (
    SandboxCredentials,
    TargetCredentialSpec,
    resolve_test_target_credentials,
)


def _spec(**kw) -> TargetCredentialSpec:
    base = {"name": "login", "ref": "env:TT_SECRET", "as_secret": "TEST_PASSWORD"}
    base.update(kw)
    return TargetCredentialSpec(**base)


def _egress(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    monkeypatch.setattr(
        "pfactory_secrets.egress.egress_enabled", lambda _project_dir: enabled
    )


# ── gating: hermetic / no specs / egress off → empty ────────────────────────


@pytest.mark.parametrize("network", [None, "", "none"])
def test_hermetic_lane_is_credential_free(network, tmp_path: Path) -> None:
    out = resolve_test_target_credentials([_spec()], tmp_path, tmp_path, network)
    assert isinstance(out, SandboxCredentials)
    assert out.env == {} and out.broker is None


def test_no_specs_is_empty(tmp_path: Path) -> None:
    assert resolve_test_target_credentials([], tmp_path, tmp_path, "host").env == {}
    assert resolve_test_target_credentials(None, tmp_path, tmp_path, "host").env == {}


def test_egress_off_is_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _egress(monkeypatch, False)
    out = resolve_test_target_credentials([_spec()], tmp_path, tmp_path, "host")
    assert out.env == {} and out.broker is None


# ── resolution (egress on) ──────────────────────────────────────────────────


def test_env_ref_maps_to_as_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _egress(monkeypatch, True)
    monkeypatch.setenv("TT_SECRET", "s3cr3t")
    out = resolve_test_target_credentials([_spec()], tmp_path, tmp_path, "host")
    assert out.env == {"TEST_PASSWORD": "s3cr3t"}
    assert out.broker is not None  # retained for wipe()


def test_username_ref_maps_to_as_username(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _egress(monkeypatch, True)
    monkeypatch.setenv("TT_SECRET", "pw")
    monkeypatch.setenv("TT_USER", "qa@acme.test")
    out = resolve_test_target_credentials(
        [_spec(as_username="TEST_USERNAME", username_ref="env:TT_USER")],
        tmp_path, tmp_path, "host",
    )
    assert out.env == {"TEST_PASSWORD": "pw", "TEST_USERNAME": "qa@acme.test"}


def test_multiple_specs_resolve_independently(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _egress(monkeypatch, True)
    monkeypatch.setenv("A_PW", "a")
    monkeypatch.setenv("B_PW", "b")
    out = resolve_test_target_credentials(
        [
            _spec(name="a", ref="env:A_PW", as_secret="A"),
            _spec(name="b", ref="env:B_PW", as_secret="B"),
        ],
        tmp_path, tmp_path, "host",
    )
    assert out.env == {"A": "a", "B": "b"}


# ── fault tolerance ─────────────────────────────────────────────────────────


def test_store_ref_is_skipped_not_resolved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _egress(monkeypatch, True)
    monkeypatch.setenv("OK_PW", "ok")
    out = resolve_test_target_credentials(
        [
            _spec(name="store", ref="store:tc_123", as_secret="SHOULD_NOT_APPEAR"),
            _spec(name="ok", ref="env:OK_PW", as_secret="OK"),
        ],
        tmp_path, tmp_path, "host",
    )
    assert "SHOULD_NOT_APPEAR" not in out.env
    assert out.env == {"OK": "ok"}


def test_unresolvable_ref_is_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _egress(monkeypatch, True)
    monkeypatch.delenv("MISSING_PW", raising=False)
    monkeypatch.setenv("GOOD_PW", "good")
    out = resolve_test_target_credentials(
        [
            _spec(name="bad", ref="env:MISSING_PW", as_secret="BAD"),
            _spec(name="good", ref="env:GOOD_PW", as_secret="GOOD"),
        ],
        tmp_path, tmp_path, "host",
    )
    assert "BAD" not in out.env
    assert out.env == {"GOOD": "good"}
