"""Tests for the registry (#25) + provider/template base contracts (#23/#26)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from plan.providers.base import (  # noqa: E402
    BestPracticeCheck,
    ProviderMCP,
    get_provider,
    register_provider,
    run_all,
)
from plan.registry import load_registry  # noqa: E402
from plan.review.models import Finding  # noqa: E402
from plan.templates.models import Policy, Template, TemplateMetadata  # noqa: E402

# ── registry (#25) ─────────────────────────────────────────────────────

def test_registry_includes_builtins_and_catalogue():
    reg = load_registry()
    ids = {e.id for e in reg.entries}
    # built-ins reflected from the live adapter/connector registries
    assert "infra:kubernetes" in ids
    assert "knowledge:backstage" in ids
    # seed catalogue providers + template
    assert "provider:aws" in ids
    assert "template:gcp-project" in ids


def test_registry_query_helpers():
    reg = load_registry()
    assert {e.id for e in reg.by_kind("provider")} >= {"provider:aws", "provider:terraform"}
    assert reg.is_enabled("provider:gcp")
    assert reg.with_capability("read-only")  # providers/adapters advertise it


def test_registry_overrides_disable_entry():
    reg = load_registry(overrides={"provider:aws": {"enabled": False}})
    assert reg.is_enabled("provider:aws") is False
    assert "provider:aws" not in {e.id for e in reg.enabled("provider")}


# ── provider MCP base (#23/#24) ────────────────────────────────────────

def test_provider_registry_and_graceful_findings():
    @register_provider
    class _Dummy(ProviderMCP):
        name = "dummy-prov"
        capabilities = ["best-practice"]

        def available(self) -> bool:
            return True

        def check(self, context):
            return BestPracticeCheck(
                provider=self.name,
                findings=[Finding(title="rotate keys", severity="medium")],
            )

    p = get_provider("dummy-prov")
    assert [f.title for f in p.to_findings({})] == ["rotate keys"]


def test_provider_unavailable_returns_no_findings():
    @register_provider
    class _Off(ProviderMCP):
        name = "off-prov"

        def available(self) -> bool:
            return False

        def check(self, context):  # pragma: no cover - not reached
            return BestPracticeCheck(provider=self.name)

    assert get_provider("off-prov").to_findings({}) == []
    # run_all aggregates only available providers, never raises
    assert isinstance(run_all({}), list)


# ── template policy (#26) ──────────────────────────────────────────────

def _gcp_template():
    return Template(
        metadata=TemplateMetadata(name="gcp-project", title="GCP Project"),
        policy=Policy(
            required_tags=["cost-center", "owner"],
            allowed_regions=["europe-west1", "europe-west4"],
            required_iam=["roles/viewer"],
        ),
    )


def test_template_policy_flags_violations():
    findings = _gcp_template().check(
        {"tags": ["owner"], "region": "us-central1", "iam": []}
    )
    titles = " ".join(f.title for f in findings)
    assert "missing required tag 'cost-center'" in titles
    assert "region 'us-central1' not allowed" in titles
    assert "missing required IAM 'roles/viewer'" in titles
    assert any(f.blocking for f in findings)  # region violation is blocking


def test_template_policy_passes_when_compliant():
    findings = _gcp_template().check(
        {"tags": ["cost-center", "owner"], "region": "europe-west1", "iam": ["roles/viewer"]}
    )
    assert findings == []
