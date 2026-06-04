"""Catalogue of known provider-MCP servers + advisory suggest-install (#3).

The provider adapters (`terraform`/`aws`/`azure`/`gcp`) run external best-practice
MCP servers to verify a plan. This registry is the declarative half: for each
known provider it records *when it's relevant* to a plan, *how to launch* it, and
*how to install* it when it's missing.

Per the product principle "help, never override", a relevant-but-uninstalled
provider never fails the run — :func:`suggest_installs` emits a **non-blocking,
cited advisory** naming the missing MCP and the exact install command, and the
pipeline continues with whatever enrichment is available.
"""

from __future__ import annotations

import shutil

from plan.review.models import Citation, Finding
from pydantic import BaseModel, Field


class ProviderSpec(BaseModel):
    """Declarative metadata for one known provider-MCP server."""

    id: str
    title: str
    # Lowercased substrings that make this provider relevant to a plan.
    relevance_keywords: list[str] = Field(default_factory=list)
    # Binary checked on PATH to decide whether the server is installed.
    probe_binary: str = ""
    # Documented one-line install command shown in the advisory.
    install_command: str = ""
    # Docs URL — cited so the advisory is auditable.
    docs_uri: str = ""
    # Argv to launch the server over stdio (used by plan.providers._mcp).
    launch_argv: list[str] = Field(default_factory=list)


# Representative, documented provider-MCPs. Install commands/URIs are the
# upstream-documented ones; adjust here (not in code) as the ecosystem moves.
_KNOWN: dict[str, ProviderSpec] = {
    "terraform": ProviderSpec(
        id="terraform",
        title="HashiCorp Terraform / IaC scanning (Checkov)",
        relevance_keywords=["terraform", "hcl", ".tf", "iac", "provisioning", "module"],
        probe_binary="checkov",
        install_command="pipx install checkov   # or: uvx checkov",
        docs_uri="https://www.checkov.io/",
        launch_argv=["checkov"],
    ),
    "aws": ProviderSpec(
        id="aws",
        title="AWS best-practice MCP (AWS Labs)",
        relevance_keywords=["aws", "eks", "ecs", "s3", "rds", "lambda", "ec2", "dynamodb"],
        probe_binary="uvx",
        install_command="uvx awslabs.aws-api-mcp-server@latest",
        docs_uri="https://github.com/awslabs/mcp",
        launch_argv=["uvx", "awslabs.aws-api-mcp-server@latest"],
    ),
    "azure": ProviderSpec(
        id="azure",
        title="Azure MCP Server",
        relevance_keywords=["azure", "aks", "arm", "bicep", "entra"],
        probe_binary="azmcp",
        install_command="npx -y @azure/mcp@latest server start",
        docs_uri="https://github.com/Azure/azure-mcp",
        launch_argv=["npx", "-y", "@azure/mcp@latest", "server", "start"],
    ),
    "gcp": ProviderSpec(
        id="gcp",
        title="Google Cloud MCP",
        relevance_keywords=["gcp", "gke", "bigquery", "cloud run", "gcs"],
        probe_binary="gcp-mcp",
        install_command="npx -y gcp-mcp",
        docs_uri="https://cloud.google.com/sdk/docs/install",
        launch_argv=["npx", "-y", "gcp-mcp"],
    ),
}


def known_providers() -> dict[str, ProviderSpec]:
    """All registered provider specs, keyed by id."""
    return dict(_KNOWN)


def get_spec(provider_id: str) -> ProviderSpec | None:
    return _KNOWN.get(provider_id)


def is_installed(spec: ProviderSpec) -> bool:
    """True when the provider's probe binary is on PATH."""
    return bool(spec.probe_binary) and shutil.which(spec.probe_binary) is not None


def _context_text(context: dict) -> str:
    parts = [str(context.get("text", ""))]
    for entry in context.get("infra", []) or []:
        if isinstance(entry, dict):
            parts.append(" ".join(str(v) for v in entry.values()))
    for entry in context.get("iac", []) or []:
        if isinstance(entry, dict):
            parts.append(" ".join(str(v) for v in entry.values()))
    return " ".join(parts).lower()


def relevant_providers(context: dict) -> list[ProviderSpec]:
    """Providers whose relevance keywords appear in the plan/enrichment context."""
    text = _context_text(context)
    out: list[ProviderSpec] = []
    for spec in _KNOWN.values():
        if any(kw in text for kw in spec.relevance_keywords):
            out.append(spec)
    return out


def suggest_installs(context: dict) -> list[Finding]:
    """Advisory findings for providers that are relevant but not installed (#3).

    Never blocking. Each finding cites the provider's docs and gives the exact
    install command, so an engineer can opt in — PFactory assists, it doesn't
    gate on a missing optional tool.
    """
    findings: list[Finding] = []
    for spec in relevant_providers(context):
        if is_installed(spec):
            continue
        findings.append(
            Finding(
                title=f"{spec.title} not installed — deeper verification skipped",
                detail=(
                    f"This plan looks relevant to {spec.id!r} but its MCP/scanner "
                    f"isn't on PATH, so PFactory couldn't run it. Install to enable "
                    f"it:  {spec.install_command}"
                ),
                # Info, not a penalty: a suggestion to install an *optional* tool
                # must never fail the gate (help, never override).
                severity="info",
                source=f"provider:{spec.id}",
                blocking=False,
                citations=[
                    Citation(
                        why=(
                            f"Running the {spec.title} gives provider-native "
                            "best-practice + security checks PFactory can't infer alone."
                        ),
                        uri=spec.docs_uri,
                        title=spec.title,
                        source="provider-registry",
                    )
                ],
            )
        )
    return findings
