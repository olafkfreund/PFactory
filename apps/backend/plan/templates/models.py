"""Template + embedded policy contracts (issue #26).

PFactory templates are **Backstage Software Template (`template.yaml`) compatible**
so they drop into a customer's golden paths, plus an embedded ``policy`` block —
the PFactory extension that carries the *rules* a plan using the template must
follow (required tags, allowed regions, IAM baselines, security requirements).
:meth:`Template.check` enforces the policy against a plan context and returns
review :class:`~plan.review.models.Finding` objects, so templates plug into the
review gates.
"""

from __future__ import annotations

from plan.review.models import Finding
from pydantic import BaseModel, ConfigDict, Field


class PolicyRule(BaseModel):
    """One extra free-form policy rule beyond the structured fields."""

    id: str
    description: str = ""
    severity: str = "medium"
    blocking: bool = False


class Policy(BaseModel):
    """The embedded rules a plan using this template must satisfy."""

    required_tags: list[str] = Field(default_factory=list)
    allowed_regions: list[str] = Field(default_factory=list)
    required_iam: list[str] = Field(default_factory=list)
    security_baselines: list[str] = Field(default_factory=list)
    rules: list[PolicyRule] = Field(default_factory=list)


class TemplateMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class Template(BaseModel):
    """A Backstage-compatible template with an embedded PFactory policy block."""

    model_config = ConfigDict(extra="allow")

    apiVersion: str = "scaffolder.backstage.io/v1beta3"
    kind: str = "Template"
    metadata: TemplateMetadata
    spec: dict = Field(default_factory=dict)
    policy: Policy = Field(default_factory=Policy)

    def check(self, context: dict) -> list[Finding]:
        """Enforce the embedded policy against a plan ``context``.

        ``context`` is a loose dict the caller assembles from the plan +
        enrichment, e.g. ``{"tags": [...], "region": "...", "iam": [...],
        "text": "..."}``. Returns a Finding per violated rule.
        """
        findings: list[Finding] = []
        tags = set(context.get("tags") or [])
        for required in self.policy.required_tags:
            if required not in tags:
                findings.append(Finding(
                    title=f"missing required tag '{required}'",
                    detail=f"template '{self.metadata.name}' requires tag '{required}'",
                    severity="medium", source=f"template:{self.metadata.name}",
                ))

        region = context.get("region")
        if self.policy.allowed_regions and region and region not in self.policy.allowed_regions:
            findings.append(Finding(
                title=f"region '{region}' not allowed",
                detail=f"allowed: {', '.join(self.policy.allowed_regions)}",
                severity="high", source=f"template:{self.metadata.name}", blocking=True,
            ))

        iam = set(context.get("iam") or [])
        for required in self.policy.required_iam:
            if required not in iam:
                findings.append(Finding(
                    title=f"missing required IAM '{required}'",
                    severity="high", source=f"template:{self.metadata.name}",
                ))

        return findings
