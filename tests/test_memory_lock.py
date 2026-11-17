"""Cross-process memory lock (RFC-0021 follow-up, #3796)."""

import contextlib
import multiprocessing
from pathlib import Path

import pytest
from memory import gates, locking, patterns, retention, scratchpad

CTX = multiprocessing.get_context("fork")
ALPHA = "abcdefghij"


def _text(tag: str, i: int) -> str:
    return tag + " " + "".join(ALPHA[int(d)] for d in str(i))  # digits are normalised away


def _worker(memory_dir: str, tag: str, count: int) -> None:
    for i in range(count):
        scratchpad.observe(Path(memory_dir), "error_fingerprint", _text(tag, i))
        retention.confirm(Path(memory_dir), [_text(tag, i)])


def _holder(memory_dir: str, ready, release) -> None:
    with locking.locked(Path(memory_dir)):
        ready.set()
        release.wait(10)


@pytest.fixture
def busy(tmp_path, monkeypatch):
    """A directory another process holds the lock on; the wait is short."""
    monkeypatch.setattr(locking, "WAIT", 0.2)
    ready, release = CTX.Event(), CTX.Event()
    proc = CTX.Process(target=_holder, args=(str(tmp_path), ready, release))
    proc.start()
    assert ready.wait(10)
    yield tmp_path, release
    release.set()
    proc.join(10)


def test_two_processes_lose_nothing(tmp_path):
    procs = [CTX.Process(target=_worker, args=(str(tmp_path), t, 25)) for t in ("aa", "bb")]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    assert len(scratchpad.read(tmp_path)["error_fingerprint"]) == 50
    assert len(retention._read(tmp_path)) == 50


def test_busy_lock_means_no_write(busy):
    memory_dir, _ = busy
    assert scratchpad.observe(memory_dir, "error_fingerprint", "boom") is False
    assert scratchpad.record_dead_end(memory_dir, "approach") is False
    retention.confirm(memory_dir, ["x"])
    assert not (memory_dir / "scratchpad.json").exists()
    assert not (memory_dir / "confirmed.json").exists()


def test_busy_lock_blocks_promote(busy):
    memory_dir, release = busy
    store = {
        "error_fingerprint": {
            "k": {
                "text": "boom",
                "n": 3,
                "bad": 0,
                "ab": 0,
                "last": "2026-11-17T00:00:00+00:00",
            }
        }
    }
    scratchpad.write(memory_dir, store)  # direct write: the test seeds the store
    assert gates.promote(memory_dir) == []
    assert not (memory_dir / "gotchas.md").exists()
    release.set()
    assert gates.promote(memory_dir)  # free again: the same call now writes


def test_append_gotcha_skips_when_its_dir_is_locked(tmp_path, monkeypatch):
    monkeypatch.setattr(locking, "WAIT", 0.1)
    ready, release = CTX.Event(), CTX.Event()
    proc = CTX.Process(target=_holder, args=(str(tmp_path / "memory"), ready, release))
    (tmp_path / "memory").mkdir()
    proc.start()
    assert ready.wait(10)
    patterns.append_gotcha(tmp_path, "held")
    assert not (tmp_path / "memory" / "gotchas.md").exists()
    release.set()
    proc.join(10)
    patterns.append_gotcha(tmp_path, "free")
    assert "- free" in (tmp_path / "memory" / "gotchas.md").read_text()


def test_mutation_without_the_lock_a_busy_directory_is_written(busy, monkeypatch):
    memory_dir, _ = busy
    monkeypatch.setattr(scratchpad, "locked", lambda _d: contextlib.nullcontext())
    assert scratchpad.observe(memory_dir, "error_fingerprint", "boom") is True


def test_lock_is_reentrant_in_one_thread(tmp_path):
    with locking.locked(tmp_path), locking.locked(tmp_path):
        assert scratchpad.observe(tmp_path, "error_fingerprint", "boom")
