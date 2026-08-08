"""Tests for the stdlib .env → os.environ loader (env_bootstrap)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_WS = Path(__file__).resolve().parents[1]
if str(_WS) not in sys.path:
    sys.path.insert(0, str(_WS))

from server.env_bootstrap import _load  # noqa: E402


def test_load_populates_and_respects_existing(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        "PFACTORY_COMPLETION_WEBHOOK=http://localhost:3111/api/events\n"
        'QUOTED="value with spaces"\n'
        "ALREADY_SET=from_file\n"
        "malformed line without equals\n"
    )
    monkeypatch.delenv("PFACTORY_COMPLETION_WEBHOOK", raising=False)
    monkeypatch.setenv("ALREADY_SET", "from_env")  # real env must win

    _load(env)

    assert os.environ["PFACTORY_COMPLETION_WEBHOOK"] == "http://localhost:3111/api/events"
    assert os.environ["QUOTED"] == "value with spaces"
    assert os.environ["ALREADY_SET"] == "from_env"  # setdefault: not overridden


def test_load_missing_file_is_noop(tmp_path):
    _load(tmp_path / "does-not-exist.env")  # must not raise
