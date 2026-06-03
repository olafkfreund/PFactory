"""Send a correction hand-back to AIFactory (#185).

P4 of epic #182. Wraps the pure builder (``request.py``) with the side-effecting
half: it **always** writes the hand-back artifacts to ``findings/`` and, only
when explicitly opted in, POSTs the fix-request to AIFactory's receiver (P3 —
``POST /api/tasks/{task_id}/apply-correction``).

Two independent gates, mirroring the Triager's ``git_writer`` / ``pr_comment``:

  - ``dry_run`` (default ``True``)  — artifacts written, nothing sent.
  - ``confirm`` (default ``False``) — the second gate the interactive skill (P5)
    flips after showing the operator the preview.

A send only happens when ``dry_run is False and confirm``. Transport failures are
swallowed into a graceful ``SendResult`` — a hand-back must never break the
pipeline (best-effort, like the completion webhook).

``sender_fn`` is the injectable AIFactory client; tests pass a fake so the suite
never touches a real AIFactory. The default posts via stdlib ``urllib`` (no new
dependency).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .render import render_fix_request_md
from .request import CorrectionRequest

__all__ = ["SendResult", "send_correction", "default_sender"]

Sender = Callable[[dict], dict]


@dataclass
class SendResult:
    ok: bool
    dry_run: bool
    sent: bool
    task_id: str
    artifact_md: str | None = None
    artifact_json: str | None = None
    response: dict | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_sender(payload: dict) -> dict:
    """POST the fix-request to AIFactory's apply-correction route (P3).

    ``payload`` = ``{api_url, task_id, fix_request_md, source, confirm}``.
    Raises on transport error or non-2xx — ``send_correction`` catches it.
    """
    import urllib.error
    import urllib.request

    api_url = (payload.get("api_url") or "").rstrip("/")
    task_id = payload["task_id"]
    if not api_url:
        raise ValueError("no AIFactory api_url in source.json aifactory envelope")

    url = f"{api_url}/api/tasks/{task_id}/apply-correction"
    body = json.dumps(
        {
            "fix_request_md": payload["fix_request_md"],
            "source": payload.get("source"),
            "confirm": bool(payload.get("confirm")),
        }
    ).encode()
    req = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (trusted host)
        raw = resp.read().decode() or "{}"
    return json.loads(raw)


def send_correction(
    request: CorrectionRequest,
    spec_dir: Path | str,
    *,
    dry_run: bool = True,
    confirm: bool = False,
    sender_fn: Sender | None = None,
    now: str | None = None,
) -> SendResult:
    """Write the hand-back artifacts and optionally POST them to AIFactory.

    Artifacts (``findings/handback_request.{md,json}``) are **always** written
    when there is something to hand back. A POST happens only when
    ``dry_run is False and confirm`` (and the request is non-empty).
    """
    task_id = request.aifactory_task_id
    spec = Path(spec_dir)

    if request.nothing_to_hand_back:
        # Nothing to do — no artifacts, no send. Caller usually guards on this.
        return SendResult(ok=True, dry_run=dry_run, sent=False, task_id=task_id)

    findings = spec / "findings"
    findings.mkdir(parents=True, exist_ok=True)

    md = render_fix_request_md(request)
    md_path = findings / "handback_request.md"
    md_path.write_text(md)

    json_doc = {
        **request.to_dict(),
        "generated_at": now or _now_iso(),
        "dry_run": dry_run,
        "fix_request_md_path": "findings/handback_request.md",
    }
    json_path = findings / "handback_request.json"
    json_path.write_text(json.dumps(json_doc, indent=2))

    result = SendResult(
        ok=True,
        dry_run=dry_run,
        sent=False,
        task_id=task_id,
        artifact_md=str(md_path),
        artifact_json=str(json_path),
    )

    if not (not dry_run and confirm):
        return result  # prepared only — the dry-run / unconfirmed path

    payload = {
        "api_url": (request.aifactory or {}).get("api_url"),
        "task_id": task_id,
        "fix_request_md": md,
        "source": request.source_kind,
        "confirm": True,
    }
    try:
        result.response = (sender_fn or default_sender)(payload)
        result.sent = True
    except Exception as exc:  # best-effort — never raise into the pipeline
        result.ok = False
        result.error = f"{type(exc).__name__}: {exc}"
    return result
