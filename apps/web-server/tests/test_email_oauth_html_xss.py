"""The OAuth result page must not execute anything the caller put in the URL.

``GET /api/email/oauth/callback`` (Microsoft) and ``/google/callback`` are
reached by the identity provider's redirect, which means anyone can hit them
directly with whatever query string they like. On the error branch the route
does ``message=error_description or error`` - both raw query parameters - and
that string was interpolated into an HTML page twice:

* into ``<p>{message}</p>`` with no escaping at all, and
* into an inline ``<script>`` through ``_js_string``, which escaped ``\\``,
  ``'`` and newline but not ``<``.

The second one is the interesting half, because it LOOKED escaped. A JS string
literal sitting inside ``<script>`` is tokenized by the HTML parser first, and
the HTML parser ends the script at the first ``</script`` no matter what the JS
quoting says. So ``?error=</script><img src=x onerror=alert(1)>`` closed the
block and the rest was parsed as markup - the quote escaping was irrelevant.

That is why every assertion below is about ``<`` and about the absence of a
live tag, not about quotes. CodeQL: ``py/reflective-xss``, CWE-79.

Mutation check: revert ``_js_string`` to the ``.replace("'", "\\\\'")`` form, or
drop the ``html_lib.escape`` from the ``<p>``, and the matching
``test_*_cannot_break_out`` goes red naming the payload it let through.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.routes.email import _js_string, _oauth_result_html  # noqa: E402

#: What an attacker actually sends. Each one is a real break-out, not a
#: character-class sample: the first closes the script block, the second closes
#: the paragraph, the third is the pre-ES2019 line-terminator trick.
PAYLOADS = {
    "script_close": "</script><img src=x onerror=alert(1)>",
    "tag_in_text": "<img src=x onerror=alert(1)>",
    "line_separator": "a\u2028alert(1)//",
}


def _body(message: str) -> str:
    # `.body` is `bytes | memoryview[int]`; bytes() accepts either.
    return bytes(_oauth_result_html(success=False, message=message, email=message).body).decode()


def test_js_string_never_emits_a_raw_angle_bracket() -> None:
    """The single property that makes an inline <script> safe."""
    for name, payload in PAYLOADS.items():
        encoded = _js_string(payload)
        assert "<" not in encoded, f"{name}: _js_string passed a raw '<': {encoded!r}"
        assert "\u2028" not in encoded, f"{name}: raw U+2028 survives: {encoded!r}"
        assert "\u2029" not in encoded, f"{name}: raw U+2029 survives: {encoded!r}"


def test_js_string_round_trips_through_json() -> None:
    """Escaping that changes the VALUE is a bug report waiting to happen.

    ``\\u003c`` is what JS reads back as ``<``, so the page still displays the
    real message; this pins that the fix escaped rather than stripped.
    """
    for payload in PAYLOADS.values():
        assert json.loads(_js_string(payload)) == payload


def test_oauth_page_cannot_break_out_of_the_script_block() -> None:
    for name, payload in PAYLOADS.items():
        html = _body(payload)
        # Exactly one script element: the page's own. A second </script> means
        # the payload terminated it early and everything after is markup.
        assert html.count("</script>") == 1, f"{name}: payload closed the script block"
        assert "<img" not in html, f"{name}: payload became a live tag: {payload!r}"


def test_oauth_page_escapes_the_visible_message() -> None:
    """The <p> is the other sink, and it had no escaping whatsoever."""
    html = _body(PAYLOADS["tag_in_text"])
    paragraph = re.search(r"<p>(.*?)</p>", html, re.S)
    assert paragraph, f"the message paragraph disappeared: {html!r}"
    assert "<img" not in paragraph.group(1)
    assert "&lt;img" in paragraph.group(1), "the message was dropped, not escaped"


def test_ordinary_messages_are_still_readable() -> None:
    """A fix that mangles the happy path gets reverted the first time a user
    reads "Connected: a&#x27;b@example.com" on the success page."""
    html = _body("Connected: user@example.com")
    assert "Connected: user@example.com" in html
