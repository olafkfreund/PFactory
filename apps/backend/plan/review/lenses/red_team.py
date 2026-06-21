"""Adversarial "Red Team" spec-review lens (RFC-0015 §4 D1 — PFactory #216).

spec-kit has a consistency check (``/speckit.analyze``) and a community "Red
Team" extension; RFC-0015 makes adversarial review a first-class, gated lens in
PFactory. This lens runs **before emit** and actively tries to *break* the spec
rather than score it generously:

* **missing ACs** — a software plan with no acceptance criteria,
* **ambiguous ACs** — criteria built on vague, untestable language
  ("fast", "user-friendly", "etc."),
* **contradictory ACs** — pairs that assert opposing requirements,
* **infeasible constraints** — impossible absolutes ("100% uptime", "zero
  latency", "infinitely scalable"),
* **unstated security/access scope** — a networked plan that never mentions
  auth/authz,
* **wrong-language / target mismatch** — the spec asks for a language the target
  repo is not (the known Factory #585 trap), when reconnaissance grounding is
  available.

Findings are **blocking at/above the RFC-0014 risk threshold** (default
``high``): a high-risk red-team finding hard-fails the gate (and the contract's
autonomy verdict already escalates high risk to human approval). The lens is
**gated**: it only contributes when the ``red-team-review`` extension is enabled
in the declarative registry (RFC-0015 §4 D3) or via the operator env override —
until then it is inert (clean pass), exactly as the registry's
``enabled: false`` declares.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from plan.review.lenses.base import register_lens
from plan.review.models import Finding, LensScore

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan
    from plan.models import NormalizedPlan

# RFC-0014 risk severities, ordered. A finding is blocking when its severity is
# at/above the threshold (default "high").
_SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_DEFAULT_RISK_THRESHOLD = "high"
_PENALTY = {"info": 0.0, "low": 0.05, "medium": 0.15, "high": 0.3, "critical": 0.6}

# Vague, untestable language that makes an AC unverifiable.
_VAGUE_RE = re.compile(
    r"(?i)\b(fast|quick(?:ly)?|slow|user[\s-]?friendly|intuitive|nice|good|bad|"
    r"robust|scalable|performant|efficient|simple|easy|etc\.?|and so on|"
    r"as needed|appropriate|reasonable|some|several|various|many)\b"
)
# Impossible absolutes — infeasible constraints.
_INFEASIBLE_RE = re.compile(
    r"(?i)(100\s*%\s*(up\s*time|uptime|available|reliab)|zero\s+(latency|downtime|bugs?)|"
    r"infinite(?:ly)?\s+scal|never\s+fail|always\s+available|unlimited\s+(scale|throughput))"
)
# A networked surface that ought to consider access control.
_NETWORKED_RE = re.compile(
    r"(?i)\b(api|endpoint|https?|rest|graphql|websocket|server|request|login|"
    r"session|database|postgres|mysql|redis|microservice|deploy|ingress|socket)\b"
)
_AUTH_RE = re.compile(
    r"(?i)\b(auth|authn|authz|authentication|authorization|login|oauth|rbac|abac|"
    r"permission|access control|token|iam|irsa|m[\s-]?tls|mutual tls|"
    r"service[\s-]?account|least[\s-]?privilege|secrets?[\s-]?manager|kms|"
    r"sso|saml|oidc|jwt|identity provider|credential)\b"
)
# Contradiction pairs: if both words of a pair appear across the criteria.
_CONTRADICTION_PAIRS = (
    ("synchronous", "asynchronous"),
    ("stateless", "stateful"),
    ("public", "private"),
    ("required", "optional"),
    ("enabled by default", "disabled by default"),
    ("must not", "must "),
)
# A language explicitly named in the spec text.
_LANG_RE = re.compile(
    r"(?i)\b(python|rust|go(?:lang)?|java(?:script)?|typescript|ruby|c\+\+|c#|"
    r"kotlin|swift|scala|php|elixir|haskell)\b"
)

_RED_TEAM_EXTENSION = "red-team-review"


def _scan_text(plan: NormalizedPlan) -> str:
    parts = [plan.title, plan.description, plan.raw_text or ""]
    parts.extend(c.text for c in plan.criteria)
    return "\n".join(p for p in parts if p)


def _threshold_rank() -> int:
    import os  # noqa: PLC0415 - tiny, read at call time so tests can override

    raw = os.environ.get("PFACTORY_RED_TEAM_RISK_THRESHOLD", "").strip().lower()
    return _SEVERITY_RANK.get(raw, _SEVERITY_RANK[_DEFAULT_RISK_THRESHOLD])


def _finding(title: str, detail: str, severity: str) -> Finding:
    """Build a finding, marking it blocking at/above the risk threshold."""
    blocking = _SEVERITY_RANK.get(severity, 0) >= _threshold_rank()
    return Finding(
        title=title,
        detail=detail,
        severity=severity,
        source="red-team",
        blocking=blocking,
    )


class RedTeamLens:
    """Adversarial spec review — tries to break the spec before emit (gated)."""

    name = "red-team"

    def evaluate(self, plan: NormalizedPlan, epic: EpicPlan) -> LensScore:  # noqa: ARG002 - Lens protocol signature
        # Gated: inert unless the registry / operator enables red-team-review.
        from plan.review.extension_registry import is_enabled  # noqa: PLC0415

        if not is_enabled(_RED_TEAM_EXTENSION):
            return LensScore(lens=self.name, score=1.0, findings=[])

        findings: list[Finding] = []
        text = _scan_text(plan)
        is_software = plan.target_kind == "software"

        findings.extend(self._missing_acs(plan, is_software))
        findings.extend(self._ambiguous_acs(plan))
        findings.extend(self._contradictory_acs(plan))
        findings.extend(self._infeasible(text))
        findings.extend(self._security_scope(text, is_software))
        findings.extend(self._language_mismatch(plan))

        score = 1.0
        blocking = False
        for f in findings:
            score -= _PENALTY.get(f.severity, 0.0)
            blocking = blocking or f.blocking

        if not findings:
            findings.append(
                Finding(
                    title="Adversarial review found no spec-breaking gaps",
                    detail="No missing/ambiguous/contradictory ACs, infeasible constraints, "
                    "unstated security scope, or language mismatch detected.",
                    severity="info",
                    source="red-team",
                )
            )

        return LensScore(
            lens=self.name,
            score=round(max(0.0, min(1.0, score)), 4),
            findings=findings,
            blocking=blocking,
        )

    # ── individual adversarial checks ──────────────────────────────────────

    def _missing_acs(self, plan: NormalizedPlan, is_software: bool) -> list[Finding]:
        if is_software and not plan.criteria:
            return [
                _finding(
                    "No acceptance criteria — the spec is unverifiable",
                    "A software plan with zero acceptance criteria cannot be proven done; "
                    "the build can pass and still not meet intent. Add explicit, testable ACs.",
                    "high",
                )
            ]
        return []

    def _ambiguous_acs(self, plan: NormalizedPlan) -> list[Finding]:
        out: list[Finding] = []
        for c in plan.criteria:
            m = _VAGUE_RE.search(c.text)
            if m:
                out.append(
                    _finding(
                        f"Ambiguous, untestable criterion ({c.id})",
                        f"{c.id} relies on vague language ({m.group(0)!r}) that cannot be "
                        "objectively verified. Restate it with a measurable threshold.",
                        "medium",
                    )
                )
        return out

    def _contradictory_acs(self, plan: NormalizedPlan) -> list[Finding]:
        if not plan.criteria:
            return []
        joined = " ".join(c.text.lower() for c in plan.criteria)
        out: list[Finding] = []
        for a, b in _CONTRADICTION_PAIRS:
            if a in joined and b in joined:
                out.append(
                    _finding(
                        "Contradictory acceptance criteria",
                        f"The criteria assert both {a!r} and {b!r}; resolve the conflict "
                        "before build, or the implementation will pick one arbitrarily.",
                        "high",
                    )
                )
        return out

    def _infeasible(self, text: str) -> list[Finding]:
        m = _INFEASIBLE_RE.search(text)
        if m:
            return [
                _finding(
                    "Infeasible constraint",
                    f"The spec demands an impossible absolute ({m.group(0)!r}). Replace it with "
                    "a realistic SLO (e.g. 99.9% availability, p99 < 200ms).",
                    "high",
                )
            ]
        return []

    def _security_scope(self, text: str, is_software: bool) -> list[Finding]:
        if is_software and _NETWORKED_RE.search(text) and not _AUTH_RE.search(text):
            return [
                _finding(
                    "Unstated security / access scope",
                    "A networked plan never states an authentication/authorization scope. "
                    "Confirm access control is in scope (and how) or explicitly out of scope.",
                    "medium",
                )
            ]
        return []

    def _language_mismatch(self, plan: NormalizedPlan) -> list[Finding]:
        rm = getattr(plan, "repo_map", None)
        if rm is None or not getattr(rm, "available", False):
            return []  # no reconnaissance grounding → cannot judge a mismatch
        # A migration deliberately changes language; not a mismatch.
        if (plan.change_mode or "").lower() == "migration":
            return []
        repo_langs = {str(x).lower() for x in (getattr(rm, "languages", None) or [])}
        if not repo_langs:
            return []
        spec_langs = {m.lower() for m in _LANG_RE.findall(plan.title + " " + plan.description)}
        # Normalise a couple of aliases.
        spec_langs = {"go" if x == "golang" else x for x in spec_langs}
        repo_langs = {"go" if x == "golang" else x for x in repo_langs}
        if spec_langs and not (spec_langs & repo_langs):
            return [
                _finding(
                    "Wrong-language / target mismatch",
                    f"The spec asks for {sorted(spec_langs)} but the target repo is "
                    f"{sorted(repo_langs)}. Without a migration this silently produces the "
                    "repo's language (Factory #585). Re-classify as a migration or fix the spec.",
                    "high",
                )
            ]
        return []


register_lens(RedTeamLens())
