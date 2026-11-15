"""Tests for the memory novelty gate (exact-repeat filtering)."""

import asyncio
import copy
import json
import logging

import pytest

from agents import memory_manager
from agents.tools_pkg.tools import memory as memory_tools
from memory.novelty import drop_repeats
from memory.paths import get_session_insights_dir
from memory.sessions import save_session_insights

DISCOVERY = (
    "patterns_found",
    "gotchas_encountered",
    "patterns_discovered",
    "gotchas_discovered",
    "recommendations",
)
GATED = ["what_worked", "recommendations_for_next_session", *DISCOVERY]


def _insights(field=None, items=(), **extra):
    data = {
        "subtasks_completed": [],
        "discoveries": {
            "files_understood": {},
            "patterns_found": [],
            "gotchas_encountered": [],
        },
        "what_worked": [],
        "what_failed": [],
        "recommendations_for_next_session": [],
    }
    if field in DISCOVERY:
        data["discoveries"][field] = list(items)
    elif field:
        data[field] = list(items)
    data.update(extra)
    return data


def _get(saved, field):
    if field in DISCOVERY:
        return saved["discoveries"][field]
    return saved[field]


def _load(spec_dir, num):
    path = get_session_insights_dir(spec_dir) / f"session_{num:03d}.json"
    return json.loads(path.read_text())


# --- drop_repeats ---------------------------------------------------------


def test_drop_repeats_end_whitespace():
    assert drop_repeats(["a", "a \n"], set(), "t") == ["a"]


def test_drop_repeats_in_batch():
    assert drop_repeats(["a", "b", "a"], set(), "t") == ["a", "b"]


@pytest.mark.parametrize(
    "pair",
    [
        ("timeout < 30", "timeout > 30"),
        ("Do not enable caching", "Enable caching"),
        ("Use Foo", "use foo"),
        ('PREFIX="a b"', 'PREFIX="a  b"'),
    ],
)
def test_drop_repeats_keeps_distinct(pair):
    assert drop_repeats(list(pair), set(), "t") == list(pair)


def test_drop_repeats_non_string_kept():
    assert drop_repeats([1, None, "", "a"], set(), "t") == [1, None, "", "a"]


def test_drop_repeats_does_not_mutate_input():
    src = ["a", "a"]
    drop_repeats(src, set(), "t")
    assert src == ["a", "a"]


# --- save_session_insights -----------------------------------------------


@pytest.mark.parametrize("field", GATED)
def test_cross_session_repeat_dropped(tmp_path, field):
    save_session_insights(tmp_path, 1, _insights(field, ["x"]))
    save_session_insights(tmp_path, 2, _insights(field, ["x ", "y"]))
    assert _get(_load(tmp_path, 2), field) == ["y"]


@pytest.mark.parametrize("field", GATED)
def test_in_list_repeat_dropped(tmp_path, field):
    save_session_insights(tmp_path, 1, _insights(field, ["x", "x", "z"]))
    assert _get(_load(tmp_path, 1), field) == ["x", "z"]


def test_same_text_in_different_fields_kept(tmp_path):
    save_session_insights(tmp_path, 1, _insights("what_worked", ["x"]))
    save_session_insights(tmp_path, 2, _insights("patterns_found", ["x"]))
    assert _load(tmp_path, 2)["discoveries"]["patterns_found"] == ["x"]


def test_ungated_fields_kept(tmp_path):
    save_session_insights(
        tmp_path,
        1,
        _insights(
            what_failed=["f"],
            subtasks_completed=["s"],
            discoveries={"files_understood": {"a.py": "p"}},
        ),
    )
    save_session_insights(
        tmp_path,
        2,
        _insights(
            what_failed=["f"],
            subtasks_completed=["s"],
            discoveries={"files_understood": {"a.py": "p"}},
        ),
    )
    saved = _load(tmp_path, 2)
    assert saved["what_failed"] == ["f"]
    assert saved["subtasks_completed"] == ["s"]
    assert saved["discoveries"]["files_understood"] == {"a.py": "p"}


def test_missing_discovery_keys(tmp_path):
    save_session_insights(tmp_path, 1, _insights(discoveries={}))
    save_session_insights(tmp_path, 2, _insights(discoveries={"patterns_found": ["p"]}))
    saved = _load(tmp_path, 2)
    assert saved["discoveries"]["patterns_found"] == ["p"]
    assert "gotchas_encountered" not in saved["discoveries"]  # stored as given


def test_corrupt_prior_file_skipped(tmp_path):
    save_session_insights(tmp_path, 1, _insights("what_worked", ["x"]))
    (get_session_insights_dir(tmp_path) / "session_009.json").write_text("{not json")
    save_session_insights(tmp_path, 2, _insights("what_worked", ["x", "y"]))
    assert _load(tmp_path, 2)["what_worked"] == ["y"]


def test_caller_dict_not_mutated(tmp_path):
    save_session_insights(tmp_path, 1, _insights("patterns_found", ["x"]))
    data = _insights("patterns_found", ["x", "x"], what_worked=["w", "w"])
    before = copy.deepcopy(data)
    save_session_insights(tmp_path, 2, data)
    assert data == before


def test_resave_same_session_keeps_own_items(tmp_path):
    save_session_insights(tmp_path, 1, _insights("what_worked", ["x"]))
    save_session_insights(tmp_path, 1, _insights("what_worked", ["x"]))
    assert _load(tmp_path, 1)["what_worked"] == ["x"]


def test_rejection_logged(tmp_path, caplog):
    save_session_insights(tmp_path, 1, _insights("what_worked", ["x"]))
    with caplog.at_level(logging.INFO, logger="memory.novelty"):
        save_session_insights(tmp_path, 2, _insights("what_worked", ["x"]))
    assert any(r.name == "memory.novelty" and r.levelno == logging.INFO for r in caplog.records)


# --- record_gotcha --------------------------------------------------------


@pytest.fixture
def record_gotcha(tmp_path, monkeypatch):
    # conftest mocks claude_agent_sdk, so @tool is a MagicMock; make it a no-op.
    monkeypatch.setattr(memory_tools, "tool", lambda *_a, **_k: lambda f: f)
    monkeypatch.setattr(memory_tools, "SDK_TOOLS_AVAILABLE", True)
    fn = next(
        t
        for t in memory_tools.create_memory_tools(tmp_path, tmp_path)
        if t.__name__ == "record_gotcha"
    )

    def call(gotcha, context=""):
        res = asyncio.run(fn({"gotcha": gotcha, "context": context}))
        return res["content"][0]["text"]

    return call


def _gotchas(tmp_path):
    return (tmp_path / "memory" / "gotchas.md").read_text()


def test_gotcha_repeat_not_appended(tmp_path, record_gotcha, caplog):
    record_gotcha("watch out")
    before = _gotchas(tmp_path)
    with caplog.at_level(logging.INFO, logger="memory.novelty"):
        text = record_gotcha("watch out")
    assert text.startswith("Gotcha already recorded")
    assert _gotchas(tmp_path) == before
    assert any(r.name == "memory.novelty" for r in caplog.records)


def test_gotcha_new_appended(tmp_path, record_gotcha):
    record_gotcha("one")
    assert record_gotcha("two").startswith("Recorded gotcha")
    assert _gotchas(tmp_path).count("## [") == 2


def test_gotcha_different_context_appended(tmp_path, record_gotcha):
    record_gotcha("same", "ctx a")
    assert record_gotcha("same", "ctx b").startswith("Recorded gotcha")
    assert _gotchas(tmp_path).count("## [") == 2


def test_gotcha_repeat_outside_window_appended(tmp_path, record_gotcha):
    record_gotcha("old")
    for i in range(30):
        record_gotcha(f"filler {i} " + "z" * 50)
    assert len(_gotchas(tmp_path)) > memory_tools.GOTCHA_CONTEXT_CHARS
    assert record_gotcha("old").startswith("Recorded gotcha")
    assert _gotchas(tmp_path).count("\nold\n") == 2


def test_gotcha_equal_to_cut_entry_suffix_appended(tmp_path, record_gotcha):
    # The window cuts an entry so that what remains is exactly the candidate;
    # a cut entry must never count as a match.
    n = memory_tools.GOTCHA_CONTEXT_CHARS
    candidate = "c" * (n - 1)
    gotchas = tmp_path / "memory" / "gotchas.md"
    gotchas.parent.mkdir(parents=True, exist_ok=True)
    gotchas.write_text("## [2026-01-01 00:00]\nprefix " + candidate + "\n")
    assert gotchas.read_text()[-n:].strip() == candidate
    assert record_gotcha(candidate).startswith("Recorded gotcha")


# --- memory_manager -------------------------------------------------------


@pytest.mark.parametrize("success", [True, False])
def test_save_session_memory_markers(tmp_path, monkeypatch, success):
    monkeypatch.setattr(memory_manager, "is_graphiti_enabled", lambda: False)
    ok, backend = asyncio.run(
        memory_manager.save_session_memory(
            tmp_path, tmp_path, "subtask-1", 1, success, ["subtask-1"]
        )
    )
    assert (ok, backend) == (True, "file")
    saved = _load(tmp_path, 1)
    assert saved["what_worked"] == []
    assert saved["subtasks_completed"] == ["subtask-1"]
    if success:
        assert saved["what_failed"] == []
    else:
        assert saved["what_failed"] == ["Failed to complete subtask: subtask-1"]


# --- extractor-shaped discoveries and non-list values -------------------


def test_extractor_dict_entry_repeat_dropped(tmp_path):
    first = {"gotcha": "g", "trigger": "t", "solution": "s", "severity": "low"}
    reordered = {"severity": "low", "solution": "s", "trigger": "t", "gotcha": "g"}
    other = {**first, "severity": "high"}
    save_session_insights(tmp_path, 1, {"discoveries": {"gotchas_discovered": [first]}})
    save_session_insights(tmp_path, 2, {"discoveries": {"gotchas_discovered": [reordered, other]}})
    assert _load(tmp_path, 2)["discoveries"]["gotchas_discovered"] == [other]


def test_extractor_discoveries_keep_their_keys(tmp_path):
    extracted = {"file_insights": [{"path": "a.py"}], "recommendations": ["r"]}
    save_session_insights(tmp_path, 1, {"discoveries": extracted})
    assert _load(tmp_path, 1)["discoveries"] == extracted


def test_non_list_values_stored_as_given(tmp_path):
    save_session_insights(tmp_path, 1, {"what_worked": "run tests", "discoveries": None})
    saved = _load(tmp_path, 1)
    assert saved["what_worked"] == "run tests"
    assert saved["discoveries"] is None


def test_discoveries_key_named_like_top_level_field_not_gated(tmp_path):
    data = _insights("what_worked", ["x"])
    data["discoveries"]["what_worked"] = ["x"]
    save_session_insights(tmp_path, 1, data)
    saved = _load(tmp_path, 1)
    assert saved["what_worked"] == ["x"]
    assert saved["discoveries"]["what_worked"] == ["x"]


def test_gotcha_with_non_timestamp_header_line_not_split(tmp_path, record_gotcha):
    record_gotcha("See:\n## [config]\nfoo")
    assert record_gotcha("foo").startswith("Recorded gotcha")
    assert record_gotcha("See:\n## [config]\nfoo").startswith("Gotcha already recorded")
