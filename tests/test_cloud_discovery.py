"""Tests for the cloud discovery primitive (#133/#135).

Backend-pure: the provider CLI runner is injected with canned JSON — no real
cloud, no network, no mutations.
"""

from __future__ import annotations

import json

import pytest
from agents.cloud.discovery import (
    AccessResult,
    CloudDiscoveryError,
    access_check,
    discover,
)


def _cmd(returncode: int, stdout: str):
    return type("C", (), {"returncode": returncode, "stdout": stdout})()


def _aws_runner(*, identity_ok=True, fail=None):
    """Return a runner that answers AWS read-only calls from canned data.

    ``fail`` is a set of argv-substrings whose calls should return rc=1.
    """
    fail = fail or set()

    def run(argv):
        joined = " ".join(argv)
        if any(f in joined for f in fail):
            return _cmd(1, "")
        if "get-caller-identity" in joined:
            if not identity_ok:
                return _cmd(1, "")
            return _cmd(0, json.dumps({
                "Account": "533267307120",
                "Arn": "arn:aws:iam::533267307120:user/Olaf.Freund",
            }))
        if "list-buckets" in joined:
            return _cmd(0, json.dumps([{"Name": f"b{i}"} for i in range(12)]))
        if "get-account-summary" in joined:
            return _cmd(0, json.dumps({"SummaryMap": {"Users": 18, "Roles": 121, "Policies": 113}}))
        if "describe-vpcs" in joined:
            return _cmd(0, json.dumps({"Vpcs": [{}, {}, {}]}))
        if "describe-instances" in joined:
            return _cmd(0, json.dumps({"Reservations": [{}, {}, {}]}))
        if "list-functions" in joined:
            return _cmd(0, json.dumps({"Functions": [{}] * 10}))
        return _cmd(0, "{}")

    return run


# ── access_check ─────────────────────────────────────────────────────────────


def test_access_check_aws_success() -> None:
    r = access_check("aws", profile="Calitii", runner=_aws_runner())
    assert isinstance(r, AccessResult)
    assert r.ok is True
    assert r.account == "533267307120"
    assert r.identity == "Olaf.Freund"


def test_access_check_aws_failure() -> None:
    r = access_check("aws", runner=_aws_runner(identity_ok=False))
    assert r.ok is False
    assert r.error


def test_access_check_passes_profile() -> None:
    seen = {}

    def run(argv):
        seen["argv"] = argv
        return _cmd(0, json.dumps({"Account": "1", "Arn": "arn:aws:iam::1:user/x"}))

    access_check("aws", profile="Calitii", runner=run)
    assert "--profile" in seen["argv"] and "Calitii" in seen["argv"]


def test_access_check_unsupported_provider_raises() -> None:
    with pytest.raises(CloudDiscoveryError):
        access_check("digitalocean")


def _gcp_runner():
    """Runner answering host gcloud read-only calls from canned JSON."""

    def run(argv):
        joined = " ".join(argv)
        if "auth list" in joined:
            return _cmd(0, "olaf@freundcloud.com\n")
        if "config get-value project" in joined:
            return _cmd(0, "sarc-493418\n")
        if "buckets list" in joined:
            return _cmd(0, json.dumps([{"name": f"bkt{i}"} for i in range(4)]))
        if "service-accounts list" in joined:
            return _cmd(0, json.dumps([{"email": "a"}, {"email": "b"}]))
        return _cmd(0, "[]")

    return run


def _azure_runner():
    """Runner answering host az read-only calls from canned JSON."""

    def run(argv):
        joined = " ".join(argv)
        if "account show" in joined:
            return _cmd(0, json.dumps({
                "id": "46b2dfbe-fe9e-4433-b327-b2dc32c8af5e",
                "name": "Development",
                "user": {"name": "olaf.freund@outlook.com"},
            }))
        if "group list" in joined:
            return _cmd(0, json.dumps([{"name": "rg1"}, {"name": "rg2"}, {"name": "rg3"}]))
        if "storage account list" in joined:
            return _cmd(0, json.dumps([{"name": "sa1"}]))
        if "vm list" in joined:
            return _cmd(0, json.dumps([{"name": "vm1"}, {"name": "vm2"}]))
        return _cmd(0, "[]")

    return run


def test_access_check_gcp_success() -> None:
    r = access_check("gcp", runner=_gcp_runner())
    assert r.ok is True
    assert r.account == "sarc-493418"
    assert r.identity == "olaf@freundcloud.com"


def test_access_check_gcp_profile_overrides_project() -> None:
    r = access_check("gcp", profile="other-proj", runner=_gcp_runner())
    assert r.account == "other-proj"


def test_access_check_azure_success() -> None:
    r = access_check("azure", runner=_azure_runner())
    assert r.ok is True
    assert r.account == "46b2dfbe-fe9e-4433-b327-b2dc32c8af5e"
    assert r.identity == "olaf.freund@outlook.com"


def test_access_check_azure_failure() -> None:
    r = access_check("azure", runner=lambda argv: _cmd(1, ""))
    assert r.ok is False and r.error


def test_assumed_role_arn_name() -> None:
    def run(argv):
        return _cmd(0, json.dumps({
            "Account": "1",
            "Arn": "arn:aws:sts::1:assumed-role/AdminRole/session-123",
        }))

    r = access_check("aws", runner=run)
    assert r.identity == "AdminRole"


# ── discover ─────────────────────────────────────────────────────────────────


def test_discover_builds_inventory() -> None:
    inv = discover(
        "aws", profile="Calitii", regions=["us-east-1", "eu-west-2"], runner=_aws_runner()
    )
    assert inv["provider"] == "aws"
    assert inv["account"] == "533267307120"
    assert inv["identity"] == "Olaf.Freund"
    assert inv["global"]["s3"]["count"] == 12
    assert inv["global"]["iam"] == {"users": 18, "roles": 121, "policies": 113}
    assert inv["regions"]["us-east-1"] == {"vpcs": 3, "instances": 3, "lambdas": 10}
    assert inv["regions"]["eu-west-2"]["vpcs"] == 3


def test_discover_shape_feeds_render_cloud_topology() -> None:
    from agents.diagrams import render_cloud_topology

    inv = discover("aws", regions=["us-east-1"], runner=_aws_runner())
    out = render_cloud_topology(inv)
    assert out.startswith("graph LR\n")
    assert "AWS Account 533267307120" in out
    assert "📍 us-east-1" in out


def test_discover_services_filter_restricts_global_calls() -> None:
    inv = discover("aws", services=["s3"], runner=_aws_runner())
    assert "s3" in inv["global"]
    assert "iam" not in inv["global"]  # iam filtered out


def test_discover_access_failure_returns_error_inventory() -> None:
    inv = discover("aws", regions=["us-east-1"], runner=_aws_runner(identity_ok=False))
    assert inv["error"]
    assert inv["regions"] == {}  # no enumeration without access


def test_discover_tolerates_denied_service_calls() -> None:
    # iam denied → that section simply omitted, the rest still populates.
    inv = discover("aws", runner=_aws_runner(fail={"get-account-summary"}))
    assert "s3" in inv["global"]
    assert "iam" not in inv["global"]


def test_discover_unsupported_provider_raises() -> None:
    with pytest.raises(CloudDiscoveryError):
        discover("oracle")


def test_discover_gcp_builds_inventory() -> None:
    inv = discover("gcp", runner=_gcp_runner())
    assert inv["provider"] == "gcp"
    assert inv["account"] == "sarc-493418"
    assert inv["global"]["storage"]["count"] == 4
    assert inv["global"]["iam"]["service_accounts"] == 2
    assert inv["regions"] == {}  # GCP enumerated globally, not per-region here


def test_discover_azure_builds_inventory() -> None:
    inv = discover("azure", runner=_azure_runner())
    assert inv["provider"] == "azure"
    assert inv["account"] == "46b2dfbe-fe9e-4433-b327-b2dc32c8af5e"
    assert inv["global"]["resource_groups"]["count"] == 3
    assert inv["global"]["storage"]["count"] == 1
    assert inv["global"]["compute"]["vms"] == 2


def test_discover_gcp_services_filter() -> None:
    inv = discover("gcp", services=["storage"], runner=_gcp_runner())
    assert "storage" in inv["global"] and "iam" not in inv["global"]


def test_discover_gcp_and_azure_feed_render_topology() -> None:
    from agents.diagrams import render_cloud_topology

    gcp = render_cloud_topology(discover("gcp", runner=_gcp_runner()))
    assert gcp.startswith("graph LR\n")
    assert "GCP Account sarc-493418" in gcp
    assert "STORAGE · 4" in gcp and "IAM · service_accounts 2" in gcp

    az = render_cloud_topology(discover("azure", runner=_azure_runner()))
    assert "AZURE Account 46b2dfbe" in az
    assert "RESOURCE GROUPS · 3" in az and "COMPUTE · vms 2" in az
