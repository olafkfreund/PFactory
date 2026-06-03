"""Helm chart acceptance tests for the MCP credentials toggle (Tier 2-A).

When ``mcpCredentials.enabled=false`` (default):
  • No mcp-* volumes / volumeMounts
  • No MCP credential env vars
  • Chart renders identically to the pre-#100 baseline

When ``mcpCredentials.enabled=true``:
  • Operator MUST set ``secretName`` — chart fails clearly otherwise
  • Per-provider toggles in ``mcpCredentials.providers.*`` independently
    enable env vars + file mounts
  • File mounts use ``subPath`` + ``readOnly: true`` + ``defaultMode: 0400``
  • Env-only providers (github, gitlab, azure_devops, azure) inject the
    canonical env vars the matching MCP server's docs document
"""

from __future__ import annotations

import subprocess

import pytest
import yaml


def _render(chart_dir, set_values: list[str] | None = None) -> list[dict]:
    cmd = ["helm", "template", "test-release", str(chart_dir)]
    for kv in set_values or []:
        cmd.extend(["--set", kv])
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def _render_expect_error(chart_dir, set_values: list[str] | None = None) -> str:
    cmd = ["helm", "template", "test-release", str(chart_dir)]
    for kv in set_values or []:
        cmd.extend(["--set", kv])
    out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert out.returncode != 0, (
        f"expected helm template to fail; got rc=0 stdout={out.stdout[:200]}"
    )
    return out.stderr


def _find_deployment(docs: list[dict]) -> dict:
    for d in docs:
        if d.get("kind") == "Deployment":
            return d
    raise AssertionError("no Deployment in rendered manifests")


def _container_envs(deployment: dict) -> list[dict]:
    containers = deployment["spec"]["template"]["spec"]["containers"]
    assert containers, "no containers in deployment"
    return containers[0].get("env", [])


def _volume_names(deployment: dict) -> set[str]:
    return {v["name"] for v in deployment["spec"]["template"]["spec"].get("volumes", [])}


def _volume_mount_names(deployment: dict) -> set[str]:
    containers = deployment["spec"]["template"]["spec"]["containers"]
    return {m["name"] for m in containers[0].get("volumeMounts", [])}


def _find_volume(deployment: dict, name: str) -> dict:
    for v in deployment["spec"]["template"]["spec"]["volumes"]:
        if v["name"] == name:
            return v
    raise AssertionError(f"volume {name!r} not in rendered manifest")


def _find_mount(deployment: dict, name: str) -> dict:
    for m in deployment["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]:
        if m["name"] == name:
            return m
    raise AssertionError(f"volumeMount {name!r} not in rendered manifest")


@pytest.fixture
def chart_dir(request):
    """Chart root — assumes test invocation from repo root."""
    from pathlib import Path
    return Path(__file__).parent.parent.parent / "charts" / "pfactory"


# ---------------------------------------------------------------------------
# Disabled (default) — no MCP wiring leaks in
# ---------------------------------------------------------------------------


@pytest.mark.helm
def test_disabled_by_default_no_mcp_volumes(chart_dir):
    docs = _render(chart_dir)
    deployment = _find_deployment(docs)
    vols = _volume_names(deployment)
    assert not any(v.startswith("mcp-") for v in vols), (
        f"unexpected MCP volumes when mcpCredentials.enabled=false: {vols}"
    )


@pytest.mark.helm
def test_disabled_by_default_no_mcp_env_vars(chart_dir):
    docs = _render(chart_dir)
    deployment = _find_deployment(docs)
    env_names = [e["name"] for e in _container_envs(deployment)]
    for forbidden in (
        "GITHUB_TOKEN",
        "GITLAB_TOKEN",
        "AZURE_DEVOPS_PERSONAL_ACCESS_TOKEN",
        "AZURE_CLIENT_ID",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        assert forbidden not in env_names, (
            f"unexpected env var {forbidden!r} when mcpCredentials.enabled=false"
        )


# ---------------------------------------------------------------------------
# Enabled — required secret name
# ---------------------------------------------------------------------------


@pytest.mark.helm
def test_enabled_without_secret_name_fails_clearly(chart_dir):
    # Empty secretName should error with a clear message
    err = _render_expect_error(
        chart_dir,
        set_values=[
            "mcpCredentials.enabled=true",
            "mcpCredentials.providers.aws=true",
            "mcpCredentials.secretName=",
        ],
    )
    assert "mcpCredentials.secretName is required" in err


# ---------------------------------------------------------------------------
# Per-provider: env-only providers
# ---------------------------------------------------------------------------


@pytest.mark.helm
def test_github_provider_injects_GITHUB_TOKEN(chart_dir):
    docs = _render(
        chart_dir,
        set_values=[
            "mcpCredentials.enabled=true",
            "mcpCredentials.providers.github=true",
            "mcpCredentials.secretName=my-secret",
        ],
    )
    envs = _container_envs(_find_deployment(docs))
    gh_env = next((e for e in envs if e["name"] == "GITHUB_TOKEN"), None)
    assert gh_env, f"GITHUB_TOKEN missing — got env names: {[e['name'] for e in envs]}"
    assert gh_env["valueFrom"]["secretKeyRef"]["name"] == "my-secret"
    assert gh_env["valueFrom"]["secretKeyRef"]["key"] == "github-token"


@pytest.mark.helm
def test_gitlab_provider_injects_token_and_url(chart_dir):
    docs = _render(
        chart_dir,
        set_values=[
            "mcpCredentials.enabled=true",
            "mcpCredentials.providers.gitlab=true",
            "mcpCredentials.secretName=my-secret",
        ],
    )
    env_names = [e["name"] for e in _container_envs(_find_deployment(docs))]
    assert "GITLAB_TOKEN" in env_names
    assert "GITLAB_INSTANCE_URL" in env_names


@pytest.mark.helm
def test_azure_devops_provider_injects_token_and_url(chart_dir):
    docs = _render(
        chart_dir,
        set_values=[
            "mcpCredentials.enabled=true",
            "mcpCredentials.providers.azureDevOps=true",
            "mcpCredentials.secretName=my-secret",
        ],
    )
    env_names = [e["name"] for e in _container_envs(_find_deployment(docs))]
    assert "AZURE_DEVOPS_PERSONAL_ACCESS_TOKEN" in env_names
    assert "AZURE_DEVOPS_ORG_SERVICE_URL" in env_names


@pytest.mark.helm
def test_azure_provider_injects_service_principal_triplet(chart_dir):
    docs = _render(
        chart_dir,
        set_values=[
            "mcpCredentials.enabled=true",
            "mcpCredentials.providers.azure=true",
            "mcpCredentials.secretName=my-secret",
        ],
    )
    env_names = [e["name"] for e in _container_envs(_find_deployment(docs))]
    assert {"AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"} <= set(env_names)


# ---------------------------------------------------------------------------
# Per-provider: file-mounted providers
# ---------------------------------------------------------------------------


@pytest.mark.helm
def test_aws_provider_mounts_credentials_file_at_default_path(chart_dir):
    docs = _render(
        chart_dir,
        set_values=[
            "mcpCredentials.enabled=true",
            "mcpCredentials.providers.aws=true",
            "mcpCredentials.secretName=my-secret",
        ],
    )
    deployment = _find_deployment(docs)
    mount = _find_mount(deployment, "mcp-aws-credentials")
    assert mount["mountPath"] == "/home/nonroot/.aws/credentials"
    assert mount["subPath"] == "aws-credentials"
    assert mount["readOnly"] is True

    vol = _find_volume(deployment, "mcp-aws-credentials")
    assert vol["secret"]["secretName"] == "my-secret"
    assert vol["secret"]["defaultMode"] == 256  # 0400 in decimal


@pytest.mark.helm
def test_kubernetes_provider_mounts_kubeconfig(chart_dir):
    docs = _render(
        chart_dir,
        set_values=[
            "mcpCredentials.enabled=true",
            "mcpCredentials.providers.kubernetes=true",
            "mcpCredentials.secretName=my-secret",
        ],
    )
    deployment = _find_deployment(docs)
    mount = _find_mount(deployment, "mcp-kubeconfig")
    assert mount["mountPath"] == "/home/nonroot/.kube/config"
    assert mount["subPath"] == "kubeconfig"
    assert mount["readOnly"] is True

    vol = _find_volume(deployment, "mcp-kubeconfig")
    assert vol["secret"]["defaultMode"] == 256  # 0400


@pytest.mark.helm
def test_gcp_provider_mounts_sa_and_sets_GOOGLE_APPLICATION_CREDENTIALS(chart_dir):
    docs = _render(
        chart_dir,
        set_values=[
            "mcpCredentials.enabled=true",
            "mcpCredentials.providers.gcp=true",
            "mcpCredentials.secretName=my-secret",
        ],
    )
    deployment = _find_deployment(docs)
    # File mount
    mount = _find_mount(deployment, "mcp-gcp-sa")
    assert mount["mountPath"] == "/etc/pfactory/gcp-sa.json"
    assert mount["subPath"] == "gcp-service-account.json"
    assert mount["readOnly"] is True

    # Env var pointing at the file
    envs = _container_envs(deployment)
    gac = next((e for e in envs if e["name"] == "GOOGLE_APPLICATION_CREDENTIALS"), None)
    assert gac is not None
    assert gac["value"] == "/etc/pfactory/gcp-sa.json"


# ---------------------------------------------------------------------------
# Mixed-provider sanity check
# ---------------------------------------------------------------------------


@pytest.mark.helm
def test_partial_provider_set_only_enables_selected(chart_dir):
    """Operator chose AWS + GitHub only — Azure / GCP / K8s mounts must NOT appear."""
    docs = _render(
        chart_dir,
        set_values=[
            "mcpCredentials.enabled=true",
            "mcpCredentials.providers.aws=true",
            "mcpCredentials.providers.github=true",
            "mcpCredentials.secretName=my-secret",
        ],
    )
    deployment = _find_deployment(docs)
    mounts = _volume_mount_names(deployment)
    assert "mcp-aws-credentials" in mounts
    assert "mcp-kubeconfig" not in mounts
    assert "mcp-gcp-sa" not in mounts

    env_names = [e["name"] for e in _container_envs(deployment)]
    assert "GITHUB_TOKEN" in env_names
    assert "AZURE_TENANT_ID" not in env_names
    assert "GITLAB_TOKEN" not in env_names
