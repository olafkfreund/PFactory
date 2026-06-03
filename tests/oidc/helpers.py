"""Utilities shared across P3 OIDC acceptance tests."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_SERVER_ROOT = REPO_ROOT / "apps" / "web-server"

if str(WEB_SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(WEB_SERVER_ROOT))


# Module names we evict between tests to force a fresh OIDC client
# instance under a different env (issuer URL / client_id / etc.). Same
# pattern as tests/secrets/helpers._MODULES_TO_EVICT.
_MODULES_TO_EVICT = (
    "server.oidc.client",
    "server.oidc",
    "server.routes.oidc_routes",
)


def reimport_oidc(env: dict[str, str]) -> None:
    """Re-import ``server.oidc`` with fresh OIDC env vars."""
    import os
    for k, v in env.items():
        os.environ[k] = v
    for m in _MODULES_TO_EVICT:
        sys.modules.pop(m, None)


def authlib_available() -> bool:
    """True iff ``authlib`` is importable. OIDC tests skip cleanly
    when the library isn't installed yet (pre-P3.1 state)."""
    try:
        importlib.import_module("authlib")
        return True
    except ImportError:
        return False


def keycloak_drive_login_url(
    *,
    auth_url: str,
    username: str,
    password: str,
) -> str:
    """Drive Keycloak's HTML login form headlessly; return the auth code.

    ``auth_url`` is the COMPLETE authorization URL produced by our
    OIDC /login endpoint — it includes the relying-party's client_id,
    redirect_uri, state, code_challenge, AND any nonce the authlib-side
    client added. We pass the URL through unchanged so every parameter
    ends up in Keycloak's auth request exactly as authlib expects.

    Flow mirrors a browser:
      1. GET ``auth_url`` — Keycloak renders its login page HTML.
      2. Extract the ``<form action=...>`` URL from the HTML.
      3. POST username/password to that action (with cookies from step 1
         so Keycloak's AUTH_SESSION_ID is preserved).
      4. Keycloak 302s back to ``redirect_uri`` with ``?code=...&state=...``.
      5. Extract and return the ``code``.
    """
    import re

    import httpx

    with httpx.Client(follow_redirects=False, timeout=10.0) as client:
        resp = client.get(auth_url)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Keycloak auth endpoint returned {resp.status_code}, "
                f"expected the login page HTML. Body head: {resp.text[:300]!r}"
            )
        match = re.search(r'action="([^"]+)"', resp.text)
        if not match:
            raise RuntimeError(
                "Could not find <form action=> in Keycloak login page"
            )
        action = match.group(1).replace("&amp;", "&")

        resp = client.post(
            action,
            data={"username": username, "password": password, "credentialId": ""},
        )
        if resp.status_code != 302:
            raise RuntimeError(
                f"Keycloak credential POST returned {resp.status_code} "
                f"(expected 302). Body head: {resp.text[:300]!r}"
            )
        loc = resp.headers.get("location", "")
        parsed = urlparse(loc)
        qs = parse_qs(parsed.query)
        if "code" not in qs:
            raise RuntimeError(
                f"Keycloak redirect missing ?code=... in Location: {loc!r}"
            )
        return qs["code"][0]
