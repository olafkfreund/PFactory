"""Shared, lane-agnostic helpers for the Evaluator lane pipeline.

Extracted verbatim from ``agents/evaluator.py`` (issue #193 god-file split).
These are the cross-lane primitives the per-lane modules (unit/api/browser/jest)
and the thin orchestrator depend on: status-file I/O, target resolution from the
snapshotted ``.pfactory.yml``, kubernetes port-forward runtime selection, and
test-target credential specs.

Behaviour is identical to the original module — this is a pure move.
"""

from __future__ import annotations

import json
import logging as _logging
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol

from agents.stage_events import emit_stage_event
from tools.runners.kubernetes_runtime import KubernetesRuntime
from tools.runners.sandbox_credentials import TargetCredentialSpec

_eval_log = _logging.getLogger("agents.evaluator")


# ─── Runner image tags ──────────────────────────────────────────────────

_PYTEST_IMAGE = "pfactory-runner-pytest:latest"
_PLAYWRIGHT_IMAGE = "pfactory-runner-playwright:latest"
_JEST_IMAGE = "pfactory-runner-jest:latest"


# ─── Runner-fn seam duck type ───────────────────────────────────────────


class _RunResultLike(Protocol):
    """Same duck-type as stability_runner/mutate_probe expect."""

    @property
    def returncode(self) -> int: ...
    @property
    def stdout(self) -> str: ...
    @property
    def stderr(self) -> str: ...


# ─── Workspace / status helpers ─────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _read_status(spec_dir: Path) -> dict:
    status_path = spec_dir / "status.json"
    if not status_path.exists():
        return {}
    try:
        return json.loads(status_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_status_patch(spec_dir: Path, **fields: object) -> None:
    status = _read_status(spec_dir)
    status.update(fields)
    status["updated_at"] = _now_iso()
    (spec_dir / "status.json").write_text(json.dumps(status, indent=2))
    # Best-effort push-based progress event (#95); no-op unless opted in.
    emit_stage_event(spec_dir, status, stage="evaluator")


# ─── Target resolution from snapshotted .pfactory.yml ───────────────────


def _browser_target_url(spec_dir: Path, subtask: dict) -> str | None:
    """Resolve the base_url for the subtask's target from the snapshotted
    .pfactory.yml (context/pfactory_yml.json). Falls back to the default
    target when the subtask has no target_name.

    The trailing slash is stripped: the parser normalises base_url to end in
    ``/``, but api tests build URLs as ``f"{base_url}/api/..."`` — a trailing
    slash would produce ``//api/...`` (a different path → spurious 404s). A
    bare origin is also valid for Playwright ``page.goto``.
    """
    ctx = spec_dir / "context" / "pfactory_yml.json"
    if not ctx.exists():
        return None
    try:
        cfg = json.loads(ctx.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    targets = cfg.get("targets") or []
    want = subtask.get("target_name") or cfg.get("default_target")

    def _norm(u: str) -> str:
        # Strip a single trailing slash from the origin/path, but never reduce
        # to empty (keep at least the scheme+host).
        return u[:-1] if (u.endswith("/") and not u.endswith("://")) else u

    for t in targets:
        if t.get("name") == want and t.get("base_url"):
            return _norm(t["base_url"])
    # last resort: first http target with a base_url
    for t in targets:
        if t.get("base_url"):
            return _norm(t["base_url"])
    return None


def _resolve_target(spec_dir: Path, subtask: dict) -> dict | None:
    """Resolve the subtask's *target object* from the snapshotted .pfactory.yml.

    Like :func:`_browser_target_url` but returns the whole target dict (so the
    caller can branch on ``type``), preferring the subtask's ``target_name``,
    then the config ``default_target``, then the first target.
    """
    ctx = spec_dir / "context" / "pfactory_yml.json"
    if not ctx.exists():
        return None
    try:
        cfg = json.loads(ctx.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    targets = cfg.get("targets") or []
    want = subtask.get("target_name") or cfg.get("default_target")
    for t in targets:
        if t.get("name") == want:
            return t
    return targets[0] if targets else None


def _kube_runtime_for(target: dict | None, *, runtime_cls=None):
    """A ``KubernetesRuntime`` for a kubernetes port-forward target, else None (#108).

    When the resolved target is ``type: kubernetes`` with ``port_forward: true``,
    the api/browser lane has no static ``base_url`` — the URL only exists while a
    ``kubectl port-forward`` is live. The caller uses the returned runtime as a
    context manager so the forward is up during the test run and torn down after
    (on success *and* failure). Auth rides the materialised read-only kubeconfig
    (``KUBECONFIG``). Returns None for non-k8s targets (the static-URL path).
    """
    if not target or target.get("type") != "kubernetes" or not target.get("port_forward"):
        return None
    cls = runtime_cls or KubernetesRuntime
    t = SimpleNamespace(
        name=target.get("name"),
        context=target.get("context"),
        namespace=target.get("namespace"),
        service=target.get("service"),
        port=target.get("port"),
        # KubernetesRuntime.start() reads target.port_forward — without it the
        # real runtime AttributeErrors (the mocked dispatch test never hits
        # start(), so this only surfaces live). #108.
        port_forward=target.get("port_forward"),
    )
    return cls(t, kubeconfig=os.environ.get("KUBECONFIG"))


def _test_credential_specs(spec_dir: Path, subtask: dict | None) -> list:
    """TargetCredentialSpec list for a subtask's ``ref``-auth target (#107).

    Reads the snapshotted .pfactory.yml: when the subtask's target uses
    ``auth: {type: ref}``, turn the referenced ``test_credentials`` entry into a
    resolver spec (``resolve_test_target_credentials`` injects it as login env).
    Empty list when there is no ref-auth / no matching credential.
    """
    if subtask is None:
        return []
    target = _resolve_target(spec_dir, subtask)
    auth = (target or {}).get("auth") or {}
    if auth.get("type") != "ref" or not auth.get("ref"):
        return []
    ctx = spec_dir / "context" / "pfactory_yml.json"
    try:
        cfg = json.loads(ctx.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    entry = (cfg.get("test_credentials") or {}).get(auth["ref"])
    if not entry:
        return []
    return [
        TargetCredentialSpec(
            name=auth["ref"],
            ref=entry["ref"],
            as_secret=entry["as_secret"],
            as_username=entry.get("as_username"),
            username_ref=entry.get("username_ref"),
        )
    ]
