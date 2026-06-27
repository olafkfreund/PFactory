"""Tests for plan.recon.paas_detect — managed-PaaS intent detection (RFC-0013)."""

from __future__ import annotations

import sys
from pathlib import Path

import importlib.util

_MOD = Path(__file__).parent.parent / "apps" / "backend" / "plan" / "recon" / "paas_detect.py"
_spec = importlib.util.spec_from_file_location("paas_detect", _MOD)
_pd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pd)
detect_paas_target = _pd.detect_paas_target


def test_none_when_no_cloud_target():
    assert detect_paas_target("Build a CLI tool with a Postgres database") is None
    assert detect_paas_target("") is None


def test_gcp_cloud_run_with_services():
    r = detect_paas_target("Deploy the app to GCP Cloud Run with Redis and Postgres")
    assert r is not None
    assert r["cloud"] == "gcp"
    assert r["deploy_system"] == "gcp-cloud-run"
    assert r["managed_services"] == ["postgres", "redis"]


def test_azure_container_apps():
    r = detect_paas_target("Ship it to Azure Container Apps using Azure Cache and Cloud SQL is n/a")
    assert r["deploy_system"] == "azure-container-apps"
    assert "redis" in r["managed_services"]


def test_azure_app_service_phrase_maps_to_container_apps():
    assert (
        detect_paas_target("host on Azure App Service")["deploy_system"] == "azure-container-apps"
    )


def test_aws_app_runner():
    r = detect_paas_target("Deploy to AWS App Runner with an RDS database")
    assert r["deploy_system"] == "aws-app-runner"
    assert r["managed_services"] == ["postgres"]


def test_earliest_cloud_wins_on_tie():
    # Azure mentioned before GCP -> azure
    r = detect_paas_target("Prefer Azure Container Apps, not Cloud Run")
    assert r["cloud"] == "azure"


def test_case_insensitive_and_no_services():
    r = detect_paas_target("DEPLOY TO CLOUD RUN")
    assert r["deploy_system"] == "gcp-cloud-run"
    assert r["managed_services"] == []
