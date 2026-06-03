"""Tests for the concrete best-practice MCP providers (issues #23/#24).

Covers the AWS / Azure / GCP cloud providers and the Terraform (Checkov/tfsec)
IaC provider on the :class:`ProviderMCP` base, plus the ``review_runner`` adapter
that feeds provider findings into the review gates' external-policy seam.

Every provider is exercised with an injected *fake* client so no real cloud SDK,
MCP server, or scanner binary is required.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.decompose.models import ChildIssue, EpicPlan  # noqa: E402
from plan.models import Criterion, Enrichment, NormalizedPlan  # noqa: E402
from plan.providers import base as provider_base  # noqa: E402
from plan.providers.aws import AwsProvider  # noqa: E402
from plan.providers.azure import AzureProvider  # noqa: E402
from plan.providers.gcp import GcpProvider  # noqa: E402
from plan.providers.review_runner import build_context, provider_runner  # noqa: E402
from plan.providers.terraform import TerraformProvider  # noqa: E402
from plan.review.gates import run_gates  # noqa: E402
from plan.review.models import Finding  # noqa: E402

# Env vars each provider treats as ambient credentials — cleared in tests.
_ALL_CRED_ENV = (
    "AWS_ACCESS_KEY_ID",
    "AWS_PROFILE",
    "AWS_SESSION_TOKEN",
    "AZURE_SUBSCRIPTION_ID",
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_PROJECT",
    "GCLOUD_PROJECT",
)


@pytest.fixture(autouse=True)
def _no_ambient_creds(monkeypatch):
    """Ensure no ambient cloud credentials leak into availability tests."""
    for var in _ALL_CRED_ENV:
        monkeypatch.delenv(var, raising=False)


# ── fake clients ───────────────────────────────────────────────────────


class _FakeCloudClient:
    """Fake cloud assessment client returning canned ``assess`` output."""

    def __init__(self, items):
        self._items = items

    def assess(self, context):  # noqa: ARG002 - context unused in the fake
        return self._items


class _FakeScanClient:
    """Fake IaC scan client returning canned ``scan`` output."""

    def __init__(self, items):
        self._items = items

    def scan(self, context):  # noqa: ARG002 - context unused in the fake
        return self._items


class _ThrowingClient:
    """Client whose analyse methods always raise."""

    def assess(self, context):
        raise RuntimeError("boom")

    def scan(self, context):
        raise RuntimeError("boom")


# ── AWS provider (#23) ─────────────────────────────────────────────────


def test_aws_check_maps_findings_with_severity_and_source():
    client = _FakeCloudClient(
        [
            {"title": "S3 bucket public", "severity": "critical", "status": "fail"},
            {"title": "IAM over-privileged", "severity": "high"},
            {"title": "passing control", "severity": "low", "status": "pass"},
        ]
    )
    provider = AwsProvider(client=client)
    result = provider.check({"account": "123456789012"})

    assert result.provider == "aws"
    assert result.target == "123456789012"
    titles = [f.title for f in result.findings]
    assert titles == ["S3 bucket public", "IAM over-privileged"]  # pass dropped
    assert all(f.source == "cloud-mcp" for f in result.findings)
    s3 = result.findings[0]
    assert s3.severity == "critical" and s3.blocking is True
    assert result.findings[1].severity == "high" and result.findings[1].blocking is False


def test_aws_available_logic(monkeypatch):
    assert AwsProvider(client=_FakeCloudClient([])).available() is True
    assert AwsProvider().available() is False
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
    # With the env var set, creds-only availability is true.
    assert AwsProvider().available() is True


def test_aws_to_findings_empty_when_unavailable():
    assert AwsProvider().to_findings({}) == []


def test_aws_to_findings_never_raises_on_throwing_client():
    provider = AwsProvider(client=_ThrowingClient())
    # available() is True (client injected); check() swallows the error so
    # to_findings returns [] rather than propagating.
    assert provider.to_findings({}) == []


def test_aws_defensive_on_odd_client_output():
    for junk in (None, "nope", 42, [None, "x", 7], {}):
        provider = AwsProvider(client=_FakeCloudClient(junk))
        assert provider.check({}).findings == []


# ── Azure provider (#23) ───────────────────────────────────────────────


def test_azure_check_maps_policy_findings():
    client = _FakeCloudClient(
        [
            {"policy": "Storage must use HTTPS", "severity": "high", "outcome": "fail"},
            {"policy": "Compliant rule", "severity": "low", "outcome": "pass"},
        ]
    )
    provider = AzureProvider(client=client)
    assert "azure-policy" in provider.capabilities
    result = provider.check({"subscription": "sub-1"})
    assert [f.title for f in result.findings] == ["Storage must use HTTPS"]
    assert result.findings[0].source == "cloud-mcp"
    assert result.findings[0].severity == "high"


def test_azure_available_and_creds(monkeypatch):
    assert AzureProvider(client=_FakeCloudClient([])).available() is True
    assert AzureProvider().available() is False
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant-xyz")
    assert AzureProvider().available() is True


def test_azure_throwing_client_is_safe():
    assert AzureProvider(client=_ThrowingClient()).to_findings({}) == []


# ── GCP provider (#23) ─────────────────────────────────────────────────


def test_gcp_check_maps_org_policy_findings():
    client = _FakeCloudClient(
        [
            {"category": "PUBLIC_BUCKET_ACL", "severity": "critical", "state": "active"},
            {"category": "Resolved", "severity": "high", "state": "resolved"},
        ]
    )
    provider = GcpProvider(client=client)
    assert "org-policy" in provider.capabilities
    result = provider.check({"project": "proj-1"})
    assert [f.title for f in result.findings] == ["PUBLIC_BUCKET_ACL"]
    finding = result.findings[0]
    assert finding.source == "cloud-mcp"
    assert finding.severity == "critical" and finding.blocking is True


def test_gcp_available_and_creds(monkeypatch):
    assert GcpProvider(client=_FakeCloudClient([])).available() is True
    assert GcpProvider().available() is False
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj-1")
    assert GcpProvider().available() is True


# ── Terraform provider (#24) ───────────────────────────────────────────


def test_terraform_check_maps_failed_checks_high_is_blocking():
    client = _FakeScanClient(
        [
            {
                "check_id": "CKV_AWS_20",
                "check_name": "S3 bucket public read",
                "severity": "high",
                "result": "failed",
                "resource": "aws_s3_bucket.data",
            },
            {
                "check_id": "CKV_AWS_21",
                "check_name": "Enable versioning",
                "severity": "medium",
                "result": "failed",
            },
            {"check_id": "CKV_AWS_99", "severity": "low", "result": "passed"},
        ]
    )
    provider = TerraformProvider(client=client)
    assert provider.capabilities == ["iac", "checkov", "tfsec", "read-only"]
    result = provider.check({"terraform_dir": "/infra"})

    assert result.target == "/infra"
    assert [f.title for f in result.findings] == [
        "CKV_AWS_20: S3 bucket public read",
        "CKV_AWS_21: Enable versioning",
    ]
    assert all(f.source == "checkov" for f in result.findings)
    high = result.findings[0]
    assert high.severity == "high" and high.blocking is True
    assert "aws_s3_bucket.data" in high.detail
    assert result.findings[1].blocking is False


def test_terraform_available_logic(tmp_path):
    assert TerraformProvider(client=_FakeScanClient([])).available() is True
    assert TerraformProvider().available() is False
    assert TerraformProvider(config={"terraform_dir": str(tmp_path)}).available() is True
    assert TerraformProvider(config={"hcl": "resource ..."}).available() is True


def test_terraform_defensive_and_throwing():
    assert TerraformProvider(client=_FakeScanClient(None)).check({}).findings == []
    assert TerraformProvider(client=_FakeScanClient(["junk", 1])).check({}).findings == []
    assert TerraformProvider(client=_ThrowingClient()).to_findings({}) == []
    assert TerraformProvider().to_findings({}) == []  # unavailable → []


# ── review_runner adapter (#23/#24) ────────────────────────────────────


def _plan():
    return NormalizedPlan(
        plan_id="001-svc",
        title="Build a payments service",
        description="Provision AWS infra for payments",
        source_format="markdown",
        target_kind="software",
        criteria=[Criterion(id="AC#1", text="Data encrypted at rest")],
        enrichment=Enrichment(
            infra=[{"kind": "terraform", "dir": "/infra"}, {"kind": "s3"}]
        ),
    )


def _epic():
    return EpicPlan(
        plan_id="001-svc",
        epic_title="Build a payments service",
        epic_body="Stand up payments.",
        children=[ChildIssue(key="C1", title="API", kind="feature")],
        summary="payments",
    )


def test_build_context_surfaces_text_and_iac():
    ctx = build_context(_plan(), _epic())
    assert "payments" in ctx["text"].lower()
    assert "Data encrypted at rest" in ctx["text"]
    assert len(ctx["infra"]) == 2
    # IaC entries are the terraform-flagged infra findings.
    assert ctx["iac"] == [{"kind": "terraform", "dir": "/infra"}]


def test_provider_runner_collects_findings_from_registered_providers(monkeypatch):
    """Register a fake provider so run_all yields a finding via the runner."""

    @provider_base.register_provider
    class _FakeProv(provider_base.ProviderMCP):
        name = "fake-runner-prov"
        capabilities = ["best-practice"]

        def available(self):
            return True

        def check(self, context):
            return provider_base.BestPracticeCheck(
                provider=self.name,
                findings=[
                    Finding(title="rotate keys", severity="medium", source="cloud-mcp")
                ],
            )

    # Limit run_all to just our fake provider for a deterministic result.
    monkeypatch.setattr(
        provider_base, "available_providers", lambda: ["fake-runner-prov"]
    )

    findings = provider_runner(_plan(), _epic())
    assert [f.title for f in findings] == ["rotate keys"]


def test_provider_runner_compatible_with_run_gates(monkeypatch):
    """provider_runner plugs into run_gates(..., external_runner=...)."""

    canned = [
        Finding(
            title="S3 bucket public",
            severity="high",
            source="cloud-mcp",
            blocking=True,
        )
    ]
    # Bypass real provider discovery: make run_all return the canned finding.
    monkeypatch.setattr(
        "plan.providers.review_runner.run_all", lambda context: canned
    )

    review = run_gates(_plan(), _epic(), external_runner=provider_runner)
    all_findings = [f for ls in review.lenses for f in ls.findings]
    assert any(f.title == "S3 bucket public" for f in all_findings)
    # A blocking high finding fails the gate.
    assert review.gates_passed is False
    assert any(f.blocking for f in review.blocking_findings())
