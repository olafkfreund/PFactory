"""Documentation emit (P1) — render a plan into durable docs.

A third emission alongside GitHub epics + the Task Contract: turn an approved
plan into Markdown and fan it out to documentation targets. P1 ships the pure
renderer + the always-available repo/directory target + the gated orchestrator.
Backstage/Confluence targets + Settings connections land in later phases.

Design: ``docs/plans/2026-06-10-plan-docs-emit-design.md``.
"""

from .bundle import DocBundle, TargetResult
from .emit_docs import emit_docs, is_enabled
from .render import render_plan_docs

__all__ = [
    "DocBundle",
    "TargetResult",
    "emit_docs",
    "is_enabled",
    "render_plan_docs",
]
