"""Enforced-safe client-facing error messages (Factory#718).

``except ValueError as exc: raise HTTPException(detail=str(exc))`` is ambiguous
by construction: ``except ValueError`` catches every ``ValueError`` a called
function's *own* dependencies raise, including ones from a library nobody here
wrote whose wording was never reviewed for what it reveals to a caller. The
same problem exists one level up for a repo-owned exception type whose message
happens to embed a caught inner exception (``f"{service}: GET {path} failed:
{exc}"``) -- the type is repo-owned, the *content* isn't.

``str(exc)`` is also the wrong read regardless of type: ``BaseException.__str__``
renders ``args``, which every exception writes (deliberately or not, including
ones no one meant to make client-facing).

The fix is a single attribute with exactly one writer. :class:`InputRejectedError`
sets ``client_message`` in its constructor for genuinely new call sites; an
existing repo-owned exception type (``PlanServiceError``, ``SpecSourceError``, ...)
opts in the same way once its raise sites are verified to carry only
developer-written text (see the classification in Factory#718's issue
comments). :func:`client_error` reads ONLY that attribute -- never ``str(exc)``
-- so an exception that never opted in, whatever its type, gets the generic
fallback instead of whatever a dependency happened to write into ``args``.
"""

from __future__ import annotations

__all__ = ["InputRejectedError", "client_error"]


class InputRejectedError(ValueError):
    """Raise this instead of a bare ``ValueError`` for a validated-input rejection.

    Subclasses ``ValueError`` on purpose: every existing ``except ValueError``
    handler in a caller keeps catching it unchanged, so promoting a raise site
    to this type is not a breaking change at any call site that doesn't yet
    know about it. What changes is that :func:`client_error` can now tell this
    exception's message apart from an arbitrary ``ValueError`` surfacing from
    somewhere a caller called, and trust only the one that was actually
    written for a caller to read.
    """

    def __init__(self, client_message: str, *args: object) -> None:
        super().__init__(client_message, *args)
        self.client_message = client_message


def client_error(exc: BaseException, *, default: str = "invalid request") -> str:
    """The safe client-facing message for a caught exception.

    Returns ``exc.client_message`` when it is present and a string -- set by
    :class:`InputRejectedError`, or by a repo-owned exception type that opts in
    the same way once its raise sites are verified (see this module's
    docstring). Every other exception, regardless of type, gets ``default``:
    never ``str(exc)``, which would repeat whatever text a dependency wrote.
    """
    message = getattr(exc, "client_message", None)
    return message if isinstance(message, str) else default
