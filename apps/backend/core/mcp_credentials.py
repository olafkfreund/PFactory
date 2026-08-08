"""
MCP Credentials Chain
=====================

Single source of truth for "do we have credentials for provider X?"

Resolution order (first hit wins):
  1. Operator-level ~/.pfactory/mcp-credentials.json (references env vars / files,
     NEVER stores secrets in plain text).
  2. Cloud-CLI native discovery — the files and env vars each provider's official
     CLI reads (~/.aws/credentials, ~/.kube/config, GOOGLE_APPLICATION_CREDENTIALS,
     etc.).
  3. K8s in-cluster identity — service account token, IRSA, Workload Identity.

Probes are cheap and do NOT validate that credentials WORK — only that something
*could* be picked up by the MCP server subprocess. If a probe says "yes" but the
creds are stale, the MCP server's own startup surfaces the failure as a tool-side
error during the agent run. Acceptable trade-off: API-validation probes would
add latency and rate-limit pressure every spawn.

Used by ``agents.tools_pkg.mcp_catalog`` to decide which servers to auto-enable
per project; the catalog walks the chain via ``get_credential_status()`` and
skips any server whose creds are missing.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

OPERATOR_CONFIG_PATH = Path.home() / ".pfactory" / "mcp-credentials.json"
K8S_SERVICE_ACCOUNT_TOKEN = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")


@dataclass(frozen=True)
class CredentialStatus:
    """Outcome of a credential probe for one MCP provider.

    ``available`` is the truthiness check the catalog cares about. ``source``
    is a short tag suitable for logs and the ``pfactory mcp-doctor`` output
    ("operator-config", "env:GITHUB_TOKEN", "file:~/.kube/config", "irsa", ...).
    ``env_vars`` is the dict the MCP subprocess should be launched with —
    typically maps the canonical env names the server's docs document.
    """

    available: bool
    source: str
    env_vars: dict[str, str] = field(default_factory=dict)


@lru_cache(maxsize=1)
def _load_operator_config() -> dict:
    """Read ~/.pfactory/mcp-credentials.json if present.

    Cached for the lifetime of the process. Refuses to read with looser perms
    than 0600 — same posture Claude Code takes for ~/.claude/.credentials.json.
    Returns an empty dict if the file is missing, unreadable, or perm-checked
    out.
    """
    if not OPERATOR_CONFIG_PATH.exists():
        return {}

    try:
        mode = OPERATOR_CONFIG_PATH.stat().st_mode & 0o777
        # 0o600 is the strictest; anything looser (group/other read) is unsafe
        if mode & 0o077:
            logger.warning(
                "Refusing to read %s with permissions %o (must be 0600 or stricter)",
                OPERATOR_CONFIG_PATH,
                mode,
            )
            return {}
        with OPERATOR_CONFIG_PATH.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read %s: %s", OPERATOR_CONFIG_PATH, exc)
        return {}


def _resolve_env(env_name: str | None) -> str | None:
    """Return the value of the named env var, or None if unset/empty."""
    if not env_name:
        return None
    value = os.environ.get(env_name)
    return value if value else None


def _file_exists(path_str: str | None) -> bool:
    """Return True if the path resolves to an existing readable file."""
    if not path_str:
        return False
    try:
        return Path(path_str).expanduser().is_file()
    except (OSError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Per-provider probes
# ---------------------------------------------------------------------------


def _probe_github() -> CredentialStatus:
    op = _load_operator_config().get("github") or {}
    token_env = op.get("tokenEnv") or "GITHUB_TOKEN"
    token = _resolve_env(token_env)
    if token:
        return CredentialStatus(
            available=True,
            source=f"env:{token_env}",
            env_vars={"GITHUB_PERSONAL_ACCESS_TOKEN": token},
        )
    for fallback in ("GITHUB_PERSONAL_ACCESS_TOKEN", "GH_TOKEN"):
        token = _resolve_env(fallback)
        if token:
            return CredentialStatus(
                available=True,
                source=f"env:{fallback}",
                env_vars={"GITHUB_PERSONAL_ACCESS_TOKEN": token},
            )
    return CredentialStatus(False, "none")


def _probe_gitlab() -> CredentialStatus:
    op = _load_operator_config().get("gitlab") or {}
    token_env = op.get("tokenEnv") or "GITLAB_TOKEN"
    token = _resolve_env(token_env) or _resolve_env("GITLAB_API_TOKEN")
    instance_url = op.get("instanceUrl") or _resolve_env("GITLAB_INSTANCE_URL")
    if not token:
        return CredentialStatus(False, "none")
    env_vars = {"GITLAB_PERSONAL_ACCESS_TOKEN": token}
    if instance_url:
        env_vars["GITLAB_API_URL"] = instance_url
    return CredentialStatus(available=True, source=f"env:{token_env}", env_vars=env_vars)


def _probe_azure_devops() -> CredentialStatus:
    op = _load_operator_config().get("azureDevOps") or {}
    token_env = op.get("tokenEnv") or "AZURE_DEVOPS_PERSONAL_ACCESS_TOKEN"
    token = _resolve_env(token_env)
    org_url = op.get("orgUrl") or _resolve_env("AZURE_DEVOPS_ORG_SERVICE_URL")
    if not token or not org_url:
        return CredentialStatus(False, "none")
    return CredentialStatus(
        available=True,
        source=f"env:{token_env}",
        env_vars={
            "AZURE_DEVOPS_PERSONAL_ACCESS_TOKEN": token,
            "AZURE_DEVOPS_ORG_SERVICE_URL": org_url,
        },
    )


def _probe_kubernetes() -> CredentialStatus:
    # 1. Operator-configured kubeconfig path
    op = _load_operator_config().get("kubernetes") or {}
    kubeconfig_path = op.get("kubeconfigPath")
    if _file_exists(kubeconfig_path):
        return CredentialStatus(
            available=True,
            source=f"file:{kubeconfig_path}",
            env_vars={"KUBECONFIG": str(Path(kubeconfig_path).expanduser())},
        )

    # 2. KUBECONFIG env var
    env_kubeconfig = _resolve_env("KUBECONFIG")
    if env_kubeconfig and any(_file_exists(p) for p in env_kubeconfig.split(":")):
        return CredentialStatus(
            available=True, source="env:KUBECONFIG", env_vars={"KUBECONFIG": env_kubeconfig}
        )

    # 3. Default ~/.kube/config
    default = Path.home() / ".kube" / "config"
    if default.is_file():
        return CredentialStatus(
            available=True,
            source="file:~/.kube/config",
            env_vars={"KUBECONFIG": str(default)},
        )

    # 4. K8s in-cluster service account
    if K8S_SERVICE_ACCOUNT_TOKEN.is_file():
        return CredentialStatus(available=True, source="k8s-in-cluster")

    return CredentialStatus(False, "none")


def _probe_aws() -> CredentialStatus:
    op = _load_operator_config().get("aws") or {}

    # Operator-configured profile
    profile = op.get("profile")
    if profile:
        creds_file = Path.home() / ".aws" / "credentials"
        if creds_file.is_file():
            return CredentialStatus(
                available=True,
                source=f"profile:{profile}",
                env_vars={"AWS_PROFILE": profile},
            )

    # Explicit access keys via env (most direct)
    if _resolve_env("AWS_ACCESS_KEY_ID") and _resolve_env("AWS_SECRET_ACCESS_KEY"):
        return CredentialStatus(available=True, source="env:AWS_ACCESS_KEY_ID")

    # AWS_PROFILE pointing into ~/.aws/credentials
    env_profile = _resolve_env("AWS_PROFILE")
    creds_file = Path.home() / ".aws" / "credentials"
    if env_profile and creds_file.is_file():
        return CredentialStatus(available=True, source=f"env:AWS_PROFILE={env_profile}")

    # Default ~/.aws/credentials (default profile)
    if creds_file.is_file():
        return CredentialStatus(available=True, source="file:~/.aws/credentials")

    # IRSA — EKS service account web identity (presence of the projected token file is sufficient)
    if _resolve_env("AWS_ROLE_ARN") and _resolve_env("AWS_WEB_IDENTITY_TOKEN_FILE"):
        return CredentialStatus(available=True, source="irsa")

    return CredentialStatus(False, "none")


def _probe_azure() -> CredentialStatus:
    op = _load_operator_config().get("azure") or {}

    # Service principal via operator config (refs env vars; doesn't store secret)
    tenant = op.get("tenantId") or _resolve_env("AZURE_TENANT_ID")
    client = op.get("clientId") or _resolve_env("AZURE_CLIENT_ID")
    secret_env = op.get("clientSecretEnv") or "AZURE_CLIENT_SECRET"
    secret = _resolve_env(secret_env)
    if tenant and client and secret:
        return CredentialStatus(
            available=True,
            source="service-principal",
            env_vars={
                "AZURE_TENANT_ID": tenant,
                "AZURE_CLIENT_ID": client,
                "AZURE_CLIENT_SECRET": secret,
            },
        )

    # Azure CLI cache — `az login` populates ~/.azure/
    az_profile = Path.home() / ".azure" / "azureProfile.json"
    if az_profile.is_file():
        return CredentialStatus(available=True, source="file:~/.azure/azureProfile.json")

    # Managed Identity is host-side and not probe-able without an API call;
    # the MCP server's MSAL layer will discover it at startup. We treat
    # AZURE_USE_MSI=true as the operator's signal that MI is available.
    if _resolve_env("AZURE_USE_MSI") == "true":
        return CredentialStatus(available=True, source="managed-identity")

    return CredentialStatus(False, "none")


def _probe_gcp() -> CredentialStatus:
    op = _load_operator_config().get("gcp") or {}

    creds_path = op.get("credentialsPath") or _resolve_env("GOOGLE_APPLICATION_CREDENTIALS")
    if _file_exists(creds_path):
        return CredentialStatus(
            available=True,
            source=f"file:{creds_path}",
            env_vars={"GOOGLE_APPLICATION_CREDENTIALS": str(Path(creds_path).expanduser())},
        )

    # gcloud ADC default location
    adc = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
    if adc.is_file():
        return CredentialStatus(
            available=True,
            source="file:~/.config/gcloud/application_default_credentials.json",
            env_vars={"GOOGLE_APPLICATION_CREDENTIALS": str(adc)},
        )

    return CredentialStatus(False, "none")


# `dict[str, callable]` named the BUILTIN `callable()`, which is not a type --
# mypy rejected it and the annotation guaranteed nothing. Every probe below has
# the same real signature.
_PROBES: dict[str, Callable[[], CredentialStatus]] = {
    "github": _probe_github,
    "gitlab": _probe_gitlab,
    "azure_devops": _probe_azure_devops,
    "kubernetes": _probe_kubernetes,
    "aws": _probe_aws,
    "azure": _probe_azure,
    "gcp": _probe_gcp,
}


def get_credential_status(provider: str) -> CredentialStatus:
    """Return whether credentials for ``provider`` appear available.

    See module docstring for the resolution order. Unknown providers always
    return ``available=False``.
    """
    probe = _PROBES.get(provider)
    if probe is None:
        return CredentialStatus(False, f"unknown-provider:{provider}")
    return probe()


def reset_cache() -> None:
    """Drop the cached operator config. Test/CLI helper — not used at runtime."""
    _load_operator_config.cache_clear()
