"""Shared per-agent infrastructure (factory-agents dedup, #195).

Several agent modules (``planner.py``, ``gen_functional.py``, and friends)
independently reimplemented the same small primitives:

- ``now_iso`` — tz-aware ISO-8601 timestamp (seconds resolution).
- ``read_status`` / ``write_status_patch`` — read and merge-patch a spec's
  ``status.json``, emitting a best-effort stage event on every write (#95).
- ``truthy`` — the canonical env-var truthiness check.
- a fire-and-forget background-task registry (the ``set[asyncio.Task]``
  GC-anchor + ``add_done_callback`` discard pattern).
- ``iter_subtasks`` — the ``for phase: for subtask:`` double-loop.

This module is the single home for those, so every agent shares one
implementation instead of copying it. The behaviour is identical to the
previous per-module copies; callers keep their own env-var names and
``stage`` discriminators by passing them in.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterator
    from pathlib import Path

__all__ = [
    "BackgroundTaskRegistry",
    "iter_subtasks",
    "now_iso",
    "read_status",
    "truthy",
    "write_status_patch",
]


def now_iso() -> str:
    """Tz-aware ISO-8601 timestamp at seconds resolution."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def truthy(env_val: str | None) -> bool:
    """Canonical env-var truthiness check ("1"/"true"/"yes"/"on")."""
    if env_val is None:
        return False
    return env_val.strip().lower() in ("1", "true", "yes", "on")


def read_status(spec_dir: Path) -> dict[str, Any]:
    """Read ``status.json`` or return an empty dict if missing/corrupt."""
    status_path = spec_dir / "status.json"
    if not status_path.exists():
        return {}
    try:
        parsed: dict[str, Any] = json.loads(status_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return parsed


def write_status_patch(spec_dir: Path, stage: str, **fields: object) -> None:
    """Merge ``fields`` into ``status.json`` (atomic-ish single-file write).

    ``stage`` is the discriminator forwarded to the best-effort push-based
    progress event (#95); the event is a no-op unless opted in via env var.
    """
    status = read_status(spec_dir)
    status.update(fields)
    status["updated_at"] = now_iso()
    (spec_dir / "status.json").write_text(json.dumps(status, indent=2))
    # Lazy import: stage_events imports this module at top-level, so importing
    # it here at top-level would be circular.
    from agents.stage_events import (  # noqa: PLC0415
        emit_stage_event,
    )

    emit_stage_event(spec_dir, status, stage=stage)


def iter_subtasks(
    plan: Any,
    predicate: Callable[[Any], bool] | None = None,
) -> Iterator[tuple[Any, Any]]:
    """Yield ``(phase, subtask)`` across all phases, in plan order.

    ``plan`` is duck-typed: any object whose ``.phases`` each expose a
    ``.subtasks`` sequence (the agents' ``ImplementationPlan``). If
    ``predicate`` is given, only subtasks for which it returns truthy are
    yielded. This is the shared form of the ``for phase: for subtask:``
    double-loop reimplemented across the agent modules.
    """
    for phase in plan.phases:
        for subtask in phase.subtasks:
            if predicate is None or predicate(subtask):
                yield phase, subtask


_T = TypeVar("_T")


class BackgroundTaskRegistry:
    """GC-anchor registry for fire-and-forget ``asyncio`` tasks.

    ``asyncio.create_task`` only holds a weak reference to the task, so a
    fire-and-forget run can be garbage-collected mid-flight if the caller
    drops its reference. Each agent kept a module-level ``set[asyncio.Task]``
    and an ``add_done_callback`` discard to anchor the task until it finishes;
    this is the shared form of that pattern.

    The live ``set`` is exposed as :attr:`tasks` so existing module-level
    aliases (e.g. ``_BG_PLANNER_TASKS``) can point at the same object.
    """

    def __init__(self) -> None:
        self.tasks: set[asyncio.Task[Any]] = set()

    def schedule(self, coro: Coroutine[Any, Any, _T]) -> asyncio.Task[_T]:
        """Create, anchor, and return a task for ``coro``."""
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task
