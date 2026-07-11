"""Build the RFC-0002 Task Contract plan from a decomposed epic (epic #65, child 2).

Turns the flat :class:`~plan.decompose.models.EpicPlan` (a DAG of
:class:`~plan.decompose.models.ChildIssue` joined by ``depends_on``) into the
contract's ``phases`` → ``subtasks`` structure, plus the top-level task fields.
Phases are dependency *layers* (topological levels): everything in a phase can
run once the prior layers are done, which is exactly the shape AIFactory's wave
executor consumes when it skips planning.

This child produces the *plan* only — the ``execution`` profile, the ``tfactory``
block, per-subtask ``verification`` specs, and the HMAC ``approval`` envelope are
later children of #65. The result is validated against the vendored schema by
:func:`plan.emit.task_contract.validate_contract`.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from plan.recon.delta import blast_radius, compute_footprints

if TYPE_CHECKING:
    from plan.decompose.models import ChildIssue, EpicPlan
    from plan.models import NormalizedPlan

CONTRACT_VERSION = "2"

# ChildIssue.kind → the phase type its subtasks vote for (schema phase.type enum).
_KIND_TO_PHASE_TYPE: dict[str, str] = {
    "feature": "implementation",
    "task": "implementation",
    "infra": "setup",
    "chore": "setup",
    "research": "investigation",
    "docs": "cleanup",
    "testing": "integration",
    "cicd": "integration",
}

# plan_type → contract workflow_type (schema enum).
_PLAN_TYPE_TO_WORKFLOW: dict[str, str] = {
    "infra-change": "migration",
    "data-pipeline": "migration",
    "software-service": "feature",
    "feature": "feature",
    "refactor": "refactor",
}


def _service_of(child: ChildIssue) -> str | None:
    """Best-effort service from a child's ``area:``/``service:`` label."""
    for label in child.labels:
        for prefix in ("service:", "area:"):
            if label.startswith(prefix):
                return label[len(prefix) :] or None
    return None


def _dependency_levels(epic: EpicPlan) -> dict[str, int]:
    """Longest-path level per child over valid ``depends_on`` edges (0-based).

    Unknown/self deps are ignored (the readiness ``deps-sound`` check governs
    those); cycles are broken defensively so this never recurses forever.
    """
    keys = {c.key for c in epic.children}
    deps = {c.key: [d for d in c.depends_on if d in keys and d != c.key] for c in epic.children}
    level: dict[str, int] = {}

    def resolve(key: str, seen: frozenset[str]) -> int:
        if key in level:
            return level[key]
        if key in seen:  # cycle guard
            return 0
        parents = deps.get(key, [])
        lv = 0 if not parents else 1 + max(resolve(p, seen | {key}) for p in parents)
        level[key] = lv
        return lv

    for c in epic.children:
        resolve(c.key, frozenset())
    return level


def _phase_type(kinds: list[str]) -> str:
    """Majority-vote a phase type from its subtasks' kinds (tie → implementation)."""
    if not kinds:
        return "implementation"
    votes = Counter(_KIND_TO_PHASE_TYPE.get(k, "implementation") for k in kinds)
    top = votes.most_common()
    best = top[0][1]
    winners = sorted(t for t, n in top if n == best)
    return "implementation" if "implementation" in winners else winners[0]


def _subtask(
    child: ChildIssue,
    all_keys: set[str],
    footprint: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Render one ChildIssue as a contract subtask.

    ``footprint`` (RFC-0010 delta pass) carries the files this child touches when
    the plan was grounded in a RepoMap; absent → empty (greenfield) as before.
    """
    description = child.title.strip()
    if child.body.strip() and child.body.strip() != description:
        description = f"{description}\n\n{child.body.strip()}"
    fp = footprint or {}
    return {
        "id": child.key,
        "description": description,
        "status": "pending",
        "service": _service_of(child),
        "depends_on": [d for d in child.depends_on if d in all_keys and d != child.key],
        # RFC-0010: real footprints from reconnaissance (empty when greenfield).
        "files_to_create": fp.get("files_to_create", []),
        "files_to_modify": fp.get("files_to_modify", []),
        "patterns_from": fp.get("patterns_from", []),
        # Carried for the execution-profile child (#65): not in the base schema
        # but additionalProperties allows it.
        "complexity": child.complexity,
        "acceptance_criteria": list(child.acceptance_criteria),
    }


def build_phases(
    epic: EpicPlan, footprints: dict[str, dict[str, list[str]]] | None = None
) -> list[dict[str, Any]]:
    """Group the epic's children into dependency-layer phases.

    ``footprints`` (keyed by child.key) carries the RFC-0010 delta — the files
    each child touches — populated by :func:`build_task_contract` from the plan's
    RepoMap. None/empty → greenfield, footprints stay empty.
    """
    if not epic.children:
        return []
    levels = _dependency_levels(epic)
    all_keys = {c.key for c in epic.children}
    footprints = footprints or {}

    # children grouped by level, preserving input order within a level.
    by_level: dict[int, list[ChildIssue]] = {}
    for child in epic.children:
        by_level.setdefault(levels[child.key], []).append(child)

    phases: list[dict[str, Any]] = []
    for level in sorted(by_level):
        children = by_level[level]
        subtasks = [_subtask(c, all_keys, footprints.get(c.key)) for c in children]
        # phase depends on the (1-based) phase numbers holding any child's deps.
        dep_levels = {
            levels[d] for c in children for d in c.depends_on if d in levels and d != c.key
        }
        phase = {
            "phase": level + 1,
            "name": _phase_name(children),
            "type": _phase_type([c.kind for c in children]),
            "depends_on": sorted(dl + 1 for dl in dep_levels),
            # Same-level children are independent by construction, so a phase
            # with >1 subtask can run them as a concurrent wave (#65 child 4).
            "parallel_safe": len(subtasks) > 1,
            "subtasks": subtasks,
        }
        phases.append(phase)
    return phases


def _phase_name(children: list[ChildIssue]) -> str:
    """A short human name for a phase from its dominant kind."""
    kind = Counter(c.kind for c in children).most_common(1)[0][0]
    names = {
        "feature": "Implementation",
        "task": "Implementation",
        "infra": "Infrastructure",
        "chore": "Setup",
        "research": "Investigation",
        "docs": "Documentation",
        "testing": "Testing",
        "cicd": "CI/CD",
    }
    return names.get(kind, "Implementation")


def _workflow_type(plan: NormalizedPlan) -> str:
    # RFC-0010: a migration change_mode is, unambiguously, a migration workflow.
    if plan.change_mode == "migration":
        return "migration"
    pt = (plan.plan_type or "").lower()
    if pt in _PLAN_TYPE_TO_WORKFLOW:
        return _PLAN_TYPE_TO_WORKFLOW[pt]
    if "refactor" in pt:
        return "refactor"
    return "feature"


def build_task_contract(
    plan: NormalizedPlan,
    epic: EpicPlan,
    *,
    workflow_type: str | None = None,
    repo: str | None = None,
    correlation_key: str | None = None,
) -> dict[str, Any]:
    """Build the RFC-0002 Task Contract *plan* from a normalized plan + its epic.

    Produces a schema-valid contract carrying ``contract_version``, ``feature``,
    ``workflow_type``, ``phases`` (dependency layers) and ``final_acceptance``.
    The ``execution`` / ``tfactory`` / ``approval`` blocks are added by later
    children of #65.
    """
    # RFC-0010: derive the delta (per-child file footprints) from the RepoMap, so
    # subtasks carry real files_to_modify/create instead of empty lists.
    footprints = compute_footprints(plan, epic)
    phases = build_phases(epic, footprints)
    services = sorted({s for ph in phases for st in ph["subtasks"] if (s := st.get("service"))})
    rm = plan.repo_map
    provenance: dict[str, Any] = {
        "source": "pfactory",
        "plan_id": plan.plan_id,
        **({"repo": repo} if repo else {}),
    }
    # Factory#273: carry the intake trust marking so downstream defenses
    # (AIFactory scan gate) know issue/spec-derived text is untrusted.
    if plan.content_trust:
        provenance["content_trust"] = plan.content_trust
    if rm is not None and rm.available:
        if rm.base_ref:
            provenance["base_ref"] = rm.base_ref
        if rm.commit:
            provenance["baseline_commit"] = rm.commit
    contract: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "feature": (epic.epic_title or plan.title).strip(),
        "workflow_type": workflow_type or _workflow_type(plan),
        "services_involved": services,
        "final_acceptance": [c.text for c in plan.criteria],
        "phases": phases,
        "provenance": provenance,
    }
    # RFC-0010: the recon verdict + the grounding the human approves.
    if plan.change_mode:
        contract["change_mode"] = plan.change_mode
    if rm is not None and rm.available:
        baseline = rm.to_baseline_block()
        baseline["blast_radius"] = blast_radius(footprints, rm)
        contract["baseline"] = baseline
    # RFC-0013: attach the deployment-aware planning block derived during
    # process() (CI/deploy surface + risk/scan/gate policy + DORA + readiness).
    # Additive and optional: absent => the contract has no deployment dimension.
    if epic.deployment:
        contract["deployment"] = epic.deployment
    if correlation_key:
        contract["correlation_key"] = correlation_key
    # An explicit, ALWAYS-scalar epic reference for downstream task descriptions
    # (e.g. AIFactory's "Correlation epic #<id>"). Consumers must read this, never
    # the ``epic`` object on the emitted session — str()-ing that whole EpicPlan
    # is what produced the cockpit "wall of text" (the description embedded the
    # full plan dict). Prefer the GitHub issue number (when correlation_key is the
    # numeric issue#), else the short plan_id (e.g. "016-fastapi-api-gateway").
    contract["epic_ref"] = (
        correlation_key if (correlation_key and correlation_key.isdigit()) else plan.plan_id
    )
    return contract
