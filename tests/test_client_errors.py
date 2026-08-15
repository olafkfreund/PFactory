"""Tests for the client-facing error message contract (Factory#718).

The property that matters is not "a deliberate InputRejectedError's message
survives" -- that would pass even on the unfixed `detail=str(exc)` code,
which is exactly the trap the issue warns about. The test that proves
enforcement is a FOREIGN exception -- one that never opted in -- reaching
`client_error` and getting redacted instead of leaking its own text.
"""

from __future__ import annotations

from client_errors import InputRejectedError, client_error


def test_input_rejected_error_message_is_trusted():
    exc = InputRejectedError("invalid ref: 'weird'")
    assert client_error(exc) == "invalid ref: 'weird'"


def test_foreign_valueerror_is_redacted_not_echoed():
    """The property Factory#718 exists for.

    A ValueError this code never wrote and never reviewed -- e.g. one a
    library raised -- must NOT have its own text handed to the caller, even
    though `except ValueError` (existing handlers fleet-wide) still catches
    it.
    """
    exc = ValueError("Errno 2: No such file or directory: '/srv/internal/db.sock'")
    assert client_error(exc) == "invalid request"
    assert "/srv/internal/db.sock" not in client_error(exc)


def test_foreign_exception_of_any_type_is_redacted():
    """Not ValueError-specific: client_error trusts the ATTRIBUTE, not the type."""
    exc = RuntimeError("internal state: pool exhausted at host db-3.internal:5432")
    assert client_error(exc) == "invalid request"
    assert "db-3.internal" not in client_error(exc)


def test_custom_default_is_honoured_for_foreign_exceptions():
    exc = KeyError("some-internal-key")
    assert client_error(exc, default="not found") == "not found"


def test_input_rejected_error_is_still_caught_by_except_valueerror():
    """Promoting a raise site to InputRejectedError must not silently convert
    an existing `except ValueError` 400 into an unhandled 500 (the exact
    regression the issue calls out).
    """
    caught = None
    try:
        raise InputRejectedError("bad input")
    except ValueError as exc:
        caught = exc
    assert isinstance(caught, InputRejectedError)
    assert client_error(caught) == "bad input"


def test_domain_exception_opts_in_via_client_message_duck_typing():
    """A repo-owned exception type (PlanServiceError, MCPAuthError, ...) opts
    in without subclassing InputRejectedError -- client_error only checks for
    the attribute, so a KeyError can carry it too (provider_runtime.py's
    get_runtime does exactly this, since its callers already `except
    KeyError` and the raised type can't change).
    """
    exc = KeyError("unknown provider runtime 'bogus'")
    exc.client_message = "unknown provider runtime 'bogus'"
    assert client_error(exc) == "unknown provider runtime 'bogus'"


def test_non_string_client_message_is_not_trusted():
    """A stray non-string `client_message` (e.g. someone sets it to an int by
    mistake) must fall back rather than be handed to a caller unexamined.
    """
    exc = ValueError("whatever")
    exc.client_message = 12345  # not a string
    assert client_error(exc) == "invalid request"
