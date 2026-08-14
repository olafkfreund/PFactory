"""The OAuth result page must name the origin it posts to (PFactory#541).

``_oauth_result_html`` used to end its ``postMessage`` with a literal ``'*'``.
A wildcard targetOrigin means "deliver to whatever is loaded in the opener
right now, whoever that is". The popup's lifetime spans a full round trip to
Microsoft or Google, so the opener can navigate in the meantime, and the
payload carries the connected ``email`` address and the provider - so this
leaked personal data, not just hygiene.

The inbound half was already closed: the frontend listener checks
``event.origin`` against an allowlist before acting. This file covers the
outbound half.

The origin is captured at ``/start`` and carried in the pending-state entry,
because by the time the ``/callback`` navigation arrives it comes from the
identity provider - the request that lands on the callback has no header
identifying our portal.

Two properties are asserted, and the second is the one that is easy to get
wrong:

* a recognised origin is emitted verbatim as the targetOrigin, and
* an UNRECOGNISED origin emits no ``postMessage`` at all. It must not fall
  back to ``'*'``. A fallback that reinstates the wildcard on the unknown path
  IS the wildcard, because the unknown path is exactly the one where we cannot
  name the recipient.

Mutation check: put ``'*'`` back as the targetOrigin, or make the ``None``
branch fall back to ``'*'``, and ``test_wildcard_is_never_emitted`` /
``test_unknown_origin_posts_nothing`` go red.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server.routes import email as email_routes  # noqa: E402
from server.routes.email import _oauth_result_html, _opener_origin  # noqa: E402

_PORTAL = "https://portal.example"


class _FakeRequest:
    """Only the surface ``_opener_origin`` touches."""

    def __init__(self, **headers: str) -> None:
        self.headers = {k.replace("_", "-"): v for k, v in headers.items()}


@pytest.fixture
def allowlist(monkeypatch):
    """Point ``get_settings().CORS_ORIGINS`` at a known allowlist."""

    def _apply(origins: list[str]) -> None:
        fake = SimpleNamespace(CORS_ORIGINS=origins)

        def _get_settings() -> SimpleNamespace:
            return fake

        monkeypatch.setattr(email_routes, "get_settings", _get_settings)

    return _apply


def _body(resp) -> str:
    return resp.body.decode()


# --------------------------------------------------------------------------
# The rendered page
# --------------------------------------------------------------------------


def test_recognised_origin_is_the_target_origin():
    html = _body(_oauth_result_html(success=True, message="ok", target_origin=_PORTAL))
    assert "postMessage" in html
    assert _PORTAL in html


def test_wildcard_is_never_emitted():
    """The regression this file exists for."""
    html = _body(_oauth_result_html(success=True, message="ok", target_origin=_PORTAL))
    assert "'*'" not in html
    assert '"*"' not in html


def test_unknown_origin_posts_nothing():
    """No recognised opener => no postMessage, rather than a wildcard one."""
    html = _body(_oauth_result_html(success=True, message="ok", email="a@b.com"))
    assert "postMessage" not in html
    # The page must still tell the user what happened and still close itself,
    # otherwise the popup silently strands them.
    assert "window.close" in html


def test_payload_is_not_leaked_when_origin_is_unknown():
    """The address must not reach the page at all on the unnamed-recipient path."""
    html = _body(_oauth_result_html(success=True, message="Connected", email="secret@example.com"))
    assert "secret@example.com" not in html


# --------------------------------------------------------------------------
# Choosing the origin
# --------------------------------------------------------------------------


def test_origin_header_must_be_allowlisted(allowlist):
    allowlist([_PORTAL])
    assert _opener_origin(_FakeRequest(origin=_PORTAL)) == _PORTAL


def test_unlisted_origin_is_rejected(allowlist):
    allowlist([_PORTAL])
    assert _opener_origin(_FakeRequest(origin="https://evil.example")) is None


def test_referer_is_used_when_origin_is_absent(allowlist):
    """Same-origin GET omits Origin in most browsers; Referer carries a path."""
    allowlist([_PORTAL])
    req = _FakeRequest(referer=f"{_PORTAL}/settings/integrations?tab=email")
    assert _opener_origin(req) == _PORTAL


def test_no_headers_yields_none(allowlist):
    allowlist([_PORTAL])
    assert _opener_origin(_FakeRequest()) is None


def test_wildcard_in_cors_config_does_not_authorize_everything(allowlist):
    """A '*' entry must not re-authorize the wildcard being removed here."""
    allowlist(["*", _PORTAL])
    assert _opener_origin(_FakeRequest(origin="https://evil.example")) is None
    assert _opener_origin(_FakeRequest(origin=_PORTAL)) == _PORTAL


def test_referer_path_cannot_smuggle_an_allowed_origin(allowlist):
    """Reduction to scheme://netloc must happen before the comparison."""
    allowlist([_PORTAL])
    req = _FakeRequest(referer=f"https://evil.example/redirect?to={_PORTAL}")
    assert _opener_origin(req) is None
