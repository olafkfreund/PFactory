"""Azure best-practice MCP provider (issue #23).

Wraps an Azure Policy / PSRule-for-Azure style best-practice MCP server. The
injected ``client`` exposes a read-only ``assess(context)`` method returning
policy/PSRule finding dicts; in tests it's a fake so no Azure SDK/MCP is needed.
Read-only by contract — the provider only consumes assessment output.
"""

from __future__ import annotations

import os

from plan.providers.base import BestPracticeCheck, ProviderMCP, register_provider
from plan.review.models import Finding

# Azure Policy / PSRule severities → review severities.
_SEVERITY_MAP: dict[str, str] = {
    "information": "info",
    "informational": "info",
    "info": "info",
    "low": "low",
    "medium": "medium",
    "warning": "medium",
    "high": "high",
    "error": "high",
    "critical": "critical",
}

# Env vars that indicate ambient Azure credentials are present.
_CRED_ENV = ("AZURE_SUBSCRIPTION_ID", "AZURE_TENANT_ID", "AZURE_CLIENT_ID")


@register_provider
class AzureProvider(ProviderMCP):
    """Best-practice provider backed by an Azure Policy / PSRule MCP server."""

    name = "azure"
    capabilities = ["best-practice", "azure-policy", "psrule", "read-only"]

    def available(self) -> bool:
        """Available when a client is injected or Azure credentials are in env."""
        if self.client is not None:
            return True
        return any(os.environ.get(var) for var in _CRED_ENV)

    def _client(self) -> object | None:
        """Return the injected client, else lazily build a real MCP client."""
        if self.client is not None:
            return self.client
        try:  # pragma: no cover - exercised only with a real MCP install
            from plan.providers._mcp import AzureMcpClient

            return AzureMcpClient(**self.config)
        except Exception:
            return None

    def check(self, context: dict) -> BestPracticeCheck:
        """Run a read-only Azure Policy / PSRule assessment over ``context``."""
        target = str(context.get("subscription") or context.get("resource_group") or "azure")
        client = self._client()
        if client is None:
            return BestPracticeCheck(
                provider=self.name,
                target=target,
                available=False,
                summary="no Azure client available",
            )

        try:
            raw = client.assess(context)
        except Exception as exc:  # defensive: a throwing client → empty result
            return BestPracticeCheck(
                provider=self.name,
                target=target,
                available=True,
                error=str(exc),
                summary="Azure assessment failed",
            )

        findings = _map_findings(raw)
        return BestPracticeCheck(
            provider=self.name,
            target=target,
            available=True,
            findings=findings,
            summary=f"{len(findings)} Azure policy finding(s)",
            raw={"count": len(findings)},
        )


def _map_findings(raw: object) -> list[Finding]:
    """Map Azure Policy / PSRule results to review findings, defensively."""
    if not isinstance(raw, list):
        return []
    findings: list[Finding] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        outcome = str(item.get("outcome") or item.get("status") or "fail").lower()
        if outcome in {"pass", "passed", "compliant", "ok"}:
            continue
        severity = _SEVERITY_MAP.get(str(item.get("severity", "medium")).lower(), "medium")
        title = str(
            item.get("title") or item.get("policy") or item.get("rule") or "Azure policy issue"
        )
        findings.append(
            Finding(
                title=title,
                detail=str(item.get("detail") or item.get("description") or ""),
                severity=severity,
                source="cloud-mcp",
                blocking=severity == "critical",
            )
        )
    return findings
