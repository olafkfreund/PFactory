"""Implicit service requirements (RFC-0008 §3.1, #166).

Users describe *intent*, not implementation. "A task board" implies a service
that boots, declares its runtime dependencies, answers a health check, and is
deployable — none of which the user writes, so nothing in the plan demands them.
The consequence (the 2026-06-18 factory-demo-taskboard run): the build satisfied
every *stated* acceptance criterion yet the service did not actually run.

This module closes that gap for **deployable software-service** plans by:

  * :func:`inject_into_epic` — appending the implicit acceptance criteria the
    user never wrote (the "complete the plan" half of #166); and
  * :func:`missing_requirements` — the canonical list the review layer enforces
    (the completeness lens in ``plan/review/lenses/completeness.py`` and the
    ``service-requirements-covered`` readiness check).

The defaults are sourced from the plan type. When the plan carries Backstage
Component annotations (e.g. a custom health path), :func:`health_path` refines
the health-check criterion; otherwise a sensible default is used.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan
    from plan.models import NormalizedPlan
    from plan.plan_types.loader import PlanTypeDescriptor


# Each implicit requirement is (key, acceptance-criterion text, match keywords).
# The keywords are matched case-insensitively against the epic's existing child
# ACs + bodies so an equivalent criterion the user *did* write isn't duplicated.
# ``{health_path}`` in the health text is filled by :func:`service_requirements`.
SERVICE_IMPLICIT_REQUIREMENTS: list[tuple[str, str, tuple[str, ...]]] = [
    (
        "starts",
        "The service starts and serves without error.",
        ("start", "boot", "launch", "serves", "comes up", "runs without"),
    ),
    (
        "dependencies",
        "The service declares its runtime dependencies in a manifest "
        "(requirements.txt / package.json / go.mod / pyproject.toml).",
        ("dependenc", "manifest", "requirements.txt", "package.json", "go.mod", "pyproject"),
    ),
    (
        "health",
        "The service exposes a health-check endpoint ({health_path}) that "
        "returns a success status.",
        ("health", "/healthz", "/readyz", "readiness", "liveness"),
    ),
    (
        "deployable",
        "The service is deployable — the build produces a runnable artifact "
        "(container image / runnable package).",
        ("deploy", "dockerfile", "container image", "runnable artifact"),
    ),
]

_DEFAULT_HEALTH_PATH = "GET /health"


def is_deployable_service(plan: NormalizedPlan, descriptor: PlanTypeDescriptor) -> bool:
    """True when the plan describes a deployable software service.

    Either the plan type is explicitly ``software-service``, or it is a software
    plan whose pipeline synthesizes CI/CD (i.e. it produces something meant to be
    built and deployed). Non-software plans (SOWs, infra changes, data pipelines,
    product planning) are never given service runtime ACs.
    """
    name = (getattr(descriptor, "name", "") or "").lower()
    category = (getattr(descriptor, "category", "") or "").lower()
    stages = getattr(descriptor, "stages", None)
    synth_cicd = bool(getattr(stages, "synthesize_cicd", False))
    return name == "software-service" or (category == "software" and synth_cicd)


def health_path(plan: NormalizedPlan) -> str:
    """The service's health endpoint.

    Prefers a Backstage Component annotation surfaced in enrichment
    (``pfactory.io/health-path`` or a generic ``health``/``healthcheck`` key);
    falls back to ``GET /health``. Best-effort — never raises.
    """
    try:
        knowledge = getattr(getattr(plan, "enrichment", None), "knowledge", []) or []
        for entry in knowledge:
            if not isinstance(entry, dict):
                continue
            annotations = entry.get("annotations") or entry.get("metadata") or {}
            if not isinstance(annotations, dict):
                continue
            for key in ("pfactory.io/health-path", "healthcheck", "health"):
                val = annotations.get(key)
                if isinstance(val, str) and val.strip():
                    path = val.strip()
                    return path if path.upper().startswith("GET") else f"GET {path}"
    except Exception:  # noqa: BLE001 — annotation sourcing is best-effort
        pass
    return _DEFAULT_HEALTH_PATH


def service_requirements(plan: NormalizedPlan) -> list[tuple[str, str]]:
    """Return the implicit service requirements as ``(key, ac_text)`` pairs, with
    the health path resolved from Backstage annotations when available."""
    hp = health_path(plan)
    return [
        (key, text.format(health_path=hp) if "{health_path}" in text else text)
        for key, text, _kw in SERVICE_IMPLICIT_REQUIREMENTS
    ]


def _haystack(epic: EpicPlan) -> str:
    """All existing child ACs + bodies, lowercased, for keyword coverage tests."""
    parts: list[str] = []
    for child in epic.children:
        parts.extend(ac.lower() for ac in child.acceptance_criteria)
        parts.append((child.body or "").lower())
    return "\n".join(parts)


def missing_requirements(epic: EpicPlan) -> list[tuple[str, str]]:
    """Return the implicit requirements not already covered by any child.

    Coverage is keyword-based: if the user (or the decomposer) already wrote an
    AC/body that speaks to a requirement, it is considered covered and not
    re-injected or flagged. Returns ``(key, ac_text)`` for each missing one.
    """
    hay = _haystack(epic)
    missing: list[tuple[str, str]] = []
    for key, text, keywords in SERVICE_IMPLICIT_REQUIREMENTS:
        if not any(kw in hay for kw in keywords):
            missing.append((key, text))
    return missing


def inject_into_epic(
    plan: NormalizedPlan, epic: EpicPlan, descriptor: PlanTypeDescriptor
) -> list[str]:
    """Append any missing implicit service ACs to the epic's primary child.

    No-op unless the plan is a deployable software service with at least one
    child. Idempotent: requirements already covered (by the user or a prior call)
    are skipped. The criteria land on the first ``feature`` child (or the first
    child) so AIFactory/TFactory verify them like any other AC. Returns the AC
    texts that were injected (empty when nothing was added).
    """
    if not is_deployable_service(plan, descriptor) or not epic.children:
        return []
    missing_keys = {key for key, _text in missing_requirements(epic)}
    if not missing_keys:
        return []
    # Resolve health path etc. now (annotation-aware) for the texts we inject.
    injected = [text for key, text in service_requirements(plan) if key in missing_keys]
    target = next((c for c in epic.children if c.kind == "feature"), epic.children[0])
    target.acceptance_criteria = list(target.acceptance_criteria) + injected
    return injected
