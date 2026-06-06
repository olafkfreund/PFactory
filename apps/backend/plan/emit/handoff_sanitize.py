"""Sanitize live-cloud enrichment into Task Contract constraints (issue #80).

The Enrich stage attaches read-only live-cloud findings to
:class:`~plan.models.NormalizedPlan.enrichment` — ``infra`` snapshots and
``knowledge`` links. Those snapshots carry raw provider payloads, account
identifiers, ARNs, IPs and security-group rules that must **never** cross the
factory boundary into AIFactory.

This module is the WS2 sanitizer: it reduces each infra snapshot to a small,
deduped, redacted ``constraints`` shape and runs a :func:`redact` pass over every
retained string so secrets/identifiers collapse to ``[redacted]``. Everything
here is pure and **never raises** — malformed/empty input degrades to safe,
empty output, because a sanitizer that throws is a sanitizer that leaks (the
caller would skip it and emit the raw bundle).
"""

from __future__ import annotations

import json
import re
from typing import Any

# ── size caps ──────────────────────────────────────────────────────────────
_MAX_CONSTRAINTS = 8
_MAX_INSTANCE_TYPES = 20
_MAX_NOTES = 10
_MAX_STR = 200
_MAX_KNOWLEDGE_LINKS = 25
_MAX_JSON_BYTES = 32 * 1024  # 32KB whole-list budget

# ── redaction patterns ─────────────────────────────────────────────────────
# Order matters: longer / more-specific identifiers first so an ARN (which may
# embed an account id) is masked whole before the bare-12-digit rule fires.
_REDACT = "[redacted]"
_REDACTORS: tuple[re.Pattern[str], ...] = (
    re.compile(r"AKIA[0-9A-Z]{16}"),                          # AWS access key id
    re.compile(r"arn:[^\s\"']+"),                             # ARN
    re.compile(r"\bi-[0-9a-f]{6,}\b"),                        # EC2 instance id
    re.compile(r"\bsg-[0-9a-f]{6,}\b"),                       # security-group id
    re.compile(                                              # IPv4 (optional CIDR)
        r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b"
    ),
    re.compile(                                              # IPv6 (optional CIDR)
        r"\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{0,4}(?:/\d{1,3})?\b"
    ),
    re.compile(r"\b\d{12}\b"),                                # AWS account id
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),                     # long hex token
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),            # long base64 token
)


def redact(text: Any) -> str:
    """Mask cloud identifiers/secrets in ``text`` → ``[redacted]``.

    Coerces non-strings via ``str`` and never raises. Masks AWS account ids,
    ARNs, instance/security-group ids, IPv4/IPv6/CIDR, AKIA access keys, and long
    hex/base64 tokens. Applied to every string the sanitizer retains.
    """
    try:
        s = text if isinstance(text, str) else str(text)
        for pat in _REDACTORS:
            s = pat.sub(_REDACT, s)
        return s
    except Exception:
        return _REDACT


def _clip(text: Any, limit: int = _MAX_STR) -> str:
    """Redact then truncate a string to ``limit`` chars."""
    return redact(text)[:limit]


# ── policy categorization ──────────────────────────────────────────────────
# Map policy substrings → a coarse, safe flag token. Raw rules/ids/CIDRs are
# never emitted; only the deduped category tokens are.
_POLICY_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("public", "public-access"),
    ("0.0.0.0", "open-ingress"),
    ("ingress", "ingress-rule"),
    ("egress", "egress-rule"),
    ("encrypt", "encryption"),
    ("mfa", "mfa-required"),
    ("admin", "admin-policy"),
    ("wildcard", "wildcard-policy"),
    ("*", "wildcard-policy"),
    ("deny", "deny-policy"),
    ("allow", "allow-policy"),
)


def _policy_text(policy: Any) -> str:
    """Best-effort string view of a policy entry for keyword matching."""
    if isinstance(policy, str):
        return policy.lower()
    if isinstance(policy, dict):
        # Join values (not keys like 'id'/'arn') for matching.
        parts: list[str] = []
        for key, value in policy.items():
            if str(key).lower() in {"id", "arn", "cidr", "ip", "rule_id"}:
                continue
            parts.append(str(value))
        return " ".join(parts).lower()
    return str(policy).lower()


def _policy_flags(policies: Any) -> list[str]:
    """Categorize ``policies`` into a deduped, sorted flag-token list.

    Drops raw rules / ids / CIDRs entirely — only coarse category tokens survive.
    """
    if not isinstance(policies, list):
        return []
    flags: set[str] = set()
    for policy in policies:
        text = _policy_text(policy)
        for needle, flag in _POLICY_KEYWORDS:
            if needle in text:
                flags.add(flag)
    return sorted(flags)


# ── resource reduction ─────────────────────────────────────────────────────


def _reduce_resources(resources: Any) -> dict[str, Any]:
    """Keep only regions, instance_types (shape map), and ``*_count`` counters."""
    if not isinstance(resources, dict):
        return {}
    out: dict[str, Any] = {}

    regions = resources.get("regions")
    if isinstance(regions, list):
        out["regions"] = sorted({redact(r) for r in regions if isinstance(r, str)})

    instance_types = resources.get("instance_types")
    if isinstance(instance_types, dict):
        # Shape map of type → count/shape; cap key count, redact keys defensively.
        reduced: dict[str, Any] = {}
        for name, shape in list(instance_types.items())[:_MAX_INSTANCE_TYPES]:
            reduced[redact(name)[:_MAX_STR]] = shape if isinstance(shape, (int, float, str)) else None
        out["instance_types"] = reduced
    elif isinstance(instance_types, list):
        out["instance_types"] = sorted(
            {redact(t)[:_MAX_STR] for t in instance_types if isinstance(t, str)}
        )[:_MAX_INSTANCE_TYPES]

    # Preserve any *_count integer counters (e.g. vpc_count, bucket_count).
    for key, value in resources.items():
        if str(key).endswith("_count") and isinstance(value, (int, float)):
            out[str(key)] = value

    return out


def _sanitize_snapshot(snapshot: Any) -> dict[str, Any] | None:
    """Reduce one InfraSnapshot-shaped dict to a safe constraint, or None."""
    if not isinstance(snapshot, dict):
        return None

    constraint: dict[str, Any] = {}

    adapter = snapshot.get("adapter")
    if adapter is not None:
        constraint["adapter"] = _clip(adapter)

    if "available" in snapshot:
        constraint["available"] = bool(snapshot.get("available"))

    error = snapshot.get("error")
    if error:
        constraint["error"] = _clip(error)

    resources = _reduce_resources(snapshot.get("resources"))
    if resources:
        constraint["resources"] = resources

    policy_flags = _policy_flags(snapshot.get("policies"))
    if policy_flags:
        constraint["policy_flags"] = policy_flags

    findings = snapshot.get("findings")
    if isinstance(findings, list) and findings:
        notes = [_clip(f) for f in findings[:_MAX_NOTES]]
        notes = [n for n in notes if n]
        if notes:
            constraint["notes"] = notes

    # An entirely empty snapshot still yields {} — skip it as carrying nothing.
    return constraint or None


def _json_bytes(obj: Any) -> int:
    try:
        return len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    except Exception:
        return 0


def sanitize_constraints(infra: Any) -> list[dict[str, Any]]:
    """Sanitize a list of InfraSnapshot-shaped dicts into safe constraints.

    Per snapshot: drop ``raw``/``load``/``target``; keep ``adapter``,
    ``available``, ``error`` (<=200 chars); reduce ``resources`` to
    regions/instance_types/``*_count``; categorize ``policies`` into
    ``policy_flags``; copy ``findings``→``notes`` (<=10, each <=200, redacted).
    Caps: <=8 constraints; instance_types<=20 keys. If the JSON of the whole
    result exceeds 32KB, notes are dropped first, then tail entries are trimmed.

    Pure and never raises — malformed/empty input → ``[]``.
    """
    try:
        if not isinstance(infra, list):
            return []

        constraints: list[dict[str, Any]] = []
        for snapshot in infra:
            reduced = _sanitize_snapshot(snapshot)
            if reduced is not None:
                constraints.append(reduced)
            if len(constraints) >= _MAX_CONSTRAINTS:
                break

        # 32KB budget: drop notes first, then trim tail entries.
        if _json_bytes(constraints) > _MAX_JSON_BYTES:
            for constraint in constraints:
                constraint.pop("notes", None)
        while constraints and _json_bytes(constraints) > _MAX_JSON_BYTES:
            constraints.pop()

        return constraints
    except Exception:
        return []


def sanitize_knowledge_links(knowledge: Any) -> list[dict[str, str]]:
    """Reduce knowledge entries to deduped ``{uri, title}`` links (<=25).

    Deduped by ``(uri, title)``; both fields redacted + clipped. Pure / never
    raises — malformed/empty input → ``[]``.
    """
    try:
        if not isinstance(knowledge, list):
            return []
        seen: set[tuple[str, str]] = set()
        links: list[dict[str, str]] = []
        for entry in knowledge:
            if not isinstance(entry, dict):
                continue
            uri = _clip(entry.get("uri") or entry.get("url") or "")
            title = _clip(entry.get("title") or entry.get("name") or "")
            if not uri and not title:
                continue
            key = (uri, title)
            if key in seen:
                continue
            seen.add(key)
            links.append({"uri": uri, "title": title})
            if len(links) >= _MAX_KNOWLEDGE_LINKS:
                break
        return links
    except Exception:
        return []


def attach_constraints(contract: dict[str, Any], plan: Any) -> dict[str, Any]:
    """Attach sanitized enrichment to ``contract['epic_context']`` in place.

    Sets ``epic_context.constraints`` from ``plan.enrichment.infra`` and
    ``epic_context.knowledge_links`` from ``plan.enrichment.knowledge``. Marks
    ``epic_context.truncated`` when the 32KB budget forced entries/notes to be
    dropped. Creates the ``epic_context`` key if absent. Returns the contract for
    composability. Never raises.
    """
    try:
        infra: Any = []
        knowledge: Any = []
        enrichment = getattr(plan, "enrichment", None)
        if enrichment is not None:
            infra = getattr(enrichment, "infra", []) or []
            knowledge = getattr(enrichment, "knowledge", []) or []

        raw_count = len(infra) if isinstance(infra, list) else 0
        constraints = sanitize_constraints(infra)
        links = sanitize_knowledge_links(knowledge)

        epic_context = contract.get("epic_context")
        if not isinstance(epic_context, dict):
            epic_context = {}
            contract["epic_context"] = epic_context

        epic_context["constraints"] = constraints
        epic_context["knowledge_links"] = links

        # truncated when dropped due to caps (count cap or 32KB budget).
        if raw_count > len(constraints):
            epic_context["truncated"] = True

        return contract
    except Exception:
        return contract
