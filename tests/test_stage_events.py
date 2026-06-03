"""Tests for per-stage pipeline events (#95).

Both channels are opt-in and best-effort; the default must be a true no-op
so the existing pipeline and test suite are unaffected.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents.stage_events import emit_stage_event, stage_event_payload

_STATUS = {
    "task_id": "spec-001",
    "project_id": "proj-x",
    "status": "generated",
    "phase": "gen_functional_complete",
    "updated_at": "2026-06-01T00:00:00+00:00",
}


# ── payload shape ───────────────────────────────────────────────────────────


def test_payload_carries_stage_and_completion_fields() -> None:
    payload = stage_event_payload(Path("/tmp/spec-001"), _STATUS, "evaluator")
    assert payload["stage"] == "evaluator"
    assert payload["status"] == "generated"
    assert payload["phase"] == "gen_functional_complete"
    assert payload["task_id"] == "spec-001"
    assert payload["project_id"] == "proj-x"


def test_payload_falls_back_to_spec_dir_name_for_task_id() -> None:
    payload = stage_event_payload(Path("/tmp/fallback-id"), {}, "planner")
    assert payload["task_id"] == "fallback-id"
    assert payload["project_id"] is None


# ── default: no channel opted in → true no-op ───────────────────────────────


def test_default_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PFACTORY_STAGE_EVENT_SENTINEL", raising=False)
    monkeypatch.delenv("PFACTORY_STAGE_EVENT_WEBHOOK", raising=False)
    emit_stage_event(tmp_path, _STATUS, stage="planner")
    assert not (tmp_path / "findings").exists()


# ── sentinel channel ────────────────────────────────────────────────────────


def test_sentinel_appends_one_jsonl_line_per_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PFACTORY_STAGE_EVENT_SENTINEL", "1")
    monkeypatch.delenv("PFACTORY_STAGE_EVENT_WEBHOOK", raising=False)

    emit_stage_event(tmp_path, {**_STATUS, "status": "planning"}, stage="planner")
    emit_stage_event(tmp_path, {**_STATUS, "status": "generated"}, stage="gen_functional")

    log = tmp_path / "findings" / "stage_events.jsonl"
    assert log.exists()
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first, second = (json.loads(line) for line in lines)
    assert first["stage"] == "planner" and first["status"] == "planning"
    assert second["stage"] == "gen_functional" and second["status"] == "generated"


# ── webhook channel ─────────────────────────────────────────────────────────


def test_webhook_posts_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PFACTORY_STAGE_EVENT_WEBHOOK", "http://localhost:9/hook")
    monkeypatch.delenv("PFACTORY_STAGE_EVENT_SENTINEL", raising=False)

    captured: dict[str, object] = {}

    class _Resp:
        def close(self) -> None:  # pragma: no cover - trivial
            pass

    def _fake_urlopen(req, timeout=None):  # type: ignore[no-untyped-def]
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _Resp()

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    emit_stage_event(tmp_path, _STATUS, stage="triager")

    assert captured["url"] == "http://localhost:9/hook"
    assert captured["method"] == "POST"
    assert captured["body"]["stage"] == "triager"  # type: ignore[index]
    assert captured["timeout"] == 5.0


def test_webhook_failure_is_swallowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PFACTORY_STAGE_EVENT_WEBHOOK", "http://localhost:9/hook")

    def _boom(req, timeout=None):  # type: ignore[no-untyped-def]
        raise OSError("connection refused")

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    # Must not raise.
    emit_stage_event(tmp_path, _STATUS, stage="triager")
