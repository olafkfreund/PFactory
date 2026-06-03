"""
MCP Server Catalog
==================

Declarative catalog of "default" MCP servers PFactory ships out of the box.

Each entry says:
- *what* to launch (subprocess command/args, or HTTP endpoint)
- *when* to enable (project markers from ``project_context.detect_infra_markers``)
- *how* to authenticate (key into ``core.mcp_credentials.get_credential_status``)
- *for whom* (which agent types receive the tools by default)

The runtime decision is made in two layers:
1. ``get_required_mcp_servers()`` (models.py) walks the catalog and includes a
   server ID in the agent's name list IFF agent + markers + creds all align.
2. ``client.py`` then asks the catalog for the actual subprocess config dict
   to pass to ``ClaudeAgentOptions(mcp_servers=...)``.

Why a catalog vs. inline if-blocks? The four entries here (GitHub, K8s, AWS,
Azure) all follow the same shape — launcher + markers + creds — and the next
batch (GitLab, ADO, GCP) will too. A data structure beats six more
copy-pasted if-blocks in client.py.

V1 entries (this file): github, kubernetes, aws, azure — the four mature,
ship-today options as of 2026-05.

Deferred:
- gitlab     → V1.5, awaiting decision between official (immature) and the
               @zereight community fork.
- azure_devops → V1.5, the local server is being sunset for Remote MCP.
- gcp        → V2, requires HTTP transport rather than stdio subprocess
               (Google's design is remote-first).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.mcp_credentials import CredentialStatus


@dataclass(frozen=True)
class MCPCatalogEntry:
    """One default MCP server.

    The ``id`` matches the key used in ``ClaudeAgentOptions(mcp_servers={...})``
    AND the name accepted by ``AGENT_MCP_<agent>_ADD/REMOVE`` overrides — so
    operators can force-disable a catalog server per project with the same
    mechanism they already use for context7/playwright.
    """

    id: str
    """Stable identifier. Also the dict key the Claude SDK sees."""

    launcher_command: str
    """First element of the subprocess argv (e.g. "npx", "uvx", "/usr/bin/something")."""

    launcher_args: list[str] = field(default_factory=list)
    """Remaining argv. ``readonly_args`` are appended when read-only mode is on."""

    marker_capability_keys: list[str] = field(default_factory=list)
    """Capability flags from ``detect_infra_markers()`` — ANY match enables the
    server. Empty list = always-on (used for github where every repo qualifies).
    """

    credential_provider: str = ""
    """Key passed to ``mcp_credentials.get_credential_status()``. Empty = no
    credentials required (none of the V1 entries hit this path; reserved for
    future no-auth servers)."""

    readonly_args: list[str] = field(default_factory=list)
    """Extra argv appended in read-only mode. Empty if the server doesn't have
    a flag (then we rely on IAM-side read-only roles documented in the docs
    page)."""

    default_for_agents: list[str] = field(default_factory=list)
    """Agent types that get this server by default. Other agents need an
    explicit ``AGENT_MCP_<agent>_ADD`` to receive it."""

    docs_url: str = ""
    """Pointer the docs page links out to. Also useful for log messages."""

    def build_server_config(
        self, creds: CredentialStatus | None, read_only: bool = True
    ) -> dict[str, Any]:
        """Materialize the dict the Claude Agent SDK expects.

        Caller is responsible for having already checked credentials (and that
        the agent should get this server). This function is pure — it just
        translates the entry's launcher + creds into the SDK's config schema.
        """
        args = list(self.launcher_args)
        if read_only:
            args = args + list(self.readonly_args)
        config: dict[str, Any] = {"command": self.launcher_command, "args": args}
        if creds and creds.env_vars:
            config["env"] = dict(creds.env_vars)
        return config


# ---------------------------------------------------------------------------
# V1 catalog — 2026-05 mature, ship-today set
# ---------------------------------------------------------------------------
#
# Pinning policy:
# - GitHub:     Anthropic-maintained reference implementation; track latest.
# - Kubernetes: PIN ">=3.6.0" — earlier versions have CVE-2026-46519 (CVSS 8.8)
#               where the --read-only flag is bypass-able at the execution
#               layer. v3.6.0+ fixed this. Defense in depth: the kubeconfig
#               itself should use a read-only ServiceAccount/RBAC; do not
#               rely on the flag alone.
# - AWS:        AWS Labs ships as a uvx-installable Python package (no npm).
# - Azure:      Microsoft-official, 2.0 stable as of 2026-04. ~276 tools.

CATALOG: list[MCPCatalogEntry] = [
    MCPCatalogEntry(
        id="github",
        launcher_command="npx",
        launcher_args=["-y", "@modelcontextprotocol/server-github"],
        marker_capability_keys=[],  # always-on if creds present (every repo is "a repo")
        credential_provider="github",
        readonly_args=[],
        default_for_agents=[
            "coder",
            "planner",
            "qa_reviewer",
            "pr_reviewer",
            "spec_gatherer",
        ],
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/github",
    ),
    MCPCatalogEntry(
        id="kubernetes",
        launcher_command="npx",
        launcher_args=["-y", "kubernetes-mcp-server@>=3.6.0"],
        marker_capability_keys=["has_kubernetes"],
        credential_provider="kubernetes",
        readonly_args=["--read-only"],
        default_for_agents=["coder", "qa_reviewer"],
        docs_url="https://github.com/containers/kubernetes-mcp-server",
    ),
    MCPCatalogEntry(
        id="aws",
        launcher_command="uvx",
        launcher_args=["awslabs.aws-api-mcp-server@latest"],
        marker_capability_keys=["has_aws"],
        credential_provider="aws",
        readonly_args=[],  # no native flag; require read-only IAM role in docs
        default_for_agents=["coder", "qa_reviewer"],
        docs_url="https://awslabs.github.io/mcp/",
    ),
    MCPCatalogEntry(
        id="azure",
        launcher_command="npx",
        launcher_args=["-y", "@azure/mcp@latest", "server", "start"],
        marker_capability_keys=["has_azure"],
        credential_provider="azure",
        readonly_args=[],  # no native flag; require Reader-role principal in docs
        default_for_agents=["coder", "qa_reviewer"],
        docs_url="https://devblogs.microsoft.com/azure-sdk/announcing-azure-mcp-server-2-0-stable-release/",
    ),
    # ---- V1.5: GitLab + Azure DevOps -----------------------------------
    #
    # GitLab: NO proper canonical npm package exists as of 2026-05.  The
    # ``@modelcontextprotocol/server-gitlab`` package is too immature, so
    # we ship the community fork ``@zereight/mcp-gitlab`` (72+ tools,
    # actively shipping releases).  The vendor-disclosure note lives in
    # docs/docs/concepts/mcp-servers.md — this catalog entry IS the
    # disclosure: the launcher arg names the vendor explicitly so
    # operators see what they're opting into.
    MCPCatalogEntry(
        id="gitlab",
        launcher_command="npx",
        launcher_args=["-y", "@zereight/mcp-gitlab@latest"],
        marker_capability_keys=["has_gitlab_ci"],
        credential_provider="gitlab",
        readonly_args=[],  # rely on PAT scope (read_api, read_repository)
        default_for_agents=["coder", "qa_reviewer"],
        docs_url="https://github.com/zereight/gitlab-mcp",
    ),
    # Azure DevOps: Microsoft's local ``@azure-devops/mcp@next`` server
    # works today but is being phased out in favour of an Azure DevOps
    # Remote MCP HTTP server (public preview since 2026-03).  We ship the
    # local version with a sunset notice in the docs — a follow-up Epic
    # will migrate to Remote MCP once it goes GA.
    MCPCatalogEntry(
        id="azure_devops",
        launcher_command="npx",
        launcher_args=["-y", "@azure-devops/mcp@next"],
        marker_capability_keys=["has_azure_devops"],
        credential_provider="azure_devops",
        readonly_args=[],  # rely on PAT scope (read-only)
        default_for_agents=["coder", "qa_reviewer"],
        docs_url="https://github.com/microsoft/azure-devops-mcp",
    ),
]


_CATALOG_BY_ID: dict[str, MCPCatalogEntry] = {entry.id: entry for entry in CATALOG}


def get_catalog_entry(server_id: str) -> MCPCatalogEntry | None:
    """Lookup helper for client.py / models.py / mcp_doctor CLI."""
    return _CATALOG_BY_ID.get(server_id)


def is_catalog_server(server_id: str) -> bool:
    """Cheap "do we ship this server?" check used by name-mapping in models.py."""
    return server_id in _CATALOG_BY_ID


def catalog_ids() -> list[str]:
    """Stable ordering — matches CATALOG list above."""
    return [entry.id for entry in CATALOG]
