"""Tests for the cloud assessment orchestrator (#133/#138).

Backend-pure: discovery + Prowler are injected; no cloud, no Docker.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from agents.cloud.report import cloud_findings_paths
from agents.cloud.runner import (
    _docker_argv,
    build_prowler_command,
    run_cloud_assessment,
)


def _target(provider="aws", profile="Calitii", regions=("us-east-1",),
            services=(), fail_on="high"):
    return SimpleNamespace(
        provider=provider,
        profile=profile,
        regions=list(regions),
        scan=SimpleNamespace(services=list(services), fail_on_severity=fail_on),
    )


def _ocsf(status="FAIL", severity="High"):
    return [{
        "status_code": status,
        "severity": severity,
        "finding_info": {"title": "EBS volume is encrypted", "uid": "x"},
        "resources": [{"name": "r", "region": "us-east-1"}],
        "cloud": {"region": "us-east-1"},
    }]


# ── build_prowler_command (pure) ─────────────────────────────────────────────


def test_build_prowler_command_basic() -> None:
    cmd = build_prowler_command("aws")
    assert cmd[:2] == ["prowler", "aws"]
    assert "--output-formats" in cmd and "json-ocsf" in cmd
    assert cmd[cmd.index("--output-directory") + 1] == "/scratch"


def test_build_prowler_command_services_and_regions() -> None:
    cmd = build_prowler_command("aws", regions=["us-east-1", "eu-west-2"], services=["iam", "s3"])
    assert cmd.count("--service") == 2 and "iam" in cmd and "s3" in cmd
    assert cmd.count("--region") == 2 and "eu-west-2" in cmd


def test_build_prowler_command_azure_uses_az_cli_auth() -> None:
    cmd = build_prowler_command("azure")
    assert cmd[:2] == ["prowler", "azure"]
    assert "--az-cli-auth" in cmd


def test_build_prowler_command_gcp_pins_project() -> None:
    cmd = build_prowler_command("gcp", project_id="sarc-493418")
    assert cmd[:2] == ["prowler", "gcp"]
    assert cmd[cmd.index("--project-id") + 1] == "sarc-493418"
    # no project pin when not provided
    assert "--project-id" not in build_prowler_command("gcp")


# ── _docker_argv (per-provider auth wiring) ──────────────────────────────────


def test_docker_argv_aws_mounts_profile() -> None:
    argv = _docker_argv("aws", "Calitii", "/scr", ["prowler", "aws"])
    assert "--network=bridge" in argv
    assert "AWS_PROFILE=Calitii" in argv
    assert "--user" not in argv  # AWS keeps the image's default user


def test_docker_argv_gcp_runs_as_host_uid_with_adc() -> None:
    argv = _docker_argv("gcp", None, "/scr", ["prowler", "gcp"])
    assert "--user" in argv
    assert "CLOUDSDK_CONFIG=/gcloud" in argv
    assert "GOOGLE_APPLICATION_CREDENTIALS=/gcloud/application_default_credentials.json" in argv
    assert any(a.endswith(":/gcloud:ro") for a in argv)


def test_docker_argv_azure_copies_login_to_writable_config() -> None:
    argv = _docker_argv("azure", None, "/scr", ["prowler", "azure", "--az-cli-auth"])
    assert "--user" in argv
    assert "AZURE_CONFIG_DIR=/scratch/azure-cfg" in argv
    assert any(a.endswith(":/azure-src:ro") for a in argv)
    # az config is read-only on the host → copied into scratch, then prowler execs
    assert argv[-3:][0] == "sh" and argv[-2] == "-lc"
    assert "cp -r /azure-src/." in argv[-1] and "exec prowler azure" in argv[-1]


# ── run_cloud_assessment (orchestration) ─────────────────────────────────────


def test_run_writes_findings_and_returns_verdict(tmp_path: Path) -> None:
    seen = {}

    def fake_discover(provider, *, profile=None, regions=None, services=None):
        seen["discover"] = (provider, profile, tuple(regions or ()))
        return {"provider": provider, "account": "1", "global": {}, "regions": {}}

    def fake_prowler(target):
        seen["prowler_target"] = target
        return _ocsf(severity="High")

    result = run_cloud_assessment(
        tmp_path, _target(), discover_fn=fake_discover, prowler_fn=fake_prowler
    )
    assert result["verdict"] == "reject"  # a high fail at the default gate
    # discovery got the target's provider/profile/regions
    assert seen["discover"] == ("aws", "Calitii", ("us-east-1",))
    # artifacts written
    p = cloud_findings_paths(tmp_path)
    assert p["report_md"].is_file() and p["diagram_mmd"].is_file()
    data = json.loads(p["report_json"].read_text())
    assert data["verdict"] == "reject"


def test_run_honours_target_gate(tmp_path: Path) -> None:
    # only a medium fail; target gate=high → flag, not reject
    result = run_cloud_assessment(
        tmp_path,
        _target(fail_on="high"),
        discover_fn=lambda *a, **k: {"provider": "aws", "account": "1"},
        prowler_fn=lambda t: _ocsf(severity="Medium"),
    )
    assert result["verdict"] == "flag"


def test_run_accept_when_clean(tmp_path: Path) -> None:
    result = run_cloud_assessment(
        tmp_path,
        _target(),
        discover_fn=lambda *a, **k: {"provider": "aws", "account": "1"},
        prowler_fn=lambda t: _ocsf(status="PASS"),
    )
    assert result["verdict"] == "accept"
