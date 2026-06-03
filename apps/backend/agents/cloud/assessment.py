"""Cloud assessment: Prowler OCSF → PFactory verdict (#133/#138).

The cloud runner (#137) emits Prowler findings in OCSF JSON. This module turns
that into PFactory's vocabulary:

* :func:`parse_ocsf` — OCSF records → flat :class:`CloudFinding` list.
* :func:`assess` — apply the ``fail_on_severity`` gate (from the
  ``CloudProviderTarget.scan`` config) → a verdict (``reject``/``flag``/
  ``accept``) plus severity counts.
* :func:`to_inventory_findings` — the failing findings in the shape
  ``agents.diagrams.render_cloud_topology`` flags red.

Pure: no network, no Prowler — parses already-produced JSON. Defensive against
OCSF shape drift across Prowler versions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

__all__ = [
    "CloudAssessment",
    "CloudFinding",
    "assess",
    "parse_ocsf",
    "to_inventory_findings",
]

# Severity rank — higher is worse. Used for the fail_on_severity gate + sorting.
_SEV_RANK = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
    "informational": 0,
    "info": 0,
    "": 0,
}
# OCSF severity_id → name (fallback when the string severity is absent).
_SEV_ID = {
    6: "critical",
    5: "critical",
    4: "high",
    3: "medium",
    2: "low",
    1: "informational",
}


def _rank(severity: str) -> int:
    return _SEV_RANK.get(severity.lower(), 0)


@dataclass(frozen=True)
class CloudFinding:
    check_id: str
    title: str
    severity: str  # lowercased: critical|high|medium|low|informational
    status: str  # lowercased: pass|fail|muted
    region: str | None
    resource: str | None
    remediation: str = ""  # how to fix (Prowler remediation.desc)
    risk: str = ""  # why it matters (risk_details / status_detail)
    references: tuple[str, ...] = ()  # doc links


@dataclass(frozen=True)
class CloudAssessment:
    verdict: str  # reject | flag | accept
    fail_on_severity: str
    total: int
    passed: int
    failed: int
    muted: int
    fail_counts: dict[str, int]  # failing findings by severity


# ── parse ────────────────────────────────────────────────────────────────────


def _one(rec: dict) -> CloudFinding:
    fi = rec.get("finding_info") or {}
    meta = rec.get("metadata") or {}
    resources = rec.get("resources") or []
    res0 = resources[0] if resources else {}
    cloud = rec.get("cloud") or {}

    sev = str(rec.get("severity") or "").strip().lower()
    if not sev:
        sev = _SEV_ID.get(rec.get("severity_id"), "")

    region = cloud.get("region") or res0.get("region") or None
    rem = rec.get("remediation") or {}
    refs = rem.get("references") or []

    return CloudFinding(
        check_id=str(
            fi.get("uid") or meta.get("event_code") or rec.get("type_name") or "?"
        ),
        title=str(fi.get("title") or rec.get("message") or "finding"),
        severity=sev,
        status=str(rec.get("status_code") or rec.get("status") or "").strip().lower(),
        region=region,
        resource=res0.get("name") or res0.get("uid") or None,
        remediation=str(rem.get("desc") or ""),
        risk=str(rec.get("risk_details") or rec.get("status_detail") or ""),
        references=tuple(r for r in refs if isinstance(r, str)),
    )


def parse_ocsf(data: list | dict | str) -> list[CloudFinding]:
    """Parse Prowler OCSF output (a JSON list, a dict, or a JSON string)."""
    if isinstance(data, str):
        data = json.loads(data)
    if isinstance(data, dict):
        data = [data]
    return [_one(r) for r in data if isinstance(r, dict)]


# ── assess (the verdict gate) ────────────────────────────────────────────────


def assess(
    findings: list[CloudFinding], *, fail_on_severity: str = "high"
) -> CloudAssessment:
    """Map findings to a verdict via the ``fail_on_severity`` gate.

    - any FAIL at or above ``fail_on_severity`` → ``reject``
    - any FAIL below the gate → ``flag``
    - no FAILs → ``accept``
    """
    gate = _rank(fail_on_severity)
    passed = sum(1 for f in findings if f.status == "pass")
    muted = sum(1 for f in findings if f.status == "muted")
    fails = [f for f in findings if f.status == "fail"]

    fail_counts: dict[str, int] = {}
    breaches = False
    for f in fails:
        fail_counts[f.severity] = fail_counts.get(f.severity, 0) + 1
        if _rank(f.severity) >= gate:
            breaches = True

    if breaches:
        verdict = "reject"
    elif fails:
        verdict = "flag"
    else:
        verdict = "accept"

    return CloudAssessment(
        verdict=verdict,
        fail_on_severity=fail_on_severity.lower(),
        total=len(findings),
        passed=passed,
        failed=len(fails),
        muted=muted,
        fail_counts=fail_counts,
    )


# ── diagram findings ─────────────────────────────────────────────────────────


def to_inventory_findings(
    findings: list[CloudFinding], *, limit: int = 20
) -> list[dict]:
    """The failing findings as the ``findings`` list ``render_cloud_topology`` flags.

    Sorted worst-severity first; capped at ``limit`` so the diagram stays legible.
    ``scope`` is the region (or ``global`` for account-wide findings).
    """
    fails = [f for f in findings if f.status == "fail"]
    fails.sort(key=lambda f: (-_rank(f.severity), f.check_id))
    out: list[dict] = []
    for f in fails[:limit]:
        out.append(
            {
                "severity": f.severity,
                "title": f.title,
                "scope": f.region or "global",
            }
        )
    return out
