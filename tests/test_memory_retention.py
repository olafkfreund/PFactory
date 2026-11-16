"""Retention rules for file-backed session memory."""

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from agents.tools_pkg.tools import memory as memory_tools
from memory import retention
from memory.paths import get_session_insights_dir
from memory.sessions import save_session_insights


def _sidecar(spec_dir):
    return spec_dir / "memory" / "confirmed.json"


def _key(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _saved(spec_dir):
    return json.loads((get_session_insights_dir(spec_dir) / "session_001.json").read_text())


def _tool(spec_dir, monkeypatch, name):
    monkeypatch.setattr(memory_tools, "tool", lambda *_args, **_kw: lambda f: f)
    monkeypatch.setattr(memory_tools, "SDK_TOOLS_AVAILABLE", True)
    return next(
        t for t in memory_tools.create_memory_tools(spec_dir, spec_dir) if t.__name__ == name
    )


def test_cap_table():
    assert retention.CAPS == {
        "build": 20,
        "planning": 5,
        "verification": 8,
        "insights": 5,
        "docs": 0,
    }
    assert retention.DEFAULT_SESSION_TYPE == "planning"


@pytest.mark.parametrize("kind,cap", retention.CAPS.items())
def test_session_cap_per_type(tmp_path, kind, cap):
    save_session_insights(tmp_path, 1, {"what_worked": [f"w{i}" for i in range(25)]}, kind)
    assert len(_saved(tmp_path)["what_worked"]) == cap


def test_shared_budget_order_and_ungated_fields(tmp_path):
    discoveries = {
        name: [name]
        for name in (
            "patterns_found",
            "gotchas_encountered",
            "patterns_discovered",
            "gotchas_discovered",
            "recommendations",
        )
    }
    discoveries["files_understood"] = {"a.py": "purpose"}
    save_session_insights(
        tmp_path,
        1,
        {
            "discoveries": discoveries,
            "what_worked": ["worked"],
            "recommendations_for_next_session": ["next"],
            "what_failed": ["failed"],
            "subtasks_completed": ["s"],
        },
        "planning",
    )
    saved = _saved(tmp_path)
    assert all(saved["discoveries"][name] == [name] for name in list(discoveries)[:5])
    assert saved["what_worked"] == []
    assert saved["recommendations_for_next_session"] == []
    assert saved["what_failed"] == ["failed"]
    assert saved["subtasks_completed"] == ["s"]
    assert saved["discoveries"]["files_understood"] == {"a.py": "purpose"}


def test_session_type_env_and_unknown(monkeypatch, caplog):
    monkeypatch.setenv("MEMORY_SESSION_TYPE", "docs")
    assert retention.session_type() == "docs"
    assert retention.session_type("planning") == "planning"
    with caplog.at_level(logging.WARNING):
        assert retention.session_type("unknown") == retention.DEFAULT_SESSION_TYPE
    assert "unknown memory session type" in caplog.text


def test_rejected_repeat_refreshes_sidecar(tmp_path):
    save_session_insights(tmp_path, 1, {"what_worked": ["lesson"]})
    path = _sidecar(tmp_path)
    data = json.loads(path.read_text())
    data[_key("lesson")] = "2000-01-01T00:00:00+00:00"
    path.write_text(json.dumps(data))
    save_session_insights(tmp_path, 2, {"what_worked": ["lesson"]})
    assert json.loads(path.read_text())[_key("lesson")] != data[_key("lesson")]


def test_gotcha_cap_and_repeat_after_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_SESSION_TYPE", "planning")
    tool = _tool(tmp_path, monkeypatch, "record_gotcha")

    def call(value):
        return asyncio.run(tool({"gotcha": value}))["content"][0]["text"]

    for i in range(5):
        assert call(f"g{i}").startswith("Recorded gotcha")
    before = (tmp_path / "memory" / "gotchas.md").read_text()
    assert "cap reached" in call("new")
    assert (tmp_path / "memory" / "gotchas.md").read_text() == before
    path = _sidecar(tmp_path)
    data = json.loads(path.read_text())
    data[_key("g4")] = "2000-01-01T00:00:00+00:00"
    path.write_text(json.dumps(data))
    assert call("g4").startswith("Gotcha already recorded")
    assert json.loads(path.read_text())[_key("g4")] != data[_key("g4")]


def _entry(now, days, body):
    timestamp = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    return f"\n## [{timestamp}]\n{body}\n"


def test_fresh_demoted_expired_and_confirmation(tmp_path):
    now = datetime(2026, 11, 16, tzinfo=UTC)
    text = (
        "# Gotchas\n"
        + _entry(now, 2, "fresh")
        + _entry(now, 40, "old")
        + _entry(now, 100, "expired")
    )
    result = retention.gotchas_context(tmp_path, text, 1000, now)
    assert "fresh" in result and "old" in result
    assert "## Low confidence (not re-observed in 30 days)" in result
    assert "expired" not in result
    retention.confirm(tmp_path, ["old"], now)
    refreshed = retention.gotchas_context(tmp_path, text, 1000, now)
    assert "Low confidence" not in refreshed
    assert "old" in refreshed and "expired" not in refreshed


def test_context_unchanged_when_fresh_and_legacy(tmp_path):
    now = datetime(2026, 11, 16, tzinfo=UTC)
    fresh = "# Gotchas\n" + _entry(now, 1, "recent")
    assert retention.gotchas_context(tmp_path, fresh, 1000, now) == fresh
    legacy = "# Gotchas\n" + "legacy " * 200
    assert retention.gotchas_context(tmp_path, legacy, 1000, now) == legacy[-1000:]


def test_corrupt_sidecar_and_window_fallback(tmp_path, monkeypatch):
    now = datetime(2026, 11, 16, tzinfo=UTC)
    _sidecar(tmp_path).parent.mkdir()
    _sidecar(tmp_path).write_text("{broken")
    monkeypatch.setenv("MEMORY_DEMOTE_DAYS", "100")
    monkeypatch.setenv("MEMORY_EXPIRE_DAYS", "20")
    assert retention.windows() == (30, 90)
    text = _entry(now, 40, "old")
    assert "Low confidence" in retention.gotchas_context(tmp_path / "memory", text, 1000, now)
    retention.confirm(tmp_path / "memory", ["old"], now)
    assert _key("old") in json.loads(_sidecar(tmp_path).read_text())
    monkeypatch.setenv("MEMORY_DEMOTE_DAYS", "bad")
    assert retention.windows() == (30, 90)


def test_get_session_context_uses_retention(tmp_path, monkeypatch):
    tool = _tool(tmp_path, monkeypatch, "get_session_context")
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "gotchas.md").write_text("## [2000-01-01 00:00]\nexpired\n")
    result = asyncio.run(tool({}))["content"][0]["text"]
    assert "expired" not in result


def test_undated_preamble_survives_filtering(tmp_path):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    text = "- legacy bullet\n" + _entry(now, 40, "old")
    out = retention.gotchas_context(tmp_path, text, 1000, now)
    assert "- legacy bullet" in out and "Low confidence" in out


def test_gotcha_cap_is_per_spec_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_SESSION_TYPE", "planning")
    specs = [tmp_path / "a", tmp_path / "b"]
    for spec in specs:
        spec.mkdir()
    current = [specs[0]]
    tool = _tool(lambda: current[0], monkeypatch, "record_gotcha")
    for spec in specs:
        current[0] = spec
        for i in range(5):
            text = asyncio.run(tool({"gotcha": f"g{i}"}))["content"][0]["text"]
            assert text.startswith("Recorded gotcha")
