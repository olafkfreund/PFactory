"""Org scoping for plan session reads and lazy org stamps (#968)."""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_WEB_SERVER_DIR = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))
_BACKEND_DIR = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from plan.service import PlanService  # noqa: E402
from server.jobstore import PlanSessionStore  # noqa: E402
from server.jobstore.plan_session_models import (  # noqa: E402
    PlanSessionCounter,
    PlanSessionRow,
)

_PLAN = """# Widget service
A FastAPI service tested with pytest.
## Acceptance Criteria
- exposes a widget API
"""


@pytest.fixture(autouse=True)
def _no_project_tracking(monkeypatch) -> None:
    """Stub the tracked-project side effect on ingest."""
    projects = importlib.import_module("server.routes.projects")
    monkeypatch.setattr(projects, "ensure_tracked_project", lambda _repo: None)


@pytest.fixture
def env(tmp_path, monkeypatch):
    models = importlib.import_module("server.database.models")
    pa = importlib.import_module("server.routes.project_authz")
    pp = importlib.import_module("server.routes.plan_pipeline")

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    session_local = async_sessionmaker(engine, expire_on_commit=False)

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(models.Base.metadata.create_all)
        async with session_local() as db:
            for uid in ("viewer-a", "owner"):
                db.add(models.User(id=uid, email=f"{uid}@x.test", password_hash=uid))
            await db.flush()
            db.add(models.Organization(id="A", name="A", slug="A", owner_id="owner"))
            db.add(models.Organization(id="B", name="B", slug="B", owner_id="owner"))
            await db.flush()
            db.add(models.OrgMember(org_id="A", user_id="viewer-a", role="viewer"))
            await db.commit()

    asyncio.run(_init())
    audits: list[dict[str, Any]] = []

    async def _capture(**kw: Any) -> None:
        audits.append(kw)

    monkeypatch.setattr(pa.audit_service, "log_audit_event_bg", _capture)
    projects = {
        "pa": {"repo": "acme/a", "org_id": "A"},
        "pb": {"repo": "acme/b", "org_id": "B"},
    }
    # Patched on the registry module, which dev's routes also read (tests run on dev).
    registry = importlib.import_module("server.services.project_paths")
    monkeypatch.setattr(registry, "load_projects", lambda: projects)
    settings = SimpleNamespace(DISABLE_AUTH=False, RBAC_ENFORCE_TASK_ROUTES=False)
    monkeypatch.setattr(pa, "get_settings", lambda: settings)

    service = PlanService(persist=False)
    monkeypatch.setattr(pp, "SERVICE", service)
    sid_a = service.ingest_text(_PLAN, title="A", repo="acme/a").session_id
    sid_b = service.ingest_text(_PLAN, title="B", repo="acme/b").session_id
    sid_u = service.ingest_text(_PLAN, title="U").session_id

    app = FastAPI()
    app.include_router(pp.router)

    async def _db():
        async with session_local() as db:
            yield db

    app.dependency_overrides[pp.get_db] = _db

    @app.middleware("http")
    async def _principal(request: Request, call_next):
        user: dict[str, Any] = {"id": request.headers.get("X-U", "viewer-a")}
        if "X-Org" in request.headers:
            user["org_id"] = request.headers["X-Org"]
        if "X-Key" in request.headers:
            user["api_key_id"] = request.headers["X-Key"]
        request.state.user = user
        return await call_next(request)

    yield TestClient(app), settings, audits, service, registry, session_local, (sid_a, sid_b, sid_u)
    asyncio.run(engine.dispose())


def test_rest_org_key_scopes_list_and_per_session_reads(env):
    client, _, _, _, _, _, (sid_a, sid_b, sid_u) = env
    key_b = {"X-U": "viewer-a", "X-Key": "key-b", "X-Org": "B"}

    listed = client.get("/api/plan/sessions", headers=key_b)
    assert listed.status_code == 200
    assert [s["session_id"] for s in listed.json()["sessions"]] == [sid_b]

    for sid in (sid_a, sid_u):
        for suffix in ("", "/audit-pack"):
            resp = client.get(f"/api/plan/sessions/{sid}{suffix}", headers=key_b)
            assert resp.status_code == 404
            assert resp.json()["detail"] == f"unknown session '{sid}'"

    for suffix in ("", "/audit-pack"):
        assert client.get(f"/api/plan/sessions/{sid_b}{suffix}", headers=key_b).status_code == 200

    # Org-A key on the unknown session also gets 404 (not a cross-org A!=B mismatch).
    key_a = {"X-U": "viewer-a", "X-Key": "key-a", "X-Org": "A"}
    for suffix in ("", "/audit-pack"):
        assert client.get(f"/api/plan/sessions/{sid_u}{suffix}", headers=key_a).status_code == 404
    assert client.get(f"/api/plan/sessions/{sid_a}", headers=key_a).status_code == 200


def test_mcp_org_key_and_legacy_key_on_real_plan_handlers(env):
    _, _, _, _, _, session_local, (sid_a, sid_b, sid_u) = env
    proxy = importlib.import_module("server.mcp_stdio.router")
    auth = importlib.import_module("server.mcp_remote.auth")
    legacy = importlib.import_module("server.mcp_stdio.auth")._LegacyAdminKey()
    key_b = auth.AuthenticatedKey(
        key_id="key-b",
        scopes=frozenset({"mcp:read"}),
        user_id="viewer-a",
        org_id="B",
    )
    key_a = auth.AuthenticatedKey(
        key_id="key-a",
        scopes=frozenset({"mcp:read"}),
        user_id="viewer-a",
        org_id="A",
    )
    selected: dict[str, Any] = {"key": key_b}

    app = FastAPI()
    app.include_router(proxy.router)

    async def _db():
        async with session_local() as db:
            yield db

    app.dependency_overrides[proxy.get_db] = _db
    for route in app.routes:
        if hasattr(route, "dependant"):
            for dependency in route.dependant.dependencies:
                if dependency.call.__closure__:
                    app.dependency_overrides[dependency.call] = lambda: selected["key"]

    mcp = TestClient(app)
    listed = mcp.get("/api/mcp-stdio/plan/sessions")
    assert listed.status_code == 200
    assert [s["session_id"] for s in listed.json()["sessions"]] == [sid_b]

    for sid in (sid_a, sid_u):
        for suffix in ("", "/audit-pack"):
            resp = mcp.get(f"/api/mcp-stdio/plan/sessions/{sid}{suffix}")
            assert resp.status_code == 404
            assert resp.json()["detail"] == f"unknown session '{sid}'"

    for suffix in ("", "/audit-pack"):
        assert mcp.get(f"/api/mcp-stdio/plan/sessions/{sid_b}{suffix}").status_code == 200

    selected["key"] = key_a
    for suffix in ("", "/audit-pack"):
        assert mcp.get(f"/api/mcp-stdio/plan/sessions/{sid_u}{suffix}").status_code == 404

    selected["key"] = legacy
    all_sessions = mcp.get("/api/mcp-stdio/plan/sessions").json()["sessions"]
    assert {s["session_id"] for s in all_sessions} == {sid_a, sid_b, sid_u}
    for sid in (sid_a, sid_b, sid_u):
        assert mcp.get(f"/api/mcp-stdio/plan/sessions/{sid}").status_code == 200


def test_human_viewer_shadow_and_enforce_modes(env):
    client, settings, audits, _, _, _, (sid_a, sid_b, sid_u) = env
    headers = {"X-U": "viewer-a"}

    # Flag off: B session returns 200 with one shadow audit row; A and unknown have no audit.
    assert client.get(f"/api/plan/sessions/{sid_b}", headers=headers).status_code == 200
    assert len(audits) == 1
    assert audits[0]["details"]["shadow"] is True
    assert audits[0]["resource_id"] == sid_b

    assert client.get(f"/api/plan/sessions/{sid_a}", headers=headers).status_code == 200
    assert client.get(f"/api/plan/sessions/{sid_u}", headers=headers).status_code == 200
    assert len(audits) == 1

    # Flag on: B and unknown return 404, A returns 200, and the list holds only A.
    settings.RBAC_ENFORCE_TASK_ROUTES = True
    assert client.get(f"/api/plan/sessions/{sid_b}", headers=headers).status_code == 404
    assert client.get(f"/api/plan/sessions/{sid_b}/audit-pack", headers=headers).status_code == 404
    assert client.get(f"/api/plan/sessions/{sid_u}", headers=headers).status_code == 404
    assert client.get(f"/api/plan/sessions/{sid_a}", headers=headers).status_code == 200

    listed = client.get("/api/plan/sessions", headers=headers).json()["sessions"]
    assert [s["session_id"] for s in listed] == [sid_a]


def test_rule2_lazy_stamp_persists_once_and_never_reresolves(env, monkeypatch):
    client, settings, _, service, registry, _, (sid_a, _, _) = env

    # First read resolves acme/a -> org A and persists the lazy stamp.
    assert client.get(f"/api/plan/sessions/{sid_a}", headers={"X-U": "viewer-a"}).status_code == 200
    assert service.org_stamps() == {sid_a: "A"}

    # Re-register acme/a under org B: the persisted stamp still wins.
    monkeypatch.setattr(
        registry,
        "load_projects",
        lambda: {"pa": {"repo": "acme/a", "org_id": "B"}, "pb": {"repo": "acme/b", "org_id": "B"}},
    )
    key_b = {"X-U": "viewer-a", "X-Key": "key-b", "X-Org": "B"}
    assert client.get(f"/api/plan/sessions/{sid_a}", headers=key_b).status_code == 404

    settings.RBAC_ENFORCE_TASK_ROUTES = True
    assert client.get(f"/api/plan/sessions/{sid_a}", headers={"X-U": "viewer-a"}).status_code == 200
    assert service.org_stamps() == {sid_a: "A"}


@pytest.mark.parametrize(
    "projects",
    [
        {"pb": {"repo": "acme/b", "org_id": "B"}},
        {"pa": {"repo": "acme/a", "org_id": "A"}, "pb": {"repo": "acme/a", "org_id": "B"}},
        {"pa": {"repo": "acme/a"}},
    ],
)
def test_rule3_unmapped_ambiguous_or_orgless_repo_is_unknown(env, monkeypatch, projects):
    client, _, audits, service, registry, _, (sid_a, _, _) = env
    # Patched on the registry module, which dev's routes also read (tests run on dev).
    registry = importlib.import_module("server.services.project_paths")
    monkeypatch.setattr(registry, "load_projects", lambda: projects)

    key_a = {"X-U": "viewer-a", "X-Key": "key-a", "X-Org": "A"}
    assert client.get(f"/api/plan/sessions/{sid_a}", headers=key_a).status_code == 404
    assert client.get("/api/plan/sessions", headers=key_a).json()["sessions"] == []

    audits.clear()
    assert client.get(f"/api/plan/sessions/{sid_a}", headers={"X-U": "viewer-a"}).status_code == 200
    assert audits == []
    assert sid_a not in service.org_stamps()
    # Checked last, so on dev the HTTP assertions above are what fail.
    pp = importlib.import_module("server.routes.plan_pipeline")
    assert pp._repo_org("acme/a", projects) is None


def test_persisting_read_does_not_break_inflight_process_save(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'plan_sessions.db'}"

    async def _create_schema() -> None:
        eng = create_async_engine(url)
        async with eng.begin() as conn:
            await conn.run_sync(PlanSessionRow.__table__.create)
            await conn.run_sync(PlanSessionCounter.__table__.create)
        await eng.dispose()

    asyncio.run(_create_schema())
    store = PlanSessionStore(database_url=url)
    try:
        svc = PlanService(store_dir=tmp_path / "sessions", persist=False, session_store=store)
        sid = svc.ingest_text(_PLAN, title="In-flight", repo="acme/a").session_id

        copy = svc.get(sid)  # as process() does before its long run
        assert svc.session_org(svc.get(sid), lambda _s: "A") == "A"

        copy.selected_category = "updated-by-process"
        svc._save(copy)  # must not raise StaleSessionError
        assert svc.org_stamps() == {sid: "A"}
    finally:
        store.close()


def test_stamped_payload_org_beats_derived_and_gitlab_prefix_resolves(env):
    _, _, _, service, registry, _, _ = env
    pp = importlib.import_module("server.routes.plan_pipeline")
    projects = registry.load_projects()

    stamped = service.ingest_text(_PLAN, title="Stamped", repo="acme/a", org_id="B")
    assert pp._session_org(stamped, None, projects=projects) == "B"
    assert service.org_stamps() == {}

    assert pp._repo_org("gitlab:acme/a", projects) == "A"
    gl_session = service.ingest_text(_PLAN, title="GitLab", repo="gitlab:acme/a")
    assert pp._session_org(gl_session, None, projects=projects) == "A"


def test_ingest_text_stamps_org_from_repo(env):
    client, _, _, service, _, _, _ = env
    resp = client.post(
        "/api/plan/sessions/ingest-text",
        json={"text": _PLAN, "title": "Ingested", "repo": "acme/a"},
    )
    assert resp.status_code == 200
    assert resp.json()["org_id"] == "A"
    assert service.get(resp.json()["session_id"]).org_id == "A"


def test_registry_error_with_org_key_on_unstamped_session_returns_503(env, monkeypatch):
    client, _, _, _, registry, _, (sid_a, _, _) = env

    def _boom():
        raise RuntimeError("projects.json unreadable")

    monkeypatch.setattr(registry, "load_projects", _boom)
    key_b = {"X-U": "viewer-a", "X-Key": "key-b", "X-Org": "B"}
    assert client.get(f"/api/plan/sessions/{sid_a}", headers=key_b).status_code == 503
    assert client.get(f"/api/plan/sessions/{sid_a}/audit-pack", headers=key_b).status_code == 503


def test_human_under_enforce_loses_orgless_sessions(env, monkeypatch):
    client, settings, _, _, registry, _, (sid_a, _, _) = env
    monkeypatch.setattr(registry, "load_projects", lambda: {"pa": {"repo": "acme/a"}})
    settings.RBAC_ENFORCE_TASK_ROUTES = True
    headers = {"X-U": "viewer-a"}
    assert client.get(f"/api/plan/sessions/{sid_a}", headers=headers).status_code == 404
    assert client.get("/api/plan/sessions", headers=headers).json()["sessions"] == []


def test_org_key_ingest_of_unregistered_repo_stamps_key_org(env):
    client, _, _, service, _, _, _ = env
    key_b = {"X-U": "viewer-a", "X-Key": "key-b", "X-Org": "B"}
    resp = client.post(
        "/api/plan/sessions/ingest-text",
        json={"text": _PLAN, "title": "Mine", "repo": "acme/new"},
        headers=key_b,
    )
    assert resp.status_code == 200
    sid = resp.json()["session_id"]
    assert service.get(sid).org_id == "B"
    # The key reads its own session back, and sees it in its list.
    assert client.get(f"/api/plan/sessions/{sid}", headers=key_b).status_code == 200
    listed = client.get("/api/plan/sessions", headers=key_b).json()["sessions"]
    assert sid in {s["session_id"] for s in listed}


def test_human_ingest_of_unregistered_repo_stamps_no_org(env):
    client, _, _, service, _, _, _ = env
    resp = client.post(
        "/api/plan/sessions/ingest-text",
        json={"text": _PLAN, "title": "Theirs", "repo": "acme/new"},
        headers={"X-U": "viewer-a"},
    )
    assert resp.status_code == 200
    assert service.get(resp.json()["session_id"]).org_id is None


def test_org_key_cross_org_ingest_is_refused_with_flag_off(env):
    client, settings, audits, service, _, _, _ = env
    assert settings.RBAC_ENFORCE_TASK_ROUTES is False
    before = {s["session_id"] for s in service.list_sessions()}
    key_b = {"X-U": "viewer-a", "X-Key": "key-b", "X-Org": "B"}
    resp = client.post(
        "/api/plan/sessions/ingest-text",
        json={"text": _PLAN, "title": "Theirs", "repo": "acme/a"},
        headers=key_b,
    )
    assert resp.status_code == 403
    assert {s["session_id"] for s in service.list_sessions()} == before
    # One enforced (non-shadow) denial, on org A. The route's PlanAccessChecker may
    # add its own shadow row: viewer-a holds no role in the key's org B.
    enforced = [a for a in audits if not a["details"].get("shadow")]
    assert [a["org_id"] for a in enforced] == ["A"]


def test_human_cross_org_ingest_shadow_then_enforce(env):
    client, settings, audits, service, _, _, _ = env
    # viewer-a is only a viewer in A; ingest needs member in the repo's org (B here).
    body = {"text": _PLAN, "title": "Cross", "repo": "acme/b"}
    resp = client.post("/api/plan/sessions/ingest-text", json=body, headers={"X-U": "viewer-a"})
    assert resp.status_code == 200
    assert service.get(resp.json()["session_id"]).org_id == "B"
    assert [a["details"].get("shadow") for a in audits if a["org_id"] == "B"] == [True]

    settings.RBAC_ENFORCE_TASK_ROUTES = True
    resp = client.post("/api/plan/sessions/ingest-text", json=body, headers={"X-U": "viewer-a"})
    assert resp.status_code == 403


def test_mcp_ingest_registry_error_with_org_key_returns_503(env, monkeypatch):
    _, _, _, _, registry, session_local, _ = env
    proxy = importlib.import_module("server.mcp_stdio.router")
    auth = importlib.import_module("server.mcp_remote.auth")
    key_b = auth.AuthenticatedKey(
        key_id="key-b", scopes=frozenset({"plan:write"}), user_id="viewer-a", org_id="B"
    )
    app = FastAPI()
    app.include_router(proxy.router)

    async def _db():
        async with session_local() as db:
            yield db

    app.dependency_overrides[proxy.get_db] = _db
    for route in app.routes:
        if hasattr(route, "dependant"):
            for dependency in route.dependant.dependencies:
                if dependency.call.__closure__:
                    app.dependency_overrides[dependency.call] = lambda: key_b
    monkeypatch.setattr(proxy, "log_audit_event_bg", _noop_audit)

    def _boom():
        raise RuntimeError("projects.json unreadable")

    monkeypatch.setattr(registry, "load_projects", _boom)
    resp = TestClient(app).post(
        "/api/mcp-stdio/plan/sessions/ingest-text",
        json={"text": _PLAN, "title": "X", "repo": "acme/a"},
    )
    assert resp.status_code == 503


async def _noop_audit(**_kw: Any) -> None:
    return None
