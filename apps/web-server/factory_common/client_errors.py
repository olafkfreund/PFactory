"""The one exception whose message is DELIBERATELY safe to hand back to a caller.

CWE-209 asks a question that is easy to get backwards. The distinction it
actually cares about is not "was this an exception" but *who wrote the text*.
"unsupported URL scheme 'ftp' (only http/https)" is developer-written and quotes
only the field the caller just sent, so hiding it behind a correlation id turns
a fixable 400 into a support ticket. A ``FileNotFoundError`` from the stdlib is
the opposite: nobody here chose its wording and it names a path on our disk.

So validators raise :class:`InputRejectedError`, each service's ``client_error``
helper hands its message back verbatim, and everything else still gets a
reference id and a log record.

It lives here because it was independently reinvented four times -- TFactory and
AIFactory in ``server/error_ref.py``, CFactory in ``cfactory/error_ref.py``,
PFactory in ``apps/backend/client_errors.py`` -- and because
:mod:`factory_common.url_safety` needs to raise it. A guard in the hub that can
only raise ``ValueError`` forces every consumer that wants the marked behaviour
to keep a forked copy of the guard, which is exactly the divergence TFactory#1111
was filed about.
"""

from __future__ import annotations

__all__ = ["InputRejectedError"]


class InputRejectedError(ValueError):
    """A validation failure whose message is safe to return to the caller.

    Subclasses ``ValueError`` so that every existing ``except ValueError``
    handler in the fleet keeps catching it. That is not a nicety: the guards
    that raise this are caught by ``except ValueError`` in AIFactory's WebFetch
    hook and in PFactory's route handlers, and a non-subclass would silently
    turn each of those blocks into an unhandled 500.

    The safe sentence is carried in :attr:`client_message`, a plain string this
    class was *given* at construction -- **not** read back out with
    ``str(exc)``. That distinction is the whole point of the attribute:
    ``BaseException.__str__`` renders ``args``, and ``args`` is populated by
    every exception in the process, so a handler that forwards ``str(exc)``
    forwards text of unknown authorship. ``client_message`` has exactly one
    writer, the constructor below.

    What this does NOT establish is that a raise site chose its wording wisely.
    ``InputRejectedError(f"failed: {inner}")`` launders ``inner``'s text straight
    through and no mechanism here can stop it. Raise sites are held honest by
    review, and by asserting on the response BODY of a real route rather than on
    this class in isolation.
    """

    def __init__(self, client_message: str) -> None:
        super().__init__(client_message)
        self.client_message = client_message
