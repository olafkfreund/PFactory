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
# Auth only matters when there's a network surface to protect. A purely local /
# client-side plan (e.g. a single-screen browser game) needs no auth, so we only
# require it when these specific signals are present (deliberately NOT generic
# words like "network"/"account" that a plan's out-of-scope list might mention).
_NETWORKED_RE = re.compile(
    r"(?i)\b(api|endpoint|https?|rest|graphql|websocket|server|request|login|"
    r"oauth|session|database|postgres|mysql|redis|microservice|deploy|ingress|"
    r"socket)\b"
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

        # Public exposure from live infra — drill into each adapter's policies so
        # findings name the actual resource (e.g. the security group) + ports/CIDRs,
        # deduped by resource so a re-run doesn't multiply them.
        seen: set[str] = set()
        for entry in plan.enrichment.infra:
            if not isinstance(entry, dict):
                continue
            adapter = entry.get("adapter", "infra")
            target = entry.get("target", "")
            for pol in entry.get("policies", []) or []:
                if not (isinstance(pol, dict) and pol.get("public")):
                    continue
                res = pol.get("group_name") or pol.get("group_id") or pol.get("kind") or "resource"
                rid = pol.get("group_id") or res
                key = f"{adapter}:{rid}"
                if key in seen:
                    continue
                seen.add(key)
                ports = ", ".join(str(p) for p in pol.get("open_ports", [])) or "?"
                cidrs = ", ".join(str(c) for c in pol.get("open_cidrs", [])) or "0.0.0.0/0"
                score = min(score, 0.6)
                findings.append(
                    Finding(
                        title=f"{res} ({rid}) is open to the internet",
                        detail=(
                            f"{adapter}{('/' + target) if target else ''}: security group "
                            f"'{res}' allows ingress from {cidrs} on port(s) {ports}. "
                            "Restrict the source CIDR to known ranges or place it behind "
                            "a load balancer / bastion."
                        ),
                        severity="high",
                        source=self.name,
                    )
                )

        # Networked software plans should explicitly call out auth somewhere.
        if (
            not blocking
            and plan.target_kind == "software"
            and _NETWORKED_RE.search(text)
            and not _AUTH_RE.search(text)
        ):
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
