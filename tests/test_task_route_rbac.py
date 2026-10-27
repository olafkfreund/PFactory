"""Org role checks on task and plan write routes, shadow mode first (PFactory#939)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

_WEB_SERVER_DIR = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))

PROJECTS = {"p1": {"org_id": "A"}, "legacy": {}}
# user id -> memberships: viewer-a (viewer in A), member-a (member in A),
# mixed (viewer in A, member in B), loner (no membership, JWT role admin).
MEMBERS = [
    ("viewer-a", "A", "viewer"),
    ("member-a", "A", "member"),
    ("mixed", "A", "viewer"),
    ("mixed", "B", "member"),
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from server.config import get_settings
    from server.database.models import Base, Organization, OrgMember, User
    from server.routes import project_authz as pa
    from server.services import audit_service, project_paths

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    session_local = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_local() as db:
            for uid in ("viewer-a", "member-a", "mixed", "loner", "owner"):
                db.add(User(id=uid, email=f"{uid}@x.test", password_hash="x"))
            await db.flush()
            for oid in ("A", "B"):
                db.add(Organization(id=oid, name=oid, slug=oid, owner_id="owner"))
            await db.flush()
            for uid, oid, role in MEMBERS:
                db.add(OrgMember(org_id=oid, user_id=uid, role=role))
            await db.commit()

    asyncio.run(_init())

    audits: list[dict] = []
    events: list[str] = []  # request-session rollbacks and audit writes, in order

    async def _capture(**kw):
        events.append("audit")
        audits.append(kw)

    monkeypatch.setattr(audit_service, "log_audit_event_bg", _capture)
    monkeypatch.setattr(project_paths, "load_projects", lambda: PROJECTS)
    settings = get_settings()

    def enforce(on: bool):
        monkeypatch.setattr(settings, "RBAC_ENFORCE_TASK_ROUTES", on)

    def make(*, guarded=True, plan=False, broken_db=False):
        app = FastAPI()
        if plan:
            deps = [Depends(pa.require_plan_access("member"))] if guarded else []
            path = "/plan/{sid}/approve"
        else:
            deps = [Depends(pa.require_task_access("member"))] if guarded else []
            path = "/tasks/{task_id}/start"
        app.post(path, dependencies=deps)(lambda: {"ok": True})

        async def _db():
            async with session_local() as session:
                rollback = session.rollback

                async def _spy_rollback():
                    events.append("rollback")
                    await rollback()

                async def _boom(*_a, **_k):
                    raise RuntimeError("db down")

                session.rollback = _spy_rollback
                if broken_db:
                    session.execute = _boom
                yield session

        # Override on the module's own reference: other tests reload server.*.
        app.dependency_overrides[pa.get_db] = _db

        @app.middleware("http")
        async def _principal(request: Request, call_next):
            if uid := request.headers.get("X-U"):
                request.state.user = {"id": uid, "role": "admin"}
                if "X-Svc" in request.headers:
                    request.state.user["is_service"] = True
                if key_org := request.headers.get("X-Key-Org"):
                    request.state.user.update(api_key_id="k1", org_id=key_org)
            return await call_next(request)

        return TestClient(app), path

    make.events = events
    yield make, enforce, audits
    asyncio.run(engine.dispose())


def _post(client, path, project="p1", user="member-a", **headers):
    url = path.replace("{task_id}", f"{project}:spec").replace("{sid}", "s1")
    return client.post(url, headers={"X-U": user, **headers})


@pytest.mark.parametrize(
    ("user", "headers", "expected"),
    [
        ("viewer-a", {}, 403),
        ("member-a", {}, 200),
        ("loner", {}, 403),  # JWT role=admin, no OrgMember row
        ("member-a", {"X-Key-Org": "B"}, 403),  # acw_ key bound to another org
        ("member-a", {"X-Key-Org": "A"}, 200),
        ("loner", {"X-Svc": "1"}, 200),
    ],
)
def test_task_route_enforced(env, user, headers, expected):
    make, enforce, audits = env
    enforce(True)
    client, path = make()
    assert _post(client, path, user=user, **headers).status_code == expected
    assert len(audits) == (expected == 403)
    assert all("shadow" not in a["details"] for a in audits)


def test_shadow_viewer_passes_and_audits_once(env):
    make, enforce, audits = env
    enforce(False)
    client, path = make()
    r = _post(client, path, user="viewer-a")
    assert (r.status_code, r.json()) == (200, {"ok": True})
    assert len(audits) == 1
    assert audits[0]["action"] == "gate.rejected"
    assert audits[0]["details"]["shadow"] is True
    assert audits[0]["details"]["status"] == 403


def test_shadow_unknown_project_matches_unguarded_app(env):
    make, enforce, audits = env
    enforce(False)
    guarded, path = make()
    bare, _ = make(guarded=False)
    a, b = _post(guarded, path, project="nope"), _post(bare, path, project="nope")
    assert (a.status_code, a.json()) == (b.status_code, b.json())
    assert audits[0]["details"]["status"] == 404


def test_shadow_decision_error_allows(env, monkeypatch):
    from server.services import project_paths

    make, enforce, _ = env
    enforce(False)

    def boom():
        raise RuntimeError("projects.json unreadable")

    monkeypatch.setattr(project_paths, "load_projects", boom)
    client, path = make()
    assert _post(client, path, user="viewer-a").status_code == 200
    enforce(True)
    with pytest.raises(RuntimeError):  # enforce mode does not swallow it
        _post(client, path, user="viewer-a")


@pytest.mark.parametrize(
    ("user", "headers", "expected"),
    [
        ("viewer-a", {}, 403),
        ("member-a", {}, 200),
        ("mixed", {}, 200),  # highest role across orgs
        ("mixed", {"X-Key-Org": "A"}, 403),  # key limited to its own org (viewer)
        ("mixed", {"X-Key-Org": "B"}, 200),
        ("loner", {}, 403),
        ("loner", {"X-Svc": "1"}, 200),
    ],
)
def test_plan_route(env, user, headers, expected):
    make, enforce, audits = env
    enforce(True)
    client, path = make(plan=True)
    assert _post(client, path, user=user, **headers).status_code == expected
    assert [a["resource_type"] for a in audits] == ["plan_session"] * (expected == 403)


def test_plan_route_shadow_passes(env):
    make, enforce, audits = env
    enforce(False)
    client, path = make(plan=True)
    assert _post(client, path, user="viewer-a").status_code == 200
    assert audits[0]["details"]["shadow"] is True


@pytest.mark.parametrize("plan", [False, True])
def test_shadow_releases_request_session_before_audit(env, plan):
    # Two pooled connections per would-deny can stall the pool (AIFactory#1936).
    make, enforce, _ = env
    enforce(False)
    client, path = make(plan=plan)
    assert _post(client, path, user="viewer-a").status_code == 200
    assert make.events[:2] == ["rollback", "audit"]


def test_plan_route_shadow_db_error_allows(env):
    make, enforce, audits = env
    enforce(False)
    client, path = make(plan=True, broken_db=True)
    assert _post(client, path, user="viewer-a").status_code == 200
    assert audits == []  # a lookup error is not a would-deny
    assert "rollback" in make.events


TASK_ROUTES = {
    "execution": {
        "member": ["/{task_id}/start", "/{task_id}/stop", "/{task_id}/recover"],
    },
    "tasks": {
        "member": [
            "/{task_id}/clarifications",
            "/{task_id}/clarifications/answers",
            "/{task_id}/status",
            "/{task_id}",  # PUT, PATCH and DELETE
            "/{task_id}/approve-plan",
            "/{task_id}/reject-plan",
            "/{task_id}/worktree/resolve-conflicts",
            "/{task_id}/worktree/resolve-uncommitted",
            "/{task_id}/worktree/resolve-git-merge",
            "/{task_id}/worktree/abort-merge",
            "/{task_id}/worktree/create-pr",
            "/{task_id}/worktree/merge",
            "/{task_id}/worktree/discard",
        ],
        "viewer": ["/{task_id}/logs/watch", "/{task_id}/logs/unwatch"],
    },
}
PROJECT_ROUTES = {
    "member": [
        "/{project_id}/tasks",
        "/{project_id}/tasks/archive",
        "/{project_id}/tasks/unarchive",
    ],
    "viewer": [
        "/{project_id}/tasks/{spec_id}/logs/watch",
        "/{project_id}/tasks/{spec_id}/logs/unwatch",
    ],
}
PLAN_ROUTES = [
    ("POST", s)
    for s in [
        "ingest-text",
        "from-issue",
        "ingest",
        "{session_id}/process",
        "{session_id}/suggestions/apply",
        "{session_id}/re-gate",
        "{session_id}/approve",
        "{session_id}/access/approve",
        "{session_id}/waive",
        "{session_id}/reject",
        "{session_id}/discard",
        "{session_id}/emit",
        "{session_id}/emit-contract",
    ]
] + [("DELETE", "{session_id}")]


def _guards(module, method, path):
    from importlib import import_module

    router = import_module(f"server.routes.{module}").router
    hits = [r for r in router.routes if method in r.methods and r.path == path]
    assert len(hits) == 1, (module, method, path)
    return [(type(d.dependency).__name__, d.dependency.minimum_role) for d in hits[0].dependencies]


def test_every_listed_route_carries_the_dependency():
    n = 0
    for module, by_role in TASK_ROUTES.items():
        for role, paths in by_role.items():
            for path in paths:
                methods = ("PUT", "PATCH", "DELETE") if path == "/{task_id}" else ("POST",)
                if path == "/{task_id}/status":
                    methods = ("PATCH",)
                for m in methods:
                    assert _guards(module, m, path) == [("TaskAccessChecker", role)], (m, path)
                    n += 1
    for role, paths in PROJECT_ROUTES.items():
        for path in paths:
            assert _guards("projects", "POST", path) == [("ProjectAccessChecker", role)], path
            n += 1
    for method, suffix in PLAN_ROUTES:
        path = f"/api/plan/sessions/{suffix}".rstrip("/")
        assert _guards("plan_pipeline", method, path) == [("PlanAccessChecker", "member")], path
        n += 1
    assert n == 39
