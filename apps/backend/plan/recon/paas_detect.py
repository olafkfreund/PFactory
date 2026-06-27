"""Detect managed-PaaS deploy intent from plan text (RFC-0013, producing side).

Greenfield plans name their deploy target in prose ("deploy to GCP Cloud Run with
Redis and Postgres") rather than in repo manifests, so the manifest probe
(:func:`plan.recon.ci_probe.probe_deploy`) can't see it. This module reads the
plan's text and resolves an explicit PaaS target + the managed data services it
asks for, so the ``deployment`` block can carry a concrete ``deploy_system``
(e.g. ``gcp-cloud-run``) instead of ``unknown``.

Conservative by design: returns ``None`` unless a cloud PaaS target is named
(never fabricate a target), and maps each cloud's phrasings to the single deploy
system the Factory has a proven template for (the ``templates/cloud-deploy`` pack).
"""

from __future__ import annotations

from typing import TypedDict

# Cloud PaaS target phrases -> the contract deploy_system the Factory can emit.
# Order within a cloud doesn't matter; cross-cloud ties break on earliest mention.
_TARGETS: dict[str, tuple[str, tuple[str, ...]]] = {
    "gcp": ("gcp-cloud-run", ("cloud run", "cloud-run", "cloudrun", "app engine")),
    "azure": (
        "azure-container-apps",
        ("container apps", "container app", "app service", "azure web app"),
    ),
    "aws": ("aws-app-runner", ("app runner", "apprunner")),
}

# Managed data-service phrases -> the contract managed_services token.
_SERVICES: dict[str, tuple[str, ...]] = {
    "postgres": ("postgres", "postgresql", "cloud sql", "azure database for postgres", "rds", "aurora"),
    "redis": ("redis", "memorystore", "elasticache", "azure cache"),
    "mysql": ("mysql", "mariadb"),
    "mongodb": ("mongodb", "mongo", "cosmos db", "cosmosdb", "documentdb"),
}


class PaasTarget(TypedDict):
    cloud: str
    deploy_system: str
    managed_services: list[str]


def detect_paas_target(text: str) -> PaasTarget | None:
    """Resolve an explicit managed-PaaS deploy target from ``text``.

    Returns a :class:`PaasTarget` (cloud + deploy_system + sorted managed
    services) when a cloud PaaS target is named, else ``None``. Case-insensitive.
    When more than one cloud is mentioned, the earliest-mentioned wins.
    """
    if not text:
        return None
    low = text.lower()

    best_cloud: str | None = None
    best_pos = len(low) + 1
    for cloud, (_system, phrases) in _TARGETS.items():
        pos = min((low.find(p) for p in phrases if p in low), default=-1)
        if pos != -1 and pos < best_pos:
            best_pos, best_cloud = pos, cloud
    if best_cloud is None:
        return None

    services = sorted(
        token
        for token, phrases in _SERVICES.items()
        if any(p in low for p in phrases)
    )
    return PaasTarget(
        cloud=best_cloud,
        deploy_system=_TARGETS[best_cloud][0],
        managed_services=services,
    )
