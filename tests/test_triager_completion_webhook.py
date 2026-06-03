"""Tests for the Triager completion callback (#85).

When the Triager reaches a terminal status, an opt-in sentinel file and an
opt-in webhook fire so the /pfactory-watch round-trip needs no polling. Both
default OFF and are strictly best-effort — a missing/failing target must never
break the pipeline.

Covered:
  - default (no env): terminal write fires neither channel
  - sentinel opt-in: terminal write → findings/COMPLETED.json with task_id+status
  - non-terminal status never fires the sentinel
  - webhook opt-in: terminal write → POST with the JSON payload
  - webhook failure is swallowed (pipeline-safe)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents.triager import _write_status_patch


def _seed_status(spec_dir: Path) -> None:
    (spec_dir / "findings").mkdir(parents=True, exist_ok=True)
    (spec_dir / "status.json").write_text(
        json.dumps({"task_id": "042", "project_id": "demo", "status": "triaging"})
    )


def test_default_fires_no_channel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PFACTORY_COMPLETION_WEBHOOK", raising=False)
    monkeypatch.delenv("PFACTORY_COMPLETION_SENTINEL", raising=False)
    _seed_status(tmp_path)
    _write_status_patch(tmp_path, status="triaged")
    assert not (tmp_path / "findings" / "COMPLETED.json").exists()


def test_sentinel_opt_in_writes_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PFACTORY_COMPLETION_SENTINEL", "1")
    _seed_status(tmp_path)
    _write_status_patch(tmp_path, status="triaged")
    marker = tmp_path / "findings" / "COMPLETED.json"
    assert marker.exists()
    body = json.loads(marker.read_text())
    assert body["task_id"] == "042"
    assert body["status"] == "triaged"


def test_non_terminal_status_does_not_fire(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PFACTORY_COMPLETION_SENTINEL", "1")
    _seed_status(tmp_path)
    _write_status_patch(tmp_path, status="triaging")  # not terminal
    assert not (tmp_path / "findings" / "COMPLETED.json").exists()


def test_webhook_opt_in_posts_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PFACTORY_COMPLETION_WEBHOOK", "http://hook.test/notify")
    _seed_status(tmp_path)

    captured: dict = {}

    class _Resp:
        def close(self) -> None:
            pass

    def _fake_urlopen(req, timeout=None):  # noqa: ANN001
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _Resp()

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    _write_status_patch(tmp_path, status="triager_failed", phase="boom")

    assert captured["url"] == "http://hook.test/notify"
    assert captured["method"] == "POST"
    assert captured["body"]["task_id"] == "042"
    assert captured["body"]["status"] == "triager_failed"
    assert captured["body"]["phase"] == "boom"


def test_webhook_failure_is_swallowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PFACTORY_COMPLETION_WEBHOOK", "http://hook.test/notify")
    _seed_status(tmp_path)

    def _boom(req, timeout=None):  # noqa: ANN001
        raise OSError("connection refused")

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    # Must NOT raise — the pipeline can never break on a failed webhook.
    _write_status_patch(tmp_path, status="triaged")
    assert json.loads((tmp_path / "status.json").read_text())["status"] == "triaged"
