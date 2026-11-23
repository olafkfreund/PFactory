"""New projects carry the creating principal's org, so enforce mode shows them (#1036)."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import delete

_WEB_SERVER_DIR = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))

from fastapi import HTTPException, UploadFile  # noqa: E402

from server.database.models import Organization, OrgMember  # noqa: E402
from server.routes import projects  # noqa: E402

SERVICE = {"id": "default", "is_service": True}


async def _remove_orgs(db, *org_ids):
    await db.execute(delete(OrgMember).where(OrgMember.org_id.in_(org_ids)))
    await db.execute(delete(Organization).where(Organization.id.in_(org_ids)))
    await db.commit()

# user id -> memberships: solo (member of A only), multi (A and B).
MEMBERS = [("solo", "A", "member"), ("multi", "A", "member"), ("multi", "B", "member")]


@pytest.fixture
def env(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from server.database.models import Base, User
    from server.routes import project_authz as pa
    from server.services import project_paths

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    session_local = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_local() as db:
            for uid in ("solo", "multi", "owner", "outsider"):
                db.add(User(id=uid, email=f"{uid}@x.test", password_hash="x"))
            await db.flush()
            for oid in ("A", "B"):
                db.add(Organization(id=oid, name=oid, slug=oid, owner_id="owner"))
            await db.flush()
            for uid, oid, role in MEMBERS:
                db.add(OrgMember(org_id=oid, user_id=uid, role=role))
            db.add(OrgMember(org_id="B", user_id="outsider", role="member"))
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

    return SimpleNamespace(run=run, pa=pa, session_local=session_local)


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
    pid = projects.ensure_tracked_project("acme/app", "A")
    assert projects.load_projects()[pid]["org_id"] == "A"
    user = {"id": "solo"}
    env.run(lambda db: env.pa.authorize_project_for_user(user, pid, db))  # no 403
    with pytest.raises(HTTPException) as outsider:
        env.run(lambda db: env.pa.authorize_project_for_user({"id": "outsider"}, pid, db))
    with pytest.raises(HTTPException) as exc:
        env.run(lambda db: env.pa.authorize_project_for_user({"id": "multi"}, "nope", db))
    assert outsider.value.status_code == exc.value.status_code == 404
    assert outsider.value.detail == exc.value.detail


def test_backfill_only_single_org_and_idempotent(env, monkeypatch):
    from server import main  # noqa: PLC0415 - defer app initialization until test execution

    monkeypatch.setattr(main, "async_session_factory", env.session_local)
    pid = projects.ensure_tracked_project("acme/legacy")
    assert env.run(env.pa.resolve_default_org_id) is None
    service = {"id": "default", "is_service": True}
    assert env.run(
        lambda db: env.pa.require_repo_project_role(service, "acme/new", "member", db, for_intake=True)
    ).org_id is None
    asyncio.run(main.backfill_project_orgs())
    assert "org_id" not in projects.load_projects()[pid]

    async def remove_second_org(db):
        await db.execute(delete(OrgMember).where(OrgMember.org_id == "B"))
        await db.execute(delete(Organization).where(Organization.id == "B"))
        await db.commit()

    env.run(remove_second_org)
    assert env.run(env.pa.resolve_default_org_id) == "A"
    assert env.run(
        lambda db: env.pa.require_repo_project_role(service, "acme/new", "member", db, for_intake=True)
    ).org_id == "A"
    asyncio.run(main.backfill_project_orgs())
    assert projects.load_projects()[pid]["org_id"] == "A"
    before = projects.load_projects()
    asyncio.run(main.backfill_project_orgs())
    assert projects.load_projects() == before


def test_backfill_repairs_legacy_default_and_logs(env, monkeypatch, caplog):
    from server import main

    monkeypatch.setattr(main, "async_session_factory", env.session_local)
    projects.save_projects({"legacy": {"path": "", "org_id": "default"}})
    with caplog.at_level(logging.WARNING, logger=main.logger.name):
        asyncio.run(main.backfill_project_orgs())
    assert projects.load_projects()["legacy"]["org_id"] == "default"
    assert "project.org_unassigned project_id=legacy" in caplog.text

    env.run(lambda db: _remove_orgs(db, "B"))
    with caplog.at_level(logging.INFO, logger=main.logger.name):
        asyncio.run(main.backfill_project_orgs())
    assert projects.load_projects()["legacy"]["org_id"] == "A"
    assert "project.org_backfilled project_id=legacy org_id=A" in caplog.text


def test_backfill_zero_orgs_and_invalid_registry_continue(env, monkeypatch, caplog):
    from server import main
    from server.services import project_paths

    monkeypatch.setattr(main, "async_session_factory", env.session_local)
    env.run(lambda db: _remove_orgs(db, "A", "B"))
    path = project_paths.get_projects_file()
    projects.save_projects({"orphan": {"path": ""}})
    with caplog.at_level(logging.WARNING, logger=main.logger.name):
        asyncio.run(main.backfill_project_orgs())
    assert "project.org_unassigned project_id=orphan" in caplog.text
    assert "org_id" not in projects.load_projects()["orphan"]
    for bad in ('{', '[]', '{"orphan": null}'):
        path.write_text(bad)
        with caplog.at_level(logging.ERROR, logger=main.logger.name):
            asyncio.run(main.backfill_project_orgs())
        assert path.read_text() == bad
    assert caplog.text.count("project.org_backfill_invalid_registry") == 3


def test_save_projects_uses_same_lock_and_atomic_replace(env, monkeypatch):
    from server.services import project_paths

    path = project_paths.get_projects_file()
    projects.save_projects({"old": {"path": ""}})
    old = path.read_text()
    original_replace = project_paths.os.replace

    def fail_replace(_src, _dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(project_paths.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failure"):
        projects.save_projects({"new": {"path": ""}})
    assert path.read_text() == old
    monkeypatch.setattr(project_paths.os, "replace", original_replace)

    entered = threading.Event()
    finished = threading.Event()

    def writer():
        entered.set()
        projects.save_projects({"new": {"path": ""}})
        finished.set()

    with project_paths.locked_projects_file(path):
        thread = threading.Thread(target=writer)
        thread.start()
        assert entered.wait(1)
        assert not finished.wait(0.05)
    thread.join(timeout=1)
    assert finished.is_set()
    assert json.loads(path.read_text()) == {"new": {"path": ""}}


def test_service_project_role_and_ingest_keep_multi_org_unstamped(env, monkeypatch):
    from plan.service import PlanService
    from server.routes import plan_pipeline as pp

    pid = projects.ensure_tracked_project("acme/legacy")
    access = env.run(
        lambda db: env.pa.require_project_role(SERVICE, pid, "member", db=db)
    )
    assert access.org_id is None
    service = PlanService()
    monkeypatch.setattr(pp, "SERVICE", service)
    request = SimpleNamespace(headers={}, state=SimpleNamespace(user=SERVICE))
    body = pp.IngestTextBody(
        text="# Legacy\n## Acceptance Criteria\n- It works\n", repo="acme/legacy"
    )
    out = env.run(lambda db: pp.ingest_text(body, request, db))
    assert service.get(out["session_id"]).org_id is None
    assert "org_id" not in projects.load_projects()[pid]


@pytest.mark.parametrize("single_org", [False, True])
def test_add_project_service_principal(env, tmp_path, monkeypatch, single_org):
    if single_org:
        env.run(lambda db: _remove_orgs(db, "B"))
    monkeypatch.setattr(projects, "confine_to_workspace", lambda p: Path(p))
    request = SimpleNamespace(state=SimpleNamespace(user=SERVICE))
    body = projects.ProjectCreate(path=str(tmp_path / "service-project"))
    out = env.run(lambda db: projects.add_project(body, request, db))
    assert projects.load_projects()[out["id"]].get("org_id") == ("A" if single_org else None)


def test_proxy_add_project_service_principal(env, tmp_path, monkeypatch):
    import importlib

    from server.mcp_stdio.auth import _LegacyAdminKey

    proxy = importlib.import_module("server.mcp_stdio.router")
    monkeypatch.setattr(projects, "confine_to_workspace", lambda p: Path(p))

    async def no_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(proxy, "_audit_mcp_write", no_audit)

    class Request:
        def __init__(self, name):
            self.name = name

        async def body(self):
            return json.dumps({"path": str(tmp_path / self.name)}).encode()

    out = env.run(lambda db: proxy.proxy_add_project(Request("proxy-multi"), db, _LegacyAdminKey()))
    assert projects.load_projects()[out["id"]].get("org_id") is None
    env.run(lambda db: _remove_orgs(db, "B"))
    out = env.run(lambda db: proxy.proxy_add_project(Request("proxy-single"), db, _LegacyAdminKey()))
    assert projects.load_projects()[out["id"]]["org_id"] == "A"


def test_ingest_routes_pass_authorized_org_to_tracking(env, monkeypatch):
    from plan.service import PlanService
    from server.routes import plan_pipeline as pp

    service = PlanService()
    monkeypatch.setattr(pp, "SERVICE", service)
    request = SimpleNamespace(headers={}, state=SimpleNamespace(user={"id": "solo"}))
    body = pp.IngestTextBody(
        text="# Text\n## Acceptance Criteria\n- It works\n", repo="acme/text"
    )
    text_out = env.run(lambda db: pp.ingest_text(body, request, db))
    assert service.get(text_out["session_id"]).org_id == "A"
    assert next(p for p in projects.load_projects().values() if p.get("repo") == "acme/text")["org_id"] == "A"

    upload = UploadFile(filename="plan.md", file=io.BytesIO(body.text.encode()))
    upload_out = env.run(
        lambda db: pp.ingest_upload(
            request, upload, title="Upload", category="", template="",
            repo="acme/upload", base_ref=None, db=db,
        )
    )
    assert service.get(upload_out["session_id"]).org_id == "A"
    assert next(p for p in projects.load_projects().values() if p.get("repo") == "acme/upload")["org_id"] == "A"


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
