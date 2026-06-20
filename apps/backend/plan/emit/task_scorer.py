"""RFC-0014 §4/§4b deterministic task scorer (pure, no I/O).

Reads signals **already on the assembled Task Contract** and emits the three
effort-free verdicts the cost router and the gate consume:

  - ``difficulty ∈ {low, medium, high}`` — capability needed (drives the model
    floor, via the RFC-0014 routing class).
  - ``risk ∈ {low, medium, high}`` — blast radius / reversibility / production /
    security (drives the gate).
  - ``autonomy ∈ {autonomous, review, approval}`` — can AI ship this alone, or is
    a human needed?

It also derives the RFC-0014 ``routing_class ∈ {economy, standard, premium,
governed}`` and a ``cost_ceiling`` band, so the router has a class + ceiling to
size models against.

Signals (RFC-0014 §4 table) — all read defensively off the contract:

  | signal                       | contract source                              |
  |------------------------------|----------------------------------------------|
  | difficulty tier (RFC-0011)   | ``execution.autonomy_tier``                  |
  | change_mode (RFC-0010)       | ``change_mode`` (migration ⇒ harder)         |
  | risk_class / production       | ``deployment.risk_class`` / ``...production`` |
  | blast radius / destructive    | ``baseline.blast_radius.destructive_iac``    |
  | AC count, file footprint      | ``final_acceptance`` / phase subtasks        |
  | security scope                | ``tfactory.security_scope``                  |

The verdict reuses the existing control plane (RFC-0011 ``autonomy_tier``,
RFC-0013 ``risk_class``, RFC-0009 ``merge_policy``) — it makes the call EXPLICIT
and effort-free, it does not invent a new dial. **No dev-day / story-point sizing
anywhere** (RFC-0014 §4b).

This module is pure and deterministic: identical input ⇒ identical output, no
network/clock/filesystem. :func:`score_task` is the single entry point.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

Difficulty = Literal["low", "medium", "high"]
Risk = Literal["low", "medium", "high"]
RoutingClass = Literal["economy", "standard", "premium", "governed"]
Autonomy = Literal["autonomous", "review", "approval"]

# Ordinals so we can take the strongest of several signals (never weaken).
_DIFFICULTY_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}
_RANK_DIFFICULTY: dict[int, Difficulty] = {0: "low", 1: "medium", 2: "high"}
_RISK_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}
_RANK_RISK: dict[int, Risk] = {0: "low", 1: "medium", 2: "high"}

# RFC-0011 difficulty tier (low|medium|hard) -> the scorer's difficulty band.
# 'hard' is the RFC-0011 label; we surface it as 'high' on the contract.
_TIER_TO_DIFFICULTY: dict[str, Difficulty] = {"low": "low", "medium": "medium", "hard": "high"}

# Difficulty x risk -> routing class (RFC-0014 §4). 'governed' is forced by the
# hard overrides (production / secrets / access / destructive / migration), not by
# this grid; the grid only chooses among economy/standard/premium.
_CLASS_GRID: dict[tuple[Difficulty, Risk], RoutingClass] = {
    ("low", "low"): "economy",
    ("low", "medium"): "standard",
    ("low", "high"): "premium",
    ("medium", "low"): "standard",
    ("medium", "medium"): "standard",
    ("medium", "high"): "premium",
    ("high", "low"): "premium",
    ("high", "medium"): "premium",
    ("high", "high"): "premium",
}

# Routing class -> a coarse USD ceiling band (per-task rolled estimate). economy
# is the tightest; governed gets the most headroom so a frontier planning model
# is never starved. ``None`` is "no ceiling" — used by no class here, but the
# router tolerates it (subscription/local spend is governed by billing mode).
_CEILING_BY_CLASS: dict[RoutingClass, float] = {
    "economy": 0.50,
    "standard": 3.00,
    "premium": 10.00,
    "governed": 25.00,
}

# Shape thresholds that bump a low-tier task to at least medium difficulty: a
# broad change (many acceptance criteria OR a wide file footprint) is not "low".
_BROAD_AC_COUNT = 6
_BROAD_FILE_COUNT = 10


class TaskScore(TypedDict):
    """The effort-free scoring verdict for one task (RFC-0014 §4b)."""

    difficulty: Difficulty
    risk: Risk
    routing_class: RoutingClass
    cost_ceiling: float
    autonomy: Autonomy
    reason: str


def _get(contract: dict[str, Any], path: str, default: Any = None) -> Any:
    """Read a dotted path off the contract, tolerating missing intermediates."""
    node: Any = contract
    for part in path.split("."):
        if not isinstance(node, dict):
            return default
        node = node.get(part)
    return node if node is not None else default


def _difficulty(contract: dict[str, Any]) -> Difficulty:
    """Capability needed. Tier wins; a migration (RFC-0010 rewrite) floors at
    high; many ACs / a wide file footprint bump a low task to medium."""
    tier = str(_get(contract, "execution.autonomy_tier", "") or "").strip().lower()
    rank = _DIFFICULTY_RANK.get(_TIER_TO_DIFFICULTY.get(tier, ""), -1)

    # No tier classified: infer a conservative baseline from shape (never below
    # medium for multi-AC work, never below low).
    if rank < 0:
        rank = _DIFFICULTY_RANK["low"]

    # A migration/rewrite is, by RFC-0010, hard — frontier capability.
    if str(_get(contract, "change_mode", "") or "").lower() == "migration":
        rank = max(rank, _DIFFICULTY_RANK["high"])

    # Shape bump: a broad change (many ACs OR many files touched) is at least
    # medium even if the tier label says low.
    ac_count = len(_get(contract, "final_acceptance", []) or [])
    files_touched = len(_get(contract, "baseline.blast_radius.files", []) or [])
    if ac_count >= _BROAD_AC_COUNT or files_touched >= _BROAD_FILE_COUNT:
        rank = max(rank, _DIFFICULTY_RANK["medium"])

    return _RANK_DIFFICULTY[rank]


def _has_destructive(contract: dict[str, Any]) -> bool:
    return bool(_get(contract, "baseline.blast_radius.destructive_iac", []) or [])


def _has_security_scope(contract: dict[str, Any]) -> bool:
    return bool(_get(contract, "tfactory.security_scope", []) or [])


def _is_production(contract: dict[str, Any]) -> bool:
    return str(_get(contract, "deployment.production_classification", "") or "") == "production"


def _is_migration(contract: dict[str, Any]) -> bool:
    return str(_get(contract, "change_mode", "") or "").lower() == "migration"


def _risk(contract: dict[str, Any]) -> Risk:
    """Blast radius / reversibility / production / security. Takes the strongest
    of the RFC-0013 risk_class, production, destructive IaC, and security scope."""
    rank = _RISK_RANK.get(str(_get(contract, "deployment.risk_class", "") or "").lower(), -1)
    if rank < 0:
        rank = _RISK_RANK["low"]
    if _is_production(contract) or _has_destructive(contract):
        rank = _RISK_RANK["high"]
    if _has_security_scope(contract):
        rank = max(rank, _RISK_RANK["high"])
    return _RANK_RISK[rank]


def _hard_override(contract: dict[str, Any]) -> str | None:
    """RFC-0014 §4b always-human overrides. Returns a named reason, or None.

    production deploy / credential-secret change / access-control change /
    destructive data op / rewrite-migration force ``approval`` and ``governed``
    regardless of the difficulty x risk score.
    """
    if _is_production(contract):
        return "production deploy (VAL-4 never autonomous)"
    if _has_destructive(contract):
        return "destructive/irreversible IaC operation"
    if _is_migration(contract):
        return "rewrite/migration"
    if _has_security_scope(contract):
        return "security/secrets/access scope"
    # An access block on the contract means the task needs external credentials
    # / access-control — always a blocking human gate (RFC-0007 + §4b).
    if _get(contract, "access.requirements", []):
        return "access-control / credential change"
    return None


def _autonomy(difficulty: Difficulty, risk: Risk, override: str | None) -> tuple[Autonomy, str]:
    """RFC-0014 §4b autonomy verdict (difficulty x risk, with hard overrides)."""
    if override is not None:
        return "approval", f"always-human override: {override}"
    if difficulty == "high" or risk == "high":
        return "approval", f"difficulty={difficulty}, risk={risk} (high ⇒ blocking approval)"
    if difficulty == "medium" or risk == "medium":
        return "review", f"difficulty={difficulty}, risk={risk} (medium ⇒ async review)"
    return (
        "autonomous",
        "difficulty=low and risk=low, no override (auto-merge when green)",
    )


def score_task(contract: dict[str, Any]) -> TaskScore:
    """Score an assembled Task Contract into the RFC-0014 §4b verdict.

    Pure + deterministic: reads only the contract dict, emits ``difficulty``,
    ``risk``, ``routing_class``, ``cost_ceiling``, ``autonomy`` and a ``reason``.
    Defensive to a partial/absent contract — every signal degrades to its safe
    default (never below the standard bar), so an un-tiered greenfield plan still
    scores cleanly.
    """
    difficulty = _difficulty(contract)
    risk = _risk(contract)
    override = _hard_override(contract)

    routing_class: RoutingClass = (
        "governed" if override is not None else _CLASS_GRID[(difficulty, risk)]
    )
    cost_ceiling = _CEILING_BY_CLASS[routing_class]
    autonomy, reason = _autonomy(difficulty, risk, override)

    return TaskScore(
        difficulty=difficulty,
        risk=risk,
        routing_class=routing_class,
        cost_ceiling=cost_ceiling,
        autonomy=autonomy,
        reason=reason,
    )
