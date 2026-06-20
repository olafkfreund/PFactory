"""RFC-0007 (#86 PR-c): Class C ephemeral-target provisioning.

A disposable target the pipeline owns (LocalStack / testcontainers / ephemeral
Keycloak realm / throwaway cloud project) lets VAL-3 run with no production
credential. This module is the pure abstraction enforcing the two non-negotiable
safety rules from RFC-0006 §2 / RFC-0007:

  1. **Cost guard** — never provision an effectful target without an explicit cost
     ceiling (no ceiling => denied), and never above it.
  2. **Mandatory auto-teardown** — a provisioned target is ALWAYS torn down, even
     if the body raises. No leaked targets (mirrors the k8s Job ttl/GC posture).

Concrete provisioners (docker-compose, LocalStack, a disposable cloud project)
plug in as a ``factory() -> EphemeralTarget``; TFactory supplies the runtime ones.
``make_liveness_check`` adapts a live target into the curation gate's
``liveness_check`` (#86 PR-b).
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


class EphemeralTarget(Protocol):
    """A provisioned disposable target. ``factory()`` returns one (provisioned)."""

    def live(self) -> bool: ...
    def teardown(self) -> None: ...


@dataclass(frozen=True)
class CostGuard:
    """Cost ceiling for provisioning an effectful target (RFC-0006 §2)."""

    estimate_usd: float
    max_usd: float | None = None

    def allowed(self) -> bool:
        # No ceiling => deny: we never provision an effectful target without one.
        return self.max_usd is not None and self.estimate_usd <= self.max_usd

    def reason(self) -> str:
        if self.max_usd is None:
            return "no cost ceiling set (VAL-3 requires an explicit one)"
        if self.estimate_usd > self.max_usd:
            return f"estimate ${self.estimate_usd:g} exceeds ceiling ${self.max_usd:g}"
        return "within budget"


class CostGuardError(RuntimeError):
    """Raised when the cost guard denies provisioning (before anything is created)."""


@contextmanager
def provisioned(factory, *, cost_guard: CostGuard):
    """Provision an ephemeral target under a cost guard, GUARANTEEING teardown.

    ``factory() -> EphemeralTarget`` provisions on call. Raises ``CostGuardError``
    *before* provisioning anything when the guard denies. Teardown ALWAYS runs in
    ``finally`` — even if the body raises — so a target is never leaked. A teardown
    failure is logged, never raised over the body's own outcome.
    """
    if not cost_guard.allowed():
        raise CostGuardError(cost_guard.reason())
    target = factory()
    try:
        yield target
    finally:
        try:
            target.teardown()
        except Exception as exc:
            logger.warning("ephemeral target teardown failed: %s", exc)


def make_liveness_check(target: EphemeralTarget):
    """Adapt a provisioned target into the curation gate's ``liveness_check`` (#86)."""

    def liveness_check(_req) -> bool:
        try:
            return bool(target.live())
        except Exception as exc:
            logger.warning("ephemeral target liveness probe failed: %s", exc)
            return False

    return liveness_check
