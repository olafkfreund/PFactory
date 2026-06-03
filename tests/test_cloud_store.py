"""Tests for the cloud assessment portal store (#133/#152)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents.cloud import store


def _write_assessment(root: Path, aid: str, *, verdict="reject", failed=5, account="1") -> Path:
    d = root / aid
    (d / "diagrams").mkdir(parents=True)
    (d / "cloud_assessment.json").write_text(
        json.dumps({"provider": "aws", "account": account, "verdict": verdict, "failed": failed, "passed": 10})
    )
    (d / "cloud_assessment.md").write_text("# report")
    (d / "cloud_remediation_plan.md").write_text("# plan")
    (d / "cloud_issues.json").write_text('{"epic": {}, "children": []}')
    (d / "diagrams" / "cloud_topology.mmd").write_text("graph LR")
    return d


@pytest.fixture
def store_root(tmp_path, monkeypatch):
    root = tmp_path / "cloud-assessments"
    root.mkdir()
    monkeypatch.setenv("PFACTORY_CLOUD_ASSESSMENT_ROOT", str(root))
    return root


def test_list_empty(store_root) -> None:
    assert store.list_assessments() == []


def test_list_sorted_newest_first(store_root) -> None:
    import os
    import time

    a = _write_assessment(store_root, "aws-1-20260101", account="1")
    b = _write_assessment(store_root, "aws-2-20260102", account="2")
    # make b newer
    now = time.time()
    os.utime(a / "cloud_assessment.json", (now - 100, now - 100))
    os.utime(b / "cloud_assessment.json", (now, now))
    ids = [x["id"] for x in store.list_assessments()]
    assert ids[0] == "aws-2-20260102"
    assert store.list_assessments()[0]["account"] == "2"


def test_read_assessment(store_root) -> None:
    _write_assessment(store_root, "aws-1-x")
    d = store.read_assessment("aws-1-x")
    assert d["present"] is True
    assert d["json"]["verdict"] == "reject"
    assert d["reportMarkdown"] == "# report"
    assert d["remediationMarkdown"] == "# plan"
    assert d["diagramMermaid"] == "graph LR"
    assert d["issuesJson"]


def test_read_missing_returns_none(store_root) -> None:
    assert store.read_assessment("nope") is None


def test_safe_id_rejects_traversal(store_root) -> None:
    assert store.read_assessment("../etc") is None
    assert store.download_path("../../x", "report.md") is None


def test_download_path_md_and_json(store_root) -> None:
    _write_assessment(store_root, "aws-1-x")
    assert store.download_path("aws-1-x", "report.md").name == "cloud_assessment.md"
    assert store.download_path("aws-1-x", "remediation.md").name == "cloud_remediation_plan.md"
    assert store.download_path("aws-1-x", "issues.json").name == "cloud_issues.json"
    assert store.download_path("aws-1-x", "bogus.kind") is None


# ── write_assessment + new_assessment_id (#133 portal run) ───────────────────


def test_new_assessment_id_is_sortable_and_safe() -> None:
    import datetime

    now = datetime.datetime(2026, 6, 3, 8, 30, 0, tzinfo=datetime.timezone.utc)
    aid = store.new_assessment_id("gcp", "sarc-493418", now=now)
    assert aid == "gcp-sarc-493418-20260603083000"
    # unsafe chars in the account are slugged away
    assert store.new_assessment_id("azure", "sub/with space", now=now).startswith("azure-sub-with-space-")


def test_write_assessment_mirrors_findings_into_store(store_root, tmp_path) -> None:
    # lay out a finished run's findings/ dir
    findings = tmp_path / "spec" / "findings"
    (findings / "diagrams").mkdir(parents=True)
    (findings / "cloud_assessment.json").write_text(
        json.dumps({"provider": "gcp", "account": "sarc-493418", "verdict": "reject", "failed": 8})
    )
    (findings / "cloud_assessment.md").write_text("# report")
    (findings / "cloud_remediation_plan.md").write_text("# plan")
    (findings / "cloud_issues.json").write_text('{"epic": {}, "children": []}')
    (findings / "diagrams" / "cloud_topology.mmd").write_text("graph LR")

    aid = store.new_assessment_id("gcp", "sarc-493418")
    dst = store.write_assessment(tmp_path / "spec", aid)

    assert dst == store_root / aid
    # it now appears in the listing + reads back
    listed = store.list_assessments()
    assert len(listed) == 1 and listed[0]["id"] == aid and listed[0]["provider"] == "gcp"
    detail = store.read_assessment(aid)
    assert detail["reportMarkdown"] == "# report"
    assert detail["diagramMermaid"] == "graph LR"
    assert store.download_path(aid, "remediation.md").is_file()


def test_write_assessment_tolerates_missing_artifacts(store_root, tmp_path) -> None:
    # only the JSON exists; writer copies what it finds, skips the rest
    findings = tmp_path / "spec" / "findings"
    findings.mkdir(parents=True)
    (findings / "cloud_assessment.json").write_text(json.dumps({"provider": "aws", "account": "1"}))
    dst = store.write_assessment(tmp_path / "spec", "aws-1-20260603000000")
    assert (dst / "cloud_assessment.json").is_file()
    assert not (dst / "cloud_remediation_plan.md").exists()
