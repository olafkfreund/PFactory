"""Plan-session writes must authorize the session's organization (#977)."""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_WEB_SERVER_DIR = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))
_BACKEND_DIR = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from plan.service import PlanService  # noqa: E402

_PLAN = """# Widget service
A FastAPI service tested with pytest.
## Acceptance Criteria
- exposes a widget API
"""


@pytest.fixture(autouse=True)
def _no_project_tracking(monkeypatch) -> None:
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
    registry = importlib.import_module("server.services.project_paths")
    monkeypatch.setattr(registry, "load_projects", lambda: projects)
    settings = SimpleNamespace(DISABLE_AUTH=False, RBAC_ENFORCE_TASK_ROUTES=False)
    monkeypatch.setattr(pa, "get_settings", lambda: settings)

    service = PlanService(persist=False)
    monkeypatch.setattr(pp, "SERVICE", service)
    sid_a = service.ingest_text(_PLAN, title="Secret org A title", repo="acme/a").session_id
    sid_b = service.ingest_text(_PLAN, title="B", repo="acme/b").session_id

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

    yield TestClient(app), settings, audits, service, session_local, (sid_a, sid_b)
    asyncio.run(engine.dispose())


_WRITES = [
    ("POST", "/process", None, "process_async"),
    ("POST", "/suggestions/apply", {"accepted": [], "reprocess": False}, "apply_suggestions"),
    ("POST", "/re-gate", None, "re_gate"),
    ("POST", "/approve", {"approver": "x"}, "approve"),
    (
        "POST",
        "/access/approve",
        {"resource": "repo", "approved_by": "x", "scope": "read"},
        "approve_access",
    ),
    ("POST", "/waive", {"check_ids": [], "reason": "x", "waived_by": "x"}, "waive"),
    ("POST", "/reject", {"approver": "x", "feedback": "x"}, "reject"),
    ("POST", "/discard", {"actor": "x", "reason": "x"}, "discard"),
    ("DELETE", "", {"actor": "x"}, "delete_session"),
    ("POST", "/emit", {"repo": "acme/a"}, "emit"),
    ("POST", "/emit-contract", {}, "emit_contract"),
]


def _reached(*_args: Any, **_kwargs: Any) -> None:
    raise HTTPException(status_code=418, detail="handler reached")


@pytest.mark.parametrize("route", _WRITES)
def test_org_key_cannot_write_another_org_session(env, monkeypatch, route):
    client, settings, _, service, _, (sid_a, _) = env
    method, suffix, body, service_method = route
    assert settings.RBAC_ENFORCE_TASK_ROUTES is False
    monkeypatch.setattr(service, service_method, _reached)
    before = service.get(sid_a)
    state = before.model_dump()
    version = before._store_version

    response = client.request(
        method,
        f"/api/plan/sessions/{sid_a}{suffix}",
        json=body,
        headers={"X-U": "viewer-a", "X-Key": "key-b", "X-Org": "B"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == f"unknown session '{sid_a}'"
    assert "Secret org A title" not in response.text
    after = service.get(sid_a)
    assert after.status == before.status
    assert after._store_version == version
    assert after.model_dump() == state


@pytest.mark.parametrize(
    "suffix,body,service_method",
    [
        ("/process", None, "process_async"),
        ("/approve", {"approver": "x"}, "approve"),
    ],
)
def test_org_key_reaches_own_session(env, monkeypatch, suffix, body, service_method):
    client, _, _, service, _, (_, sid_b) = env
    monkeypatch.setattr(service, service_method, _reached)
    response = client.post(
        f"/api/plan/sessions/{sid_b}{suffix}",
        json=body,
        headers={"X-U": "viewer-a", "X-Key": "key-b", "X-Org": "B"},
    )
    assert response.status_code == 418
    assert response.json()["detail"] == "handler reached"


@pytest.mark.parametrize(
    "suffix,body,service_method",
    [
        ("/process", None, "process_async"),
        ("/approve", {"approver": "x"}, "approve"),
    ],
)
def test_human_nonmember_shadows_then_enforces(env, monkeypatch, suffix, body, service_method):
    client, settings, audits, service, session_local, (_, sid_b) = env
    monkeypatch.setattr(service, service_method, _reached)
    path = f"/api/plan/sessions/{sid_b}{suffix}"
    response = client.post(path, json=body, headers={"X-U": "viewer-a"})
    assert response.status_code == 418
    assert any(a["resource_id"] == sid_b and a["details"].get("shadow") is True for a in audits)

    settings.RBAC_ENFORCE_TASK_ROUTES = True
    # The older route dependency returns 403 first; call the handler to check
    # this step's per-session guard and its 404 translation.
    pp = importlib.import_module("server.routes.plan_pipeline")
    request = Request({"type": "http"})
    request.state.user = {"id": "viewer-a"}

    async def _call():
        async with session_local() as db:
            if suffix == "/process":
                await pp.process(sid_b, request, db)
            else:
                await pp.approve(sid_b, pp.ApproveBody(**body), request, db)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(_call())
    assert caught.value.status_code == 404
    assert caught.value.detail == f"unknown session '{sid_b}'"


@pytest.mark.parametrize(
    "suffix,body,service_method",
    [
        ("/process", None, "process_async"),
        ("/approve", {"approver": "x"}, "approve"),
    ],
)
def test_mcp_org_key_cannot_write_another_org_session(
    env, monkeypatch, suffix, body, service_method
):
    _, _, _, service, session_local, (sid_a, _) = env
    proxy = importlib.import_module("server.mcp_stdio.router")
    auth = importlib.import_module("server.mcp_remote.auth")
    key_b = auth.AuthenticatedKey(
        key_id="key-b", scopes=frozenset({"plan:write"}), user_id="viewer-a", org_id="B"
    )
    monkeypatch.setattr(service, service_method, _reached)
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

    response = TestClient(app).post(f"/api/mcp-stdio/plan/sessions/{sid_a}{suffix}", json=body)
    assert response.status_code == 404
    assert response.json()["detail"] == f"unknown session '{sid_a}'"
    assert "Secret org A title" not in response.text
