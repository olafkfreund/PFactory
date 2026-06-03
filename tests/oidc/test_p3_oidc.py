"""P3 — OIDC SSO acceptance tests.

Six tests map directly to the six acceptance bullets in Epic #26
issue #30. As implementation chunks land, the ``@pytest.mark.skip``
decorator is removed from the relevant test and a real body replaces
the ``pytest.fail`` placeholder.

Status:
  - P3.1 (Keycloak login happy path) — test_login_callback_pkce_roundtrip GREEN
  - P3.2..P3.5 — still skipped (each names the chunk that will flip it)
"""

from __future__ import annotations

import os
from urllib.parse import parse_qs, urlparse

import pytest

from tests.oidc.helpers import (
    authlib_available,
    keycloak_drive_login_url,
    reimport_oidc,
)

# Keycloak realm config (mirrors tests/oidc/fixtures/keycloak-realm.json).
TEST_USER_EMAIL = "alice@example.com"
TEST_USER_NAME = "Alice Example"
TEST_USER_USERNAME = "alice"
TEST_USER_PASSWORD = "alice-test-pass"


def _build_test_app():
    """Construct a fresh FastAPI app instance with OIDC env wired.

    Each call:
      - Re-imports server.oidc with the fixture's IdP env so the
        authlib client points at the test Keycloak.
      - Creates a fresh in-memory async SQLite engine for the call's
        scope (table schema is taken straight from Base.metadata, so
        any new column on a model is picked up without an explicit
        migration step).
      - Wires the new engine in as a dependency override for get_db.

    Returns ``(app, engine)`` so tests that want to inspect rows
    post-flow can do so without going through the API.
    """
    reimport_oidc({
        "APP_OIDC_ENABLED": "true",
        "APP_OIDC_ISSUER_URL": os.environ["OIDC_ISSUER_URL"],
        "APP_OIDC_CLIENT_ID": os.environ["OIDC_CLIENT_ID"],
        "APP_OIDC_CLIENT_SECRET": os.environ["OIDC_CLIENT_SECRET"],
    })

    # Fresh in-memory SQLite per app. The "?cache=shared&uri=true"
    # combo lets aiosqlite share the in-memory DB across the multiple
    # connections async_sessionmaker creates — but we MUST give each
    # test its own DB name (otherwise all tests share one DB and
    # session rows leak between tests). A token_urlsafe nonce in the
    # path provides per-test isolation.
    import secrets as _test_secrets

    from fastapi import FastAPI
    from server.database.engine import get_db
    from server.database.models import Base
    from server.routes import oidc_routes
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from starlette.middleware.sessions import SessionMiddleware
    db_nonce = _test_secrets.token_hex(8)
    engine = create_async_engine(
        f"sqlite+aiosqlite:///file:p3test-{db_nonce}?mode=memory&cache=shared&uri=true"
    )

    import asyncio
    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.get_event_loop().run_until_complete(_init()) if False else None
    # The above pattern doesn't work cleanly inside test sync context.
    # Use a fresh event loop instead:
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_init())
    finally:
        loop.close()

    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with SessionLocal() as session:
            yield session

    app = FastAPI()
    app.add_middleware(
        SessionMiddleware,
        secret_key="test-session-secret-for-p3-oidc",
        session_cookie="aif_oidc_session",
        max_age=600,
        same_site="lax",
        https_only=False,
    )
    app.include_router(oidc_routes.router)
    app.dependency_overrides[get_db] = _override_get_db
    app.state.test_engine = engine
    app.state.test_session_local = SessionLocal
    return app


@pytest.mark.oidc
@pytest.mark.slow
@pytest.mark.skipif(not authlib_available(), reason="authlib not installed")
def test_login_callback_pkce_roundtrip(oidc_issuer_url, oidc_client_id) -> None:
    """Authorization Code + PKCE end-to-end against Keycloak.

    Flow:
      1. Test client GET /api/auth/oidc/login
         → expect 302 to Keycloak with state + code_challenge in query.
      2. Drive Keycloak's HTML login form headlessly with the test user;
         Keycloak issues an auth code.
      3. Test client GET /api/auth/oidc/callback?code=...&state=...
         → server retrieves stashed PKCE verifier from its Starlette
         session, exchanges the code with Keycloak, JIT-provisions a
         User, and 302s back to "/" with HTTP-only access_token cookie.
      4. Assert the cookie is set and decodes as a valid internal JWT
         carrying the test user's email + role.
    """
    # Build a sync TestClient so cookie persistence works trivially.
    from fastapi.testclient import TestClient
    from jose import jwt
    from server.config import get_settings

    # Each test gets a fresh app — get_settings() is itself a singleton
    # but JWT_SECRET is stable across the run.
    app = _build_test_app()

    # IMPORTANT: For Keycloak to accept the callback redirect_uri, the
    # realm must whitelist it. Our realm fixture includes "*", so any
    # URI works. The TestClient's base URL is "http://testserver".
    with TestClient(app, follow_redirects=False) as client:
        # Step 1: kick off OIDC login.
        resp = client.get("/api/auth/oidc/login")
        assert resp.status_code in (302, 307), (
            f"/login should 302 to Keycloak; got {resp.status_code} "
            f"body={resp.text[:300]!r}"
        )
        kc_url = resp.headers["location"]
        parsed = urlparse(kc_url)
        qs = parse_qs(parsed.query)
        assert qs.get("response_type") == ["code"], "expected response_type=code"
        assert qs.get("client_id") == [oidc_client_id]
        assert "code_challenge" in qs, "PKCE code_challenge missing from auth URL"
        assert qs.get("code_challenge_method") == ["S256"]
        assert "state" in qs, "state nonce missing from auth URL"

        state = qs["state"][0]

        # Step 2: drive Keycloak's login form using the COMPLETE auth URL
        # so every param (nonce, code_challenge, redirect_uri, etc.)
        # authlib added is preserved.
        code = keycloak_drive_login_url(
            auth_url=kc_url,
            username=TEST_USER_USERNAME,
            password=TEST_USER_PASSWORD,
        )

        # Step 3: complete the callback. The TestClient still carries
        # the SessionMiddleware cookie from step 1, which holds the
        # PKCE verifier authlib needs for the token exchange.
        resp = client.get(
            f"/api/auth/oidc/callback?code={code}&state={state}"
        )

        assert resp.status_code in (302, 307), (
            f"/callback should redirect on success; got {resp.status_code} "
            f"body={resp.text[:500]!r}"
        )
        assert resp.headers["location"] == "/", (
            f"expected redirect to '/'; got {resp.headers['location']!r}"
        )

        # Step 4: assert the access token cookie is valid.
        access_cookie = client.cookies.get("access_token")
        assert access_cookie, "access_token cookie not set after callback"

        settings = get_settings()
        payload = jwt.decode(
            access_cookie,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
        assert payload["type"] == "access"
        assert payload["email"] == TEST_USER_EMAIL
        assert payload["role"] in {"member", "admin"}  # JIT default

        # Verify a User row was JIT-provisioned for the OIDC sub.
        # (We don't query the DB directly here — that's covered in
        # test_jit_provisions_user_and_org_member in P3.3.)


@pytest.mark.oidc
@pytest.mark.slow
@pytest.mark.skipif(not authlib_available(), reason="authlib not installed")
def test_pkce_state_tamper_rejected(oidc_issuer_url, oidc_client_id) -> None:
    """Tampering the ``state`` parameter at /callback must raise.

    Drive the normal flow up to the moment we'd hit /callback, then
    swap the state value before submission. authlib's
    authorize_access_token raises a state-mismatch error; our handler
    returns 400 with a generic "OIDC callback rejected" message and
    NEVER echoes the tampered state back (reflected-XSS defense).
    No session cookie is set.
    """
    from fastapi.testclient import TestClient

    app = _build_test_app()

    with TestClient(app, follow_redirects=False) as client:
        # Begin the OIDC flow normally.
        resp = client.get("/api/auth/oidc/login")
        assert resp.status_code in (302, 307)
        kc_url = resp.headers["location"]
        parsed = urlparse(kc_url)
        qs = parse_qs(parsed.query)
        original_state = qs["state"][0]

        # Get an auth code from Keycloak — this code is bound to the
        # *original* state (and PKCE verifier in the session).
        code = keycloak_drive_login_url(
            auth_url=kc_url,
            username=TEST_USER_USERNAME,
            password=TEST_USER_PASSWORD,
        )

        # Now tamper: replace state with attacker-controlled value.
        tampered_state = "attacker-injected-state-value-xxxxxxxxx"
        assert tampered_state != original_state, "fixture must use a distinct value"

        resp = client.get(
            f"/api/auth/oidc/callback?code={code}&state={tampered_state}"
        )
        assert resp.status_code == 400, (
            f"tampered state must be rejected; got {resp.status_code} "
            f"body={resp.text[:300]!r}"
        )

        # Defense-in-depth: the response body must NOT echo back the
        # tampered state value (would create a reflected-XSS sink).
        assert tampered_state not in resp.text, (
            "response body must not echo tampered state (reflected-XSS defense)"
        )

        # No session was minted.
        assert client.cookies.get("access_token") is None
        assert client.cookies.get("refresh_token") is None


@pytest.mark.oidc
@pytest.mark.slow
@pytest.mark.skipif(not authlib_available(), reason="authlib not installed")
def test_jit_provisions_user_and_org_member(oidc_issuer_url, oidc_client_id) -> None:
    """First OIDC login from an unknown ``sub`` creates a User + OrganizationMember.

    Asserts:
      - users.oidc_sub is populated with Keycloak's sub.
      - org_members row exists in the default org with the
        claim-mapped role (default "member" when no group map env).
      - A second login (different TestClient session but same sub)
        reuses the same User row (id stable).
    """
    import asyncio

    from fastapi.testclient import TestClient
    from server.database.models import Organization, OrgMember, User
    from sqlalchemy import select

    app = _build_test_app()
    SessionLocal = app.state.test_session_local

    # ---- First login ----
    with TestClient(app, follow_redirects=False) as client:
        resp = client.get("/api/auth/oidc/login")
        assert resp.status_code in (302, 307)
        kc_url = resp.headers["location"]
        state = parse_qs(urlparse(kc_url).query)["state"][0]
        code = keycloak_drive_login_url(
            auth_url=kc_url,
            username=TEST_USER_USERNAME,
            password=TEST_USER_PASSWORD,
        )
        resp = client.get(f"/api/auth/oidc/callback?code={code}&state={state}")
        assert resp.status_code in (302, 307), (
            f"callback should redirect on success; got {resp.status_code} "
            f"body={resp.text[:300]!r}"
        )

    # Inspect DB state.
    async def _check_first_login():
        async with SessionLocal() as session:
            users = (await session.execute(select(User))).scalars().all()
            assert len(users) == 1, f"expected 1 user, got {len(users)}"
            user = users[0]
            assert user.email == TEST_USER_EMAIL
            assert user.oidc_sub, "oidc_sub must be set after JIT provisioning"
            assert user.password_hash == "", "OIDC users get no local password"

            orgs = (await session.execute(select(Organization))).scalars().all()
            assert len(orgs) == 1, "default org should be auto-created"
            assert orgs[0].slug == "default"

            members = (await session.execute(select(OrgMember))).scalars().all()
            assert len(members) == 1
            assert members[0].user_id == user.id
            assert members[0].org_id == orgs[0].id
            return user.id, user.oidc_sub
    user_id_1, sub_1 = asyncio.new_event_loop().run_until_complete(_check_first_login())

    # ---- Second login: same sub, fresh TestClient session ----
    with TestClient(app, follow_redirects=False) as client:
        resp = client.get("/api/auth/oidc/login")
        kc_url = resp.headers["location"]
        state = parse_qs(urlparse(kc_url).query)["state"][0]
        code = keycloak_drive_login_url(
            auth_url=kc_url,
            username=TEST_USER_USERNAME,
            password=TEST_USER_PASSWORD,
        )
        resp = client.get(f"/api/auth/oidc/callback?code={code}&state={state}")
        assert resp.status_code in (302, 307), (
            f"callback should redirect on success; got {resp.status_code} "
            f"body={resp.text[:300]!r}"
        )

    # User row count must still be 1 — same sub reuses the row.
    async def _check_second_login():
        async with SessionLocal() as session:
            users = (await session.execute(select(User))).scalars().all()
            assert len(users) == 1, (
                f"second login for same sub must not double-create; "
                f"got {len(users)} users"
            )
            assert users[0].id == user_id_1
            assert users[0].oidc_sub == sub_1

            members = (await session.execute(select(OrgMember))).scalars().all()
            assert len(members) == 1, "no duplicate membership rows"
    asyncio.new_event_loop().run_until_complete(_check_second_login())


def _complete_login_get_refresh_token(app, oidc_issuer_url) -> str:
    """Helper: drive the full login flow and return the refresh JWT."""
    from fastapi.testclient import TestClient
    with TestClient(app, follow_redirects=False) as client:
        resp = client.get("/api/auth/oidc/login")
        assert resp.status_code in (302, 307)
        kc_url = resp.headers["location"]
        state = parse_qs(urlparse(kc_url).query)["state"][0]
        code = keycloak_drive_login_url(
            auth_url=kc_url,
            username=TEST_USER_USERNAME,
            password=TEST_USER_PASSWORD,
        )
        resp = client.get(f"/api/auth/oidc/callback?code={code}&state={state}")
        assert resp.status_code in (302, 307)
        return client.cookies.get("refresh_token")


@pytest.mark.oidc
@pytest.mark.slow
@pytest.mark.skipif(not authlib_available(), reason="authlib not installed")
def test_userinfo_cache_avoids_per_request_rtt(
    oidc_issuer_url, oidc_client_id, monkeypatch,
) -> None:
    """N refreshes within one cache window hit the userinfo cache.

    Counts outbound httpx calls from ``_validate_against_idp``. The
    first refresh after a fresh login is served from the cache (seeded
    by callback's ``cache_put``), so even the first refresh should
    NOT trigger an IdP RTT. We confirm by patching the helper.
    """
    from fastapi.testclient import TestClient
    from server.routes import oidc_routes

    app = _build_test_app()
    refresh_token = _complete_login_get_refresh_token(app, oidc_issuer_url)
    assert refresh_token

    # Patch the IdP validation to count calls.
    call_count = {"n": 0}
    original = oidc_routes._validate_against_idp

    async def counting_validate(rt: str) -> bool:
        call_count["n"] += 1
        return await original(rt)

    monkeypatch.setattr(oidc_routes, "_validate_against_idp", counting_validate)

    # 5 refreshes — the cache (seeded at login) covers all of them.
    with TestClient(app) as client:
        for i in range(5):
            resp = client.post(
                "/api/auth/oidc/refresh",
                json={"refresh_token": refresh_token},
            )
            assert resp.status_code == 200, (
                f"refresh #{i} failed: {resp.status_code} {resp.text[:200]!r}"
            )
            assert "access_token" in resp.json()

    assert call_count["n"] == 0, (
        f"userinfo cache should have served all 5 refreshes; "
        f"got {call_count['n']} IdP RTTs"
    )


@pytest.mark.oidc
@pytest.mark.slow
@pytest.mark.skipif(not authlib_available(), reason="authlib not installed")
def test_user_disabled_in_idp_revoked_within_ttl(
    oidc_issuer_url, oidc_client_id, monkeypatch,
) -> None:
    """When the IdP rejects a refresh, the session row is deleted + 401 returned.

    Simulates the IdP-side "user disabled" event by:
      1. Logging in normally (creates an OidcRefreshSession row).
      2. Invalidating the userinfo cache for this sub (forces the next
         refresh to call the IdP).
      3. Patching the IdP validation to return False (the IdP would
         do this for a disabled user).
      4. Calling /refresh — expecting 401 + session row deleted.

    Bounds the revocation latency to the userinfo-cache TTL, which is
    ≤ the access-token TTL (15 min) — satisfying the P3 acceptance
    requirement.
    """
    import asyncio

    from fastapi.testclient import TestClient
    from server.database.models import OidcRefreshSession
    from server.oidc import userinfo_cache
    from server.routes import oidc_routes
    from sqlalchemy import select

    app = _build_test_app()
    SessionLocal = app.state.test_session_local
    refresh_token = _complete_login_get_refresh_token(app, oidc_issuer_url)
    assert refresh_token

    # Verify a session row was created at login.
    async def _count_sessions():
        async with SessionLocal() as s:
            rows = (await s.execute(select(OidcRefreshSession))).scalars().all()
            return len(rows)
    assert asyncio.new_event_loop().run_until_complete(_count_sessions()) == 1

    # Force cache miss + simulate IdP rejection (disabled user).
    userinfo_cache.clear_all()

    async def _reject(rt: str) -> bool:
        return False
    monkeypatch.setattr(oidc_routes, "_validate_against_idp", _reject)

    with TestClient(app) as client:
        resp = client.post(
            "/api/auth/oidc/refresh",
            json={"refresh_token": refresh_token},
        )
    assert resp.status_code == 401, (
        f"disabled-user refresh should 401; got {resp.status_code} "
        f"body={resp.text[:300]!r}"
    )

    # Session row was deleted as part of the rejection.
    assert asyncio.new_event_loop().run_until_complete(_count_sessions()) == 0, (
        "rejected refresh must delete the OidcRefreshSession row"
    )


@pytest.mark.oidc
@pytest.mark.slow
@pytest.mark.skipif(not authlib_available(), reason="authlib not installed")
def test_logout_redirects_to_end_session_endpoint(
    oidc_issuer_url, oidc_client_id,
) -> None:
    """POST /api/auth/oidc/logout redirects to the IdP's end_session_endpoint.

    Asserts:
      - Response is a 302 to the IdP's end_session_endpoint
        (Keycloak advertises this in its discovery doc).
      - The post_logout_redirect_uri query param is included.
      - The OidcRefreshSession row was deleted.
      - access_token + refresh_token cookies are cleared.
    """
    import asyncio

    from fastapi.testclient import TestClient
    from server.database.models import OidcRefreshSession
    from sqlalchemy import select

    app = _build_test_app()
    SessionLocal = app.state.test_session_local
    refresh_token = _complete_login_get_refresh_token(app, oidc_issuer_url)
    assert refresh_token

    # Confirm session was created at login.
    async def _count_sessions():
        async with SessionLocal() as s:
            return len(
                (await s.execute(select(OidcRefreshSession))).scalars().all()
            )
    assert asyncio.new_event_loop().run_until_complete(_count_sessions()) == 1

    with TestClient(app, follow_redirects=False) as client:
        # The TestClient still has the refresh_token cookie from the
        # login flow we ran earlier — but that was a different client
        # instance, so we re-send it explicitly.
        resp = client.post(
            "/api/auth/oidc/logout",
            json={"refresh_token": refresh_token},
        )

    assert resp.status_code == 302, (
        f"logout should 302; got {resp.status_code} body={resp.text[:200]!r}"
    )
    loc = resp.headers["location"]
    # Keycloak advertises this path in its discovery doc.
    assert "/protocol/openid-connect/logout" in loc, (
        f"expected redirect to Keycloak's end-session URL; got {loc!r}"
    )
    assert "post_logout_redirect_uri=" in loc, (
        "post_logout_redirect_uri should be in the redirect URL"
    )

    # The session row was deleted.
    assert asyncio.new_event_loop().run_until_complete(_count_sessions()) == 0, (
        "logout must delete the OidcRefreshSession row"
    )

    # Cookies were cleared. RFC: delete-cookie is signalled by a
    # Set-Cookie with empty value + Max-Age=0. httpx exposes that on
    # the response cookies; the test client's cookie jar reflects it.
    set_cookie_headers = resp.headers.get_list("set-cookie")
    assert any("access_token=" in h and "Max-Age=0" in h for h in set_cookie_headers)
    assert any("refresh_token=" in h and "Max-Age=0" in h for h in set_cookie_headers)
