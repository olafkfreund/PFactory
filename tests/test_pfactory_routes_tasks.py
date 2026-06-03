"""Tests for /api/pfactory/tasks REST endpoints — Task 9 (#10) commit 1.

The route handlers are plain Python functions decorated with
``@router.get(...)`` — we test them directly (no HTTP stack
required). FastAPI's own behaviour (URL routing, response model
serialisation) is exercised by FastAPI's own test suite; here we
focus on OUR business logic.

Covered:
  - Workspace root resolution (env override + default)
  - spec_id validation (path traversal protection)
  - list_tasks: empty workspace, single task, multi-task ordering
  - get_task: 400 on malformed id, 404 on missing, full status_doc
    returned on hit, artefact meta with existence flags
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

# The route module imports FastAPI's APIRouter + HTTPException. The
# PFactory backend venv does NOT install FastAPI (those deps live in
# apps/web-server/requirements.txt and are only installed when the
# portal runs). For unit-level coverage of the route's business logic
# we install a minimal stub into sys.modules BEFORE importing the
# route file — fast, no extra deps, exercises our code.
if "fastapi" not in sys.modules:
    _fastapi = types.ModuleType("fastapi")

    class _APIRouter:
        def __init__(self, *args, **kwargs): pass
        def get(self, *args, **kwargs):
            def _decorator(fn):
                return fn
            return _decorator
        def post(self, *args, **kwargs):
            def _decorator(fn):
                return fn
            return _decorator
        def websocket(self, *args, **kwargs):
            def _decorator(fn):
                return fn
            return _decorator

    class _WebSocket:  # only used for type annotations in the route module
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
            # Mirror real FastAPI's body attribute for assertions
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

# Add apps/web-server/ to sys.path so ``from server.routes...`` resolves.
WEB_SERVER_PATH = (
    Path(__file__).parent.parent / "apps" / "web-server"
)
if str(WEB_SERVER_PATH) not in sys.path:
    sys.path.insert(0, str(WEB_SERVER_PATH))


from server.routes.pfactory_tasks import (  # noqa: E402  # noqa: E402
    DEFAULT_LOG_TAIL_LINES,
    MergeRequest,
    _artefact_meta,
    _find_spec_dir,
    _resolve_log_files,
    _resolve_workspace_root,
    _serve_artefact_file,
    _summary_row,
    _tail_lines,
    _validate_spec_id,
    dismiss_run,
    get_catalog,
    get_pr_comment_body,
    get_task,
    get_test_plan,
    get_triage_report_json,
    get_triage_report_md,
    get_verdicts,
    list_tasks,
    merge_accepted_tests,
    tail_log_payload,
)

# ── Workspace builder fixture ──────────────────────────────────────────


@pytest.fixture
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a fresh ~/.pfactory-shaped tmp dir and point the env at it."""
    root = tmp_path / "pfactory"
    (root / "workspaces").mkdir(parents=True)
    monkeypatch.setenv("PFACTORY_WORKSPACE_ROOT", str(root))
    return root


def _make_task(
    workspace_root: Path,
    *,
    project_id: str,
    spec_id: str,
    status: str = "evaluated",
    phase: str = "evaluator_complete",
    updated_at: str = "2026-05-28T10:00:00+00:00",
    extra_status: dict[str, Any] | None = None,
    artefacts: list[str] | None = None,
) -> Path:
    """Scaffold a PFactory spec_dir with a status.json + optional artefacts."""
    spec_dir = workspace_root / "workspaces" / project_id / "specs" / spec_id
    spec_dir.mkdir(parents=True)
    (spec_dir / "findings").mkdir()
    doc = {
        "task_id": spec_id,
        "project_id": project_id,
        "spec_id": spec_id,
        "status": status,
        "phase": phase,
        "updated_at": updated_at,
    }
    if extra_status:
        doc.update(extra_status)
    (spec_dir / "status.json").write_text(json.dumps(doc))
    for relpath in artefacts or []:
        path = spec_dir / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}" if relpath.endswith(".json") else "# x")
    return spec_dir


# ── Workspace root resolution ──────────────────────────────────────────


def test_resolve_workspace_root_uses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PFACTORY_WORKSPACE_ROOT", "/var/lib/tf")
    assert _resolve_workspace_root() == Path("/var/lib/tf")


def test_resolve_workspace_root_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PFACTORY_WORKSPACE_ROOT", raising=False)
    root = _resolve_workspace_root()
    # Default is ~/.pfactory regardless of platform
    assert root.name == ".pfactory"
    assert root.parent == Path.home()


def test_resolve_workspace_root_expands_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PFACTORY_WORKSPACE_ROOT", "~/.tf-custom")
    root = _resolve_workspace_root()
    # ~ should be expanded
    assert not str(root).startswith("~")


# ── spec_id validation ─────────────────────────────────────────────────


def test_validate_spec_id_accepts_simple() -> None:
    _validate_spec_id("042-session-expiry")  # no raise
    _validate_spec_id("simple")
    _validate_spec_id("a-b_c.d-1")


def test_validate_spec_id_rejects_path_traversal() -> None:
    for bad in ("../etc/passwd", "../../x", "x/y", "a/../b"):
        with pytest.raises(_HTTPException) as exc:
            _validate_spec_id(bad)
        assert exc.value.status_code == 400


def test_validate_spec_id_rejects_empty() -> None:
    with pytest.raises(_HTTPException) as exc:
        _validate_spec_id("")
    assert exc.value.status_code == 400


def test_validate_spec_id_rejects_spaces() -> None:
    with pytest.raises(_HTTPException) as exc:
        _validate_spec_id("foo bar")
    assert exc.value.status_code == 400


# ── list_tasks ──────────────────────────────────────────────────────────


def test_list_tasks_empty_workspace(workspace_root: Path) -> None:
    """Workspace dir exists but no projects — empty list."""
    result = list_tasks()
    assert result == {"tasks": [], "count": 0}


def test_list_tasks_no_root_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace root doesn't exist at all — graceful empty response."""
    monkeypatch.setenv("PFACTORY_WORKSPACE_ROOT", str(tmp_path / "nonexistent"))
    result = list_tasks()
    assert result == {"tasks": [], "count": 0}


def test_list_tasks_single(workspace_root: Path) -> None:
    _make_task(
        workspace_root,
        project_id="demo",
        spec_id="042-x",
        status="evaluated",
        phase="evaluator_complete",
    )
    result = list_tasks()
    assert result["count"] == 1
    row = result["tasks"][0]
    assert row["task_id"] == "042-x"
    assert row["project_id"] == "demo"
    assert row["spec_id"] == "042-x"
    assert row["status"] == "evaluated"
    assert row["phase"] == "evaluator_complete"


def test_list_tasks_sorted_newest_first(workspace_root: Path) -> None:
    _make_task(
        workspace_root, project_id="demo", spec_id="older",
        updated_at="2026-05-01T00:00:00+00:00",
    )
    _make_task(
        workspace_root, project_id="demo", spec_id="newer",
        updated_at="2026-05-28T00:00:00+00:00",
    )
    result = list_tasks()
    assert result["count"] == 2
    # Newest first
    assert result["tasks"][0]["spec_id"] == "newer"
    assert result["tasks"][1]["spec_id"] == "older"


def test_list_tasks_across_multiple_projects(workspace_root: Path) -> None:
    _make_task(workspace_root, project_id="demo", spec_id="d1")
    _make_task(workspace_root, project_id="other", spec_id="o1")
    result = list_tasks()
    assert result["count"] == 2
    project_ids = {r["project_id"] for r in result["tasks"]}
    assert project_ids == {"demo", "other"}


def test_list_tasks_skips_malformed_status_json(workspace_root: Path) -> None:
    """A spec_dir with broken status.json still gets a row — fields
    fall back to defaults."""
    spec_dir = (
        workspace_root / "workspaces" / "demo" / "specs" / "broken"
    )
    spec_dir.mkdir(parents=True)
    (spec_dir / "status.json").write_text("not json{")

    result = list_tasks()
    assert result["count"] == 1
    row = result["tasks"][0]
    # task_id falls back to spec_id when status.json doesn't parse
    assert row["task_id"] == "broken"
    assert row["spec_id"] == "broken"
    assert row["status"] is None  # field absent in malformed doc


# ── get_task ───────────────────────────────────────────────────────────


def test_get_task_happy_returns_full_status(workspace_root: Path) -> None:
    _make_task(
        workspace_root, project_id="demo", spec_id="042-x",
        status="triaged",
        extra_status={
            "verdicts_count": 6,
            "committed_count": 4,
            "flagged_count": 1,
            "rejected_count": 1,
        },
    )
    result = get_task("042-x")
    assert result["task_id"] == "042-x"
    assert result["project_id"] == "demo"
    assert result["spec_id"] == "042-x"
    # Full status doc surfaced
    sj = result["status_json"]
    assert sj["status"] == "triaged"
    assert sj["verdicts_count"] == 6
    assert sj["committed_count"] == 4


def test_get_task_404_when_missing(workspace_root: Path) -> None:
    with pytest.raises(_HTTPException) as exc:
        get_task("nonexistent")
    assert exc.value.status_code == 404


def test_get_task_400_on_malformed_id(workspace_root: Path) -> None:
    with pytest.raises(_HTTPException) as exc:
        get_task("../../etc/passwd")
    assert exc.value.status_code == 400


def test_get_task_artefacts_meta_existence_flags(
    workspace_root: Path,
) -> None:
    _make_task(
        workspace_root, project_id="demo", spec_id="042-x",
        artefacts=[
            "test_plan.json",
            "findings/verdicts.json",
            "findings/triage_report.json",
            "findings/triage_report.md",
        ],
    )
    result = get_task("042-x")
    arts = result["artefacts"]
    assert arts["test_plan"]["exists"] is True
    assert arts["verdicts"]["exists"] is True
    assert arts["triage_report_json"]["exists"] is True
    assert arts["triage_report_md"]["exists"] is True
    # Unwritten artefact still listed with exists=False
    assert arts["pr_comment_body"]["exists"] is False


def test_get_task_artefacts_paths_relative(workspace_root: Path) -> None:
    """Artefact paths are spec_dir-relative — frontend joins them to
    its own base URL, doesn't need absolute filesystem paths."""
    _make_task(
        workspace_root, project_id="demo", spec_id="042-x",
        artefacts=["test_plan.json"],
    )
    result = get_task("042-x")
    assert result["artefacts"]["test_plan"]["path"] == "test_plan.json"
    assert result["artefacts"]["verdicts"]["path"] == "findings/verdicts.json"


def test_get_task_no_status_json_returns_empty_doc(
    workspace_root: Path,
) -> None:
    """A spec_dir with NO status.json → 404 (we use status.json's
    presence as the existence sentinel)."""
    spec_dir = workspace_root / "workspaces" / "demo" / "specs" / "incomplete"
    spec_dir.mkdir(parents=True)
    # No status.json written

    with pytest.raises(_HTTPException) as exc:
        get_task("incomplete")
    assert exc.value.status_code == 404


# ── _serve_artefact_file (shared helper) ───────────────────────────────


def test_serve_artefact_400_on_malformed_spec_id(
    workspace_root: Path,
) -> None:
    with pytest.raises(_HTTPException) as exc:
        _serve_artefact_file("../x", "test_plan.json", "application/json")
    assert exc.value.status_code == 400


def test_serve_artefact_404_when_spec_missing(workspace_root: Path) -> None:
    with pytest.raises(_HTTPException) as exc:
        _serve_artefact_file("nonexistent", "test_plan.json", "application/json")
    assert exc.value.status_code == 404
    assert "task not found" in exc.value.detail


def test_serve_artefact_404_when_artefact_missing(
    workspace_root: Path,
) -> None:
    """Spec exists but the artefact file isn't present."""
    _make_task(workspace_root, project_id="demo", spec_id="042-x")
    # No test_plan.json written
    with pytest.raises(_HTTPException) as exc:
        _serve_artefact_file("042-x", "test_plan.json", "application/json")
    assert exc.value.status_code == 404
    assert "artefact not found" in exc.value.detail


def test_serve_artefact_returns_response_with_content(
    workspace_root: Path,
) -> None:
    """Happy path: file bytes flow through verbatim with the right
    media_type."""
    _make_task(workspace_root, project_id="demo", spec_id="042-x")
    spec_dir = workspace_root / "workspaces" / "demo" / "specs" / "042-x"
    body = '{"hello": "world"}'
    (spec_dir / "test_plan.json").write_text(body)

    response = _serve_artefact_file(
        "042-x", "test_plan.json", "application/json",
    )
    assert response.media_type == "application/json"
    assert response.body == body.encode("utf-8")


# ── Per-endpoint sanity tests ──────────────────────────────────────────


def test_get_verdicts_returns_findings_verdicts_json(
    workspace_root: Path,
) -> None:
    _make_task(workspace_root, project_id="demo", spec_id="042-x")
    spec_dir = workspace_root / "workspaces" / "demo" / "specs" / "042-x"
    body = '{"evaluator_version": "task7-commit5", "verdicts": []}'
    (spec_dir / "findings" / "verdicts.json").write_text(body)

    response = get_verdicts("042-x")
    assert response.media_type == "application/json"
    assert response.body == body.encode("utf-8")


def test_get_verdicts_404_when_missing(workspace_root: Path) -> None:
    _make_task(workspace_root, project_id="demo", spec_id="042-x")
    with pytest.raises(_HTTPException) as exc:
        get_verdicts("042-x")
    assert exc.value.status_code == 404


def test_get_triage_report_json_returns_file(
    workspace_root: Path,
) -> None:
    _make_task(workspace_root, project_id="demo", spec_id="042-x")
    spec_dir = workspace_root / "workspaces" / "demo" / "specs" / "042-x"
    body = '{"triager_version": "task8-commit3", "summary": {}}'
    (spec_dir / "findings" / "triage_report.json").write_text(body)

    response = get_triage_report_json("042-x")
    assert response.media_type == "application/json"
    assert response.body == body.encode("utf-8")


def test_get_triage_report_md_returns_markdown(
    workspace_root: Path,
) -> None:
    _make_task(workspace_root, project_id="demo", spec_id="042-x")
    spec_dir = workspace_root / "workspaces" / "demo" / "specs" / "042-x"
    body = "# Triage Report\n\nLooks good.\n"
    (spec_dir / "findings" / "triage_report.md").write_text(body)

    response = get_triage_report_md("042-x")
    assert response.media_type == "text/markdown"
    assert response.body == body.encode("utf-8")


def test_get_test_plan_returns_top_level_file(
    workspace_root: Path,
) -> None:
    """test_plan.json lives at spec_dir/test_plan.json (not under findings/)."""
    _make_task(workspace_root, project_id="demo", spec_id="042-x")
    spec_dir = workspace_root / "workspaces" / "demo" / "specs" / "042-x"
    body = '{"feature": "x", "phases": []}'
    (spec_dir / "test_plan.json").write_text(body)

    response = get_test_plan("042-x")
    assert response.media_type == "application/json"
    assert response.body == body.encode("utf-8")


def test_get_pr_comment_body_returns_markdown(
    workspace_root: Path,
) -> None:
    _make_task(workspace_root, project_id="demo", spec_id="042-x")
    spec_dir = workspace_root / "workspaces" / "demo" / "specs" / "042-x"
    body = "# Triage Report\n\n_(skipped — no PR number)_\n"
    (spec_dir / "findings" / "pr_comment_body.md").write_text(body)

    response = get_pr_comment_body("042-x")
    assert response.media_type == "text/markdown"
    assert response.body == body.encode("utf-8")


# ── Path-traversal protection (re-asserted per endpoint) ───────────────


@pytest.mark.parametrize("handler", [
    get_verdicts,
    get_triage_report_json,
    get_triage_report_md,
    get_test_plan,
    get_pr_comment_body,
])
def test_all_artefact_endpoints_reject_malformed_spec_id(
    workspace_root: Path, handler,
) -> None:
    with pytest.raises(_HTTPException) as exc:
        handler("../../etc/passwd")
    assert exc.value.status_code == 400


@pytest.mark.parametrize("handler", [
    get_verdicts,
    get_triage_report_json,
    get_triage_report_md,
    get_test_plan,
    get_pr_comment_body,
])
def test_all_artefact_endpoints_404_when_spec_missing(
    workspace_root: Path, handler,
) -> None:
    with pytest.raises(_HTTPException) as exc:
        handler("nonexistent-spec")
    assert exc.value.status_code == 404


# ── UTF-8 content survives the round-trip ──────────────────────────────


def test_artefact_preserves_utf8_content(workspace_root: Path) -> None:
    """Non-ASCII characters in the file body (e.g., a Markdown report
    with em-dashes or unicode) must survive verbatim."""
    _make_task(workspace_root, project_id="demo", spec_id="042-x")
    spec_dir = workspace_root / "workspaces" / "demo" / "specs" / "042-x"
    body = "# Triage — Report\n\ncafé · résumé · 🎉\n"
    (spec_dir / "findings" / "triage_report.md").write_text(body, encoding="utf-8")

    response = get_triage_report_md("042-x")
    assert response.body.decode("utf-8") == body


# ── Log-tail primitives ───────────────────────────────────────────────


def test_resolve_log_files_empty_when_no_logs_dir(workspace_root: Path) -> None:
    spec_dir = _make_task(workspace_root, project_id="demo", spec_id="042-x")
    # No logs/ subdir
    assert _resolve_log_files(spec_dir) == {}


def test_resolve_log_files_returns_log_files_by_stem(
    workspace_root: Path,
) -> None:
    spec_dir = _make_task(workspace_root, project_id="demo", spec_id="042-x")
    (spec_dir / "logs").mkdir()
    (spec_dir / "logs" / "planner.log").write_text("p1\n")
    (spec_dir / "logs" / "gen_functional.log").write_text("g1\n")
    # Non-.log file is ignored
    (spec_dir / "logs" / "random.txt").write_text("noise")

    result = _resolve_log_files(spec_dir)
    assert set(result.keys()) == {"planner", "gen_functional"}
    assert result["planner"].name == "planner.log"


def test_tail_lines_returns_last_n(workspace_root: Path, tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_text("\n".join(f"line{i}" for i in range(50)) + "\n")
    tail = _tail_lines(log, 5)
    assert tail == [f"line{i}" for i in range(45, 50)]


def test_tail_lines_handles_fewer_lines_than_requested(
    tmp_path: Path,
) -> None:
    log = tmp_path / "small.log"
    log.write_text("only\nthree\nlines\n")
    tail = _tail_lines(log, 100)
    assert tail == ["only", "three", "lines"]


def test_tail_lines_handles_no_trailing_newline(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_text("a\nb\nc")  # no trailing newline
    tail = _tail_lines(log, 10)
    assert tail == ["a", "b", "c"]


def test_tail_lines_empty_file_returns_empty(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_text("")
    assert _tail_lines(log, 10) == []


def test_tail_lines_missing_file_returns_empty(tmp_path: Path) -> None:
    assert _tail_lines(tmp_path / "nope.log", 10) == []


def test_tail_lines_zero_n_returns_empty(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_text("a\nb\nc\n")
    assert _tail_lines(log, 0) == []


# ── tail_log_payload (the WS payload builder) ──────────────────────────


def test_tail_log_payload_400_on_malformed_spec_id(
    workspace_root: Path,
) -> None:
    with pytest.raises(_HTTPException) as exc:
        tail_log_payload("../../etc/passwd")
    assert exc.value.status_code == 400


def test_tail_log_payload_404_when_spec_missing(
    workspace_root: Path,
) -> None:
    with pytest.raises(_HTTPException) as exc:
        tail_log_payload("nonexistent")
    assert exc.value.status_code == 404


def test_tail_log_payload_empty_files_when_no_logs(
    workspace_root: Path,
) -> None:
    _make_task(workspace_root, project_id="demo", spec_id="042-x")
    payload = tail_log_payload("042-x")
    assert payload["spec_id"] == "042-x"
    assert payload["files"] == {}
    assert "captured_at" in payload


def test_tail_log_payload_includes_all_log_stems(
    workspace_root: Path,
) -> None:
    spec_dir = _make_task(workspace_root, project_id="demo", spec_id="042-x")
    (spec_dir / "logs").mkdir()
    (spec_dir / "logs" / "planner.log").write_text("p1\np2\n")
    (spec_dir / "logs" / "gen_functional.log").write_text("g1\n")
    (spec_dir / "logs" / "evaluator.log").write_text("e1\ne2\ne3\n")

    payload = tail_log_payload("042-x")
    assert set(payload["files"].keys()) == {
        "planner", "gen_functional", "evaluator",
    }
    assert payload["files"]["planner"] == ["p1", "p2"]
    assert payload["files"]["gen_functional"] == ["g1"]
    assert payload["files"]["evaluator"] == ["e1", "e2", "e3"]


def test_tail_log_payload_respects_lines_per_file_cap(
    workspace_root: Path,
) -> None:
    spec_dir = _make_task(workspace_root, project_id="demo", spec_id="042-x")
    (spec_dir / "logs").mkdir()
    (spec_dir / "logs" / "big.log").write_text(
        "\n".join(f"line{i}" for i in range(1_000)) + "\n"
    )

    payload = tail_log_payload("042-x", lines_per_file=10)
    assert len(payload["files"]["big"]) == 10
    assert payload["files"]["big"][0] == "line990"
    assert payload["files"]["big"][-1] == "line999"


def test_tail_log_payload_default_tail_lines() -> None:
    """Lock down the default cap (frontend assumes this number)."""
    assert DEFAULT_LOG_TAIL_LINES == 200


def test_tail_log_payload_decodes_utf8(workspace_root: Path) -> None:
    spec_dir = _make_task(workspace_root, project_id="demo", spec_id="042-x")
    (spec_dir / "logs").mkdir()
    body = "started café · finished résumé\nemoji 🎉 ok\n"
    (spec_dir / "logs" / "planner.log").write_text(body, encoding="utf-8")

    payload = tail_log_payload("042-x")
    assert payload["files"]["planner"] == [
        "started café · finished résumé", "emoji 🎉 ok",
    ]


def test_tail_log_payload_large_file_capped_at_byte_limit(
    workspace_root: Path,
) -> None:
    """A multi-megabyte log file is read from the tail only — we don't
    blow memory loading it all."""
    spec_dir = _make_task(workspace_root, project_id="demo", spec_id="042-x")
    (spec_dir / "logs").mkdir()
    log = spec_dir / "logs" / "huge.log"
    # 2MB of bytes — bigger than _TAIL_READ_CAP_BYTES (1MB)
    log.write_text(("x" * 80 + "\n") * 30_000)

    # Should NOT crash and should return at most lines_per_file entries
    payload = tail_log_payload("042-x", lines_per_file=50)
    assert 0 < len(payload["files"]["huge"]) <= 50


# ── /catalog endpoint (Task 14 / #30) ─────────────────────────────────────


def test_get_catalog_returns_snapshotted_catalog(workspace_root: Path) -> None:
    """Happy path: spec exists + context/tests_catalog.json written → returned verbatim."""
    spec_dir = _make_task(workspace_root, project_id="demo", spec_id="042-x")
    context_dir = spec_dir / "context"
    context_dir.mkdir(parents=True, exist_ok=True)
    catalog_body = '{"version": 1, "entries": [{"id": "test_foo", "path": "tests/test_foo.py"}]}'
    (context_dir / "tests_catalog.json").write_text(catalog_body)

    response = get_catalog("042-x")
    assert response.media_type == "application/json"
    assert response.body == catalog_body.encode("utf-8")


def test_get_catalog_returns_404_when_not_snapshotted(workspace_root: Path) -> None:
    """Spec dir exists but no context/tests_catalog.json → 404."""
    spec_dir = _make_task(workspace_root, project_id="demo", spec_id="042-x")
    (spec_dir / "context").mkdir(parents=True, exist_ok=True)
    # Intentionally NOT writing tests_catalog.json

    with pytest.raises(_HTTPException) as exc:
        get_catalog("042-x")
    assert exc.value.status_code == 404
    assert "catalog" in exc.value.detail


def test_get_catalog_returns_404_when_spec_id_unknown(workspace_root: Path) -> None:
    """Unknown spec_id → 404."""
    with pytest.raises(_HTTPException) as exc:
        get_catalog("nonexistent-spec")
    assert exc.value.status_code == 404


def test_get_catalog_rejects_path_traversal_in_spec_id(workspace_root: Path) -> None:
    """spec_id with .. or / → 400."""
    for bad in ("../etc/passwd", "../../x", "x/y"):
        with pytest.raises(_HTTPException) as exc:
            get_catalog(bad)
        assert exc.value.status_code == 400, bad


def test_get_catalog_catalog_json_is_valid_json(workspace_root: Path) -> None:
    """The returned content is valid JSON (not garbled bytes)."""
    spec_dir = _make_task(workspace_root, project_id="demo", spec_id="042-x")
    context_dir = spec_dir / "context"
    context_dir.mkdir(parents=True, exist_ok=True)
    import json
    catalog = {"version": 1, "entries": []}
    (context_dir / "tests_catalog.json").write_text(json.dumps(catalog))

    response = get_catalog("042-x")
    parsed = json.loads(response.body)
    assert parsed == catalog


# ── Merge / dismiss (the human review gate) ────────────────────────────


def _make_evaluated_task(workspace_root: Path, *, spec_id: str = "001-x") -> Path:
    """A spec with a valid verdicts.json (1 accept + 1 reject), a test file,
    and a source.json carrying the handover branch."""
    spec_dir = _make_task(workspace_root, project_id="proj", spec_id=spec_id)
    (spec_dir / "tests").mkdir()
    (spec_dir / "tests" / "test_pricing.py").write_text("def test_x():\n    assert True\n")
    (spec_dir / "context").mkdir()
    (spec_dir / "context" / "source.json").write_text(json.dumps({"branch": "feat/x"}))
    (spec_dir / "findings" / "verdicts.json").write_text(json.dumps({
        "verdicts": [
            {"test_id": "t-accept", "verdict": "accept", "test_file": "tests/test_pricing.py"},
            {"test_id": "t-reject", "verdict": "reject", "test_file": "tests/other.py"},
        ],
    }))
    return spec_dir


def test_merge_dry_run_returns_planned_files(workspace_root: Path) -> None:
    _make_evaluated_task(workspace_root)
    out = merge_accepted_tests("001-x", MergeRequest(dry_run=True))
    assert out["ok"] is True and out["dry_run"] is True
    assert out["branch"] == "feat/x"
    assert out["files"] == ["tests/test_pricing.py"]  # only the accepted one
    assert out["commit_sha"] == ""  # dry-run writes nothing
    assert any(argv for argv in out["argv"])  # git argv preview present


def test_merge_uses_target_branch_override(workspace_root: Path) -> None:
    _make_evaluated_task(workspace_root)
    out = merge_accepted_tests("001-x", MergeRequest(dry_run=True, target_branch="release/1"))
    assert out["branch"] == "release/1"


def test_merge_no_accepts_is_400(workspace_root: Path) -> None:
    spec_dir = _make_task(workspace_root, project_id="proj", spec_id="002-y")
    (spec_dir / "findings" / "verdicts.json").write_text(json.dumps({
        "verdicts": [{"test_id": "t", "verdict": "reject", "test_file": "tests/a.py"}],
    }))
    with pytest.raises(_HTTPException) as exc:
        merge_accepted_tests("002-y", MergeRequest(dry_run=True))
    assert exc.value.status_code == 400


def test_merge_no_verdicts_is_404(workspace_root: Path) -> None:
    _make_task(workspace_root, project_id="proj", spec_id="003-z")
    with pytest.raises(_HTTPException) as exc:
        merge_accepted_tests("003-z", MergeRequest(dry_run=True))
    assert exc.value.status_code == 404


def test_merge_real_run_requires_repo_dir(workspace_root: Path) -> None:
    _make_evaluated_task(workspace_root)
    with pytest.raises(_HTTPException) as exc:
        merge_accepted_tests("001-x", MergeRequest(dry_run=False))
    assert exc.value.status_code == 400


def test_dismiss_marks_status(workspace_root: Path) -> None:
    spec_dir = _make_task(workspace_root, project_id="proj", spec_id="004-d")
    out = dismiss_run("004-d")
    assert out["ok"] is True and out["dismissed"] is True
    doc = json.loads((spec_dir / "status.json").read_text())
    assert doc["dismissed"] is True and "dismissed_at" in doc
