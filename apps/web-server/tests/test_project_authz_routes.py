"""Route-table authorization and contract matrix regression tests (#1099)."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from plan.service import PlanServiceError
from server import auth
from server.database.engine import get_db
from server.database.models import Base, Organization, OrgMember, User
from server.main import create_app
from server.mcp_remote import tools as mcp_remote_tools
from server.mcp_stdio import router as mcp_stdio_router
from server.rmux import bridge as rmux_bridge
from server.routes import (
    files as files_routes,
    pfactory_tasks as pfactory_tasks_routes,
    plan_pipeline as plan_pipeline_routes,
    project_authz,
    projects as projects_routes,
    tasks as tasks_routes,
)
from server.routes.pfactory_tasks import SpecAccessChecker
from server.routes.project_authz import _AccessChecker
from server.services import project_paths

# Explicit allowlist of non-project-scoped (public, system, auth, org-admin,
# user-settings, global diagnostics, or stateless parser) REST routes.
# Any route added outside this set MUST wire a project authorization guard.
PUBLIC_SYSTEM_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/"),
        ("GET", "/.well-known/agent-skills/index.json"),
        ("GET", "/metrics"),
        ("GET", "/api/health"),
        ("GET", "/api/ready"),
        ("GET", "/api/drain"),
        ("GET", "/api/capabilities"),
        ("POST", "/mcp"),
        # Auth & OIDC
        ("POST", "/api/auth/register"),
        ("POST", "/api/auth/login"),
        ("POST", "/api/auth/refresh"),
        ("POST", "/api/auth/logout"),
        ("GET", "/api/auth/me"),
        ("GET", "/api/auth/oidc/enabled"),
        ("GET", "/api/auth/oidc/login"),
        ("GET", "/api/auth/oidc/callback"),
        ("POST", "/api/auth/oidc/refresh"),
        ("POST", "/api/auth/oidc/logout"),
        # Organizations, keys, audit, GDPR, notifications
        ("POST", "/api/orgs"),
        ("GET", "/api/orgs"),
        ("GET", "/api/orgs/{org_id}"),
        ("PUT", "/api/orgs/{org_id}"),
        ("DELETE", "/api/orgs/{org_id}"),
        ("POST", "/api/orgs/{org_id}/members/invite"),
        ("GET", "/api/orgs/{org_id}/members"),
        ("PUT", "/api/orgs/{org_id}/members/{user_id}"),
        ("DELETE", "/api/orgs/{org_id}/members/{user_id}"),
        ("GET", "/api/orgs/{org_id}/audit"),
        ("GET", "/api/audit/export"),
        ("POST", "/api/users/{user_id}/gdpr-erasure"),
        ("POST", "/api/keys"),
        ("GET", "/api/keys"),
        ("DELETE", "/api/keys/{key_id}"),
        ("GET", "/api/notifications"),
        ("GET", "/api/notifications/unread-count"),
        ("POST", "/api/notifications/{notification_id}/read"),
        ("POST", "/api/notifications/read-all"),
        # User / portal credentials & endpoints
        ("POST", "/api/git-credentials"),
        ("GET", "/api/git-credentials"),
        ("DELETE", "/api/git-credentials/{cred_id}"),
        ("POST", "/api/test-credentials"),
        ("GET", "/api/test-credentials"),
        ("DELETE", "/api/test-credentials/{cred_id}"),
        ("GET", "/api/llm-endpoints"),
        ("POST", "/api/llm-endpoints"),
        ("POST", "/api/llm-endpoints/test"),
        ("GET", "/api/docs-targets"),
        ("POST", "/api/docs-targets"),
        ("POST", "/api/docs-targets/test"),
        ("GET", "/api/email/accounts"),
        ("DELETE", "/api/email/accounts/{account_id}"),
        ("GET", "/api/email/credentials-status"),
        ("POST", "/api/email/test/{account_id}"),
        ("GET", "/api/email/auth/outlook/start"),
        ("GET", "/api/email/auth/outlook/callback"),
        ("GET", "/api/email/auth/gmail/start"),
        ("GET", "/api/email/auth/gmail/callback"),
        # Provider runtimes, cloud, visual inspection, search
        ("GET", "/api/provider-runtimes"),
        ("POST", "/api/provider-runtimes/{name}/pin"),
        ("POST", "/api/provider-runtimes/{name}/update"),
        ("POST", "/api/cloud/assessments/run"),
        ("GET", "/api/cloud/assessments"),
        ("GET", "/api/cloud/assessments/{assessment_id}"),
        ("GET", "/api/cloud/assessments/{assessment_id}/download/{kind}"),
        ("GET", "/api/cloud/assessment"),
        ("GET", "/api/visual-inspections"),
        ("GET", "/api/visual-inspections/{run_id}"),
        ("GET", "/api/visual-inspections/{run_id}/download/{kind}"),
        ("GET", "/api/search"),
        ("GET", "/api/needs-you/count"),
        # Plan meta, contract events, and stateless plan document parser
        ("POST", "/api/contract-events"),
        ("GET", "/api/contract-events/handbacks"),
        ("GET", "/api/contract-events/{correlation_key}"),
        ("GET", "/api/plan/meta/adapters"),
        ("GET", "/api/plan/meta/categories"),
        ("GET", "/api/plan/meta/providers"),
        ("GET", "/api/plan/meta/registry"),
        ("GET", "/api/plan/meta/templates"),
        ("GET", "/api/mcp-stdio/plan/meta/categories"),
        ("POST", "/api/plan/ingest"),
        ("POST", "/api/plan/ingest-text"),
        # Settings & CLI accounts
        ("GET", "/api/settings"),
        ("PUT", "/api/settings"),
        ("POST", "/api/settings/reset"),
        ("GET", "/api/settings/tab-state"),
        ("PUT", "/api/settings/tab-state"),
        ("GET", "/api/settings/token"),
        ("POST", "/api/settings/token/regenerate"),
        ("POST", "/api/settings/api-key"),
        ("GET", "/api/settings/claude-profiles"),
        ("POST", "/api/settings/claude-profiles"),
        ("PATCH", "/api/settings/claude-profiles/{profile_id}"),
        ("DELETE", "/api/settings/claude-profiles/{profile_id}"),
        ("POST", "/api/settings/claude-profiles/active"),
        ("POST", "/api/settings/claude-profiles/{profile_id}/token"),
        ("POST", "/api/settings/claude-profiles/{profile_id}/initialize"),
        ("POST", "/api/settings/claude-profiles/{profile_id}/start-oauth"),
        ("POST", "/api/settings/claude-profiles/{profile_id}/complete-oauth"),
        ("GET", "/api/settings/claude-profiles/best"),
        ("POST", "/api/settings/retry-with-profile"),
        ("GET", "/api/settings/auto-switch"),
        ("PATCH", "/api/settings/auto-switch"),
        ("POST", "/api/settings/usage-update"),
        ("GET", "/api/settings/local-llm/detect"),
        ("POST", "/api/settings/ollama/test"),
        ("GET", "/api/settings/ollama/models"),
        ("POST", "/api/settings/ollama/pull"),
        ("POST", "/api/settings/openai-compat/test"),
        ("GET", "/api/settings/openai-compat/models"),
        ("GET", "/api/settings/api-profiles"),
        ("POST", "/api/settings/api-profiles"),
        ("PUT", "/api/settings/api-profiles/{profile_id}"),
        ("DELETE", "/api/settings/api-profiles/{profile_id}"),
        ("POST", "/api/settings/api-profiles/active"),
        ("POST", "/api/settings/api-profiles/test"),
        ("POST", "/api/settings/api-profiles/discover-models"),
        ("GET", "/api/settings/source-env"),
        ("PATCH", "/api/settings/source-env"),
        ("GET", "/api/settings/source-token-check"),
        ("GET", "/api/settings/auth-status"),
        ("GET", "/api/settings/claude-credentials-exist"),
        ("POST", "/api/settings/import-claude-credentials"),
        ("GET", "/api/settings/cli-accounts/detect"),
        ("GET", "/api/settings/cli-accounts/{cli}/status"),
        ("POST", "/api/settings/cli-accounts/{cli}/import"),
        ("POST", "/api/settings/cli-accounts/{cli}/api-key"),
        ("POST", "/api/settings/cli-accounts/{cli}/start-login"),
        ("POST", "/api/settings/cli-accounts/{cli}/start-login-terminal"),
        ("POST", "/api/settings/cli-accounts/{cli}/install"),
        ("DELETE", "/api/settings/cli-accounts/{cli}"),
        # Host / user-global GitHub CLI, Ollama, Claude Code, MCP infra, logs, skills
        ("GET", "/api/github/cli/check"),
        ("GET", "/api/github/models"),
        ("GET", "/api/github/copilot/config"),
        ("POST", "/api/github/cli/install"),
        ("GET", "/api/github/auth/check"),
        ("POST", "/api/github/auth/start"),
        ("GET", "/api/github/auth/status"),
        ("GET", "/api/github/token"),
        ("GET", "/api/github/user"),
        ("GET", "/api/github/repos"),
        ("GET", "/api/github/orgs"),
        ("POST", "/api/github/repos"),
        ("GET", "/api/ollama/status"),
        ("GET", "/api/ollama/installed"),
        ("POST", "/api/ollama/install"),
        ("GET", "/api/ollama/models"),
        ("GET", "/api/ollama/embedding-models"),
        ("POST", "/api/ollama/pull"),
        ("GET", "/api/claude-code/version"),
        ("POST", "/api/claude-code/install"),
        ("POST", "/api/mcp/health"),
        ("POST", "/api/mcp/test-connection"),
        ("GET", "/api/mcp/detect"),
        ("GET", "/api/updates/source/check"),
        ("POST", "/api/updates/source/download"),
        ("GET", "/api/updates/source/version"),
        ("GET", "/api/memory/infrastructure"),
        ("GET", "/api/memory/databases"),
        ("POST", "/api/memory/test-connection"),
        ("POST", "/api/memory/validate-api-key"),
        ("POST", "/api/memory/test-graphiti"),
        ("GET", "/api/logs/"),
        ("POST", "/api/logs/frontend"),
        ("GET", "/api/logs/{log_type}"),
        ("GET", "/api/logs/{log_type}/raw"),
        ("GET", "/api/logs/{log_type}/download"),
        ("DELETE", "/api/logs/{log_type}"),
        ("DELETE", "/api/logs/"),
        ("GET", "/api/skills/categories"),
        ("GET", "/api/skills/list"),
        ("GET", "/api/skills/search"),
        ("GET", "/api/skills/suggest"),
        ("GET", "/api/skills/{category}/{skill_name}"),
        ("POST", "/api/tasks/worktree/detect-tools"),
        ("GET", "/api/terminals/session-dates"),
        ("GET", "/api/terminals/sessions/{date}"),
        ("POST", "/api/terminals/cleanup"),
        ("POST", "/api/terminals/generate-name"),
    }
)

INDIRECT_GUARD_MARKERS: tuple[str, ...] = (
    "require_project_role",
    "require_path_project_role",
    "require_repo_project_role",
    "visible_project_ids",
    "authorize_project",
    "authorize_project_for_user",
    "authorize_plan_session",
    "authorize_plan_ingest",
    "plan_session_filter",
    "_guard_session",
    "_owned",
    "_visible",
    "_authorize_path",
    "_terminal_member",
    "may_use",
    "project_id_for_path",
    "creator_org",
    "_authorize_key",
    "PlanAccessChecker",
    "list_projects",
    "list_tasks",
    "get_running_tasks",
    "create_project",
    "_resolve_state_or_404",
)

WS_GUARD_MARKERS: tuple[str, ...] = (
    "TaskSocketAccess",
    "AllTaskSocketAccess",
    "require_project_role",
    "authorize_project",
    "authorize_project_for_user",
    "may_use",
    "_org_lookup_ok",
)


def _route_has_project_guard(route: APIRoute) -> bool:
    for dep in route.dependencies:
        if isinstance(dep.dependency, (_AccessChecker, SpecAccessChecker)):
            return True
    for sub_dep in route.dependant.dependencies:
        if isinstance(sub_dep.call, (_AccessChecker, SpecAccessChecker)):
            return True
    src = inspect.getsource(route.endpoint)
    return any(marker in src for marker in INDIRECT_GUARD_MARKERS)


def test_every_non_public_route_and_websocket_wires_project_authorization() -> None:
    app = create_app()
    app.include_router(rmux_bridge.router)

    unguarded_rest: list[tuple[str, str]] = []
    project_scoped_count = 0
    ws_routes: list[str] = []

    for route in app.routes:
        if isinstance(route, APIRoute):
            methods = sorted((route.methods or set()) - {"HEAD", "OPTIONS"})
            guarded = _route_has_project_guard(route)
            for method in methods:
                key = (method, route.path)
                if key in PUBLIC_SYSTEM_ALLOWLIST:
                    continue
                if guarded:
                    project_scoped_count += 1
                else:
                    unguarded_rest.append(key)
        elif isinstance(route, APIWebSocketRoute):
            ws_routes.append(route.path)
            src = inspect.getsource(route.endpoint)
            assert any(marker in src for marker in WS_GUARD_MARKERS), (
                f"WebSocket route {route.path} lacks project authorization guard"
            )

    assert not unguarded_rest, f"Project routes missing authorization guard: {unguarded_rest}"
    assert project_scoped_count >= 150
    assert {
        "/ws/tasks/{task_id}/logs",
        "/ws/tasks/{task_id}/progress",
        "/ws/terminal/{terminal_id}",
        "/ws/events",
        "/api/pfactory/tasks/{spec_id}/logs/stream",
        "/api/tasks/{spec_id}/agent-console/ws",
    }.issubset(set(ws_routes))


def test_mcp_stdio_and_remote_tool_tables_enforce_project_authorization() -> None:
    # 1. Remote MCP tools: every tool in get_tool_definitions() must be in _SCOPE_FOR_TOOL
    # and dispatch_tool_call must route through _authorize_call or _visible_project_ids.
    tool_defs = mcp_remote_tools.get_tool_definitions()
    tool_names = {d["name"] for d in tool_defs}
    assert tool_names == set(mcp_remote_tools._SCOPE_FOR_TOOL.keys())
    dispatch_src = inspect.getsource(mcp_remote_tools.dispatch_tool_call)
    assert "_authorize_call" in dispatch_src
    assert "_list_projects" in dispatch_src
    assert "visible_project_ids" in inspect.getsource(mcp_remote_tools._visible_project_ids)

    # 2. Stdio MCP proxy routes: every non-meta route in mcp_stdio.router must enforce
    # project / plan authorization and require an acw_ scope dependency.
    stdio_routes = [r for r in mcp_stdio_router.routes if isinstance(r, APIRoute)]
    assert len(stdio_routes) >= 20
    for route in stdio_routes:
        if route.path == "/api/mcp-stdio/plan/meta/categories":
            continue
        assert _route_has_project_guard(route), f"MCP stdio route {route.path} lacks project guard"


def _seed_workspace_and_projects(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, Any]]:
    repo_a, repo_b = tmp_path / "workspace" / "proj-a", tmp_path / "workspace" / "proj-b"
    for repo in (repo_a, repo_b):
        spec_dir = repo / ".pfactory" / "specs" / "001-demo"
        spec_dir.mkdir(parents=True)
        (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
        (spec_dir / "spec.md").write_text("# Spec\n", encoding="utf-8")
        (spec_dir / "requirements.json").write_text(
            '{"title": "Demo", "description": "Desc"}', encoding="utf-8"
        )
    ws_root = tmp_path / "pf_workspaces"
    for pid, sid in (("proj-a", "spec-a"), ("proj-b", "spec-b")):
        sdir = ws_root / "workspaces" / pid / "specs" / sid
        sdir.mkdir(parents=True)
        (sdir / "status.json").write_text(
            f'{{"task_id": "{sid}", "status": "completed", "phase": "done"}}',
            encoding="utf-8",
        )
    projects_data: dict[str, Any] = {
        "proj-a": {
            "name": "Project A",
            "path": str(repo_a),
            "org_id": "org-a",
            "repo": "org-a/repo-a",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        },
        "proj-b": {
            "name": "Project B",
            "path": str(repo_b),
            "org_id": "org-b",
            "repo": "org-b/repo-b",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        },
    }
    return repo_a, repo_b, ws_root, projects_data


@pytest.fixture
def matrix_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'matrix.db'}")
    session_local = async_sessionmaker(engine, expire_on_commit=False)
    repo_a, repo_b, ws_root, projects_data = _seed_workspace_and_projects(tmp_path)

    async def _seed() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_local() as db:
            for uid in ("owner-a", "admin-a", "member-a", "viewer-a", "member-b", "outsider"):
                db.add(User(id=uid, email=f"{uid}@example.test", password_hash="x"))  # noqa: S106
            await db.flush()
            db.add(Organization(id="org-a", name="Org A", slug="org-a", owner_id="owner-a"))
            db.add(Organization(id="org-b", name="Org B", slug="org-b", owner_id="member-b"))
            await db.flush()
            for org_id, uid, role in (
                ("org-a", "owner-a", "owner"),
                ("org-a", "admin-a", "admin"),
                ("org-a", "member-a", "member"),
                ("org-a", "viewer-a", "viewer"),
                ("org-b", "member-b", "member"),
            ):
                db.add(OrgMember(org_id=org_id, user_id=uid, role=role))
            await db.commit()

    asyncio.run(_seed())

    monkeypatch.setenv("PFACTORY_WORKSPACE_ROOT", str(ws_root))
    monkeypatch.setattr(project_paths, "load_projects", lambda: dict(projects_data))
    monkeypatch.setattr(projects_routes, "load_projects", lambda: dict(projects_data))
    monkeypatch.setattr(projects_routes, "save_projects", projects_data.update)
    monkeypatch.setattr(tasks_routes, "load_projects", lambda: dict(projects_data))
    monkeypatch.setattr(files_routes, "confine_to_project", lambda p: Path(p).resolve())
    monkeypatch.setattr(files_routes, "confine_to_workspace", lambda p: Path(p).resolve())
    monkeypatch.setattr(
        project_authz,
        "get_settings",
        lambda: SimpleNamespace(DISABLE_AUTH=False, RBAC_ENFORCE_TASK_ROUTES=True),
    )

    async def _noop_audit(*_a: Any, **_kw: Any) -> None:
        return None

    monkeypatch.setattr(project_authz, "_audit_denial", _noop_audit)

    app = FastAPI()
    app.include_router(projects_routes.router, prefix="/api/projects")
    app.include_router(tasks_routes.router, prefix="/api/tasks")
    app.include_router(files_routes.router, prefix="/api/files")
    app.include_router(pfactory_tasks_routes.router, prefix="/api/pfactory/tasks")
    app.include_router(plan_pipeline_routes.router)

    async def _override_db() -> Any:
        async with session_local() as db:
            yield db

    app.dependency_overrides[get_db] = _override_db

    principals: dict[str, dict[str, Any]] = {
        "owner-a": {"id": "owner-a"},
        "admin-a": {"id": "admin-a"},
        "member-a": {"id": "member-a"},
        "viewer-a": {"id": "viewer-a"},
        "member-b": {"id": "member-b"},
        "outsider": {"id": "outsider"},
        "owner-a-mcp-read": {
            "id": "owner-a",
            "api_key_id": "k-read",
            "org_id": "org-a",
            "scopes": ("mcp:read",),
        },
        "owner-a-task-write": {
            "id": "owner-a",
            "api_key_id": "k-write",
            "org_id": "org-a",
            "scopes": ("task:write",),
        },
        "owner-a-foreign-key": {
            "id": "owner-a",
            "api_key_id": "k-foreign",
            "org_id": "org-b",
            "scopes": ("api",),
        },
    }

    @app.middleware("http")
    async def _inject_user(request: Request, call_next: Any) -> Any:
        key = request.headers.get("X-Principal", "owner-a")
        request.state.user = principals.get(key)
        return await call_next(request)

    yield TestClient(app), repo_a, repo_b
    asyncio.run(engine.dispose())


def test_contract_matrix_roles_and_scope_ceiling(matrix_env: Any) -> None:
    client, _, _ = matrix_env

    # 1. Unknown project -> 404
    assert (
        client.get("/api/projects/unknown", headers={"X-Principal": "owner-a"}).status_code == 404
    )

    # 2. Non-member project -> 404 (identical to unknown project)
    assert client.get("/api/projects/proj-b", headers={"X-Principal": "owner-a"}).status_code == 404
    assert (
        client.get("/api/projects/proj-a", headers={"X-Principal": "outsider"}).status_code == 404
    )
    assert (
        client.get(
            "/api/projects/proj-a", headers={"X-Principal": "owner-a-foreign-key"}
        ).status_code
        == 404
    )

    # 3. viewer on viewer route -> 200; viewer on member route -> 403
    assert (
        client.get("/api/projects/proj-a", headers={"X-Principal": "viewer-a"}).status_code == 200
    )
    assert (
        client.post(
            "/api/projects/proj-a/initialize", headers={"X-Principal": "viewer-a"}
        ).status_code
        == 403
    )

    # 4. member on member route -> 200; member on admin route -> 403
    assert (
        client.post(
            "/api/projects/proj-a/initialize", headers={"X-Principal": "member-a"}
        ).status_code
        == 200
    )
    assert (
        client.patch(
            "/api/projects/proj-a/settings",
            json={"mainBranch": "main"},
            headers={"X-Principal": "member-a"},
        ).status_code
        == 403
    )
    assert (
        client.patch(
            "/api/projects/proj-a/settings",
            json={"mainBranch": "main"},
            headers={"X-Principal": "admin-a"},
        ).status_code
        == 200
    )

    # 5. owner grant + mcp:read token scope -> effective viewer (200 on read, 403 on write)
    assert (
        client.get("/api/projects/proj-a", headers={"X-Principal": "owner-a-mcp-read"}).status_code
        == 200
    )
    assert (
        client.post(
            "/api/projects/proj-a/initialize", headers={"X-Principal": "owner-a-mcp-read"}
        ).status_code
        == 403
    )

    # 6. owner grant + task:write token scope -> effective member (200 on member, 403 on admin)
    assert (
        client.post(
            "/api/projects/proj-a/initialize", headers={"X-Principal": "owner-a-task-write"}
        ).status_code
        == 200
    )
    assert (
        client.patch(
            "/api/projects/proj-a/settings",
            json={"mainBranch": "main"},
            headers={"X-Principal": "owner-a-task-write"},
        ).status_code
        == 403
    )


def test_filtered_lists_and_indirect_paths(
    matrix_env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, repo_a, repo_b = matrix_env

    # 1. Filtered GET /api/projects
    proj_a_list = client.get("/api/projects", headers={"X-Principal": "viewer-a"}).json()
    assert [p["id"] for p in proj_a_list] == ["proj-a"]
    proj_b_list = client.get("/api/projects", headers={"X-Principal": "member-b"}).json()
    assert [p["id"] for p in proj_b_list] == ["proj-b"]
    assert client.get("/api/projects", headers={"X-Principal": "outsider"}).json() == []

    # 2. Filtered GET /api/tasks
    tasks_a = client.get("/api/tasks", headers={"X-Principal": "viewer-a"}).json()["tasks"]
    assert {t["project_id"] for t in tasks_a} == {"proj-a"}
    assert client.get("/api/tasks", headers={"X-Principal": "outsider"}).json()["tasks"] == []

    # 3. Indirect path: /api/files/read
    file_a = str(repo_a / "README.md")
    file_b = str(repo_b / "README.md")
    assert (
        client.get(
            "/api/files/read", params={"path": file_a}, headers={"X-Principal": "viewer-a"}
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/api/files/read", params={"path": file_b}, headers={"X-Principal": "viewer-a"}
        ).status_code
        == 404
    )

    # 4. Indirect bare spec_id: /api/pfactory/tasks and /api/pfactory/tasks/{spec_id}
    pf_list = client.get("/api/pfactory/tasks", headers={"X-Principal": "viewer-a"}).json()
    assert [t["spec_id"] for t in pf_list["tasks"]] == ["spec-a"]
    assert (
        client.get("/api/pfactory/tasks/spec-a", headers={"X-Principal": "viewer-a"}).status_code
        == 200
    )
    assert (
        client.get("/api/pfactory/tasks/spec-b", headers={"X-Principal": "viewer-a"}).status_code
        == 404
    )
    assert (
        client.post(
            "/api/pfactory/tasks/spec-a/dismiss", headers={"X-Principal": "viewer-a"}
        ).status_code
        == 403
    )

    # 5. Plan sessions list filtering & session access: /api/plan/sessions
    s_a = SimpleNamespace(
        session_id="plan-a",
        repo="org-a/repo-a",
        project_id="proj-a",
        tenant_id=None,
        model_dump=lambda: {"session_id": "plan-a", "repo": "org-a/repo-a"},
        board_state=lambda: "ingested",
    )
    s_b = SimpleNamespace(
        session_id="plan-b",
        repo="org-b/repo-b",
        project_id="proj-b",
        tenant_id=None,
        model_dump=lambda: {"session_id": "plan-b", "repo": "org-b/repo-b"},
        board_state=lambda: "ingested",
    )
    sessions_map = {"plan-a": s_a, "plan-b": s_b}

    class _FakePlanService:
        def org_stamps(self) -> dict[str, str]:
            return {"plan-a": "org-a", "plan-b": "org-b"}

        def session_org(
            self, session: Any, _resolve: Any, stamps: dict[str, str] | None = None
        ) -> str | None:
            return (stamps or self.org_stamps()).get(session.session_id)

        def list_sessions(self, *, keep: Any = None, **_kw: Any) -> list[dict[str, Any]]:
            return [s.model_dump() for s in sessions_map.values() if keep is None or keep(s)]

        def get(self, session_id: str) -> Any:
            if session_id not in sessions_map:
                raise PlanServiceError("not found")
            return sessions_map[session_id]

    monkeypatch.setattr(plan_pipeline_routes, "SERVICE", _FakePlanService())
    listed_plans = client.get("/api/plan/sessions", headers={"X-Principal": "viewer-a"}).json()
    assert [s["session_id"] for s in listed_plans["sessions"]] == ["plan-a"]
    assert (
        client.get("/api/plan/sessions/plan-a", headers={"X-Principal": "viewer-a"}).status_code
        == 200
    )
    assert (
        client.get("/api/plan/sessions/plan-b", headers={"X-Principal": "viewer-a"}).status_code
        == 404
    )


def test_legacy_shared_token_full_access_toggle(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    settings = SimpleNamespace(
        DISABLE_AUTH=False,
        API_TOKEN="legacy-shared-secret",  # noqa: S106
        LEGACY_SHARED_TOKEN_FULL_ACCESS=True,
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(auth, "_try_decode_jwt", lambda _t: None)

    app = FastAPI()
    app.add_middleware(auth.TokenAuthMiddleware)

    @app.get("/api/probe")
    async def _probe(request: Request) -> dict[str, Any]:
        user = getattr(request.state, "user", None)
        return {"is_service": bool(user and user.get("is_service"))}

    client = TestClient(app)
    headers = {"Authorization": "Bearer legacy-shared-secret"}

    # True -> warns and grants is_service=True full access
    resp = client.get("/api/probe", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"is_service": True}
    assert "DEPRECATED: legacy shared token used" in caplog.text

    # False -> rejects legacy shared token with 401
    settings.LEGACY_SHARED_TOKEN_FULL_ACCESS = False
    resp_rejected = client.get("/api/probe", headers=headers)
    assert resp_rejected.status_code == 401
