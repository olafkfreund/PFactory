"""RFC-0014 v1 routing-tier resolution for the planning stage (Factory#272, #283).

The fleet-wide knob: static per-stage tier routing. PFactory's only LLM stage
is planning (the ``decompose_with_llm`` seam), so this module resolves the
model for ``stage=planning`` with the same precedence chain AIFactory uses:

    pinned_model > override > policy > current default

* **pinned_model** — the escape hatch; ``PFACTORY_PLANNER_PINNED_MODEL`` wins
  everywhere (preserves current behaviour when set).
* **override** — a per-call override supplied by the caller (no live caller
  today; the seam exists for per-task routing).
* **policy** — ``PFACTORY_ROUTING_POLICY`` (env/Helm-fed JSON), shape
  ``{"planning": {"tier": "frontier", "model": "<provider model id>"}}``.
  Only an entry carrying a ``model`` changes behaviour.
* **current default** — whatever the supplied llm object already uses.

Planning stays frontier-tier in the v1 default policy (RFC-0014 stage table),
so with no env set the resolution is a no-op and behaviour is unchanged.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

STAGE = "planning"
# RFC-0014 v1 default stage->tier table entry for planning.
DEFAULT_TIER = "frontier"

_PINNED_ENV = "PFACTORY_PLANNER_PINNED_MODEL"
_POLICY_ENV = "PFACTORY_ROUTING_POLICY"


@dataclass(frozen=True)
class RoutingDecision:
    """The resolved model + tier for one stage, with its precedence source."""

    model: str
    tier: str
    source: str  # pinned_model | override | policy | default

    def as_block(self) -> dict[str, str]:
        """The contract/completion-event ``routing`` evidence block."""
        return {
            "stage": STAGE,
            "model": self.model,
            "tier": self.tier,
            "source": self.source,
        }


def _policy_entry() -> dict[str, str]:
    """The policy's ``planning`` entry, or ``{}`` (unset/bad JSON fail-closed)."""
    raw = (os.environ.get(_POLICY_ENV) or "").strip()
    if not raw:
        return {}
    try:
        policy = json.loads(raw)
        entry = policy.get(STAGE) if isinstance(policy, dict) else None
        return entry if isinstance(entry, dict) else {}
    except ValueError:
        logger.warning("ignoring unparseable %s (not JSON)", _POLICY_ENV)
        return {}


def resolve_planning_model(
    *,
    override: str | None = None,
    default_model: str | None = None,
) -> RoutingDecision:
    """Resolve the planning-stage model: pinned > override > policy > default.

    ``default_model`` is the model the caller's llm object already carries;
    the decision is ``source="default"`` (a no-op) unless something outranks it.
    """
    pinned = (os.environ.get(_PINNED_ENV) or "").strip()
    if pinned:
        return RoutingDecision(model=pinned, tier=DEFAULT_TIER, source="pinned_model")
    if override:
        return RoutingDecision(model=override, tier=DEFAULT_TIER, source="override")
    entry = _policy_entry()
    model = str(entry.get("model") or "").strip()
    if model:
        tier = str(entry.get("tier") or DEFAULT_TIER).strip() or DEFAULT_TIER
        return RoutingDecision(model=model, tier=tier, source="policy")
    return RoutingDecision(model=default_model or "", tier=DEFAULT_TIER, source="default")
