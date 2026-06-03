"""Tests for the portal-launched cloud check (#133): gate → assess → store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents.cloud import portal_run, store


@pytest.fixture
def store_root(tmp_path, monkeypatch):
    root = tmp_path / "cloud-assessments"
    root.mkdir()
    monkeypatch.setenv("PFACTORY_CLOUD_ASSESSMENT_ROOT", str(root))
    return root


# ── preflight (the access/discovery gate) ────────────────────────────────────


def test_preflight_ok_returns_inventory() -> None:
    inv = {"provider": "gcp", "account": "sarc-493418", "identity": "olaf@x",
           "global": {"storage": {"count": 1}}}
    r = portal_run.preflight("gcp", discover_fn=lambda *a, **k: inv)
    assert r["ok"] is True
    assert r["account"] == "sarc-493418" and r["identity"] == "olaf@x"
    assert r["inventory"]["global"]["storage"]["count"] == 1
    assert r["error"] is None


def test_preflight_no_access_blocks() -> None:
    inv = {"provider": "azure", "account": None, "error": "az account show failed"}
    r = portal_run.preflight("azure", discover_fn=lambda *a, **k: inv)
    assert r["ok"] is False
    assert r["error"] == "az account show failed"


def test_preflight_passes_target_params() -> None:
    seen = {}

    def fake_discover(provider, *, profile=None, regions=None, services=None):
        seen.update(provider=provider, profile=profile, regions=regions, services=services)
        return {"provider": provider, "account": "1"}

    portal_run.preflight("aws", profile="Calitii", regions=["us-east-1"],
                         services=["iam"], discover_fn=fake_discover)
    assert seen == {"provider": "aws", "profile": "Calitii",
                    "regions": ["us-east-1"], "services": ["iam"]}


# ── run_and_store (assessment → store) ───────────────────────────────────────


def test_run_and_store_writes_report_into_store(store_root) -> None:
    def fake_run(spec_dir, target):
        # emulate run_cloud_assessment writing findings/
        findings = Path(spec_dir) / "findings"
        findings.mkdir(parents=True, exist_ok=True)
        (findings / "cloud_assessment.json").write_text(
            json.dumps({"provider": target.provider, "account": "sarc-493418",
                        "verdict": "reject", "failed": 8})
        )
        (findings / "cloud_assessment.md").write_text("# gcp report")
        return {"verdict": "reject", "fail_counts": {"high": 2, "medium": 6}}

    out = portal_run.run_and_store("gcp", profile="sarc-493418", services=["iam"],
                                   account="sarc-493418", run_fn=fake_run)
    assert out["verdict"] == "reject"
    assert out["fail_counts"] == {"high": 2, "medium": 6}
    # the run now appears in Cloud Reports
    listed = store.list_assessments()
    assert len(listed) == 1
    assert listed[0]["id"] == out["assessment_id"]
    assert listed[0]["provider"] == "gcp" and listed[0]["account"] == "sarc-493418"


def test_run_and_store_uses_report_account_for_id(store_root) -> None:
    def fake_run(spec_dir, target):
        findings = Path(spec_dir) / "findings"
        findings.mkdir(parents=True, exist_ok=True)
        (findings / "cloud_assessment.json").write_text(
            json.dumps({"provider": "aws", "account": "533267307120", "verdict": "accept"})
        )
        return {"verdict": "accept", "fail_counts": {}}

    out = portal_run.run_and_store("aws", run_fn=fake_run)  # no account passed
    assert "533267307120" in out["assessment_id"]  # taken from the report
