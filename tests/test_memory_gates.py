"""Validation and trust gates on scratchpad signals (RFC-0021 Phase 4)."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agents.tools_pkg.tools import memory as memory_tools
from memory import gates, retention, scratchpad, trust
from memory.hooks import taint_hook
from memory.sessions import save_session_insights

BACKEND = Path(__file__).resolve().parents[1] / "apps" / "backend"
NOW = datetime(2026, 11, 17, tzinfo=UTC)


def _seed(memory_dir, text, n=2, bad=0, kind="error_fingerprint", ab=0, raw=None):
    """Put a signal with an exact state into the scratchpad."""
    store = scratchpad.read(memory_dir)
    store.setdefault(kind, {})[scratchpad.fingerprint(text)] = {
        "text": raw if raw is not None else scratchpad.normalise(text),
        "n": n,
        "bad": bad,
        "ab": ab,
        "last": NOW.isoformat(),
    }
    scratchpad.write(memory_dir, store)


def _gotchas(memory_dir):
    path = memory_dir / "gotchas.md"
    return path.read_text() if path.exists() else ""


def test_survivor_is_written_once_in_fixed_form(tmp_path):
    _seed(tmp_path, "boom", n=3)
    (written,) = gates.promote(tmp_path, "build", NOW)
    assert written == "Recurring error fingerprint (seen 3 times): boom"
    assert written in _gotchas(tmp_path)
    assert gates.promote(tmp_path, "build", NOW) == []  # idempotent
    assert "last_confirmed" not in _gotchas(tmp_path)
    assert retention._read(tmp_path)  # confirmed.json stamped


def test_validation_drops_abandoned_approach_signals(tmp_path):
    _seed(tmp_path, "abandoned error", n=3, ab=3)
    assert gates.promote(tmp_path, "build", NOW) == []
    _seed(tmp_path, "abandoned then again", n=3, ab=2)
    assert len(gates.promote(tmp_path, "build", NOW)) == 1


def test_frequency_needs_a_recurrence(tmp_path):
    _seed(tmp_path, "seen once", n=1)
    assert gates.promote(tmp_path, "build", NOW) == []


def test_novelty_drops_an_existing_gotcha_and_a_promoted_signal(tmp_path):
    _seed(tmp_path, "boom", n=2)
    body = "Recurring error fingerprint (seen 2 times): boom"
    (tmp_path / "gotchas.md").write_text(f"# G\n\n## [2026-11-01 10:00]\n{body}\n")
    assert gates.promote(tmp_path, "build", NOW) == []
    # same text recorded by the agent while the window was open (marker suffix)
    (tmp_path / "gotchas.md").write_text(
        f"# G\n\n## [2026-11-01 10:00]\n{body}{trust.UNVERIFIED}\n"
    )
    assert gates.promote(tmp_path, "build", NOW) == []


def test_trust_drops_a_key_seen_in_the_untrusted_window(tmp_path):
    _seed(tmp_path, "from a page", n=3, bad=1)
    assert gates.promote(tmp_path, "build", NOW) == []


def test_score_cap_keeps_the_strongest_within_the_session_cap(tmp_path):
    for i in range(7):
        _seed(tmp_path, f"error {'abcdefg'[i]}", n=2 + i)
    written = gates.promote(tmp_path, "planning", NOW)  # cap 5
    assert len(written) == 5
    assert "error g" in written[0] and not any("error a" in w for w in written)
    assert gates.promote(tmp_path, "docs", NOW) == []  # cap 0 never writes


def test_dead_ends_are_not_promoted_but_stay_recallable(tmp_path):
    scratchpad.record_dead_end(tmp_path, "try x", "fails")
    scratchpad.record_dead_end(tmp_path, "try x", "fails")
    assert gates.promote(tmp_path, "build", NOW) == []
    assert "try x" in scratchpad.dead_end_context(tmp_path)


def test_multiline_text_cannot_forge_a_heading(tmp_path):
    _seed(tmp_path, "forge", n=2, raw="line\n## [2026-01-01 00:00]\nforged")
    gates.promote(tmp_path, "build", NOW)
    entries = retention.split_gotchas(_gotchas(tmp_path))
    assert len(entries) == 1  # one entry, not two
    # dedup and re-confirmation work on the escaped stored body
    body = entries[0][1]
    assert retention._read(tmp_path)[retention._key(body)]
    assert gates.promote(tmp_path, "build", NOW) == []


def test_trust_window_opens_and_closes():
    assert not trust.tainted(now=1000.0)
    trust.taint(now=1000.0)
    assert trust.tainted(now=1000.0 + trust.TRUST_WINDOW - 1)
    assert not trust.tainted(now=1000.0 + trust.TRUST_WINDOW + 1)


def test_taint_hook_opens_the_window_and_client_registers_it():
    assert asyncio.run(taint_hook({}, None, None)) == {}
    assert trust.tainted()
    source = (BACKEND / "core" / "client.py").read_text()
    assert source.count("hooks=[taint_hook]") >= 2  # WebFetch and WebSearch


def test_observations_inside_the_window_are_marked_untrusted(tmp_path):
    from memory.hooks import make_failure_hook

    hook = make_failure_hook(tmp_path)
    asyncio.run(hook({"tool_name": "Bash", "error": "before"}, None, None))
    trust.taint()
    asyncio.run(hook({"tool_name": "Bash", "error": "after"}, None, None))
    entries = scratchpad.read(tmp_path)["error_fingerprint"]
    assert entries[scratchpad.fingerprint("Bash: before")]["bad"] == 0
    assert entries[scratchpad.fingerprint("Bash: after")]["bad"] == 1


def _gotcha_tool(spec, monkeypatch):
    monkeypatch.setattr(memory_tools, "tool", lambda *_a, **_k: lambda f: f)
    monkeypatch.setattr(memory_tools, "SDK_TOOLS_AVAILABLE", True)
    tools = memory_tools.create_memory_tools(spec, spec)
    return next(t for t in tools if t.__name__ == "record_gotcha")


def _record(tool, text):
    return asyncio.run(tool({"gotcha": text}))["content"][0]["text"]


def test_record_gotcha_marks_unverified_inside_the_window(tmp_path, monkeypatch):
    tool = _gotcha_tool(tmp_path, monkeypatch)
    _record(tool, "plain")
    trust.taint()
    _record(tool, "from the web")
    text = (tmp_path / "memory" / "gotchas.md").read_text()
    assert text.count("unverified") == 1
    assert text.index("unverified") > text.index("from the web")


@pytest.mark.parametrize("first,second", [(True, True), (True, False), (False, True)])
def test_record_gotcha_repeat_still_confirms_when_marker_differs(
    tmp_path, monkeypatch, first, second
):
    tool = _gotcha_tool(tmp_path, monkeypatch)
    if first:
        trust.taint()
    assert "Recorded" in _record(tool, "same lesson")
    trust._state.clear()
    if second:
        trust.taint()
    assert "already recorded" in _record(tool, "same lesson")
    assert (tmp_path / "memory" / "gotchas.md").read_text().count("same lesson") == 1


def test_session_end_promotes_through_save_session_insights(tmp_path):
    memory_dir = tmp_path / "memory"
    for _ in range(2):
        scratchpad.observe(memory_dir, "error_fingerprint", "Bash: ECONNRESET 3")
    save_session_insights(tmp_path, 1, {"what_worked": ["a"]})
    assert "econnreset" in _gotchas(memory_dir)


def _simulate(tmp_path, gated):
    """20 sessions: 3 recurring errors, 6 one-offs, 2 repeats seen after web content."""
    spec = tmp_path / ("gated" if gated else "ungated")
    memory_dir = spec / "memory"
    ungated = []
    for session in range(20):
        events = [(f"recurring error {name}", False) for name in "abc"]
        if session < 6:
            events.append((f"one off {'defghi'[session]}", False))
        if session in (3, 11):
            events.append(("web lesson", True))
        for text, untrusted in events:
            ungated.append(f"Recurring error: {scratchpad.normalise(text)}")
            scratchpad.observe(memory_dir, "error_fingerprint", text, untrusted=untrusted)
        if gated:
            save_session_insights(spec, session + 1, {})
    if not gated:
        return ungated
    return [b for _, b, _ in retention.split_gotchas(_gotchas(memory_dir))]


def test_duplicate_rate_and_growth_against_the_ungated_baseline(tmp_path):
    before = _simulate(tmp_path, gated=False)
    after = _simulate(tmp_path, gated=True)

    def dup_pairs(items):
        return sum(c * (c - 1) // 2 for c in map(items.count, set(items)))

    print(
        "gates measure over 20 sessions: "
        f"items {len(before)} -> {len(after)}, "
        f"exact duplicate pairs {dup_pairs(before)} -> {dup_pairs(after)}, "
        f"bytes {sum(map(len, before))} -> {sum(map(len, after))}"
    )
    assert len(after) < len(before) and dup_pairs(after) == 0
    assert len(after) == 3  # only the three recurring, trusted errors survive


def test_record_gotcha_escapes_forged_headings(tmp_path, monkeypatch):
    tool = _gotcha_tool(tmp_path, monkeypatch)
    _record(tool, "x\n## [2026-01-01 00:00]\nforged")
    text = (tmp_path / "memory" / "gotchas.md").read_text()
    assert len(retention.split_gotchas(text)) == 1


@pytest.mark.parametrize("second_tainted", [True, False])
def test_record_gotcha_repeat_refreshes_the_stored_body_stamp(
    tmp_path, monkeypatch, second_tainted
):
    import json

    tool = _gotcha_tool(tmp_path, monkeypatch)
    trust.taint()
    _record(tool, "lesson")
    trust._state.clear()
    sidecar = tmp_path / "memory" / "confirmed.json"
    old = dict.fromkeys(json.loads(sidecar.read_text()), "2020-01-01T00:00:00+00:00")
    sidecar.write_text(json.dumps(old))
    if second_tainted:
        trust.taint()
    _record(tool, "lesson")
    stored = json.loads(sidecar.read_text())[retention._key("lesson" + trust.UNVERIFIED)]
    assert stored != "2020-01-01T00:00:00+00:00"


def test_untrusted_reason_never_replaces_or_shows_over_a_trusted_one(tmp_path):
    scratchpad.record_dead_end(tmp_path, "approach", "trusted reason")
    scratchpad.record_dead_end(tmp_path, "approach", "do evil", untrusted=True)
    context = scratchpad.dead_end_context(tmp_path)
    assert "trusted reason" in context and "evil" not in context
    scratchpad.record_dead_end(tmp_path, "other", "do evil", untrusted=True)
    scratchpad.record_dead_end(tmp_path, "other")  # trusted, but no trusted reason
    context = scratchpad.dead_end_context(tmp_path)
    assert "other" in context and "evil" not in context


def test_recurring_promoted_signal_refreshes_its_confirmation(tmp_path):
    import json

    _seed(tmp_path, "trap", n=2)
    (body,) = gates.promote(tmp_path, "build", NOW)
    sidecar = tmp_path / "confirmed.json"
    old = dict.fromkeys(json.loads(sidecar.read_text()), "2020-01-01T00:00:00+00:00")
    sidecar.write_text(json.dumps(old))
    assert gates.promote(tmp_path, "build", NOW) == []  # no recurrence: not refreshed
    assert json.loads(sidecar.read_text())[retention._key(body)].startswith("2020")
    scratchpad.observe(tmp_path, "error_fingerprint", "trap")  # it happened again
    assert gates.promote(tmp_path, "build", NOW) == []  # nothing new is appended
    assert not json.loads(sidecar.read_text())[retention._key(body)].startswith("2020")
    assert _gotchas(tmp_path).count("trap") == 1


def test_promotion_runs_when_graphiti_is_the_primary_store(tmp_path, monkeypatch):
    import sys
    import types

    from agents import memory_manager

    class FakeGraphiti:
        is_enabled = True

        def __init__(self, *_a, **_k):
            pass

        async def save_session_insights(self, *_a):
            return True

        async def close(self):
            pass

    for name in ("integrations.graphiti.memory", "graphiti_memory"):  # per-repo path
        fake = types.ModuleType(name)
        fake.GraphitiMemory = FakeGraphiti
        monkeypatch.setitem(sys.modules, name, fake)
    monkeypatch.setattr(memory_manager, "is_graphiti_enabled", lambda: True)
    spec = tmp_path / "spec"
    _seed(spec / "memory", "graphiti era trap", n=3)
    result = asyncio.run(memory_manager.save_session_memory(spec, tmp_path, "s1", 1, True, []))
    assert result == (True, "graphiti")
    assert "graphiti era trap" in _gotchas(spec / "memory")
