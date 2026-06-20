"""Best-effort hand-back from the Triager's terminal status (#185 / #85).

When the Triager reaches a terminal status, it calls :func:`maybe_handback`,
which reads the run's failure artifacts, builds a correction request, and —
when there is something to hand back — writes the artifact and (opt-in) sends it
to AIFactory.

Two env gates, mirroring ``PFACTORY_TRIAGER_GIT_WRITE``:

  - ``PFACTORY_HANDBACK_PREPARE`` — **default ON**. Build + write the artifact.
    Set falsy to disable the hand-back entirely.
  - ``PFACTORY_HANDBACK_SEND``    — **default OFF**. Actually POST to AIFactory.

Everything here is best-effort: any failure returns ``None`` and never disturbs
the Triager's own terminal status.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .request import build_correction_request
from .send import SendResult, send_correction

__all__ = ["maybe_handback"]


def _truthy(env_val: str | None) -> bool:
    if env_val is None:
        return False
    return env_val.strip().lower() in ("1", "true", "yes", "on")


def _prepare_enabled() -> bool:
    """Default ON — disabled only by an explicit falsy PFACTORY_HANDBACK_PREPARE."""
    val = os.environ.get("PFACTORY_HANDBACK_PREPARE")
    return val is None or _truthy(val)


def _send_enabled() -> bool:
    """Default OFF — operator opts in with PFACTORY_HANDBACK_SEND=1."""
    return _truthy(os.environ.get("PFACTORY_HANDBACK_SEND"))


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def maybe_handback(spec_dir: Path | str, *, sender_fn=None) -> SendResult | None:
    """Prepare (and optionally send) a correction hand-back for a finished task.

    Returns the :class:`SendResult` when a hand-back was prepared, or ``None``
    when there was nothing to hand back or the hand-back is disabled. Never
    raises.
    """
    try:
        if not _prepare_enabled():
            return None

        spec = Path(spec_dir)
        findings = spec / "findings"
        verdicts = _load_json(findings / "verdicts.json")
        source = _load_json(spec / "context" / "source.json")
        if not verdicts or not source:
            return None
        triage = _load_json(findings / "triage_report.json")

        request = build_correction_request(verdicts, triage, source)
        if request.nothing_to_hand_back:
            return None

        send = _send_enabled()
        return send_correction(
            request,
            spec,
            dry_run=not send,
            confirm=send,
            sender_fn=sender_fn,
        )
    except Exception:
        return None
