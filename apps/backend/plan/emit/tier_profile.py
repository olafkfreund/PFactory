"""Apply the RFC-0011 difficulty tier to the contract's execution/verify blocks.

The tier (``factory:low|medium|hard``) is a coarse, label-driven dial that wins
over the per-complexity defaults computed by :mod:`execution_profile`,
:mod:`review_tier`, and :mod:`tfactory_block`. PFactory still *computes* those
blocks (so an un-tiered plan is unaffected); this module then OVERRIDES them per
the normative routing table when a tier is set:

  | tier   | model  | planning      | review_tier | tfactory lanes / coverage      |
  |--------|--------|---------------|-------------|--------------------------------|
  | low    | haiku  | skip          | auto        | unit (+api)                    |
  | medium | sonnet | skip (lite)   | async       | unit, api, integration         |
  | hard   | opus   | full decompose| blocking    | + mutation (+equivalence); ++  |

Precedence rules honoured here:
  * **highest wins** is resolved upstream (the classifier hands us one tier).
  * A **blocking gate finding still forces blocking** — the tier may *raise*
    ``review_tier`` to blocking but never *lowers* an already-blocking review
    down to ``auto``/``async`` (safety can only ratchet up).
  * ``change_mode == migration`` forcing ``hard`` is resolved upstream
    (PlanService) and arrives as ``tier == 'hard'``; the equivalence lane is
    attached by :mod:`migration_block`, so we only ensure the mutation lane +
    raised rigor here.

Pure + dependency-light: operates in place on the already-assembled contract
blocks. ``apply_tier`` is the single entry point, called by
``contract_emit.assemble_contract`` AFTER the execution/review_tier/tfactory
attaches so the tier has the last word.
"""

from __future__ import annotations

from typing import Any

# Normative tier table. ``low`` carries ``ollama`` as the preferred local model
# with a ``haiku`` fallback — AIFactory's fast path resolves ollama->haiku at
# runtime (resolve_low_tier_model, G5); the contract records the resolved
# default ``haiku`` so a consumer without an Ollama probe still has a valid model.
VALID_TIERS = ("low", "medium", "hard")

_MODEL_BY_TIER = {"low": "haiku", "medium": "sonnet", "hard": "opus"}
_REVIEW_TIER_BY_TIER = {"low": "auto", "medium": "async", "hard": "blocking"}
_SKIP_PLANNING_BY_TIER = {"low": True, "medium": True, "hard": False}

# review_tier ordinal so a tier override can only ratchet rigor UP, never down.
_REVIEW_RANK = {"auto": 0, "async": 1, "blocking": 2}

# hard raises the verification bar.
_HARD_COVERAGE_TARGET = 0.9


def normalize_tier(tier: str | None) -> str | None:
    """Return a canonical tier string, or ``None`` for unknown/absent input."""
    if not tier:
        return None
    t = str(tier).strip().lower()
    # Tolerate a scoped label form, e.g. 'factory:hard'.
    if ":" in t:
        t = t.rsplit(":", 1)[-1]
    return t if t in VALID_TIERS else None


def _apply_execution(execution: dict[str, Any], tier: str) -> None:
    """Override the ``execution`` block in place per the tier table."""
    from phase_config import infer_provider_from_model

    model = _MODEL_BY_TIER[tier]
    execution["model"] = model
    execution["provider"] = infer_provider_from_model(model)
    execution["provider_rationale"] = f"{model!r} -> {execution['provider']} (RFC-0011 tier={tier})"
    # Keep the per-phase models consistent with the tier model.
    execution["phase_models"] = {"coding": model, "qa": model, "qa_fixer": model}
    execution["autonomy_tier"] = tier
    execution["skip_planning"] = _SKIP_PLANNING_BY_TIER[tier]
    # hard runs the planner-equivalent full treatment in AIFactory too.
    agents = execution.setdefault("agents", {})
    agents["planner"] = tier == "hard"

    # review_tier ratchets up only: never lower an already-blocking review.
    desired = _REVIEW_TIER_BY_TIER[tier]
    current = execution.get("review_tier")
    if current is None or _REVIEW_RANK[desired] >= _REVIEW_RANK.get(current, 0):
        execution["review_tier"] = desired


def _apply_tfactory(tfactory: dict[str, Any], tier: str) -> None:
    """Override the ``tfactory`` verify block in place per the tier table."""
    lanes = list(tfactory.get("lanes") or ["unit"])

    def _ensure(lane: str) -> None:
        if lane not in lanes:
            lanes.append(lane)

    if tier == "low":
        # unit (+api if already present). No extra rigor.
        pass
    elif tier == "medium":
        _ensure("api")
        _ensure("integration")
    elif tier == "hard":
        _ensure("api")
        _ensure("integration")
        _ensure("mutation")
        # Raise the coverage bar (never lower a stricter target already set).
        current = tfactory.get("coverage_target")
        if not isinstance(current, (int, float)) or current < _HARD_COVERAGE_TARGET:
            tfactory["coverage_target"] = _HARD_COVERAGE_TARGET

    tfactory["lanes"] = lanes


def apply_tier(contract: dict[str, Any], tier: str | None) -> dict[str, Any]:
    """Apply the RFC-0011 ``tier`` override (in place) to ``contract``; return it.

    No-op when ``tier`` is absent/unknown, so un-tiered plans keep the
    per-complexity defaults exactly. Must run AFTER the execution/review_tier/
    tfactory blocks are attached so the tier has the final say.
    """
    canonical = normalize_tier(tier)
    if canonical is None:
        return contract

    execution = contract.setdefault("execution", {})
    _apply_execution(execution, canonical)

    tfactory = contract.get("tfactory")
    if isinstance(tfactory, dict):
        _apply_tfactory(tfactory, canonical)

    return contract
