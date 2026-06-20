"""Per-stage pipeline events (#95) — push-based progress, opt-in.

The portal today learns about pipeline progress by polling ``status.json``
on a ~3-second loop (apps/web-server ``agent_service._sync_worktree_files``).
This module is the *emission* half of replacing that poll with a push:
every stage's status write can emit a best-effort event so a watcher reacts
immediately instead of polling.

It generalises the #85 terminal-completion callback — which only fires on
``triaged`` / ``triaged_empty`` / ``triager_failed`` — to *every* stage
transition, and lives in one shared module so all four agents emit
identically rather than each copying the channel logic.

Both channels are OFF by default and strictly best-effort — a missing or
failing target must never affect the run:

- ``PFACTORY_STAGE_EVENT_SENTINEL=1`` appends one JSON line per event to
  ``<spec_dir>/findings/stage_events.jsonl`` — a same-host watcher can tail
  it instead of polling ``status.json``.
- ``PFACTORY_STAGE_EVENT_WEBHOOK=<url>`` POSTs the event payload (timeout
  ``PFACTORY_STAGE_EVENT_WEBHOOK_TIMEOUT``, default 5s).

The payload mirrors #85's completion shape plus a ``stage`` discriminator
("planner" / "gen_functional" / "evaluator" / "triager").
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agents.agent_infra import truthy as _truthy

__all__ = ["emit_stage_event", "stage_event_payload"]


def _sentinel_enabled() -> bool:
    return _truthy(os.environ.get("PFACTORY_STAGE_EVENT_SENTINEL"))


def _webhook_url() -> str | None:
    url = (os.environ.get("PFACTORY_STAGE_EVENT_WEBHOOK") or "").strip()
    return url or None


def stage_event_payload(spec_dir: Path, status: dict[str, Any], stage: str) -> dict[str, Any]:
    """Build the event payload — #85's completion shape plus ``stage``."""
    return {
        "task_id": status.get("task_id") or spec_dir.name,
        "project_id": status.get("project_id"),
        "stage": stage,
        "status": status.get("status"),
        "phase": status.get("phase"),
        "updated_at": status.get("updated_at"),
    }


def emit_stage_event(spec_dir: Path, status: dict[str, Any], *, stage: str) -> None:
    """Best-effort per-stage event. No-op unless a channel is opted in.

    Args:
        spec_dir: the task workspace (where ``findings/`` lives).
        status: the just-written ``status.json`` contents.
        stage: which agent emitted — "planner" / "gen_functional" /
            "evaluator" / "triager".

    Never raises: every failure is swallowed so a watcher target can never
    break the pipeline.
    """
    sentinel = _sentinel_enabled()
    url = _webhook_url()
    if not sentinel and not url:
        return

    payload = stage_event_payload(spec_dir, status, stage)

    if sentinel:
        try:
            findings_dir = spec_dir / "findings"
            findings_dir.mkdir(parents=True, exist_ok=True)
            with (findings_dir / "stage_events.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload) + "\n")
        except OSError:
            pass

    if not url:
        return
    try:
        import urllib.request

        timeout = float(os.environ.get("PFACTORY_STAGE_EVENT_WEBHOOK_TIMEOUT", "5"))
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=timeout).close()  # noqa: S310
    except Exception:
        # Best-effort; never surface failures into the pipeline.
        pass
