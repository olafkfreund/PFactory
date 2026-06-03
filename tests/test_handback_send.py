"""Tests for the handback sender + Triager hook (P4 / #185).

No real AIFactory: every send path injects a fake ``sender_fn`` (or stays
dry-run), so the suite asserts the dry-run/opt-in contract without a network.
"""

from __future__ import annotations

import json

import pytest

from agents.handback import build_correction_request
from agents.handback.send import send_correction
from agents.handback.trigger import maybe_handback

SOURCE = {
    "aifactory": {
        "project_id": "demo",
        "spec_id": "001-login",
        "api_url": "http://localhost:3101",
        "task_id": "demo:001-login",
    }
}
VERDICTS = {
    "verdicts": [
        {"test_id": "t_ok", "verdict": "accept"},
        {"test_id": "t_bad", "verdict": "reject", "reasons": ["boom"], "lane": "api"},
    ]
}
TRIAGE = {"rejected": [{"test_id": "t_bad", "test_file": "tests/test_x.py"}]}


def _request():
    return build_correction_request(VERDICTS, TRIAGE, SOURCE)


# ── send_correction: artifacts always; POST only when dry_run=False+confirm ──


def test_dry_run_writes_artifacts_and_does_not_send(tmp_path) -> None:
    calls = []
    res = send_correction(
        _request(), tmp_path, dry_run=True, confirm=False,
        sender_fn=lambda p: calls.append(p) or {},
        now="2026-06-03T00:00:00+00:00",
    )
    assert res.ok and not res.sent and res.dry_run
    assert calls == []  # sender never invoked on the dry-run path
    assert (tmp_path / "findings" / "handback_request.md").exists()
    doc = json.loads((tmp_path / "findings" / "handback_request.json").read_text())
    assert doc["generated_at"] == "2026-06-03T00:00:00+00:00"
    assert doc["dry_run"] is True
    assert doc["aifactory_task_id"] == "demo:001-login"
    assert doc["failing_tests"][0]["test_id"] == "t_bad"


def test_confirmed_live_send_calls_sender_with_payload(tmp_path) -> None:
    captured = {}

    def fake(payload):
        captured.update(payload)
        return {"success": True, "task_id": "demo:001-login", "status": "qa_fixing"}

    res = send_correction(
        _request(), tmp_path, dry_run=False, confirm=True, sender_fn=fake
    )
    assert res.ok and res.sent
    assert res.response["status"] == "qa_fixing"
    assert captured["task_id"] == "demo:001-login"
    assert captured["api_url"] == "http://localhost:3101"
    assert captured["confirm"] is True
    assert "QA Fix Request" in captured["fix_request_md"]


def test_not_confirmed_does_not_send_even_if_not_dry_run(tmp_path) -> None:
    calls = []
    res = send_correction(
        _request(), tmp_path, dry_run=False, confirm=False,
        sender_fn=lambda p: calls.append(p) or {},
    )
    assert not res.sent and calls == []


def test_unreachable_sender_is_graceful(tmp_path) -> None:
    def boom(payload):
        raise ConnectionError("AIFactory down")

    res = send_correction(_request(), tmp_path, dry_run=False, confirm=True, sender_fn=boom)
    assert res.ok is False and res.sent is False
    assert "ConnectionError" in res.error
    # Artifact still written despite the failed send.
    assert (tmp_path / "findings" / "handback_request.md").exists()


def test_nothing_to_hand_back_writes_nothing(tmp_path) -> None:
    req = build_correction_request({"verdicts": [{"test_id": "a", "verdict": "accept"}]}, None, SOURCE)
    res = send_correction(req, tmp_path, dry_run=True)
    assert res.ok and not res.sent
    assert not (tmp_path / "findings").exists()


# ── maybe_handback (the Triager hook) ────────────────────────────────────


def _seed_workspace(tmp_path):
    (tmp_path / "findings").mkdir(parents=True)
    (tmp_path / "context").mkdir(parents=True)
    (tmp_path / "findings" / "verdicts.json").write_text(json.dumps(VERDICTS))
    (tmp_path / "findings" / "triage_report.json").write_text(json.dumps(TRIAGE))
    (tmp_path / "context" / "source.json").write_text(json.dumps(SOURCE))


def test_hook_prepares_by_default_but_does_not_send(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("PFACTORY_HANDBACK_PREPARE", raising=False)
    monkeypatch.delenv("PFACTORY_HANDBACK_SEND", raising=False)
    _seed_workspace(tmp_path)
    calls = []
    res = maybe_handback(tmp_path, sender_fn=lambda p: calls.append(p) or {})
    assert res is not None and res.sent is False  # prepared, not sent
    assert calls == []
    assert (tmp_path / "findings" / "handback_request.md").exists()


def test_hook_sends_when_opted_in(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PFACTORY_HANDBACK_SEND", "1")
    _seed_workspace(tmp_path)
    calls = []
    res = maybe_handback(tmp_path, sender_fn=lambda p: calls.append(p) or {"success": True})
    assert res is not None and res.sent is True
    assert len(calls) == 1


def test_hook_disabled_by_prepare_flag(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PFACTORY_HANDBACK_PREPARE", "0")
    _seed_workspace(tmp_path)
    assert maybe_handback(tmp_path) is None
    assert not (tmp_path / "findings" / "handback_request.md").exists()


def test_hook_noop_when_no_failures(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("PFACTORY_HANDBACK_SEND", raising=False)
    (tmp_path / "findings").mkdir(parents=True)
    (tmp_path / "context").mkdir(parents=True)
    (tmp_path / "findings" / "verdicts.json").write_text(
        json.dumps({"verdicts": [{"test_id": "a", "verdict": "accept"}]})
    )
    (tmp_path / "context" / "source.json").write_text(json.dumps(SOURCE))
    assert maybe_handback(tmp_path) is None


def test_hook_noop_when_artifacts_missing(tmp_path) -> None:
    assert maybe_handback(tmp_path) is None  # no verdicts/source → None, no raise


# ── CLI: python -m agents.handback <spec_dir> [--send] ───────────────────


from agents.handback.__main__ import main as handback_main  # noqa: E402


def test_cli_preview_writes_artifact_and_no_send(tmp_path, capsys) -> None:
    _seed_workspace(tmp_path)
    rc = handback_main([str(tmp_path)])  # no --send → dry-run
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True and out["sent"] is False
    assert (tmp_path / "findings" / "handback_request.md").exists()


def test_cli_nothing_to_hand_back(tmp_path, capsys) -> None:
    (tmp_path / "findings").mkdir(parents=True)
    (tmp_path / "context").mkdir(parents=True)
    (tmp_path / "findings" / "verdicts.json").write_text(
        json.dumps({"verdicts": [{"test_id": "a", "verdict": "accept"}]})
    )
    (tmp_path / "context" / "source.json").write_text(json.dumps(SOURCE))
    rc = handback_main([str(tmp_path)])
    assert rc == 0
    assert "Nothing to hand back" in capsys.readouterr().out


def test_cli_missing_artifacts_errors(tmp_path) -> None:
    assert handback_main([str(tmp_path)]) == 2
