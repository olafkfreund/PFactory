"""GCP best-practice MCP provider (issue #23).

Wraps a GCP Organization Policy / Security Command Center (SCC) style
best-practice MCP server. The injected ``client`` exposes a read-only
``assess(context)`` method returning org-policy/SCC finding dicts; tests pass a
fake so no Google SDK/MCP is required. Read-only by contract.
"""

from __future__ import annotations

import os

from plan.providers.base import BestPracticeCheck, ProviderMCP, register_provider
from plan.review.models import Finding

# GCP SCC / org-policy severities → review severities.
_SEVERITY_MAP: dict[str, str] = {
    "informational": "info",
    "info": "info",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "critical": "critical",
}

# Env vars that indicate ambient GCP credentials are present.
_CRED_ENV = ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT")


@register_provider
class GcpProvider(ProviderMCP):
    """Best-practice provider backed by a GCP org-policy / SCC MCP server."""

    name = "gcp"
    capabilities = ["best-practice", "org-policy", "scc", "read-only"]

    def available(self) -> bool:
        """Available when a client is injected or GCP credentials are in env."""
        if self.client is not None:
            return True
        return any(os.environ.get(var) for var in _CRED_ENV)

    def _client(self) -> object | None:
        """Return the injected client, else lazily build a real MCP client."""
        if self.client is not None:
            return self.client
        try:  # pragma: no cover - exercised only with a real MCP install
            from plan.providers._mcp import GcpMcpClient  # type: ignore

            return GcpMcpClient(**self.config)
        except Exception:
            return None

    def check(self, context: dict) -> BestPracticeCheck:
        """Run a read-only org-policy / SCC assessment over ``context``."""
        target = str(context.get("project") or context.get("organization") or "gcp")
        client = self._client()
        if client is None:
            return BestPracticeCheck(
                provider=self.name,
                target=target,
                available=False,
                summary="no GCP client available",
            )

        try:
            raw = client.assess(context)
        except Exception as exc:  # defensive: a throwing client → empty result
            return BestPracticeCheck(
                provider=self.name,
                target=target,
                available=True,
                error=str(exc),
                summary="GCP assessment failed",
            )

        findings = _map_findings(raw)
        return BestPracticeCheck(
            provider=self.name,
            target=target,
            available=True,
            findings=findings,
            summary=f"{len(findings)} GCP org-policy finding(s)",
            raw={"count": len(findings)},
        )


def _map_findings(raw: object) -> list[Finding]:
    """Map GCP org-policy / SCC results to review findings, defensively."""
    if not isinstance(raw, list):
        return []
    findings: list[Finding] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        state = str(item.get("state") or item.get("status") or "active").lower()
        if state in {"pass", "passed", "resolved", "inactive", "ok"}:
            continue
        severity = _SEVERITY_MAP.get(str(item.get("severity", "medium")).lower(), "medium")
        title = str(
            item.get("title") or item.get("category") or item.get("constraint") or "GCP policy issue"
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
