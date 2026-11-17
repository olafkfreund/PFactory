"""Caps and the unverified rule on the Graphiti write path (RFC-0021, #3797)."""

import asyncio
import sys
import types

import pytest
from memory import graphiti_helpers, patterns, retention, trust


class FakeGraphiti:
    is_enabled = True

    def __init__(self):
        self.gotchas, self.patterns, self.sessions, self.structured = [], [], [], []

    async def save_gotcha(self, text):
        self.gotchas.append(text)

    async def save_pattern(self, text):
        self.patterns.append(text)

    async def save_session_insights(self, num, insights):
        self.sessions.append(insights)
        return True

    async def save_structured_insights(self, discoveries):
        self.structured.append(discoveries)
        return True

    async def save_codebase_discoveries(self, files):
        return True

    async def close(self):
        return None


@pytest.fixture(autouse=True)
def _clean_trust(monkeypatch):
    monkeypatch.setenv("MEMORY_SESSION_TYPE", "build")  # repos differ in their default
    trust._state.clear()
    patterns._sent.clear()
    yield
    trust._state.clear()


def _insights(n, tainted_gotchas=False):
    return {
        "subtasks_completed": ["s1"],
        "discoveries": {
            "files_understood": {f"f{i}": "p" for i in range(n)},
            "patterns_found": [f"p{i}" for i in range(n)],
            "gotchas_encountered": [f"g{i}" for i in range(n)],
        },
        "what_worked": [f"w{i}" for i in range(n)],
        "what_failed": [f"x{i}" for i in range(n)],
        "recommendations_for_next_session": [f"r{i}" for i in range(n)],
    }


def _count(p):
    d = p["discoveries"]
    return (
        len(d["files_understood"])
        + len(d["patterns_found"])
        + len(d["gotchas_encountered"])
        + len(p["what_worked"])
        + len(p["what_failed"])
        + len(p["recommendations_for_next_session"])
    )


def test_gate_applies_the_session_cap_and_leaves_the_input_alone():
    original = _insights(30)
    out = graphiti_helpers.gate_for_graphiti(original, "build")
    assert _count(out) == retention.CAPS["build"]
    assert _count(original) == 180
    assert _count(graphiti_helpers.gate_for_graphiti(original, "docs")) == 0


def test_gate_is_idempotent():
    once = graphiti_helpers.gate_for_graphiti(_insights(30), "build")
    assert graphiti_helpers.gate_for_graphiti(once, "build") == once


def test_gotchas_are_marked_only_while_the_trust_window_is_open():
    clean = graphiti_helpers.gate_for_graphiti(_insights(2), "build")
    assert not any(trust.UNVERIFIED in g for g in clean["discoveries"]["gotchas_encountered"])
    trust.taint()
    marked = graphiti_helpers.gate_for_graphiti(_insights(2), "build")
    assert all(g.endswith(trust.UNVERIFIED) for g in marked["discoveries"]["gotchas_encountered"])
    twice = graphiti_helpers.gate_for_graphiti(marked, "build")
    assert twice["discoveries"]["gotchas_encountered"][0].count(trust.UNVERIFIED) == 1
    assert marked["discoveries"]["patterns_found"] == [
        "p0",
        "p1",
    ]  # patterns are not marked


def test_structured_gotcha_dicts_are_marked_and_keep_their_fields():
    trust.taint()
    item = {"gotcha": "g", "trigger": "t", "solution": "s"}
    out = graphiti_helpers.gate_for_graphiti({"gotchas_discovered": [item]}, "build")
    got = out["gotchas_discovered"][0]
    assert got["gotcha"] == "g" + trust.UNVERIFIED
    assert (got["trigger"], got["solution"]) == ("t", "s")


def test_a_repeated_gotcha_is_not_written_twice_while_tainted(monkeypatch, tmp_path):
    trust.taint()
    fake = FakeGraphiti()
    monkeypatch.setattr(patterns, "is_graphiti_memory_enabled", lambda: True)
    monkeypatch.setattr(patterns, "get_graphiti_memory", lambda *_a, **_k: fake)
    monkeypatch.setattr(patterns, "run_async", asyncio.run)
    patterns.append_gotcha(tmp_path, "same")
    patterns.append_gotcha(tmp_path, "same")
    assert len(fake.gotchas) == 1
    assert (tmp_path / "memory" / "gotchas.md").read_text().count("- same") == 1


def _save(monkeypatch, fake):
    monkeypatch.setattr(graphiti_helpers, "get_graphiti_memory", lambda *_a, **_k: fake)
    return asyncio.run(graphiti_helpers.save_to_graphiti_async(None, 1, _insights(30)))


def test_save_to_graphiti_sends_no_more_than_the_cap(monkeypatch):
    fake = FakeGraphiti()
    _save(monkeypatch, fake)
    assert _count(fake.sessions[0]) == retention.CAPS["build"]


def test_mutation_gate_removed_sends_everything(monkeypatch):
    monkeypatch.setattr(graphiti_helpers, "gate_for_graphiti", lambda p, s=None: p)
    fake = FakeGraphiti()
    _save(monkeypatch, fake)
    assert _count(fake.sessions[0]) == 180


def _append(monkeypatch, tmp_path, fake, count):
    monkeypatch.setattr(patterns, "is_graphiti_memory_enabled", lambda: True)
    monkeypatch.setattr(patterns, "get_graphiti_memory", lambda *_a, **_k: fake)
    monkeypatch.setattr(patterns, "run_async", asyncio.run)
    for i in range(count):
        patterns.append_gotcha(tmp_path, f"gotcha {chr(97 + i)}")


def test_append_gotcha_caps_graphiti_but_not_the_file(monkeypatch, tmp_path):
    fake = FakeGraphiti()
    _append(monkeypatch, tmp_path, fake, 22)
    assert len(fake.gotchas) == retention.CAPS["build"]
    assert len(patterns.load_gotchas(tmp_path)) == 22


def test_append_gotcha_marks_file_and_graphiti_while_tainted(monkeypatch, tmp_path):
    trust.taint()
    fake = FakeGraphiti()
    _append(monkeypatch, tmp_path, fake, 1)
    assert fake.gotchas[0].endswith(trust.UNVERIFIED)
    assert "unverified" in (tmp_path / "memory" / "gotchas.md").read_text()


def test_mutation_append_counter_removed_sends_everything(monkeypatch, tmp_path):
    class Forgetful(dict):
        def get(self, key, default=0):
            return 0

    monkeypatch.setattr(patterns, "_sent", Forgetful())
    fake = FakeGraphiti()
    _append(monkeypatch, tmp_path, fake, 22)
    assert len(fake.gotchas) == 22


def test_memory_manager_gates_both_graphiti_saves(monkeypatch, tmp_path):
    from agents import memory_manager

    fake = FakeGraphiti()
    stub = types.ModuleType("integrations.graphiti.memory")
    stub.GraphitiMemory = lambda *_a, **_k: fake
    for name in ("integrations.graphiti.memory", "graphiti_memory"):  # repos differ
        monkeypatch.setitem(sys.modules, name, stub)
    monkeypatch.setattr(memory_manager, "is_graphiti_enabled", lambda: True)
    rich = {
        "file_insights": [{"f": i} for i in range(40)],
        "gotchas_discovered": ["g"] * 5,
    }
    for found in (rich, None):
        asyncio.run(
            memory_manager.save_session_memory(
                tmp_path,
                tmp_path,
                "s1",
                1,
                True,
                ["s1"],
                discoveries=found or _insights(30)["discoveries"],
            )
        )
    structured = fake.structured[0]
    assert len(structured["file_insights"]) + len(structured["gotchas_discovered"]) == 20
    assert _count(fake.sessions[0]) == retention.CAPS["build"]
