"""Security lens (issue #15).

Scans the plan text + criteria + infra enrichment for security smells:
plaintext secrets (a *blocking, critical* hit), world-open public exposure
(``0.0.0.0/0`` / public infra policies), and — for software plans — the absence
of any authentication/authorization acceptance criterion.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from plan.review.lenses.base import register_lens
from plan.review.models import Finding, LensScore

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan
    from plan.models import NormalizedPlan

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(?:password|passwd|pwd)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\b(?:api[_-]?key|secret|token)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\baws_secret_access_key\s*[:=]\s*\S+"),
    re.compile(r"(?i)-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\bAKIA[0-9A-Z]{16}\b"),
)
_AUTH_RE = re.compile(
    r"(?i)\b(auth|authn|authz|authentication|authorization|login|oauth|rbac|"
    r"permission|access control|token)\b"
)


class SecurityLens:
    """Deterministic security heuristics over plan text + infra enrichment."""

    name = "security"

    def evaluate(self, plan: NormalizedPlan, epic: EpicPlan) -> LensScore:
        findings: list[Finding] = []
        score = 1.0
        blocking = False

        text = _scan_text(plan)
        for pat in _SECRET_PATTERNS:
            match = pat.search(text)
            if match:
                blocking = True
                score = 0.0
                findings.append(
                    Finding(
                        title="Hardcoded secret in plaintext",
                        detail=(
                            "A credential appears in the plan "
                            f"(matched {match.group(0)[:40]!r}). Move it to a "
                            "secrets manager."
                        ),
                        severity="critical",
                        source=self.name,
                        blocking=True,
                    )
                )
                break

        for entry in plan.enrichment.infra:
            if not isinstance(entry, dict):
                continue
            blob = " ".join(str(v) for v in entry.values())
            if "0.0.0.0/0" in blob or str(entry.get("public", "")).lower() == "true":
                score = min(score, 0.6)
                name = entry.get("name") or entry.get("kind") or "resource"
                findings.append(
                    Finding(
                        title=f"Public network exposure on '{name}'",
                        detail="Open to 0.0.0.0/0 — restrict ingress to known CIDRs.",
                        severity="high",
                        source=self.name,
                    )
                )

        # Software plans should explicitly call out auth somewhere.
        if not blocking and plan.target_kind == "software" and not _AUTH_RE.search(text):
            score = min(score, 0.7)
            findings.append(
                Finding(
                    title="No authentication/authorization criteria",
                    detail=(
                        "A software plan has no acceptance criterion covering auth; "
                        "confirm access control is in scope or explicitly out."
                    ),
                    severity="medium",
                    source=self.name,
                )
            )

        return LensScore(
            lens=self.name,
            score=round(max(0.0, min(1.0, score)), 4),
            findings=findings,
            blocking=blocking,
        )


def _scan_text(plan: NormalizedPlan) -> str:
    parts = [plan.title, plan.description, plan.raw_text or ""]
    parts.extend(c.text for c in plan.criteria)
    return "\n".join(p for p in parts if p)


register_lens(SecurityLens())
