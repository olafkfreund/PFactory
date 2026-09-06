"""GC regression for the background-task anchor (AIFactory#1484).

A test that asserts "a task was created" already passed before the fix and
proves nothing. The regression these guard is COLLECTION UNDER GC: the event
loop holds only a weak reference to a task, so a fire-and-forget
``asyncio.create_task(coro())`` can be reclaimed between two awaits — silently,
with no exception and no log, after the endpoint has already reported success.

So each test below forces ``gc.collect()`` while the task is suspended, and
asserts the work still completed.

Why the WeakValueDictionary: the task must be suspended on something the TEST
does not hold strongly. A plain ``asyncio.Event`` or ``sleep`` would not do —
the event's waiter future (or the timer handle) keeps the task alive on its
own, so the test would pass with or without the fix. Awaiting a future created
inside the coroutine's own frame, published only through a weak reference,
leaves task ↔ future ↔ frame as an isolated cycle: reachable while something
anchors the task, collectible the moment nothing does.

MUTATION-VERIFIED: replacing ``spawn(...)`` with a bare
``asyncio.create_task(...)`` makes ``test_spawn_survives_a_gc_cycle`` fail on
the "garbage-collected mid-flight" assertion.
"""

from __future__ import annotations

import asyncio
import gc
import sys
import weakref
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.background.tasks import pending_count, spawn

# This directory is collected with pytest-asyncio's default (strict) mode:
# CI runs `pytest tests/ apps/web-server/tests/` from the repo root, where
# tests/pytest.ini (asyncio_mode = auto) is not the config file.
pytestmark = pytest.mark.asyncio


def _weak_gate() -> weakref.WeakValueDictionary[str, asyncio.Future[None]]:
    return weakref.WeakValueDictionary()


async def test_spawn_survives_a_gc_cycle() -> None:
    """The anchored task is still there after a collection, and finishes."""
    box = _weak_gate()
    ran: list[str] = []

    async def work() -> None:
        gate = asyncio.get_running_loop().create_future()
        box["gate"] = gate  # only a WEAK handle escapes this frame
        await gate
        ran.append("done")

    spawn(work())  # deliberately not bound to a local
    await asyncio.sleep(0)  # let it start and suspend on the gate
    gc.collect()

    gate = box.get("gate")
    assert gate is not None, "the background task was garbage-collected mid-flight"
    gate.set_result(None)
    await asyncio.sleep(0)
    assert ran == ["done"]


async def test_bare_create_task_is_collected_under_the_same_pressure() -> None:
    """The control: the shape ``spawn`` replaces really does lose the work.

    Without this, a green :func:`test_spawn_survives_a_gc_cycle` could equally
    mean the GC pressure never bites — the pass-shaped empty measurement.
    """
    box = _weak_gate()
    ran: list[str] = []

    async def work() -> None:
        gate = asyncio.get_running_loop().create_future()
        box["gate"] = gate
        await gate
        ran.append("done")

    asyncio.create_task(work())  # noqa: RUF006 — the unsafe shape, on purpose
    await asyncio.sleep(0)
    gc.collect()

    assert box.get("gate") is None, "expected the unanchored task to be collected"
    assert ran == []


async def test_the_anchor_is_released_when_the_task_finishes() -> None:
    """The strong reference must not turn into an unbounded leak."""
    before = pending_count()

    async def work() -> None:
        await asyncio.sleep(0)

    task = spawn(work())
    assert pending_count() == before + 1
    await task
    await asyncio.sleep(0)  # let the done-callback run
    assert pending_count() == before


async def test_a_failing_background_task_is_logged_not_swallowed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Nobody awaits these tasks, so the failure has to be logged where it happens."""

    async def work() -> None:
        raise RuntimeError("boom")

    with caplog.at_level("ERROR", logger="server.background.tasks"):
        task = spawn(work(), name="exploding-task")
        with pytest.raises(RuntimeError):
            await task
        await asyncio.sleep(0)

    assert any("exploding-task" in r.getMessage() for r in caplog.records)
