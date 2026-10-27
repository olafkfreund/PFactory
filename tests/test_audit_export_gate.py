"""Audit-export org gate (PFactory#941; mirrors AIFactory#1915)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

_WEB_SERVER_DIR = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))


@pytest.fixture
def client(tmp_path):
    """Export router on SQLite: org A (admin-a, member-a), org B (admin-b, admin-a)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from server.database.models import AuditLog, Base, Organization, OrgMember, User
    from server.routes import audit

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    session_local = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_local() as db:
            for uid in ("admin-a", "member-a", "admin-b"):
                db.add(User(id=uid, email=f"{uid}@x.test", password_hash="x"))
            await db.flush()
            db.add(Organization(id="A", name="a", slug="a", owner_id="admin-a"))
            db.add(Organization(id="B", name="b", slug="b", owner_id="admin-b"))
            await db.flush()
            db.add(OrgMember(org_id="A", user_id="admin-a", role="admin"))
            db.add(OrgMember(org_id="A", user_id="member-a", role="member"))
            db.add(OrgMember(org_id="B", user_id="admin-b", role="admin"))
            db.add(OrgMember(org_id="B", user_id="admin-a", role="admin"))
            db.add(AuditLog(org_id="A", action="a.act", resource_type="t", chain_seq=1))
            db.add(AuditLog(org_id="B", action="b.act", resource_type="t", chain_seq=2))
            await db.commit()

    asyncio.run(_init())

    app = FastAPI()
    app.include_router(audit.export_router)

    async def _db():
        async with session_local() as session:
            yield session

    async def _user(request: Request):
        return User(id=request.headers["X-Test-Principal"])

    # Override on the module's own references: other tests reload server.*.
    app.dependency_overrides[audit.get_db] = _db
    app.dependency_overrides[audit.get_current_user] = _user

    @app.middleware("http")
    async def _principal(request: Request, call_next):
        request.state.user = {"id": request.headers["X-Test-Principal"], "role": "admin"}
        if "X-Test-Service" in request.headers:
            request.state.user["is_service"] = True
        if key_org := request.headers.get("X-Test-Key-Org"):
            request.state.user.update(api_key_id="k1", org_id=key_org)
        return await call_next(request)

    with TestClient(app) as c:
        yield c
    asyncio.run(engine.dispose())


def _export(client, principal, query="", **headers):
    return client.get(
        f"/api/audit/export{query}",
        headers={"X-Test-Principal": principal, **headers},
    )


def test_export_by_org_admin_returns_only_that_orgs_rows(client):
    r = _export(client, "admin-a", "?org_id=A")
    assert r.status_code == 200
    assert "a.act" in r.text
    assert "b.act" not in r.text


def test_export_by_org_member_is_forbidden(client):
    # The JWT says role=admin; only the OrgMember row counts.
    assert _export(client, "member-a", "?org_id=A").status_code == 403


def test_export_by_non_member_is_forbidden(client):
    assert _export(client, "admin-b", "?org_id=A").status_code == 403


def test_unscoped_export_by_human_is_forbidden(client):
    assert _export(client, "admin-a").status_code == 403
    assert _export(client, "admin-a", "?format=csv").status_code == 403


def test_unscoped_export_by_service_principal_succeeds(client):
    r = _export(client, "default", **{"X-Test-Service": "1"})
    assert r.status_code == 200
    assert "a.act" in r.text
    assert "b.act" in r.text


def test_scoped_export_by_service_principal_succeeds(client):
    r = _export(client, "default", "?org_id=B", **{"X-Test-Service": "1"})
    assert r.status_code == 200
    assert "b.act" in r.text


def test_key_cannot_export_another_org(client):
    # admin-a is admin in both orgs, but the key is bound to A.
    assert _export(client, "admin-a", "?org_id=B").status_code == 200
    r = _export(client, "admin-a", "?org_id=B", **{"X-Test-Key-Org": "A"})
    assert r.status_code == 403
    assert _export(client, "admin-a", "?org_id=A", **{"X-Test-Key-Org": "A"}).status_code == 200


def test_legacy_token_is_marked_service(monkeypatch):
    # The gate trusts ``is_service``; only the real middleware may set it.
    from server import auth, config

    settings = config.get_settings()
    monkeypatch.setattr(settings, "API_TOKEN", "legacy-token-for-test")
    monkeypatch.setattr(settings, "DISABLE_AUTH", False)
    app = FastAPI()
    app.add_middleware(auth.TokenAuthMiddleware)

    @app.get("/api/whoami")
    async def whoami(request: Request):
        return request.state.user

    c = TestClient(app)
    r = c.get("/api/whoami", headers={"Authorization": "Bearer legacy-token-for-test"})
    assert r.json()["is_service"] is True
