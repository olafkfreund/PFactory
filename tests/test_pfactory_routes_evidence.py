"""Tests for GET /api/pfactory/tasks/{spec_id}/evidence/{test_id}/{artifact}.

Task 16 / #32 sub-task 16.7.

Covered:
  - Happy path: PNG, WebM, ZIP, HAR served with correct content-type
  - 404: unknown spec_id
  - 404: unknown test_id (evidence dir missing)
  - 404: artifact file not present in evidence dir
  - 400: spec_id with path traversal (../)
  - 400: test_id with path traversal
  - 400: artifact with path traversal
  - 400: artifact with absolute path prefix
  - Content-type by extension mapping
  - Subdirectory artifact (screenshots/0001.png)
  - 405 on POST (read-only endpoint — verified via decorator absence)
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

# ── FastAPI stub (same pattern as test_pfactory_routes_tasks.py) ─────────────

if "fastapi" not in sys.modules:
    _fastapi = types.ModuleType("fastapi")

    class _APIRouter:
        def __init__(self, *args, **kwargs): pass
        def get(self, *args, **kwargs):
            def _decorator(fn):
                return fn
            return _decorator
        def websocket(self, *args, **kwargs):
            def _decorator(fn):
                return fn
            return _decorator

    class _WebSocket:
        async def accept(self): pass
        async def send_text(self, _t: str): pass
        async def receive_text(self) -> str: return ""
        async def close(self, code: int = 1000, reason: str = ""): pass

    class _WebSocketDisconnect(Exception):
        pass

    class _HTTPException(Exception):
        def __init__(self, status_code: int, detail: str = "") -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class _Response:
        def __init__(self, content=b"", media_type: str = "", status_code: int = 200) -> None:
            self.content = (
                content if isinstance(content, (bytes, bytearray))
                else str(content).encode()
            )
            self.media_type = media_type
            self.status_code = status_code
            self.body = self.content

    _status = types.ModuleType("fastapi.status")
    _status.HTTP_400_BAD_REQUEST = 400
    _status.HTTP_404_NOT_FOUND = 404
    _status.HTTP_500_INTERNAL_SERVER_ERROR = 500

    _fastapi.APIRouter = _APIRouter
    _fastapi.HTTPException = _HTTPException
    _fastapi.Response = _Response
    _fastapi.WebSocket = _WebSocket
    _fastapi.WebSocketDisconnect = _WebSocketDisconnect
    _fastapi.status = _status
    sys.modules["fastapi"] = _fastapi
    sys.modules["fastapi.status"] = _status

from fastapi import HTTPException as _HTTPException  # noqa: E402

# Add apps/web-server/ to sys.path
WEB_SERVER_PATH = Path(__file__).parent.parent / "apps" / "web-server"
if str(WEB_SERVER_PATH) not in sys.path:
    sys.path.insert(0, str(WEB_SERVER_PATH))


from server.routes.pfactory_tasks import (  # noqa: E402
    _evidence_content_type,
    _validate_artifact,
    _validate_spec_id,
    _validate_test_id,
    get_evidence_artifact,
)

# ── Workspace builder fixture ─────────────────────────────────────────────────


@pytest.fixture
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "pfactory"
    (root / "workspaces").mkdir(parents=True)
    monkeypatch.setenv("PFACTORY_WORKSPACE_ROOT", str(root))
    return root


def _make_spec(workspace_root: Path, *, project_id: str, spec_id: str) -> Path:
    """Create a minimal spec directory with a status.json."""
    spec_dir = workspace_root / "workspaces" / project_id / "specs" / spec_id
    spec_dir.mkdir(parents=True)
    (spec_dir / "status.json").write_text('{"status": "triaged"}', encoding="utf-8")
    return spec_dir


def _make_evidence(spec_dir: Path, test_id: str, artifact: str, content: bytes) -> Path:
    """Create an evidence artifact file at the correct path."""
    artifact_path = spec_dir / "findings" / "evidence" / test_id / artifact
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(content)
    return artifact_path


# ── Content-type helpers ──────────────────────────────────────────────────────


@pytest.mark.parametrize("artifact,expected_ct", [
    ("screenshot.png", "image/png"),
    ("video.webm", "video/webm"),
    ("trace.zip", "application/zip"),
    ("network.har", "application/json"),
    ("events.jsonl", "application/json"),
    ("clip.mp4", "video/mp4"),
    ("thumb.jpg", "image/jpeg"),
    ("unknown.bin", "application/octet-stream"),
])
def test_evidence_content_type(artifact: str, expected_ct: str) -> None:
    assert _evidence_content_type(artifact) == expected_ct


# ── Validation helpers ────────────────────────────────────────────────────────


def test_validate_test_id_valid() -> None:
    _validate_test_id("ac1-login-flow")  # should not raise


def test_validate_test_id_empty_raises_400() -> None:
    with pytest.raises(_HTTPException) as exc_info:
        _validate_test_id("")
    assert exc_info.value.status_code == 400


def test_validate_test_id_traversal_raises_400() -> None:
    with pytest.raises(_HTTPException) as exc_info:
        _validate_test_id("../evil")
    assert exc_info.value.status_code == 400


def test_validate_artifact_valid() -> None:
    _validate_artifact("video.webm")
    _validate_artifact("screenshots/0001.png")


def test_validate_artifact_empty_raises_400() -> None:
    with pytest.raises(_HTTPException) as exc_info:
        _validate_artifact("")
    assert exc_info.value.status_code == 400


def test_validate_artifact_dotdot_raises_400() -> None:
    with pytest.raises(_HTTPException) as exc_info:
        _validate_artifact("../etc/passwd")
    assert exc_info.value.status_code == 400


def test_validate_artifact_absolute_raises_400() -> None:
    with pytest.raises(_HTTPException) as exc_info:
        _validate_artifact("/etc/passwd")
    assert exc_info.value.status_code == 400


def test_validate_artifact_dotdot_in_subpath_raises_400() -> None:
    with pytest.raises(_HTTPException) as exc_info:
        _validate_artifact("screenshots/../../../etc/passwd")
    assert exc_info.value.status_code == 400


# ── Happy path: PNG ──────────────────────────────────────────────────────────


def test_get_evidence_artifact_png(workspace_root: Path) -> None:
    spec_dir = _make_spec(workspace_root, project_id="p1", spec_id="spec-001")
    _make_evidence(spec_dir, "ac1-login", "screenshot.png", b"\x89PNG\r\n")

    response = get_evidence_artifact("spec-001", "ac1-login", "screenshot.png")
    assert response.body == b"\x89PNG\r\n"
    assert response.media_type == "image/png"


# ── Happy path: WebM ─────────────────────────────────────────────────────────


def test_get_evidence_artifact_webm(workspace_root: Path) -> None:
    spec_dir = _make_spec(workspace_root, project_id="p1", spec_id="spec-001")
    _make_evidence(spec_dir, "ac1-login", "video.webm", b"\x1aEBML")

    response = get_evidence_artifact("spec-001", "ac1-login", "video.webm")
    assert response.body == b"\x1aEBML"
    assert response.media_type == "video/webm"


# ── Happy path: ZIP ──────────────────────────────────────────────────────────


def test_get_evidence_artifact_zip(workspace_root: Path) -> None:
    spec_dir = _make_spec(workspace_root, project_id="p1", spec_id="spec-001")
    _make_evidence(spec_dir, "ac1-login", "trace.zip", b"PK\x03\x04")

    response = get_evidence_artifact("spec-001", "ac1-login", "trace.zip")
    assert response.body == b"PK\x03\x04"
    assert response.media_type == "application/zip"


# ── Happy path: HAR ──────────────────────────────────────────────────────────


def test_get_evidence_artifact_har(workspace_root: Path) -> None:
    spec_dir = _make_spec(workspace_root, project_id="p1", spec_id="spec-001")
    har_content = b'{"log": {"entries": []}}'
    _make_evidence(spec_dir, "ac1-login", "network.har", har_content)

    response = get_evidence_artifact("spec-001", "ac1-login", "network.har")
    assert response.body == har_content
    assert response.media_type == "application/json"


# ── Happy path: subdirectory artifact (screenshots/0001.png) ─────────────────


def test_get_evidence_artifact_screenshot_in_subdir(workspace_root: Path) -> None:
    spec_dir = _make_spec(workspace_root, project_id="p1", spec_id="spec-001")
    _make_evidence(spec_dir, "ac1-login", "screenshots/0001.png", b"PNG")

    response = get_evidence_artifact("spec-001", "ac1-login", "screenshots/0001.png")
    assert response.body == b"PNG"
    assert response.media_type == "image/png"


# ── 404: unknown spec_id ──────────────────────────────────────────────────────


def test_get_evidence_artifact_404_unknown_spec(workspace_root: Path) -> None:
    with pytest.raises(_HTTPException) as exc_info:
        get_evidence_artifact("no-such-spec", "t1", "video.webm")
    assert exc_info.value.status_code == 404


# ── 404: test_id evidence dir missing ────────────────────────────────────────


def test_get_evidence_artifact_404_no_evidence_dir(workspace_root: Path) -> None:
    _make_spec(workspace_root, project_id="p1", spec_id="spec-001")
    # No evidence dir created for this test_id
    with pytest.raises(_HTTPException) as exc_info:
        get_evidence_artifact("spec-001", "no-such-test", "video.webm")
    assert exc_info.value.status_code == 404


# ── 404: artifact file missing ────────────────────────────────────────────────


def test_get_evidence_artifact_404_artifact_missing(workspace_root: Path) -> None:
    spec_dir = _make_spec(workspace_root, project_id="p1", spec_id="spec-001")
    # Create evidence dir but not the specific file
    ev_dir = spec_dir / "findings" / "evidence" / "ac1-login"
    ev_dir.mkdir(parents=True)
    with pytest.raises(_HTTPException) as exc_info:
        get_evidence_artifact("spec-001", "ac1-login", "video.webm")
    assert exc_info.value.status_code == 404


# ── 400: path traversal in spec_id ───────────────────────────────────────────


def test_get_evidence_artifact_400_traversal_spec_id(workspace_root: Path) -> None:
    with pytest.raises(_HTTPException) as exc_info:
        get_evidence_artifact("../evil", "t1", "video.webm")
    assert exc_info.value.status_code == 400


# ── 400: path traversal in test_id ───────────────────────────────────────────


def test_get_evidence_artifact_400_traversal_test_id(workspace_root: Path) -> None:
    with pytest.raises(_HTTPException) as exc_info:
        get_evidence_artifact("spec-001", "../evil", "video.webm")
    assert exc_info.value.status_code == 400


# ── 400: path traversal in artifact ──────────────────────────────────────────


def test_get_evidence_artifact_400_traversal_artifact(workspace_root: Path) -> None:
    _make_spec(workspace_root, project_id="p1", spec_id="spec-001")
    with pytest.raises(_HTTPException) as exc_info:
        get_evidence_artifact("spec-001", "t1", "../../../etc/passwd")
    assert exc_info.value.status_code == 400


def test_get_evidence_artifact_400_absolute_artifact(workspace_root: Path) -> None:
    _make_spec(workspace_root, project_id="p1", spec_id="spec-001")
    with pytest.raises(_HTTPException) as exc_info:
        get_evidence_artifact("spec-001", "t1", "/etc/passwd")
    assert exc_info.value.status_code == 400


# ── GET only (no POST) ────────────────────────────────────────────────────────


def test_evidence_endpoint_has_no_post_method() -> None:
    """The function is only registered with @router.get — no POST handler."""
    import server.routes.pfactory_tasks as mod
    # The function itself exists; what matters is there's no post_evidence_artifact
    assert hasattr(mod, "get_evidence_artifact")
    assert not hasattr(mod, "post_evidence_artifact")
