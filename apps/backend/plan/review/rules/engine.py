"""The deterministic policy-as-code engine (issue #16).

This is the *hybrid* half of the review gates: where the lenses (#15) apply
soft, scored heuristics, the rules here are crisp pass/fail policies that emit
:class:`~plan.review.models.Finding` notes. The engine is intentionally tiny and
free of external dependencies so it always runs; richer external scanners
(Checkov, OPA, cloud-MCP) plug in through :func:`run_external_policy`, which is
lazy and never required.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from plan.review.models import Finding

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan
    from plan.models import NormalizedPlan

# A rule's check signature: inspect a plan + its epic, return any findings.
RuleCheck = Callable[["NormalizedPlan", "EpicPlan"], list[Finding]]

# Heuristic patterns for plaintext secrets in plan text / criteria.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(?:password|passwd|pwd)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\b(?:api[_-]?key|secret|token)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\baws_secret_access_key\s*[:=]\s*\S+"),
    re.compile(r"(?i)-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\bAKIA[0-9A-Z]{16}\b"),
)

# Phrasing that indicates a rollback / recovery strategy is documented. An
# infra-change SOW rarely says "rollback" verbatim — it documents the same intent
# as fail-over, disaster recovery, runbooks, restore and blue/green / canary cuts.
_ROLLBACK_RE = re.compile(
    r"(?i)(\broll[\s-]?back\b|\broll[\s-]?forward\b|\brevert\b|\bundo\b|"
    r"\bblue[\s-]?green\b|\bcanary\b|\bfail[\s-]?over\b|\bdisaster recovery\b|"
    r"\bdr\b|\brunbook\b|\brestore\b|\brecovery\b)"
)

# Negation / restriction context that flips a literal "0.0.0.0/0" from a smell into
# a *statement of the control* ("no SG is open to 0.0.0.0/0 except the ALB").
_EXPOSURE_NEGATION_RE = re.compile(
    r"(?i)\b(no|not|never|avoid|prevent|restrict(?:ed)?|deny|denied|without|"
    r"except|disallow|block(?:ed)?|forbid|must not|should not|ensure no)\b"
)


class Rule(Protocol):
    """A single deterministic policy check."""

    name: str

    def check(self, plan: NormalizedPlan, epic: EpicPlan) -> list[Finding]:
        """Inspect the plan/epic and return any policy findings."""
        ...


class _FuncRule:
    """Adapter wrapping a plain function as a :class:`Rule`."""

    def __init__(self, name: str, check: RuleCheck) -> None:
        self.name = name
        self._check = check

    def check(self, plan: NormalizedPlan, epic: EpicPlan) -> list[Finding]:
        return self._check(plan, epic)


_REGISTRY: dict[str, Rule] = {}


def register_rule(rule: Rule) -> Rule:
    """Register (or replace) a rule by its ``name`` and return it."""
    _REGISTRY[rule.name] = rule
    return rule


def rule(name: str) -> Callable[[RuleCheck], _FuncRule]:
    """Decorator: register a function as a named :class:`Rule`."""

    def _wrap(check: RuleCheck) -> _FuncRule:
        adapter = _FuncRule(name, check)
        register_rule(adapter)
        return adapter

    return _wrap


def _plan_text(plan: NormalizedPlan) -> str:
    """Concatenate the plan's free text + criteria for scanning."""
    parts = [plan.title, plan.description, plan.raw_text or ""]
    parts.extend(c.text for c in plan.criteria)
    return "\n".join(p for p in parts if p)


# ── built-in rules ─────────────────────────────────────────────────────


@rule("no-plaintext-secrets")
def _no_plaintext_secrets(plan: NormalizedPlan, epic: EpicPlan) -> list[Finding]:
    """A hardcoded secret in the plan text is a critical, blocking finding."""
    text = _plan_text(plan)
    for pat in _SECRET_PATTERNS:
        match = pat.search(text)
        if match:
            return [
                Finding(
                    title="Hardcoded secret in plan text",
                    detail=(
                        "A credential/secret appears in plaintext "
                        f"(matched: {match.group(0)[:40]!r}). Use a secrets "
                        "manager instead of embedding it in the plan."
                    ),
                    severity="critical",
                    source="no-plaintext-secrets",
                    blocking=True,
                )
            ]
    return []


@rule("infra-change-needs-rollback")
def _infra_change_needs_rollback(plan: NormalizedPlan, epic: EpicPlan) -> list[Finding]:
    """Infra-change plans must mention a rollback / revert strategy."""
    if (plan.plan_type or "").lower() != "infra-change":
        return []
    haystack = "\n".join([_plan_text(plan), epic.epic_body, *(c.body for c in epic.children)])
    if _ROLLBACK_RE.search(haystack):
        return []
    return [
        Finding(
            title="Infra change has no rollback plan",
            detail=(
                "plan_type is 'infra-change' but neither the plan nor any child "
                "issue documents a rollback / revert strategy."
            ),
            severity="high",
            source="infra-change-needs-rollback",
            blocking=False,
        )
    ]


@rule("k8s-workloads-need-limits")
def _k8s_workloads_need_limits(plan: NormalizedPlan, epic: EpicPlan) -> list[Finding]:
    """Kubernetes workloads in enrichment.infra must declare resource limits."""
    findings: list[Finding] = []
    for entry in plan.enrichment.infra:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind", "")).lower()
        is_workload = (
            kind
            in {
                "deployment",
                "statefulset",
                "daemonset",
                "pod",
                "workload",
            }
            or entry.get("workload") is True
        )
        if not is_workload:
            continue
        if not entry.get("limits") and not entry.get("resources"):
            name = entry.get("name") or entry.get("kind") or "workload"
            findings.append(
                Finding(
                    title=f"Workload '{name}' has no resource limits",
                    detail=(
                        "Kubernetes workloads should declare CPU/memory limits "
                        "to avoid noisy-neighbour and runaway resource use."
                    ),
                    severity="medium",
                    source="k8s-workloads-need-limits",
                    blocking=False,
                )
            )
    return findings


@rule("public-exposure")
def _public_exposure(plan: NormalizedPlan, epic: EpicPlan) -> list[Finding]:
    """Flag world-open exposure (0.0.0.0/0) written into the plan *text*.

    Live-infra exposure is owned by the security lens (which names the actual
    resource + ports), so this rule only covers the plan document itself to
    avoid duplicate, unnamed findings.
    """
    text = " ".join(
        [plan.title, plan.description, *(c.text for c in plan.criteria), plan.raw_text or ""]
    )
    needle = "0.0.0.0/0"
    idx = text.find(needle)
    while idx != -1:
        # Look at the ~80 chars leading into this mention: a negation/restriction
        # there means the plan is *describing the control*, not opening a hole.
        window = text[max(0, idx - 80) : idx]
        if not _EXPOSURE_NEGATION_RE.search(window):
            return [
                Finding(
                    title="Plan text specifies world-open exposure (0.0.0.0/0)",
                    detail="The plan opens a resource to 0.0.0.0/0 — restrict the source CIDR.",
                    severity="high",
                    source="public-exposure",
                    blocking=False,
                )
            ]
        idx = text.find(needle, idx + len(needle))
    return []


# ── runners ────────────────────────────────────────────────────────────


def default_rules() -> list[Rule]:
    """Return the built-in rules in a stable order."""
    order = [
        "no-plaintext-secrets",
        "infra-change-needs-rollback",
        "k8s-workloads-need-limits",
        "public-exposure",
    ]
    seen = list(order)
    seen.extend(n for n in _REGISTRY if n not in order)
    return [_REGISTRY[n] for n in seen if n in _REGISTRY]


def run_rules(
    plan: NormalizedPlan,
    epic: EpicPlan,
    *,
    rules: list[Rule] | None = None,
) -> list[Finding]:
    """Run every rule and collect their findings."""
    findings: list[Finding] = []
    for r in rules if rules is not None else default_rules():
        findings.extend(r.check(plan, epic))
    return findings


def run_external_policy(
    plan: NormalizedPlan,
    epic: EpicPlan,
    *,
    runner: Callable[[NormalizedPlan, EpicPlan], list[Finding]] | None = None,
) -> list[Finding]:
    """Seam for external policy-as-code (Checkov / OPA / cloud-MCP).

    The ``runner`` is injected (mocked in tests). When absent this is a no-op
    returning ``[]`` — external scanning is lazy and never required. A runner
    may return raw :class:`Finding` objects or dicts (coerced here).
    """
    if runner is None:
        return []
    raw = runner(plan, epic) or []
    findings: list[Finding] = []
    for item in raw:
        if isinstance(item, Finding):
            findings.append(item)
        elif isinstance(item, dict):
            findings.append(Finding(**item))
    return findings
