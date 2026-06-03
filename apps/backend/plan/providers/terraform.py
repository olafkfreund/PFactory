"""HashiCorp Terraform IaC best-practice provider (issue #24).

Wraps a Checkov / tfsec style IaC scanner exposed as an MCP/scan client. The
injected ``client`` exposes a read-only ``scan(context)`` method returning failed
check dicts (Checkov ``CKV_*`` / tfsec ``AVD_*`` style); tests pass a fake so no
scanner binary is required. The provider only *reads* the IaC referenced in the
context/config — it never applies or mutates Terraform.

A failed Checkov/tfsec check at ``high``/``critical`` severity is treated as a
*blocking* finding so it hard-fails the review gate.
"""

from __future__ import annotations

import os

from plan.providers.base import BestPracticeCheck, ProviderMCP, register_provider
from plan.review.models import Finding

# Checkov / tfsec severities → review severities.
_SEVERITY_MAP: dict[str, str] = {
    "info": "info",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "critical": "critical",
}


@register_provider
class TerraformProvider(ProviderMCP):
    """IaC best-practice provider backed by a Checkov/tfsec scan client."""

    name = "terraform"
    capabilities = ["iac", "checkov", "tfsec", "read-only"]

    def available(self) -> bool:
        """Available when a client is injected or an IaC dir/HCL is configured."""
        if self.client is not None:
            return True
        directory = self.config.get("terraform_dir") or self.config.get("iac_dir")
        if directory and os.path.isdir(str(directory)):
            return True
        return bool(self.config.get("hcl"))

    def _client(self) -> object | None:
        """Return the injected client, else lazily build a real scan client."""
        if self.client is not None:
            return self.client
        try:  # pragma: no cover - exercised only with a real scanner install
            from plan.providers._mcp import TerraformScanClient  # type: ignore

            return TerraformScanClient(**self.config)
        except Exception:
            return None

    def _iac_target(self, context: dict) -> str:
        """Resolve the IaC location to report against."""
        return str(
            context.get("terraform_dir")
            or context.get("iac_dir")
            or self.config.get("terraform_dir")
            or self.config.get("iac_dir")
            or "iac"
        )

    def check(self, context: dict) -> BestPracticeCheck:
        """Run a read-only Checkov/tfsec scan over the referenced IaC."""
        target = self._iac_target(context)
        client = self._client()
        if client is None:
            return BestPracticeCheck(
                provider=self.name,
                target=target,
                available=False,
                summary="no Terraform scan client available",
            )

        try:
            raw = client.scan(context)
        except Exception as exc:  # defensive: a throwing client → empty result
            return BestPracticeCheck(
                provider=self.name,
                target=target,
                available=True,
                error=str(exc),
                summary="Terraform scan failed",
            )

        findings = _map_findings(raw)
        return BestPracticeCheck(
            provider=self.name,
            target=target,
            available=True,
            findings=findings,
            summary=f"{len(findings)} failed IaC check(s)",
            raw={"count": len(findings)},
        )


def _map_findings(raw: object) -> list[Finding]:
    """Map failed Checkov/tfsec checks to findings; high/critical are blocking.

    Tolerates odd/empty scanner output: non-dict items are skipped, and items
    explicitly marked as passed are ignored.
    """
    if not isinstance(raw, list):
        return []
    findings: list[Finding] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        result = str(item.get("result") or item.get("status") or "failed").lower()
        if result in {"pass", "passed", "ok"}:
            continue
        severity = _SEVERITY_MAP.get(str(item.get("severity", "medium")).lower(), "medium")
        check_id = str(item.get("check_id") or item.get("id") or "")
        name = str(item.get("check_name") or item.get("title") or "IaC policy violation")
        title = f"{check_id}: {name}" if check_id else name
        resource = item.get("resource") or item.get("file")
        detail = str(item.get("detail") or item.get("guideline") or "")
        if resource:
            detail = f"{resource} — {detail}".strip(" —")
        findings.append(
            Finding(
                title=title,
                detail=detail,
                severity=severity,
                source="checkov",
                blocking=severity in {"high", "critical"},
            )
        )
    return findings
