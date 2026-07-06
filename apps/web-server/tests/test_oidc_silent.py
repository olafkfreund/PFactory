"""Silent SSO handoff (#149): ?prompt=none passthrough on /login and the
error=login_required fallback on /callback.

The route functions are called directly (matching the other route tests) with
is_oidc_enabled + the oauth client mocked.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from server.routes import oidc_routes


def _oauth(captured):
    async def _authorize_redirect(_request, redirect_uri, **kwargs):
        captured.update(redirect_uri=redirect_uri, **kwargs)
        return "REDIRECT"

    return SimpleNamespace(oidc=SimpleNamespace(authorize_redirect=_authorize_redirect))


def test_login_forwards_prompt_none():
    captured: dict = {}
    req = SimpleNamespace(query_params={"prompt": "none"})
    with (
        patch.object(oidc_routes, "is_oidc_enabled", return_value=True),
        patch.object(oidc_routes, "get_oauth_client", return_value=_oauth(captured)),
        patch.dict("os.environ", {"APP_OIDC_REDIRECT_URI": "https://x/api/auth/oidc/callback"}),
    ):
        out = asyncio.run(oidc_routes.oidc_login(req))
    assert out == "REDIRECT"
    assert captured.get("prompt") == "none"
    assert "nonce" in captured  # nonce is always sent


def test_login_omits_prompt_when_not_requested():
    captured: dict = {}
    req = SimpleNamespace(query_params={})
    with (
        patch.object(oidc_routes, "is_oidc_enabled", return_value=True),
        patch.object(oidc_routes, "get_oauth_client", return_value=_oauth(captured)),
        patch.dict("os.environ", {"APP_OIDC_REDIRECT_URI": "https://x/cb"}),
    ):
        asyncio.run(oidc_routes.oidc_login(req))
    assert "prompt" not in captured


def test_login_ignores_arbitrary_prompt():
    # only "none" is forwarded — a caller can't inject e.g. prompt=login
    captured: dict = {}
    req = SimpleNamespace(query_params={"prompt": "login"})
    with (
        patch.object(oidc_routes, "is_oidc_enabled", return_value=True),
        patch.object(oidc_routes, "get_oauth_client", return_value=_oauth(captured)),
        patch.dict("os.environ", {"APP_OIDC_REDIRECT_URI": "https://x/cb"}),
    ):
        asyncio.run(oidc_routes.oidc_login(req))
    assert "prompt" not in captured


def test_callback_error_redirects_to_login():
    req = SimpleNamespace(query_params={"error": "login_required"})
    with patch.object(oidc_routes, "is_oidc_enabled", return_value=True):
        out = asyncio.run(oidc_routes.oidc_callback(req, db=None))
    assert out.status_code in (302, 307)
    assert out.headers["location"] == "/login"


def test_enabled_reports_true():
    with patch.object(oidc_routes, "is_oidc_enabled", return_value=True):
        assert asyncio.run(oidc_routes.oidc_enabled()) == {"enabled": True}


def test_enabled_reports_false():
    with patch.object(oidc_routes, "is_oidc_enabled", return_value=False):
        assert asyncio.run(oidc_routes.oidc_enabled()) == {"enabled": False}
