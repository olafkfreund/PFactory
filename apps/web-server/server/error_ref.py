"""Correlation ids for failures whose detail must not reach the caller.

CodeQL ``py/stack-trace-exposure`` (CWE-209) reported 31 sinks in this app, and
they nearly all trace back to the same three lines: the shared subprocess
wrappers (``git_utils.run_git_command`` / ``run_gh_command`` and
``pr_data_service._run_gh``) end with

    except Exception as e:
        return {"success": False, "error": str(e)}

and every route that returns ``result["error"]`` to the browser republishes it.
That text is written by the standard library and by the ``gh``/``git`` binaries,
and it routinely names internal detail the caller has no business seeing: an
on-disk workspace path (``[Errno 2] No such file or directory:
'/home/projects/MagesticAI/workspaces/acme/.git'``), an internal hostname and
port from a connection failure, a driver's SQL, a library version.

Truncating it does not help - the leak is at the FRONT of the string. The fix is
to send the caller a generic sentence plus a **correlation id**, and put the full
failure in the server log under that same id, so an operator answering "what
happened to my PR merge at 14:02?" runs one grep:

    ref = error_reference(logger, f"gh {args[0]} failed in {cwd}", e)
    return {"success": False, "error": f"the command failed (reference {ref})"}

    # server log:
    # WARNING server.services.git_utils gh pr merge failed in /srv/ws/acme
    #     [ref=9f2c1ab04d3e]: FileNotFoundError: [Errno 2] ... /srv/ws/acme/.git
    #     Traceback (most recent call last): ...

Shape borrowed verbatim from CFactory's ``cfactory/error_ref.py`` (CFactory#372)
so the fleet answers this alert one way. It is not in the hub's factory_common
because it is not stdlib-only in spirit - it takes a caller's ``logging.Logger``
and the client-facing wording is per-service. If a third service needs it,
promote it to the hub rather than making a fourth copy.
"""

from __future__ import annotations

import logging
import uuid

from factory_common.logsafe import sanitize_log

#: Length of the id. 12 hex chars is 48 bits: far beyond collision range for a
#: log window an operator will ever grep, and short enough to read aloud.
_REF_CHARS = 12


def error_reference(logger: logging.Logger, context: str, exc: BaseException) -> str:
    """Log ``exc`` in full under a fresh correlation id; return just the id.

    The caller composes the client-facing sentence around the id, because what
    reads well on a git route ("the git command failed") is not what reads well
    on a terminal socket ("the session could not be opened"). What the caller
    must NOT do is put any part of ``exc`` in that sentence - not its message,
    not ``type(exc).__name__``, not a truncation of either.

    ``context`` is interpolated into the log message and is sanitized: it usually
    carries a project path, spec id or subcommand that arrived from a request.
    """
    ref = uuid.uuid4().hex[:_REF_CHARS]
    logger.warning(
        "%s [ref=%s]: %s: %s",
        sanitize_log(context),
        ref,
        type(exc).__name__,
        sanitize_log(str(exc)),
        # `exc_info=exc`, NOT `exc_info=True`. True means "call sys.exc_info()",
        # which is only populated inside an active `except` block - so a caller
        # that stashed the exception and logged it later (a task callback, a
        # retry wrapper) would silently record "NoneType: None" where the stack
        # should be, and the correlation id would lead an operator to a log line
        # with nothing under it. Passing the object works from anywhere.
        exc_info=exc,
    )
    return ref


def error_message(logger: logging.Logger, context: str, exc: BaseException, what: str) -> str:
    """A ready-made client-facing sentence: ``"<what> (reference <id>)"``.

    For the many call sites whose only client-facing wording is a single
    ``{"success": False, "error": ...}`` field, so they do not each re-invent the
    sentence and accidentally re-introduce ``{exc}`` into it.
    """
    return f"{what} (reference {error_reference(logger, context, exc)})"
