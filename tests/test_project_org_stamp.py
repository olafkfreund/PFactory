"""New projects carry the creating principal's org, so enforce mode shows them (#1036)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_WEB_SERVER_DIR = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))

from fastapi import HTTPException  # noqa: E402

# user id -> memberships: solo (member of A only), multi (A and B).
MEMBERS = [("solo", "A", "member"), ("multi", "A", "member"), ("multi", "B", "member")]


@pytest.fixture
def env(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from server.database.models import Base, Organization, OrgMember, User
    from server.routes import project_authz as pa
    from server.services import project_paths

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    session_local = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_local() as db:
            for uid in ("solo", "multi", "owner"):
                db.add(User(id=uid, email=f"{uid}@x.test", password_hash="x"))
            await db.flush()
            for oid in ("A", "B"):
                db.add(Organization(id=oid, name=oid, slug=oid, owner_id="owner"))
            await db.flush()
            for uid, oid, role in MEMBERS:
                db.add(OrgMember(org_id=oid, user_id=uid, role=role))
            await db.commit()

    asyncio.run(_init())
    settings = SimpleNamespace(DISABLE_AUTH=False, RBAC_ENFORCE_TASK_ROUTES=True)
    monkeypatch.setattr(pa, "get_settings", lambda: settings)

    async def _noop(**_kw):
        return None

    monkeypatch.setattr(pa.audit_service, "log_audit_event_bg", _noop)
    monkeypatch.setattr(project_paths, "get_projects_file", lambda: tmp_path / "projects.json")

    def run(coro_fn):
        async def _go():
            async with session_local() as db:
                return await coro_fn(db)

        return asyncio.run(_go())

    return SimpleNamespace(run=run, pa=pa)


def test_creator_org_by_principal(env):
    user = lambda **kw: {"id": "solo", **kw}  # noqa: E731
    cases = [
        (user(), "A"),  # one membership
        ({"id": "multi"}, None),  # ambiguous: left unset
        ({"id": "x", "api_key_id": "k", "org_id": "B"}, "B"),  # org key
        ({"id": "default", "is_service": True}, None),  # service principal
    ]
    for principal, expected in cases:
        assert env.run(lambda db, p=principal: env.pa.creator_org(p, db)) == expected


def test_ensure_tracked_project_stamps_org_and_is_visible_under_enforce(env):
    from server.routes import projects

    pid = projects.ensure_tracked_project("acme/app", "A")
    assert projects.load_projects()[pid]["org_id"] == "A"
    user = {"id": "solo"}
    env.run(lambda db: env.pa.authorize_project_for_user(user, pid, db))  # no 403
    with pytest.raises(HTTPException) as exc:
        env.run(lambda db: env.pa.authorize_project_for_user({"id": "multi"}, "nope", db))
    assert exc.value.status_code == 404


def test_unstamped_project_is_404_for_non_service(env):
    from server.routes import projects

    pid = projects.ensure_tracked_project("acme/orphan")  # service caller: no org
    assert "org_id" not in projects.load_projects()[pid]
    with pytest.raises(HTTPException) as exc:
        env.run(lambda db: env.pa.authorize_project_for_user({"id": "solo"}, pid, db))
    assert exc.value.status_code == 404


def test_add_project_stamps_creator_org(env, tmp_path, monkeypatch):
    from server.routes import projects

    monkeypatch.setattr(projects, "confine_to_workspace", lambda p: Path(p))
    req = SimpleNamespace(state=SimpleNamespace(user={"id": "solo"}))
    body = projects.ProjectCreate(path=str(tmp_path / "proj"))
    out = env.run(lambda db: projects.add_project(body, req, db))
    assert projects.load_projects()[out["id"]]["org_id"] == "A"
    env.run(lambda db: env.pa.authorize_project_for_user({"id": "solo"}, out["id"], db))
