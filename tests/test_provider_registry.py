"""Tests for the provider-MCP registry + suggest-install advisory (#3, Phase B).

The registry decides which provider-MCPs are *relevant* to a plan and emits a
non-blocking, cited advisory when a relevant one isn't installed. Also covers the
Checkov→failed-check mapping in plan.providers._mcp (no scanner binary required).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.providers import registry  # noqa: E402
from plan.providers._mcp import _checkov_failed_checks  # noqa: E402


def test_relevant_providers_keyed_off_context_text():
    ctx = {"text": "Provision an EKS cluster with Terraform modules in AWS", "infra": [], "iac": []}
    ids = {s.id for s in registry.relevant_providers(ctx)}
    assert "terraform" in ids and "aws" in ids
    assert "gcp" not in ids  # nothing GCP in the text


def test_suggest_installs_is_advisory_and_cited(monkeypatch):
    # Force every provider to look "not installed".
    monkeypatch.setattr(registry, "is_installed", lambda spec: False)
    ctx = {"text": "deploy to azure aks with terraform", "infra": [], "iac": []}
    findings = registry.suggest_installs(ctx)
    ids = {f.source for f in findings}
    assert "provider:terraform" in ids and "provider:azure" in ids
    for f in findings:
        assert f.blocking is False          # never blocks
        assert f.severity == "info"          # never penalises the gate
        assert f.citations and f.citations[0].uri  # always cited (why + doc source)
        assert "install" in f.detail.lower()


def test_suggest_installs_skips_installed(monkeypatch):
    monkeypatch.setattr(registry, "is_installed", lambda spec: True)
    ctx = {"text": "terraform aws azure gcp", "infra": [], "iac": []}
    assert registry.suggest_installs(ctx) == []


def test_checkov_mapping_flattens_failed_checks():
    payload = json.dumps(
        {
            "results": {
                "failed_checks": [
                    {
                        "check_id": "CKV_AWS_18",
                        "check_name": "Ensure S3 bucket has access logging",
                        "severity": "high",
                        "resource": "aws_s3_bucket.data",
                        "file_path": "/main.tf",
                        "guideline": "https://docs.bridgecrew.io/x",
                    }
                ],
                "passed_checks": [{"check_id": "CKV_AWS_19"}],
            }
        }
    )
    out = _checkov_failed_checks(payload)
    assert len(out) == 1
    assert out[0]["check_id"] == "CKV_AWS_18"
    assert out[0]["severity"] == "high"
    assert out[0]["result"] == "failed"


def test_checkov_mapping_tolerates_garbage():
    assert _checkov_failed_checks("not json") == []
    assert _checkov_failed_checks("") == []
