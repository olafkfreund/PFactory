"""Deterministic policy rules engine for the review stage (issue #16).

A small, dependency-free policy-as-code engine: built-in :class:`Rule` objects
each inspect a ``(plan, epic)`` pair and emit :class:`~plan.review.models.Finding`
notes. :func:`run_rules` runs every registered rule; :func:`run_external_policy`
is a lazy seam for plugging in external scanners (Checkov / OPA / cloud-MCP).
"""

from __future__ import annotations

from plan.review.rules.engine import (
    Rule,
    default_rules,
    register_rule,
    run_external_policy,
    run_rules,
)

__all__ = [
    "Rule",
    "default_rules",
    "register_rule",
    "run_external_policy",
    "run_rules",
]
