"""Tests for the CloudProviderTarget schema (#133 / #134).

Backend-pure: constructs PFactoryConfig models directly (no cloud calls).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from pfactory_yml.schema import (
    CloudProviderTarget,
    CloudScanConfig,
    EgressConfig,
    RefAuth,
    PFactoryConfig,
)
from pfactory_yml.schema import (
    TestCredentialEntry as CredEntry,  # avoid Test* collection
)


def _cloud(**over):
    base = {"type": "cloud_provider", "name": "aws-prod", "provider": "aws"}
    base.update(over)
    return CloudProviderTarget(**base)


def _config(targets, *, egress=True, **extra):
    return PFactoryConfig(
        version=1,
        targets=targets,
        egress=EgressConfig(enabled=egress),
        **extra,
    )


# ── target shape + defaults ──────────────────────────────────────────────────


def test_cloud_target_parses_with_profile_and_role() -> None:
    t = _cloud(
        regions=["us-east-1", "eu-west-2"],
        profile="Calitii",
        assume_role="arn:aws:iam::123456789012:role/pfactory-readonly",
    )
    assert t.provider == "aws"
    assert t.regions == ["us-east-1", "eu-west-2"]
    assert t.profile == "Calitii"
    assert t.assume_role.endswith(":role/pfactory-readonly")


def test_scan_defaults() -> None:
    t = _cloud()
    assert t.scan.discover is True
    assert t.scan.misconfiguration is True
    assert t.scan.compliance == ["cis"]
    assert t.scan.fail_on_severity == "high"
    assert t.scan.services == []


def test_scan_overrides() -> None:
    t = _cloud(scan=CloudScanConfig(services=["s3", "iam"], fail_on_severity="critical"))
    assert t.scan.services == ["s3", "iam"]
    assert t.scan.fail_on_severity == "critical"


def test_invalid_provider_rejected() -> None:
    with pytest.raises(ValidationError):
        _cloud(provider="digitalocean")


def test_invalid_fail_on_severity_rejected() -> None:
    with pytest.raises(ValidationError):
        _cloud(scan=CloudScanConfig(fail_on_severity="apocalyptic"))


# ── egress fail-closed ───────────────────────────────────────────────────────


def test_cloud_target_requires_egress() -> None:
    with pytest.raises(ValidationError, match="egress"):
        _config([_cloud()], egress=False)


def test_cloud_target_ok_with_egress() -> None:
    cfg = _config([_cloud()], egress=True)
    assert cfg.lookup_target("aws-prod") is not None
    assert cfg.lookup_target("aws-prod").provider == "aws"


# ── ref-auth on a cloud target ───────────────────────────────────────────────


def test_cloud_target_ref_auth_resolves_to_declared_credential() -> None:
    cfg = _config(
        [_cloud(auth=RefAuth(type="ref", ref="aws-key"))],
        egress=True,
        test_credentials={
            "aws-key": CredEntry(ref="env:AWS_SECRET", as_secret="AWS_SECRET")
        },
    )
    t = cfg.lookup_target("aws-prod")
    assert t.auth is not None and t.auth.type == "ref" and t.auth.ref == "aws-key"


def test_cloud_target_ref_auth_unknown_credential_rejected() -> None:
    with pytest.raises(ValidationError, match="does not"):
        _config(
            [_cloud(auth=RefAuth(type="ref", ref="missing"))],
            egress=True,
            test_credentials={},
        )
