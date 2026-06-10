"""Documentation emit (P1) — render a plan into durable docs.

A third emission alongside GitHub epics + the Task Contract: turn an approved
plan into Markdown and fan it out to documentation targets. The pure renderer
feeds a target set resolved by config — repo/GitHub always, Backstage and
Confluence when configured (default off, opt-in). Per-plan/Settings selection
(design §6d/§6e) lands in P4.

Design: ``docs/plans/2026-06-10-plan-docs-emit-design.md``.
"""

from .bundle import DocBundle, TargetResult
from .emit_docs import connections_to_targets, emit_docs, is_enabled
from .render import render_plan_docs
from .resolve import PlanDocsResolver

__all__ = [
    "DocBundle",
    "PlanDocsResolver",
    "TargetResult",
    "connections_to_targets",
    "emit_docs",
    "is_enabled",
    "render_plan_docs",
]
