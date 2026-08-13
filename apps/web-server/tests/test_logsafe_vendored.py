"""The vendored log sanitizer must stop a forged log record (CWE-117).

CodeQL reported 223 ``py/log-injection`` sinks in ``apps/web-server/server``: a
task id, project id, spec id or a line of coder stderr goes straight into an
f-string log message. A newline in any of those writes the attacker's own record
into the server log, which is what the audit trail is read from.

The fix wraps those values in ``factory_common.logsafe.sanitize_log`` (the hub
canonical, vendored at ``apps/web-server/factory_common/``). This test is the
behaviour lock: it drives a REAL ``logging`` handler and asserts the forged
record is NOT emitted. Break the sanitizer - delete one ``.replace`` from the
vendored copy - and ``test_sanitized_value_cannot_forge_a_record`` goes red,
because a second record appears on the handler.

Asserting on a real handler rather than on the returned string matters: a test
that only checks "the string changed" passes against a sanitizer that escapes
the wrong character, and the whole point is what reaches the log sink.
"""

from __future__ import annotations

import io
import logging
import sys
from pathlib import Path

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from factory_common.logsafe import sanitize_log  # noqa: E402

# What an attacker puts in a task id / spec id to forge an audit record.
FORGED_TASK_ID = "spec-001\nWARNING:server.audit:api key revoked by admin"


def _emit(task_id: object) -> list[str]:
    """Log a task id exactly as the routes do; return the emitted records."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    logger = logging.getLogger("server.tests.logsafe")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.info("[StartTask] ===== START ENDPOINT CALLED ===== task_id: %s", task_id)
    handler.flush()
    return stream.getvalue().splitlines()


def test_raw_value_forges_a_record() -> None:
    """Precondition: without the sanitizer the attack works."""
    records = _emit(FORGED_TASK_ID)
    assert len(records) == 2
    assert records[1] == "WARNING:server.audit:api key revoked by admin"


def test_sanitized_value_cannot_forge_a_record() -> None:
    records = _emit(sanitize_log(FORGED_TASK_ID))
    assert len(records) == 1
    assert not any(r.startswith("WARNING:server.audit:") for r in records)
    # The payload is still there, inert and greppable - debuggability preserved.
    assert records[0].endswith("task_id: spec-001\\nWARNING:server.audit:api key revoked by admin")


def test_carriage_return_cannot_forge_a_record() -> None:
    """\\r alone is a record separator for a log shipper reading raw bytes."""
    records = _emit(sanitize_log("spec-002\rERROR:server.audit:forged"))
    assert len(records) == 1
    assert "\\r" in records[0]


def test_control_characters_are_escaped() -> None:
    """A NUL truncates a syslog frame; an ESC injects a terminal sequence."""
    out = sanitize_log("spec\x00-003\x1b[2Jcleared")
    assert "\x00" not in out
    assert "\x1b" not in out
    assert "\\x00" in out and "\\x1b" in out


def test_real_identifiers_are_not_mangled() -> None:
    """A sanitizer that ruins normal log lines is worse than the alert."""
    for value in (
        "spec-042-add-login:1",
        "/home/projects/MagesticAI/workspaces/acme/.pfactory/specs/spec-042",
        "org_7f3a-9c11",
        "Traceback (most recent call last): ValueError('bad id')",
        "refactor: split the planner (#254)",
    ):
        assert sanitize_log(value) == value


def test_non_strings_are_accepted() -> None:
    """Call sites wrap ints, Paths and exceptions without a cast."""
    assert sanitize_log(7) == "7"
    assert sanitize_log(Path("/srv/ws/acme")) == "/srv/ws/acme"
    assert sanitize_log(ValueError("bad\nid")) == "bad\\nid"


def test_oversized_value_is_truncated_visibly() -> None:
    out = sanitize_log("x" * 5000)
    assert len(out) < 5000
    assert out.endswith("...[truncated 3000 chars]")
