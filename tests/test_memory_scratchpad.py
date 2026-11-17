"""Scratchpad observation layer and dead_end recall (RFC-0021 Phase 3)."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from agents import utils as agent_utils
from agents.tools_pkg.tools import memory as memory_tools
from memory import scratchpad
from memory.hooks import make_failure_hook
from memory.sessions import save_session_insights

BACKEND = Path(__file__).resolve().parents[1] / "apps" / "backend"
POOL = hasattr(agent_utils, "seed_memory_from_project")
NOW = datetime(2026, 11, 17, tzinfo=UTC)


def _tools(spec_dir, monkeypatch):
    monkeypatch.setattr(memory_tools, "tool", lambda *_args, **_kw: lambda f: f)
    monkeypatch.setattr(memory_tools, "SDK_TOOLS_AVAILABLE", True)
    return {t.__name__: t for t in memory_tools.create_memory_tools(spec_dir, spec_dir)}


def _call(tool, **args):
    result = asyncio.run(tool(args))
    return result["content"][0]["text"]


def test_normalise_and_key_ignore_case_space_digits():
    assert scratchpad.fingerprint("Error  42 at LINE 7") == scratchpad.fingerprint(
        "error 9 at line 1"
    )
    assert scratchpad.fingerprint("a") != scratchpad.fingerprint("b")


def test_observe_counts_and_ignores_unknown_kind(tmp_path):
    for _ in range(3):
        assert scratchpad.observe(tmp_path, "error_fingerprint", "boom 1")
    assert not scratchpad.observe(tmp_path, "made_up", "x")
    entries = scratchpad.read(tmp_path)["error_fingerprint"]
    assert [e["n"] for e in entries.values()] == [3]
    assert "made_up" not in scratchpad.read(tmp_path)


def test_key_cap_evicts_the_weakest_and_payload_is_bounded(tmp_path):
    for _ in range(5):
        scratchpad.observe(tmp_path, "error_fingerprint", "keeper")
    for i in range(scratchpad.MAX_KEYS + 20):
        scratchpad.observe(tmp_path, "error_fingerprint", f"noise {chr(97 + i % 26)}" * 1 + "z" * i)
    entries = scratchpad.read(tmp_path)["error_fingerprint"]
    assert len(entries) == scratchpad.MAX_KEYS
    assert scratchpad.fingerprint("keeper") in entries
    assert all(len(e["text"]) <= scratchpad.TEXT_LIMIT for e in entries.values())


def test_corrupt_store_reads_empty_and_recovers(tmp_path):
    (tmp_path / "scratchpad.json").write_text("{not json")
    assert scratchpad.read(tmp_path) == {}
    assert scratchpad.observe(tmp_path, "error_fingerprint", "boom")
    assert scratchpad.read(tmp_path)["error_fingerprint"]


def test_dead_end_tool_records_repeats_and_caps(tmp_path, monkeypatch):
    tool = _tools(tmp_path, monkeypatch)["record_dead_end"]
    assert "Recorded" in _call(tool, approach="use sed on yaml", reason="breaks anchors")
    assert "Recorded" in _call(tool, approach="use sed on yaml", reason="again")
    mem = tmp_path / "memory"
    (entry,) = scratchpad.read(mem)["dead_end"].values()
    assert entry["n"] == 2
    monkeypatch.setenv("MEMORY_SESSION_TYPE", "docs")  # cap 0
    assert "cap reached" in _call(tool, approach="a brand new approach")
    assert "Recorded" in _call(tool, approach="use sed on yaml")  # a repeat is free


def test_repeated_failure_recall_in_a_later_session(tmp_path, monkeypatch):
    first = _tools(tmp_path, monkeypatch)
    _call(first["record_dead_end"], approach="mock the db driver", reason="hides the bug")
    later = _tools(tmp_path, monkeypatch)  # a fresh session, same spec memory
    context = _call(later["get_session_context"])
    assert "Dead ends (already tried, do not retry)" in context
    assert "mock the db driver" in context and "hides the bug" in context


@pytest.mark.skipif(not POOL, reason="no project pool in this repo")
def test_dead_end_reaches_the_next_spec_of_the_same_project(tmp_path, monkeypatch):
    project = tmp_path / "project"
    source = project / ".aifactory" / "specs" / "001"
    source.mkdir(parents=True)
    spec_a = project / "wt-a" / "specs" / "001"
    spec_a.mkdir(parents=True)
    _call(_tools(spec_a, monkeypatch)["record_dead_end"], approach="regex the html")
    assert agent_utils.sync_memory_to_project(spec_a, source)
    spec_b = project / "wt-b" / "specs" / "002"
    spec_b.mkdir(parents=True)
    assert agent_utils.seed_memory_from_project(project, spec_b)
    assert "regex the html" in _call(_tools(spec_b, monkeypatch)["get_session_context"])


@pytest.mark.skipif(not POOL, reason="no project pool in this repo")
def test_pool_never_reads_another_project(tmp_path, monkeypatch):
    """The condition on the tenant decision: the pool path derives from the
    project directory only; project B's dead ends never reach project A."""
    from memory.paths import project_memory_dir

    a, b = tmp_path / "projA", tmp_path / "projB"
    assert project_memory_dir(a) == a / ".aifactory" / "memory"
    scratchpad.record_dead_end(project_memory_dir(b), "secret approach of project B")
    spec = a / "wt" / "specs" / "001"
    spec.mkdir(parents=True)
    agent_utils.seed_memory_from_project(a, spec)
    assert scratchpad.read(spec / "memory") == {}
    context = _call(_tools(spec, monkeypatch)["get_session_context"])
    assert "project b" not in context


def test_spec_memory_never_reads_a_sibling_project(tmp_path, monkeypatch):
    for name in ("projA", "projB"):
        scratchpad.record_dead_end(tmp_path / name / "memory", f"approach of {name}")
    context = _call(_tools(tmp_path / "projA", monkeypatch)["get_session_context"])
    assert "approach of proja" in context and "projb" not in context


def test_wholly_untrusted_dead_end_is_not_recalled(tmp_path):
    scratchpad.record_dead_end(tmp_path, "from a web page", untrusted=True)
    scratchpad.record_dead_end(tmp_path, "mixed", untrusted=True)
    scratchpad.record_dead_end(tmp_path, "mixed", untrusted=False)
    context = scratchpad.dead_end_context(tmp_path)
    assert "from a web page" not in context and "mixed" in context


def test_old_dead_end_expires_and_limit_keeps_whole_lines(tmp_path):
    scratchpad.record_dead_end(tmp_path, "ancient", now=NOW - timedelta(days=200))
    for i in range(30):
        scratchpad.record_dead_end(tmp_path, f"approach {'x' * 40} {chr(97 + i % 26)}", now=NOW)
    out = scratchpad.dead_end_context(tmp_path, limit=300, now=NOW)
    assert "ancient" not in out and len(out) <= 300 and out.endswith(")\n")


def test_dead_end_marks_recent_signals_as_abandoned(tmp_path):
    scratchpad.observe(tmp_path, "error_fingerprint", "just before", now=NOW - timedelta(minutes=5))
    scratchpad.observe(tmp_path, "error_fingerprint", "long ago", now=NOW - timedelta(hours=3))
    scratchpad.record_dead_end(tmp_path, "the approach", now=NOW)
    sigs = scratchpad.read(tmp_path)["error_fingerprint"]
    assert sigs[scratchpad.fingerprint("just before")]["ab"] == 1
    assert sigs[scratchpad.fingerprint("long ago")]["ab"] == 0


def test_failure_hook_observes_ignores_interrupts_and_never_raises(tmp_path):
    hook = make_failure_hook(tmp_path)
    assert asyncio.run(hook({"tool_name": "Bash", "error": "npm ERR 12"}, None, None)) == {}
    assert (
        asyncio.run(hook({"tool_name": "Bash", "error": "x", "is_interrupt": True}, None, None))
        == {}
    )
    assert asyncio.run(hook(None, None, None)) == {}  # type: ignore[arg-type]
    (entry,) = scratchpad.read(tmp_path)["error_fingerprint"].values()
    assert entry["text"] == "bash: npm err #" and entry["n"] == 1


def test_client_registers_the_failure_hook():
    assert "make_failure_hook(" in (BACKEND / "core" / "client.py").read_text()


def _dir_bytes(path):
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def test_store_growth_is_bounded_and_measured(tmp_path):
    sizes = {}
    for session in range(1, 21):
        scratchpad.observe(tmp_path / "memory", "error_fingerprint", "same recurring failure")
        scratchpad.observe(tmp_path / "memory", "error_fingerprint", "noise " + "z" * session)
        save_session_insights(tmp_path, session, {"what_worked": [f"w {'q' * session}"]})
        if session in (1, 5, 20):
            sizes[session] = (
                _dir_bytes(tmp_path / "memory"),
                (tmp_path / "memory" / "scratchpad.json").stat().st_size,
            )
    print(f"growth measure (memory dir bytes, scratchpad bytes) by session: {sizes}")
    data = json.loads((tmp_path / "memory" / "scratchpad.json").read_text())
    assert all(len(v) <= scratchpad.MAX_KEYS for v in data.values())
    assert sizes[20][1] < 20_000
