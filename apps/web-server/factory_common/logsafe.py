"""Canonical log-value sanitizer for the Factory fleet (CWE-117 / py/log-injection).

Every factory logs untrusted input: a task id from a URL path, a project id from
a request body, a line of stderr from a coder subprocess. If that value carries a
newline, the attacker writes their own log line:

    task_id = "abc\\nERROR:server:admin login from 10.0.0.1"
    logger.info(f"Starting task {task_id}")

...produces two records in the log file, the second one forged. The same trick
with control characters corrupts a log pipeline (terminal escapes in a `tail`,
NUL in a syslog frame, a fake field separator in a structured shipper).

This module is the single source of truth for the fix. One entry point:

    from factory_common.logsafe import sanitize_log

    logger.info("Starting task %s", sanitize_log(task_id))

Design: **escape, do not strip.** A sanitizer that deletes characters destroys
the value you were trying to debug with, and two different inputs can collapse to
the same log line. So the dangerous characters are made visible and reversible
instead:

    in : "abc\\nERROR: fake entry"
    out: "abc\\\\nERROR: fake entry"     (one record, the payload still readable)

Deliberate non-goals (so the output stays greppable):

* Backslashes are **not** escaped. Forging a log line needs a real newline; the
  literal two-character text ``\\n`` cannot. Escaping backslashes would double
  every Windows path and every regex in the logs for no security gain. The cost
  is that ``\\n`` in the output is ambiguous between "an escaped newline" and
  "the user typed a backslash and an n" - accepted.
* Tab (``\\t``) is preserved: it is not a record separator for stdlib logging and
  it keeps aligned output readable.
* Non-ASCII is preserved. Unicode is not an injection vector here, and mangling
  it would break every non-English identifier and commit message.

It is stdlib-only so it imports anywhere (CI, a coder pod, a one-off script),
exactly like the rest of the deduped hub layer.

CodeQL note: the ``.replace("\\n", ...)`` / ``.replace("\\r", ...)`` chain below is
what the stock ``py/log-injection`` query recognises as a barrier
(``LogInjection::ReplaceLineBreaksSanitizer``), so wrapping a value in
:func:`sanitize_log` clears the alert *because the value is genuinely sanitized*,
not because a rule was excluded. Keep the literal ``.replace`` calls - swapping
them for ``str.translate`` or a regex would be equivalent at runtime and
invisible to the analyzer.
"""

from __future__ import annotations

import re

__all__ = ["DEFAULT_MAX_LENGTH", "sanitize_log"]

# Everything in C0 except tab, plus DEL. \n and \r are handled separately (see
# the CodeQL note above) and must NOT be in this class.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

#: A log line long enough to be a denial-of-service against the log sink. Chosen
#: to be comfortably longer than any identifier, path or error message the fleet
#: logs, so truncation is an anomaly rather than routine.
DEFAULT_MAX_LENGTH = 2000


def sanitize_log(value: object, max_length: int | None = DEFAULT_MAX_LENGTH) -> str:
    """Return ``value`` as a string that cannot forge or corrupt a log record.

    Args:
        value: Anything. Non-strings go through ``str()`` first, so this is safe
            to wrap around ints, paths, enums and exceptions.
        max_length: Truncate beyond this many characters (after escaping), with a
            visible marker naming how much was dropped. Pass ``None`` to disable
            when you deliberately log a large payload.

    Returns:
        The value with CR/LF escaped as the two-character sequences ``\\n`` and
        ``\\r``, other control characters escaped as ``\\xNN``, and everything
        else left exactly as it was.
    """
    # Unconditional str(), not `value if isinstance(value, str) else str(value)`:
    # str() of a str returns the same object, so the branch bought nothing, and
    # keeping the original object alive in `text` let a caller's pathlib.Path
    # flow into `text.replace(...)` below - which CodeQL then resolves as
    # `Path.replace`, a filesystem RENAME, and reports as py/path-injection in
    # every consumer of this module (three such false positives in TFactory
    # alone). Stringify first and the value is a str from here down.
    text = str(value)
    # Order matters only for readability: do the record-separator characters
    # first so the common case is a single pass of cheap str.replace.
    text = text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\r")
    text = _CONTROL_CHARS.sub(lambda m: f"\\x{ord(m.group()):02x}", text)
    if max_length is not None and len(text) > max_length:
        dropped = len(text) - max_length
        text = f"{text[:max_length]}...[truncated {dropped} chars]"
    return text
