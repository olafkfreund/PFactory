"""Tests for the RFC-0010 reconnaissance engine (Phase 2).

Covers the static IaC probe (Scenario A heart), RepoMap building over a fixture
tree (no cloning / no execution), and the degrade-not-raise safety contract.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.recon.clone import clone_for_recon  # noqa: E402
from plan.recon.iac_probe import iac_tools, probe_iac  # noqa: E402
from plan.recon.reconnoiter import build_repo_map, reconnoiter  # noqa: E402

# ── IaC probe (Scenario A) ──────────────────────────────────────────────


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_probe_terraform_finds_eks_resources(tmp_path: Path):
    _write(
        tmp_path / "eks.tf",
        'provider "aws" {}\nresource "aws_eks_cluster" "main" {\n  name = "prod"\n}\n',
    )
    _write(
        tmp_path / "node_groups.tf",
        'resource "aws_eks_node_group" "workers" {}\n'
        'module "vpc" {\n  source = "terraform-aws-modules/vpc/aws"\n}\n',
    )
    inv = probe_iac(tmp_path)
    assert iac_tools(inv) == ["terraform"]
    tf = inv["terraform"]
    types = {(r["type"], r["name"], r["file"]) for r in tf["resources"]}
    assert ("aws_eks_cluster", "main", "eks.tf") in types
    assert ("aws_eks_node_group", "workers", "node_groups.tf") in types
    assert tf["providers"] == ["aws"]
    assert tf["modules"][0]["name"] == "vpc"
    assert tf["modules"][0]["source"] == "terraform-aws-modules/vpc/aws"


def test_probe_helm_and_kubernetes(tmp_path: Path):
    _write(tmp_path / "chart" / "Chart.yaml", "name: my-app\nversion: 0.1.0\n")
    _write(
        tmp_path / "chart" / "templates" / "deploy.yaml",
        "apiVersion: apps/v1\nkind: Deployment\n",
    )
    _write(tmp_path / "k8s" / "svc.yaml", "apiVersion: v1\nkind: Service\n")
    inv = probe_iac(tmp_path)
    assert "helm" in inv and inv["helm"]["charts"][0]["name"] == "my-app"
    assert "Deployment" in inv["helm"]["charts"][0]["kinds"]
    assert inv["kubernetes"]["kinds"]["Service"] == ["k8s/svc.yaml"]


def test_probe_empty_repo_returns_empty(tmp_path: Path):
    assert probe_iac(tmp_path) == {}


# ── RepoMap building (no clone, no execution) ───────────────────────────


def test_build_repo_map_iac_only_folds_iac_into_languages(tmp_path: Path):
    # A pure-Terraform repo has no code language; the IaC tool becomes the
    # grounded language so environment.language isn't blank.
    _write(tmp_path / "eks.tf", 'resource "aws_eks_cluster" "main" {}\n')
    rm = build_repo_map(tmp_path, repo="o/infra", base_ref="main", commit="c0")
    assert rm.iac == ["terraform"]
    assert rm.languages == ["terraform"]


def test_build_repo_map_python_terraform(tmp_path: Path):
    _write(tmp_path / "pyproject.toml", '[project]\nrequires-python = ">=3.11"\n')
    _write(tmp_path / "Makefile", "test:\n\tpytest\n")
    _write(tmp_path / "main.tf", 'resource "aws_s3_bucket" "b" {}\n')
    rm = build_repo_map(tmp_path, repo="o/r", base_ref="main", commit="abc123")
    assert rm.available is True
    assert rm.repo == "o/r" and rm.commit == "abc123"
    assert "python" in rm.languages
    assert rm.versions.get("python") == ">=3.11"
    assert rm.existing_test_command == "make test"
    assert rm.iac == ["terraform"]
    assert rm.to_baseline_block()["iac_resources"]["terraform"]["resources"]


# ── degrade-not-raise (safety) ──────────────────────────────────────────


def test_reconnoiter_no_repo_is_unavailable():
    rm = reconnoiter(None)
    assert rm.available is False and rm.repo is None


def test_reconnoiter_unreachable_repo_degrades(tmp_path: Path, monkeypatch):
    # Point at a bogus host with no token; clone must fail fast and degrade,
    # never raise. (Uses a non-routable .invalid TLD; no network egress.)
    monkeypatch.setenv("PFACTORY_RECON_GIT_HOST", "nonexistent.invalid")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("PFACTORY_RECON_TOKEN", raising=False)
    rm = reconnoiter("owner/does-not-exist", "main")
    assert rm.available is False
    assert rm.repo == "owner/does-not-exist"
    assert rm.error  # honest reason recorded


# ── process() integration: recon runs for software+repo, skips otherwise ─


def test_process_attaches_repo_map_for_software_with_repo(monkeypatch):
    from plan import service as service_mod
    from plan.recon import RepoMap

    calls: dict = {}

    def fake_reconnoiter(repo, base_ref=None):
        calls["repo"] = repo
        calls["base_ref"] = base_ref
        return RepoMap(
            available=True, repo=repo, base_ref=base_ref, languages=["python"]
        )

    monkeypatch.setattr(service_mod, "reconnoiter", fake_reconnoiter)
    svc = service_mod.PlanService(persist=False)
    text = "# Build an API\n\nA service.\n\n## Acceptance Criteria\n- AC#1: GET /health returns 200\n"
    s = svc.ingest_text(text, title="API", channel="cli", repo="o/r", base_ref="main")
    out = svc.process(s.session_id)
    assert calls == {"repo": "o/r", "base_ref": "main"}  # recon was invoked
    assert out.plan.repo_map is not None and out.plan.repo_map.languages == ["python"]


def test_process_skips_recon_without_repo(monkeypatch):
    from plan import service as service_mod

    def boom(*a, **k):  # must NOT be called when no repo
        raise AssertionError("reconnoiter should not run without a target repo")

    monkeypatch.setattr(service_mod, "reconnoiter", boom)
    svc = service_mod.PlanService(persist=False)
    text = "# Thing\n\nDo it.\n\n## Acceptance Criteria\n- AC#1: works\n"
    s = svc.ingest_text(text, title="T", channel="cli")  # no repo
    out = svc.process(s.session_id)
    assert out.plan.repo_map is None


def test_clone_for_recon_cleans_up_on_failure(monkeypatch):
    """A failed clone yields ok=False and leaves no temp dir behind."""
    monkeypatch.setenv("PFACTORY_RECON_GIT_HOST", "nonexistent.invalid")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    captured: dict = {}
    with clone_for_recon("owner/x", "main") as c:
        captured["ok"] = c.ok
        captured["path"] = c.path
    assert captured["ok"] is False
    # temp dir removed on context exit
    assert captured["path"] is None or not Path(captured["path"]).exists()
