"""Emit stage — create GitHub issues (#18) and hand off to AIFactory (#19)."""

from plan.emit.aifactory_handoff import (
    build_requirements,
    handover_labels,
    trigger_api,
    write_requirements,
)
from plan.emit.github_emitter import EmitResult, emit_to_github

__all__ = [
    "EmitResult",
    "build_requirements",
    "emit_to_github",
    "handover_labels",
    "trigger_api",
    "write_requirements",
]
