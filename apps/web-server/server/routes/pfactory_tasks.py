"""PFactory task endpoints — Task 9 (#10) commit 1.

Read-only endpoints over the PFactory workspace filesystem
(``~/.pfactory/workspaces/{project_id}/specs/{spec_id}/``). Powers the
portal retheme (Task 10's frontend lane status grid).

Endpoints in this commit:
  - GET /api/pfactory/tasks                 — list workspaces
  - GET /api/pfactory/tasks/{spec_id}        — status detail

Future commits add:
  - report.md / report.json / verdicts.json artefact endpoints
  - WebSocket log stream

Workspace root resolution:
  - Env ``PFACTORY_WORKSPACE_ROOT`` if set (typically the value the
    backend agents already use), else ``~/.pfactory``.
  - Workspaces live under ``<root>/workspaces/<project_id>/specs/<spec_id>/``.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from server.services.git_utils import safe_spec_component

# Evidence artifact content-type map (mirrors agents.evidence.layout)
_EVIDENCE_CONTENT_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webm": "video/webm",
    ".mp4": "video/mp4",
    ".zip": "application/zip",
    ".har": "application/json",
    ".jsonl": "application/json",
}

import json as _json_mod

from fastapi import (
    APIRouter,
    HTTPException,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status as http_status,
)
from pydantic import BaseModel

router = APIRouter()


# ─── Path resolution ────────────────────────────────────────────────────


def _resolve_workspace_root() -> Path:
    """Resolve the PFactory workspace root.

    Defaults to ``~/.pfactory``. Override via env ``PFACTORY_WORKSPACE_ROOT``
    (tests use this to point at a tmp directory)."""
    env_val = os.environ.get("PFACTORY_WORKSPACE_ROOT")
    if env_val:
        return Path(env_val).expanduser()
    return Path.home() / ".pfactory"


_SPEC_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_TEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
# Artifact names: allow subdirectory prefix (e.g. "screenshots/0001.png")
# but forbid path traversal sequences (.., absolute paths, null bytes).
_ARTIFACT_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


def _validate_spec_id(spec_id: str) -> None:
    """Reject path-traversal attempts in the spec_id path parameter.

    Delegates to the shared component check so this module and the rest of the
    route layer cannot drift. The regex here previously admitted "." and ".."
    -- both match ``^[A-Za-z0-9._-]+$`` -- and a lone ".." resolves to the
    parent of whatever root it is joined to.
    """
    try:
        safe_spec_component(spec_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from None


def _find_spec_dir(root: Path, spec_id: str) -> Path | None:
    """Locate the spec_dir for ``spec_id`` across all projects under root.

    Returns the first match (spec_ids are unique within a project but
    technically could collide across projects; production caller picks
    the right project via list endpoint).
    """
    workspaces = root / "workspaces"
    if not workspaces.exists():
        return None
    for project_dir in workspaces.iterdir():
        if not project_dir.is_dir():
            continue
        candidate = project_dir / "specs" / spec_id
        if (candidate / "status.json").exists():
            return candidate
    return None


# ─── Helpers ────────────────────────────────────────────────────────────


def _read_json(path: Path) -> dict | None:
    """Best-effort JSON read. None on missing / parse failure."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _summary_row(status_path: Path) -> dict[str, Any]:
    """Pluck the public fields from a status.json for the list response.

    Adds derived ``project_id`` + ``spec_id`` (computed from the path)
    + ``updated_at`` (mtime fallback if status.json lacks the field)."""
    status_doc = _read_json(status_path) or {}
    spec_dir = status_path.parent
    project_id = spec_dir.parent.parent.name  # workspaces/<pid>/specs/<sid>/
    spec_id = spec_dir.name

    updated_at = status_doc.get("updated_at")
    if not updated_at:
        try:
            mtime = status_path.stat().st_mtime
            updated_at = datetime.fromtimestamp(mtime).isoformat(timespec="seconds")
        except OSError:
            updated_at = ""

    return {
        "task_id": status_doc.get("task_id") or spec_id,
        "project_id": project_id,
        "spec_id": spec_id,
        "status": status_doc.get("status"),
        "phase": status_doc.get("phase"),
        "updated_at": updated_at,
    }


def _artefact_meta(spec_dir: Path) -> dict[str, dict]:
    """Build the meta block listing artefact paths + existence flags.

    The portal frontend reads this to decide which 'fetch report' buttons
    to enable.
    """
    artefacts = {
        "test_plan": spec_dir / "test_plan.json",
        "verdicts": spec_dir / "findings" / "verdicts.json",
        "triage_report_json": spec_dir / "findings" / "triage_report.json",
        "triage_report_md": spec_dir / "findings" / "triage_report.md",
        "pr_comment_body": spec_dir / "findings" / "pr_comment_body.md",
    }
    return {
        name: {
            "path": str(path.relative_to(spec_dir)),
            "exists": path.exists(),
        }
        for name, path in artefacts.items()
    }


# ─── Endpoints ──────────────────────────────────────────────────────────


@router.get("")
def list_tasks() -> dict:
    """List all PFactory tasks across all projects.

    Response shape:
        {
          "tasks": [
            {"task_id", "project_id", "spec_id", "status", "phase", "updated_at"},
            ...
          ],
          "count": N
        }

    Sorted by ``updated_at`` descending (most recent first). Empty list
    if the workspace root doesn't exist yet.
    """
    root = _resolve_workspace_root()
    workspaces = root / "workspaces"
    rows: list[dict] = []
    if workspaces.exists():
        for status_path in workspaces.glob("*/specs/*/status.json"):
            rows.append(_summary_row(status_path))
    rows.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    return {"tasks": rows, "count": len(rows)}


@router.get("/{spec_id}")
def get_task(spec_id: str) -> dict:
    """Fetch full status.json for a task + artefact-existence meta.

    Returns 400 on malformed spec_id, 404 if no matching spec dir.
    """
    _validate_spec_id(spec_id)
    root = _resolve_workspace_root()
    spec_dir = _find_spec_dir(root, spec_id)
    if spec_dir is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"task not found: {spec_id}",
        )

    status_doc = _read_json(spec_dir / "status.json") or {}
    project_id = spec_dir.parent.parent.name
    return {
        "task_id": status_doc.get("task_id") or spec_id,
        "project_id": project_id,
        "spec_id": spec_id,
        "status_json": status_doc,
        "artefacts": _artefact_meta(spec_dir),
    }


# ─── Artefact endpoints ────────────────────────────────────────────────


def _serve_artefact_file(
    spec_id: str, relpath: str, media_type: str,
) -> Response:
    """Locate ``spec_id`` and serve the file at ``spec_dir/relpath``.

    Returns 400 on malformed spec_id, 404 if spec missing or artefact
    missing. Returns raw bytes verbatim — the frontend parses /
    renders as appropriate.
    """
    _validate_spec_id(spec_id)
    root = _resolve_workspace_root()
    spec_dir = _find_spec_dir(root, spec_id)
    if spec_dir is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"task not found: {spec_id}",
        )
    target = spec_dir / relpath
    if not target.exists():
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"artefact not found: {relpath}",
        )
    try:
        content = target.read_bytes()
    except OSError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"could not read artefact: {exc}",
        ) from exc
    return Response(content=content, media_type=media_type)


@router.get("/{spec_id}/verdicts.json")
def get_verdicts(spec_id: str) -> Response:
    """Stream the Evaluator's verdicts.json verbatim."""
    return _serve_artefact_file(
        spec_id, "findings/verdicts.json", "application/json",
    )


@router.get("/{spec_id}/triage-report.json")
def get_triage_report_json(spec_id: str) -> Response:
    """Stream the Triager's triage_report.json verbatim."""
    return _serve_artefact_file(
        spec_id, "findings/triage_report.json", "application/json",
    )


@router.get("/{spec_id}/triage-report.md")
def get_triage_report_md(spec_id: str) -> Response:
    """Stream the Triager's triage_report.md verbatim."""
    return _serve_artefact_file(
        spec_id, "findings/triage_report.md", "text/markdown",
    )


@router.get("/{spec_id}/test-plan.json")
def get_test_plan(spec_id: str) -> Response:
    """Stream the Planner's test_plan.json verbatim."""
    return _serve_artefact_file(
        spec_id, "test_plan.json", "application/json",
    )


@router.get("/{spec_id}/pr-comment-body.md")
def get_pr_comment_body(spec_id: str) -> Response:
    """Stream findings/pr_comment_body.md — present when the Triager
    skipped a real gh pr comment (no PR number in source.json)."""
    return _serve_artefact_file(
        spec_id, "findings/pr_comment_body.md", "text/markdown",
    )


# ─── Logs endpoint — testable core + WS transport ──────────────────────


# Cap how many lines per log file we send per payload. Keeps the WS
# message size predictable; the frontend can request more via a query
# param in future commits.
DEFAULT_LOG_TAIL_LINES = 200

# Cap how many bytes per file we ever read for tailing — prevents a
# 100MB log file from blowing the memory budget. We seek to the end
# and read at most this many bytes.
_TAIL_READ_CAP_BYTES = 1_000_000


def _resolve_log_files(spec_dir: Path) -> dict[str, Path]:
    """Discover log files under ``spec_dir/logs/``.

    Returns a mapping ``{stem: path}`` where stem is the filename
    without ``.log`` extension (e.g., ``planner``, ``gen_functional``).
    Empty dict if logs/ doesn't exist.
    """
    logs_dir = spec_dir / "logs"
    if not logs_dir.exists() or not logs_dir.is_dir():
        return {}
    out: dict[str, Path] = {}
    for path in sorted(logs_dir.glob("*.log")):
        if path.is_file():
            out[path.stem] = path
    return out


def _tail_lines(path: Path, n: int) -> list[str]:
    """Return the last ``n`` lines of ``path``.

    Caps the read at ``_TAIL_READ_CAP_BYTES`` from EOF — guards against
    huge log files. Lines are stripped of the trailing newline. Returns
    an empty list if the file can't be read.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > _TAIL_READ_CAP_BYTES:
                fh.seek(size - _TAIL_READ_CAP_BYTES)
                # Discard the (likely partial) leading line
                fh.readline()
            tail_bytes = fh.read()
    except OSError:
        return []
    text = tail_bytes.decode("utf-8", errors="replace")
    # Use splitlines so we don't emit a trailing empty string from a
    # final newline.
    lines = text.splitlines()
    return lines[-n:] if n > 0 else []


def tail_log_payload(spec_id: str, lines_per_file: int = DEFAULT_LOG_TAIL_LINES) -> dict:
    """Build the JSON payload the WS endpoint sends.

    Returns:
        {
          "spec_id": "<id>",
          "captured_at": "<iso>",
          "files": {
            "planner": ["line1", "line2", ...],
            "gen_functional": [...],
            ...
          }
        }

    Raises:
        HTTPException 400 — malformed spec_id
        HTTPException 404 — spec dir doesn't exist
    """
    _validate_spec_id(spec_id)
    root = _resolve_workspace_root()
    spec_dir = _find_spec_dir(root, spec_id)
    if spec_dir is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"task not found: {spec_id}",
        )

    files = _resolve_log_files(spec_dir)
    payload_files = {
        name: _tail_lines(path, lines_per_file)
        for name, path in files.items()
    }
    return {
        "spec_id": spec_id,
        "captured_at": datetime.utcnow().isoformat(timespec="seconds") + "+00:00",
        "files": payload_files,
    }


@router.websocket("/{spec_id}/logs/stream")
async def websocket_logs(websocket: WebSocket, spec_id: str) -> None:
    """WebSocket log tail. Sends one ``tail_log_payload`` snapshot on
    connect; the frontend polls via reconnect for refreshes (live tail
    is a Task 11 follow-up).

    Disconnects cleanly on close. Path-traversal / missing-spec are
    reported by closing with code 4400 / 4404 so the frontend can
    distinguish them.
    """
    await websocket.accept()
    try:
        try:
            payload = tail_log_payload(spec_id)
        except HTTPException as exc:
            # 400 → close with 4400, 404 → 4404 (custom WS close codes)
            code = 4400 if exc.status_code == 400 else 4404
            await websocket.close(code=code, reason=exc.detail)
            return
        await websocket.send_text(_json_mod.dumps(payload))
        # Stay open until the client disconnects. The MVP doesn't
        # push updates — Task 11's e2e smoke will revisit live tail.
        try:
            while True:
                # recv_text raises WebSocketDisconnect on close.
                await websocket.receive_text()
        except WebSocketDisconnect:
            return
    except Exception:  # noqa: BLE001 — protect the ws loop from leaks
        try:
            await websocket.close(code=1011)  # internal error
        except Exception:  # noqa: BLE001
            pass


# ─── Catalog endpoint (Task 14 / #30) ──────────────────────────────────────


@router.get("/{spec_id}/catalog")
def get_catalog(spec_id: str) -> Response:
    """Serve the spec's ``context/tests_catalog.json`` snapshot.

    The snapshot is written by the AIFactory snapshotter (Task 4) when the
    handover happens.  It captures the state of the AIFactory repo's
    ``tests-catalog.json`` at the moment the spec was handed to PFactory.

    Returns the catalog JSON verbatim.

    Returns 400 on malformed spec_id, 404 if the spec is not found or the
    catalog has not been snapshotted for this spec yet.

    URL: ``GET /api/pfactory/tasks/{spec_id}/catalog``
    """
    _validate_spec_id(spec_id)
    root = _resolve_workspace_root()
    spec_dir = _find_spec_dir(root, spec_id)
    if spec_dir is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"task not found: {spec_id}",
        )

    catalog_path = spec_dir / "context" / "tests_catalog.json"
    if not catalog_path.exists():
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="catalog not snapshotted for this spec",
        )

    try:
        content = catalog_path.read_bytes()
    except OSError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"could not read catalog: {exc}",
        ) from exc

    return Response(content=content, media_type="application/json")


# ─── Evidence artifact endpoint (Task 16 / #32) ────────────────────────────


def _validate_test_id(test_id: str) -> None:
    """Reject path-traversal attempts in the test_id path parameter."""
    if not test_id or not _TEST_ID_RE.match(test_id):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"invalid test_id: {test_id!r}",
        )


def _validate_artifact(artifact: str) -> None:
    """Validate the artifact path segment.

    Rejects:
    * Empty strings
    * Absolute paths (starting with /)
    * Path traversal (contains ``..``)
    * Null bytes or characters outside ``[A-Za-z0-9._/-]``
    """
    if not artifact:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="artifact must not be empty",
        )
    if ".." in artifact.split("/"):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"path traversal rejected: {artifact!r}",
        )
    if artifact.startswith("/"):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"artifact must be a relative path, got: {artifact!r}",
        )
    if not _ARTIFACT_RE.match(artifact):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"invalid artifact path: {artifact!r}",
        )


def _evidence_content_type(artifact: str) -> str:
    """Return the MIME content-type for *artifact* based on its extension."""
    suffix = Path(artifact).suffix.lower()
    return _EVIDENCE_CONTENT_TYPES.get(suffix, "application/octet-stream")


@router.get("/{spec_id}/evidence/{test_id}/{artifact:path}")
def get_evidence_artifact(spec_id: str, test_id: str, artifact: str) -> Response:
    """Serve a raw evidence artifact byte-for-byte.

    URL: ``GET /api/pfactory/tasks/{spec_id}/evidence/{test_id}/{artifact}``

    The ``artifact`` segment is treated as a relative sub-path under
    ``<spec_dir>/findings/evidence/<test_id>/`` (e.g.
    ``screenshots/0001.png``, ``video.webm``, ``trace.zip``,
    ``network.har``).

    Content-type is inferred from the file extension:

    =========  =====================
    Extension  Content-Type
    =========  =====================
    .png       image/png
    .jpg/.jpeg image/jpeg
    .webm      video/webm
    .mp4       video/mp4
    .zip       application/zip
    .har       application/json
    .jsonl     application/json
    other      application/octet-stream
    =========  =====================

    Returns:

    * **200** — raw bytes with the appropriate Content-Type
    * **400** — ``spec_id``, ``test_id``, or ``artifact`` fails validation
      (path traversal / illegal characters)
    * **404** — spec not found, test evidence directory not found, or
      the specific artifact file is absent
    * **405** — method not allowed (only GET is permitted)
    """
    _validate_spec_id(spec_id)
    _validate_test_id(test_id)
    _validate_artifact(artifact)

    root = _resolve_workspace_root()
    spec_dir = _find_spec_dir(root, spec_id)
    if spec_dir is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"task not found: {spec_id}",
        )

    evidence_base = spec_dir / "findings" / "evidence" / test_id
    if not evidence_base.exists():
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"evidence not found for test_id: {test_id}",
        )

    artifact_path = evidence_base / artifact

    # Resolve symlinks and verify the resolved path stays under evidence_base
    try:
        resolved = artifact_path.resolve()
        evidence_base_resolved = evidence_base.resolve()
        if not str(resolved).startswith(str(evidence_base_resolved) + "/") and resolved != evidence_base_resolved:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=f"path traversal rejected: {artifact!r}",
            )
    except (OSError, ValueError):
        pass  # File doesn't exist yet; the 404 below will handle it

    if not artifact_path.exists():
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"artifact not found: {artifact}",
        )

    if not artifact_path.is_file():
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"artifact is not a file: {artifact}",
        )

    try:
        content = artifact_path.read_bytes()
    except OSError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"could not read artifact: {exc}",
        ) from exc

    return Response(content=content, media_type=_evidence_content_type(artifact))


# ── Merge / dismiss (the human review gate) ─────────────────────────
#
# The Triager already commits accepted tests to the feature branch
# (dry-run by default per the "no automatic pushes" policy). These
# endpoints let the operator drive that same git_writer from the
# portal's verdict-review surface: a dry-run preview by default, and an
# explicit real commit when they opt in.


class MergeRequest(BaseModel):
    """Body for POST /{spec_id}/merge."""

    dry_run: bool = True
    target_branch: str | None = None
    repo_dir: str | None = None
    include_flagged: bool = False


@router.post("/{spec_id}/merge")
def merge_accepted_tests(spec_id: str, body: MergeRequest) -> dict[str, Any]:
    """Commit the accepted (and optionally flagged) tests to the feature
    branch via the Triager's git_writer. Dry-run by default — returns the
    exact git argv + the files that would be committed, nothing written."""
    _validate_spec_id(spec_id)
    spec_dir = _find_spec_dir(_resolve_workspace_root(), spec_id)
    if spec_dir is None:
        raise HTTPException(status_code=404, detail=f"spec not found: {spec_id}")

    verdicts_doc = _read_json(spec_dir / "findings" / "verdicts.json")
    if not verdicts_doc:
        raise HTTPException(status_code=404, detail="no verdicts.json — task hasn't been evaluated")
    wanted = {"accept"} | ({"flag"} if body.include_flagged else set())
    selected = [v for v in verdicts_doc.get("verdicts", []) if v.get("verdict") in wanted]
    if not selected:
        raise HTTPException(status_code=400, detail="no accepted tests to merge")

    source = _read_json(spec_dir / "context" / "source.json") or {}
    branch = (body.target_branch or source.get("branch") or "").strip()
    if not branch:
        raise HTTPException(status_code=400, detail="no target branch (set target_branch or context/source.json)")

    files: list[tuple[str, str]] = []
    for v in selected:
        tf = v.get("test_file")
        if not tf:
            continue
        p = Path(tf)
        if not p.is_absolute():
            p = spec_dir / tf
        if not p.is_file():
            continue
        try:
            content = p.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            rel = str(p.relative_to(spec_dir))  # e.g. tests/unit/test_x.py
        except ValueError:
            rel = p.name
        files.append((rel, content))

    if not files:
        raise HTTPException(status_code=400, detail="no readable test files for the selected verdicts")

    if not body.dry_run and not body.repo_dir:
        raise HTTPException(status_code=400, detail="repo_dir is required for a real (non-dry-run) merge")
    repo_dir = Path(body.repo_dir) if body.repo_dir else spec_dir

    from tools.git_writer import GitWriteRequest, write_tests_to_branch

    n_accept = sum(1 for v in selected if v.get("verdict") == "accept")
    n_flag = len(selected) - n_accept
    request = GitWriteRequest(
        repo_dir=repo_dir,
        branch=branch,
        files=tuple(files),
        commit_msg=f"pfactory: add {n_accept} accepted"
        + (f" + {n_flag} flagged" if n_flag else "")
        + " test(s)",
    )
    result = write_tests_to_branch(request, dry_run=body.dry_run)
    return {
        "ok": result.ok,
        "dry_run": result.dry_run,
        "branch": branch,
        "files": [f[0] for f in files],
        "committed_paths": list(result.committed_paths),
        "commit_sha": result.commit_sha,
        "argv": [list(a) for a in result.argv_log],
        "error": result.error,
    }


@router.post("/{spec_id}/dismiss")
def dismiss_run(spec_id: str) -> dict[str, Any]:
    """Mark a run dismissed (operator chose not to merge). Records a
    ``dismissed`` flag on status.json — non-destructive."""
    _validate_spec_id(spec_id)
    spec_dir = _find_spec_dir(_resolve_workspace_root(), spec_id)
    if spec_dir is None:
        raise HTTPException(status_code=404, detail=f"spec not found: {spec_id}")
    status_path = spec_dir / "status.json"
    doc = _read_json(status_path) or {}
    doc["dismissed"] = True
    doc["dismissed_at"] = datetime.utcnow().isoformat() + "Z"
    try:
        status_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not write status.json: {exc}") from exc
    return {"ok": True, "dismissed": True, "dismissed_at": doc["dismissed_at"]}


# ─── Visual regression baselines (#109) ───────────────────────────────────
#
# Surface the stored visual baselines (agents.evidence.visual_baseline) so the
# portal Evidence tab can list them, view a baseline image, and accept/update a
# captured screenshot as the new baseline. Backed by the per-task workspace
# (findings/visual-baselines/<target>/<snapshot>.png).
import sys as _sys

from fastapi.responses import FileResponse as _FileResponse

_BACKEND_DIR = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND_DIR) not in _sys.path:
    _sys.path.insert(0, str(_BACKEND_DIR))


def _spec_dir_or_404(spec_id: str) -> Path:
    _validate_spec_id(spec_id)
    spec_dir = _find_spec_dir(_resolve_workspace_root(), spec_id)
    if spec_dir is None:
        raise HTTPException(status_code=404, detail=f"task not found: {spec_id}")
    return spec_dir


@router.get("/{spec_id}/visual-baselines")
def list_visual_baselines(spec_id: str, target: str) -> dict:
    """List the stored visual baselines for a target (snapshot name + size)."""
    from agents.evidence import visual_baseline as vb

    spec_dir = _spec_dir_or_404(spec_id)
    try:
        entries = vb.list_baselines(spec_dir, target)
    except vb.VisualBaselineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "target": target,
        "baselines": [
            {"snapshot": e.snapshot, "sizeBytes": e.size_bytes} for e in entries
        ],
    }


@router.get("/{spec_id}/visual-baselines/{target}/{snapshot}")
def get_visual_baseline(spec_id: str, target: str, snapshot: str):
    """Serve one baseline image (PNG)."""
    from agents.evidence import visual_baseline as vb

    spec_dir = _spec_dir_or_404(spec_id)
    try:
        path = vb.baseline_path(spec_dir, target, snapshot)
    except vb.VisualBaselineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="baseline not found")
    return _FileResponse(path, media_type="image/png", filename=snapshot)


class _AcceptBaselineBody(BaseModel):
    # path (relative to the task workspace) of the captured screenshot to promote
    source: str


@router.post("/{spec_id}/visual-baselines/{target}/{snapshot}/accept")
def accept_visual_baseline(
    spec_id: str, target: str, snapshot: str, body: _AcceptBaselineBody
) -> dict:
    """Promote a captured screenshot to the stored baseline (accept/update flow)."""
    from agents.evidence import visual_baseline as vb

    spec_dir = _spec_dir_or_404(spec_id)
    # The source must stay inside the task workspace (no traversal escape).
    src = (spec_dir / body.source).resolve()
    root = spec_dir.resolve()
    if root not in src.parents or not src.is_file():
        raise HTTPException(status_code=400, detail="invalid source image")
    try:
        dest = vb.accept_baseline(spec_dir, target, snapshot, src)
    except vb.VisualBaselineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "accepted": True,
        "target": target,
        "snapshot": snapshot,
        "path": str(dest.relative_to(spec_dir)),
    }
