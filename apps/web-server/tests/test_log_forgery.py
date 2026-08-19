"""A log record must not be forgeable through its own message (AIFactory#1320).

The portal serves a log viewer: ``routes/logs.py`` calls ``get_recent_logs``,
which reads the rotating log FILES and hands each line to ``parse_log_line``.
That parser used to split on ``" | "`` and trust the pieces, so the viewer's
timestamp, level and logger name came straight from the line's own text.

Any attacker-controlled string that reaches a log message could therefore
fabricate an entry: a filename, a URL, a subprocess's stderr, or an exception's
own text. The exception path was the one that could not be escaped away --
``error_reference`` sanitises the message, then passes the same exception to
``exc_info``, and the logging module renders ``str(exc)`` itself, never through
our sanitizer. So the raw text was always emitted alongside the escaped copy.

The fix is structural rather than another escape: records are written as one
JSON object per line. A newline inside a JSON string is encoded as ``\\n``, so
no field value can start a new line, and one record is one line by
construction.

These tests drive the REAL pipeline -- ``setup_logging`` writes to a temp log
dir, ``error_reference`` emits, and the assertions read the file back through
``parse_log_line``, which is exactly what the viewer does.

Mutation check: put the ``" | "`` formatter back in ``setup_logging`` and
``test_a_newline_cannot_fabricate_a_viewer_entry`` goes red with a CRITICAL
entry nobody logged.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from server import logging_config
from server.error_ref import error_reference
from server.logging_config import get_recent_logs, parse_log_line, setup_logging

#: A whole log line, in the exact shape the old parser trusted.
FORGED = "2026-01-01 00:00:00 | CRITICAL | server.audit:1 | admin deleted all projects"


@pytest.fixture
def log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Configure the real logging stack against a temp directory.

    ``LOG_DIR`` is patched as well as passing ``log_dir=``: ``get_log_files``
    reads the module constant, so without this the viewer test would read the
    developer's real log directory and pass or fail on its contents.
    """
    monkeypatch.setattr(logging_config, "LOG_DIR", tmp_path)
    root = logging.getLogger()
    saved = list(root.handlers)
    setup_logging(log_level="INFO", log_dir=tmp_path)
    yield tmp_path
    for handler in list(root.handlers):
        handler.close()
    root.handlers = saved


def _emit_attacker_text(text: str) -> None:
    try:
        raise ValueError(text)
    except ValueError as exc:
        error_reference(logging.getLogger("server.probe"), "ctx", exc)
    for handler in logging.getLogger().handlers:
        handler.flush()


def _lines(log_dir: Path) -> list[str]:
    text = (log_dir / "server.log").read_text()
    return [line for line in text.splitlines() if line.strip()]


def test_a_newline_cannot_fabricate_a_viewer_entry(log_dir: Path) -> None:
    """The defect itself, asserted where it would have been visible."""
    _emit_attacker_text(f"bad input\n{FORGED}")

    entries = [parse_log_line(line) for line in _lines(log_dir)]
    fabricated = [e for e in entries if e and e.get("level") == "CRITICAL"]
    assert not fabricated, f"the viewer showed an entry nobody logged: {fabricated!r}"

    loggers = {e["logger"] for e in entries if e}
    assert "server.audit:1" not in loggers, "attacker chose the logger name"


def test_one_record_is_exactly_one_line(log_dir: Path) -> None:
    """Including the traceback.

    This is the half escaping the message could not fix: exc_info is rendered
    by the logging module, so a multi-line traceback used to put its own text
    at column 0 regardless of what we did to the message.
    """
    before = len(_lines(log_dir))
    _emit_attacker_text("bad input\nsecond line\nthird line")
    assert len(_lines(log_dir)) == before + 1


def test_the_payload_is_still_readable_to_an_operator(log_dir: Path) -> None:
    """Escaped, not stripped -- whoever reads the log must still see it."""
    _emit_attacker_text(f"bad input\n{FORGED}")

    record = json.loads(_lines(log_dir)[-1])
    assert FORGED in record["event"], "the payload was dropped rather than escaped"


def test_the_traceback_survives_in_its_own_field(log_dir: Path) -> None:
    """Redaction must not cost the operator the stack.

    A 'fix' that stopped passing exc_info would pass every assertion above and
    make production undiagnosable.
    """
    _emit_attacker_text("bad input")

    record = json.loads(_lines(log_dir)[-1])
    assert "exception" in record, "the traceback was lost"
    assert "Traceback (most recent call last)" in record["exception"]
    assert "ValueError" in record["exception"]


@pytest.mark.usefixtures("log_dir")
def test_the_viewer_still_reads_its_own_output() -> None:
    """End to end: the route's own helper, not just the parser.

    usefixtures rather than a parameter: this needs the fixture's side effect
    (LOG_DIR patched, handlers installed), not its value.
    """
    _emit_attacker_text("bad input")

    entries = get_recent_logs(log_type="server", lines=50)
    assert entries, "the viewer read nothing back"
    assert any("ctx [ref=" in e["message"] for e in entries)
    assert all(e["level"] in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"} for e in entries)


def test_legacy_lines_still_parse() -> None:
    """Files rotated before the format changed must not become unreadable.

    No fixture: this exercises the parser alone and never touches LOG_DIR.
    """
    legacy = "2024-01-05 16:49:31 | INFO     | server.main:39 | started"
    entry = parse_log_line(legacy)
    assert entry is not None
    assert entry["level"] == "INFO"
    assert entry["message"] == "started"
