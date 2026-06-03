"""AWS best-practice MCP provider (issue #23).

Wraps an AWS Well-Architected / Prowler-style best-practice MCP server. The
injected ``client`` is expected to expose a read-only ``assess(context)`` method
returning a list of finding dicts; in production this is a thin MCP client, in
tests it's a fake. The provider never mutates AWS — it only reads assessment
output and maps it into review :class:`~plan.review.models.Finding` objects.
"""

from __future__ import annotations

import os

from plan.providers.base import BestPracticeCheck, ProviderMCP, register_provider
from plan.review.models import Finding

# AWS severities (Prowler / Security Hub style) → review severities.
_SEVERITY_MAP: dict[str, str] = {
    "informational": "info",
    "info": "info",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "critical": "critical",
}

# Env vars that indicate ambient AWS credentials are present.
_CRED_ENV = ("AWS_ACCESS_KEY_ID", "AWS_PROFILE", "AWS_SESSION_TOKEN")


@register_provider
class AwsProvider(ProviderMCP):
    """Best-practice provider backed by an AWS Well-Architected/Prowler MCP."""

    name = "aws"
    capabilities = ["best-practice", "well-architected", "read-only"]

    def available(self) -> bool:
        """Available when a client is injected or AWS credentials are in env."""
        if self.client is not None:
            return True
        return any(os.environ.get(var) for var in _CRED_ENV)

    def _client(self) -> object | None:
        """Return the injected client, else lazily build a real MCP client.

        The real client is imported lazily so tests need no AWS SDK/MCP.
        """
        if self.client is not None:
            return self.client
        try:  # pragma: no cover - exercised only with a real MCP install
            from plan.providers._mcp import AwsMcpClient  # type: ignore

            return AwsMcpClient(**self.config)
        except Exception:
            return None

    def check(self, context: dict) -> BestPracticeCheck:
        """Run a read-only Well-Architected/Prowler assessment over ``context``."""
        target = str(context.get("account") or context.get("region") or "aws")
        client = self._client()
        if client is None:
            return BestPracticeCheck(
                provider=self.name,
                target=target,
                available=False,
                summary="no AWS client available",
            )

        try:
            raw = client.assess(context)
        except Exception as exc:  # defensive: a throwing client → empty result
            return BestPracticeCheck(
                provider=self.name,
                target=target,
                available=True,
                error=str(exc),
                summary="AWS assessment failed",
            )

        findings = _map_findings(raw)
        return BestPracticeCheck(
            provider=self.name,
            target=target,
            available=True,
            findings=findings,
            summary=f"{len(findings)} AWS best-practice finding(s)",
            raw={"count": len(findings)},
        )


def _map_findings(raw: object) -> list[Finding]:
    """Map Prowler/Well-Architected assessment items to review findings.

    Tolerates odd/empty client output: anything that isn't a well-formed item
    dict is skipped rather than raising.
    """
    if not isinstance(raw, list):
        return []
    findings: list[Finding] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "fail")).lower()
        if status in {"pass", "passed", "ok"}:
            continue
        severity = _SEVERITY_MAP.get(str(item.get("severity", "medium")).lower(), "medium")
        title = str(item.get("title") or item.get("check_id") or "AWS best-practice issue")
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
