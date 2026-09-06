"""GC regression for the backgrounded cloud assessment (AIFactory#1484).

``POST /api/cloud/assessments/run`` runs the read-only access gate inline and
then backgrounds the assessment, answering ``{"gate": "ok", ...,
"status": "running"}``. Before AIFactory#1484 it backgrounded it with a bare
``asyncio.create_task(...)``, and the event loop holds only a WEAK reference to
a task: a collection cycle between two awaits could reclaim the assessment
mid-run — no exception, no log, no report, and a caller already told the run had
started.

The test forces ``gc.collect()`` while the assessment is suspended and asserts
it still completed. See test_background_task_gc.py for why the gate future is
published through a weak reference rather than awaited on an Event (an Event's
waiter future would anchor the task by itself, and the test would pass with or
without the fix).

MUTATION-VERIFIED: putting ``asyncio.create_task`` back in
``routes/cloud.py::run_cloud_check`` makes this test fail.
"""

from __future__ import annotations

import asyncio
import gc
import sys
import weakref
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.routes import cloud

pytestmark = pytest.mark.asyncio


async def test_the_backgrounded_assessment_survives_a_gc_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    box: weakref.WeakValueDictionary[str, asyncio.Future[None]] = weakref.WeakValueDictionary()
    ran: list[str] = []

    gated: list[str] = []

    def fake_preflight(provider: str, **_kwargs: object) -> dict[str, object]:
        gated.append(provider)
        return {"ok": True, "account": "acct", "identity": "id", "inventory": {}}

    async def fake_assessment(req: cloud.CloudRunRequest) -> None:
        gate = asyncio.get_running_loop().create_future()
        box["gate"] = gate  # only a WEAK handle escapes this frame
        await gate
        ran.append(req.provider)

    # String target: `cloud.portal_run` is re-exported through a sys.path insert,
    # which mypy --strict will not follow (attr-defined).
    monkeypatch.setattr("server.routes.cloud.portal_run.preflight", fake_preflight)
    monkeypatch.setattr(cloud, "_run_assessment_bg", fake_assessment)

    result = await cloud.run_cloud_check(cloud.CloudRunRequest(provider="aws"))
    assert result["gate"] == "ok"
    assert result["status"] == "running"

    await asyncio.sleep(0)  # let the assessment start and suspend
    gc.collect()

    gate = box.get("gate")
    assert gate is not None, (
        "the cloud assessment was garbage-collected after the endpoint already "
        "answered status=running"
    )
    gate.set_result(None)
    await asyncio.sleep(0)
    assert gated == ["aws"]
    assert ran == ["aws"]
