"""WebSocket auth must prefer the Authorization header over ?token= (#555).

Passing a bearer token in the URL leaks it into proxy logs, access logs and
browser history. The previous order here read the query param FIRST and only
fell back to the header, so a correctly-behaving client that sent a header
still had its token written to every log in the path.

The discriminating assertion is `test_header_wins_when_both_present`: the old
code passes every other test in this file, because it did read the header — just
second. Only a request carrying BOTH distinguishes the fix from the bug.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server import auth as auth_mod  # noqa: E402


def _ws(header: str | None = None, query: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        headers={"authorization": header} if header else {},
        query_params={"token": query} if query else {},
    )


def test_header_is_used() -> None:
    assert auth_mod._ws_extract_token(_ws(header="Bearer hdr-token")) == "hdr-token"


def test_query_param_still_works() -> None:
    assert auth_mod._ws_extract_token(_ws(query="qs-token")) == "qs-token"


def test_header_wins_when_both_present() -> None:
    # THE DISCRIMINATING CASE. The old order returned the query token here, so a
    # client doing the right thing still leaked its token into the URL path.
    ws = _ws(header="Bearer hdr-token", query="qs-token")
    assert auth_mod._ws_extract_token(ws) == "hdr-token"


def test_no_token_returns_none() -> None:
    assert auth_mod._ws_extract_token(_ws()) is None


def test_non_bearer_header_falls_through_to_query() -> None:
    ws = _ws(header="Basic abc", query="qs-token")
    assert auth_mod._ws_extract_token(ws) == "qs-token"


def test_query_use_warns_once(caplog) -> None:  # type: ignore[no-untyped-def]
    auth_mod._warn_ws_query_token_once.cache_clear()
    with caplog.at_level("WARNING"):
        auth_mod._ws_extract_token(_ws(query="a"))
        auth_mod._ws_extract_token(_ws(query="b"))
    warnings = [r for r in caplog.records if "DEPRECATED" in r.getMessage()]
    assert len(warnings) == 1, "the deprecation notice must be emitted once, not per call"


def test_header_use_does_not_warn(caplog) -> None:  # type: ignore[no-untyped-def]
    auth_mod._warn_ws_query_token_once.cache_clear()
    with caplog.at_level("WARNING"):
        auth_mod._ws_extract_token(_ws(header="Bearer hdr"))
    assert not [r for r in caplog.records if "DEPRECATED" in r.getMessage()]
