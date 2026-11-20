"""Step 1 project authorization contract (#1099)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server import auth
from server.database.models import Base, Organization, OrgMember, User
from server.routes import project_authz


@pytest.fixture
def authz(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'authz.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def setup():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as db:
            for user_id in ("owner", "viewer", "outsider"):
                db.add(User(id=user_id, email=f"{user_id}@example.test", password_hash="x"))  # noqa: S106 - fixture value
            await db.flush()
            db.add(Organization(id="org", name="Org", slug="org", owner_id="owner"))
            await db.flush()
            db.add(OrgMember(org_id="org", user_id="owner", role="owner"))
            db.add(OrgMember(org_id="org", user_id="viewer", role="viewer"))
            await db.commit()

    asyncio.run(setup())
    monkeypatch.setattr(
        project_authz.project_paths,
        "load_projects",
        lambda: {
            "known": {"org_id": "org", "repo": "example/repo", "path": str(tmp_path / "repo")},
            "unstamped": {},
        },
    )
    monkeypatch.setattr(project_authz, "get_settings", lambda: SimpleNamespace(DISABLE_AUTH=False))

    async def audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(project_authz, "_audit_denial", audit)

    def run(principal, project_id="known", role="viewer"):
        async def check():
            async with sessions() as db:
                return await project_authz.require_project_role(principal, project_id, role, db=db)

        return asyncio.run(check())

    run.sessions = sessions
    yield run
    asyncio.run(engine.dispose())


def test_project_role_contract(authz):
    assert authz({"id": "owner"}).role == "owner"
    assert authz({"id": "viewer"}).role == "viewer"
    assert (
        authz({"id": "owner", "api_key_id": "key", "org_id": "org", "scopes": ("api",)}).role
        == "member"
    )
    assert (
        authz({"id": "owner", "api_key_id": "key", "org_id": "org", "scopes": ("mcp:read",)}).role
        == "viewer"
    )
    for principal, project_id, role, expected in (
        ({"id": "owner"}, "missing", "viewer", 404),
        ({"id": "owner"}, "unstamped", "viewer", 404),
        ({"id": "outsider"}, "known", "viewer", 404),
        ({"id": "viewer"}, "known", "member", 403),
        (
            {"id": "owner", "api_key_id": "key", "org_id": "other", "scopes": ("api",)},
            "known",
            "viewer",
            404,
        ),
        (
            {"id": "owner", "api_key_id": "key", "org_id": "org", "scopes": ("mcp:read",)},
            "known",
            "member",
            403,
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            authz(principal, project_id, role)
        assert exc.value.status_code == expected


def test_legacy_token_flag_warns_then_rejects(monkeypatch, caplog):
    settings = SimpleNamespace(API_TOKEN="fixture-token", LEGACY_SHARED_TOKEN_FULL_ACCESS=True)  # noqa: S106 - fixture token
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(auth, "_try_decode_jwt", lambda _token: None)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="fixture-token")
    assert asyncio.run(auth.verify_token(credentials)) == "fixture-token"
    assert "DEPRECATED: legacy shared token used" in caplog.text

    settings.LEGACY_SHARED_TOKEN_FULL_ACCESS = False
    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth.verify_token(credentials))
    assert exc.value.status_code == 401


def test_visible_projects_and_indirect_resolution(authz, tmp_path):
    async def check():
        async with authz.sessions() as db:
            owner = {"id": "owner"}
            outsider = {"id": "outsider"}
            assert await project_authz.visible_project_ids(owner, db) == {"known"}
            assert await project_authz.visible_project_ids(outsider, db) == set()
            assert (
                await project_authz.require_path_project_role(
                    owner, str(tmp_path / "repo" / "file.txt"), "viewer", db
                )
            ).project_id == "known"
            assert (
                await project_authz.require_repo_project_role(owner, "example/repo", "viewer", db)
            ).project_id == "known"
            assert (
                await project_authz.require_repo_project_role(
                    owner, "example/new", "member", db, for_intake=True
                )
            ).org_id == "org"
            with pytest.raises(HTTPException) as exc:
                await project_authz.require_repo_project_role(
                    {"id": "viewer"}, "example/new", "member", db, for_intake=True
                )
            assert exc.value.status_code == 403
            with pytest.raises(HTTPException) as exc:
                await project_authz.require_repo_project_role(
                    outsider, "example/repo", "viewer", db
                )
            assert exc.value.status_code == 404

    asyncio.run(check())


def test_plan_intake_requires_org_membership_and_role(authz):
    async def check():
        async with authz.sessions() as db:
            assert await project_authz.authorize_plan_ingest({"id": "owner"}, None, db) == "org"
            for principal, expected in (({"id": "viewer"}, 403), ({"id": "outsider"}, 404)):
                with pytest.raises(HTTPException) as exc:
                    await project_authz.authorize_plan_ingest(principal, None, db)
                assert exc.value.status_code == expected

    asyncio.run(check())
