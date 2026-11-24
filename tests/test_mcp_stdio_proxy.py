"""Tests for the stdio-MCP proxy (Issues #154 + #50).

Three concerns:

1. **Auth + scope:** ``require_acw_scope`` accepts the legacy admin
   token as a wildcard, rejects missing / bad / scope-mismatched
   ``acw_`` keys with 401 vs 403 respectively, and accepts valid
   ``acw_`` keys with the right scope.

2. **Client routing:** ``http_client._read_token`` prefers env-var
   ``PFACTORY_MCP_KEY`` over the legacy admin token, and the
   ``request()`` path rewrite prepends ``/api/mcp-stdio`` to outbound
   calls so they hit the proxy.

3. **Audit logging (Epic #50 acceptance criterion #2):** every write
   route fires ``log_audit_event_bg`` with the right
   ``action=mcp.<verb>`` constant; read routes do NOT log; audit
   failures don't crash the route (failure-safe via the bg helper's
   try/except).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))
_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


from server import config  # noqa: E402
from server.database.engine import get_db  # noqa: E402
from server.mcp_remote.auth import AuthenticatedKey, MCPAuthError  # noqa: E402
from server.mcp_stdio.auth import (  # noqa: E402
    MCP_READ_SCOPE,
    PROJECT_WRITE_SCOPE,
    _LegacyAdminKey,
    require_acw_scope,
)
from server.routes import plan_pipeline as pp  # noqa: E402

# ── Legacy admin token = wildcard ────────────────────────────────────


def test_legacy_admin_key_has_every_scope():
    """The synthetic legacy key advertises every named scope."""
    k = _LegacyAdminKey()
    assert k.has_scope(MCP_READ_SCOPE)
    assert k.has_scope(PROJECT_WRITE_SCOPE)
    assert k.has_scope("task:write")
    assert k.has_scope("task:merge")
    # Unknown scopes also pass — by design, the legacy admin is
    # unconstrained, so even a typo-scope wouldn't block it.
    assert k.has_scope("anything-the-caller-passes")


def test_principal_carries_service_identity_when_key_exposes_it():
    class ServiceKey(AuthenticatedKey):
        @property
        def is_service(self):
            return True

    principal = _proxy_module()._principal(
        ServiceKey(key_id="service-key", scopes=frozenset(), user_id="service-user")
    )
    assert principal["is_service"] is True


# ── Dependency: auth + scope behaviour ───────────────────────────────


def _app_with_scope(scope: str) -> FastAPI:
    """Build a tiny FastAPI app whose only route is gated by ``scope``.

    Used to drive the auth dependency end-to-end via TestClient.
    """
    app = FastAPI()

    @app.get("/probe")
    async def probe(_=__import__("fastapi").Depends(require_acw_scope(scope))):
        return {"ok": True}

    return app


def test_missing_auth_header_returns_401():
    client = TestClient(_app_with_scope(MCP_READ_SCOPE))
    r = client.get("/probe")
    assert r.status_code == 401
    assert "Missing" in r.json()["detail"] or "malformed" in r.json()["detail"]


def test_legacy_admin_token_acts_as_wildcard(monkeypatch):
    """Token matching settings.API_TOKEN passes any scope check."""
    from server import config

    # Patch settings.API_TOKEN to a known value.
    monkeypatch.setattr(config.get_settings(), "API_TOKEN", "legacy-secret-token-for-test")
    client = TestClient(_app_with_scope("task:merge"))
    r = client.get("/probe", headers={"Authorization": "Bearer legacy-secret-token-for-test"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_unknown_acw_key_returns_401(monkeypatch):
    """An unknown bearer token falls through to acw_ validation, which
    rejects via MCPAuthError → 401."""

    async def _raise(_header):
        raise MCPAuthError("Invalid API key")

    monkeypatch.setattr("server.mcp_stdio.auth.mcp_remote_auth.authenticate", _raise)
    client = TestClient(_app_with_scope(MCP_READ_SCOPE))
    r = client.get("/probe", headers={"Authorization": "Bearer acw_unknown"})
    assert r.status_code == 401
    assert "Invalid API key" in r.json()["detail"]


def test_acw_key_with_wrong_scope_returns_403(monkeypatch):
    """A valid acw_ key that lacks the requested scope → 403, not 401.

    The 401/403 split lets the client tell 'your key is bad' (regen)
    apart from 'your key works but is scoped wrong' (mint a new one).
    """

    async def _ok(_header):
        return AuthenticatedKey(
            key_id="key-123",
            scopes=frozenset({MCP_READ_SCOPE}),  # READ only
            user_id="user-1",
        )

    monkeypatch.setattr("server.mcp_stdio.auth.mcp_remote_auth.authenticate", _ok)
    client = TestClient(_app_with_scope(PROJECT_WRITE_SCOPE))  # need WRITE
    r = client.get("/probe", headers={"Authorization": "Bearer acw_readonly"})
    assert r.status_code == 403
    assert "project:write" in r.json()["detail"]


def test_acw_key_with_right_scope_passes(monkeypatch):
    """Happy path: scoped acw_ key passes → handler runs."""

    async def _ok(_header):
        return AuthenticatedKey(
            key_id="key-456",
            scopes=frozenset({MCP_READ_SCOPE, PROJECT_WRITE_SCOPE}),
            user_id="user-2",
        )

    monkeypatch.setattr("server.mcp_stdio.auth.mcp_remote_auth.authenticate", _ok)
    client = TestClient(_app_with_scope(PROJECT_WRITE_SCOPE))
    r = client.get("/probe", headers={"Authorization": "Bearer acw_writer"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ── Client-side token resolution ─────────────────────────────────────


def test_client_prefers_env_var_over_files(tmp_path, monkeypatch):
    """$PFACTORY_MCP_KEY beats both .mcp-key and the legacy token."""
    from agents.tools_pkg import http_client

    # Set up files that should be IGNORED.
    mcp_key_file = tmp_path / ".mcp-key"
    mcp_key_file.write_text("acw_from_file\n")
    legacy_file = tmp_path / ".token"
    legacy_file.write_text("legacy_admin_token\n")

    monkeypatch.setattr(http_client, "DEFAULT_MCP_KEY_FILE", str(mcp_key_file))
    monkeypatch.setattr(http_client, "DEFAULT_TOKEN_FILE", str(legacy_file))
    monkeypatch.setenv("PFACTORY_MCP_KEY", "acw_from_env")

    assert http_client._read_token() == "acw_from_env"


def test_client_falls_back_to_mcp_key_file_then_legacy(tmp_path, monkeypatch):
    """No env var → .mcp-key file → legacy token chain."""
    from agents.tools_pkg import http_client

    mcp_key_file = tmp_path / ".mcp-key"
    legacy_file = tmp_path / ".token"
    legacy_file.write_text("legacy_admin\n")

    monkeypatch.setattr(http_client, "DEFAULT_MCP_KEY_FILE", str(mcp_key_file))
    monkeypatch.setattr(http_client, "DEFAULT_TOKEN_FILE", str(legacy_file))
    monkeypatch.delenv("PFACTORY_MCP_KEY", raising=False)
    monkeypatch.delenv("PFACTORY_API_TOKEN_FILE", raising=False)

    # No .mcp-key file → uses legacy.
    assert http_client._read_token() == "legacy_admin"

    # Drop in a .mcp-key file → it takes precedence.
    mcp_key_file.write_text("acw_scoped\n")
    assert http_client._read_token() == "acw_scoped"


def test_client_rewrites_path_to_proxy_prefix(monkeypatch):
    """``request("GET", "/api/tasks")`` → outbound hits
    ``/api/mcp-stdio/tasks``. Confirms the stdio MCP never calls the
    raw REST surface and so cannot bypass scope gates by accident."""
    from agents.tools_pkg import http_client

    captured = {}

    class _FakeResp:
        status_code = 200
        content = b"{}"
        text = "{}"

        def json(self):
            return {}

    class _FakeClient:
        async def request(self, method, path, **kwargs):
            captured["method"] = method
            captured["path"] = path
            captured["headers"] = kwargs["headers"]
            return _FakeResp()

        async def aclose(self):
            pass

    monkeypatch.setattr(http_client._state, "_client", _FakeClient())
    monkeypatch.setattr(http_client._state, "_base_url", "http://localhost:3114")
    monkeypatch.setattr(http_client, "_read_token", lambda: "acw_test_key")

    import asyncio

    asyncio.run(http_client.request("GET", "/api/tasks"))
    assert captured["path"] == "/api/mcp-stdio/tasks"
    assert captured["headers"]["X-PFactory-Channel"] == "mcp"

    # And paths already under the proxy prefix pass through unchanged.
    asyncio.run(http_client.request("GET", "/api/mcp-stdio/projects"))
    assert captured["path"] == "/api/mcp-stdio/projects"


# ── Audit logging on writes (Epic #50 acceptance criterion #2) ──────


def test_audit_helper_passes_right_fields_for_authenticated_key(monkeypatch):
    """``_audit_mcp_write`` should forward user_id + org_id + key_id +
    action to ``log_audit_event_bg`` so audit rows carry full provenance."""
    # The package __init__ re-exports the APIRouter as ``router``,
    # which shadows the module attribute. Grab the actual module
    # via sys.modules — `import server.mcp_stdio.router` ensures
    # it's loaded.
    import sys

    import server.mcp_stdio.router  # noqa: F401  (load into sys.modules)

    proxy_router = sys.modules["server.mcp_stdio.router"]

    captured: dict = {}

    async def _fake_bg(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(proxy_router, "log_audit_event_bg", _fake_bg)

    key = AuthenticatedKey(
        key_id="key-abc",
        scopes=frozenset({"task:write"}),
        user_id="user-42",
        org_id="org-99",
    )

    class _FakeRequest:
        class _Client:
            host = "10.0.0.1"

        client = _Client()

    import asyncio

    asyncio.run(
        proxy_router._audit_mcp_write(
            key,
            action="mcp.task.start",
            resource_type="task",
            resource_id="spec-001",
            request=_FakeRequest(),
        )
    )

    assert captured["user_id"] == "user-42"
    assert captured["org_id"] == "org-99"
    assert captured["action"] == "mcp.task.start"
    assert captured["resource_type"] == "task"
    assert captured["resource_id"] == "spec-001"
    assert captured["ip"] == "10.0.0.1"
    # mcp_key_id must end up in details so we can correlate audit
    # rows back to the specific minted key.
    assert captured["details"]["mcp_key_id"] == "key-abc"


def test_audit_helper_marks_legacy_admin_calls(monkeypatch):
    """Calls made via the legacy admin token wildcard should still
    audit-log, but with ``mcp_key_id=legacy-admin`` so operators can
    tell wildcard calls apart from scoped-key calls."""
    # The package __init__ re-exports the APIRouter as ``router``,
    # which shadows the module attribute. Grab the actual module
    # via sys.modules — `import server.mcp_stdio.router` ensures
    # it's loaded.
    import sys

    import server.mcp_stdio.router  # noqa: F401  (load into sys.modules)

    proxy_router = sys.modules["server.mcp_stdio.router"]

    captured: dict = {}

    async def _fake_bg(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(proxy_router, "log_audit_event_bg", _fake_bg)

    class _FakeRequest:
        client = None  # exercises the IP-less branch

    import asyncio

    asyncio.run(
        proxy_router._audit_mcp_write(
            _LegacyAdminKey(),
            action="mcp.task.stop",
            resource_type="task",
            resource_id="spec-077",
            request=_FakeRequest(),
        )
    )

    assert captured["user_id"] is None
    assert captured["org_id"] is None
    assert captured["details"]["mcp_key_id"] == "legacy-admin"
    assert captured["ip"] is None


def test_audit_constants_use_mcp_namespace():
    """Every constant added for Epic #50 must use the ``mcp.`` prefix
    so audit reviewers can grep MCP-initiated mutations apart from
    UI-driven ones (``task.start`` vs ``mcp.task.start``)."""
    from server.services.audit_service import (
        ACTION_MCP_PROJECT_CREATE,
        ACTION_MCP_TASK_APPROVE_PLAN,
        ACTION_MCP_TASK_CREATE_AND_RUN,
        ACTION_MCP_TASK_CREATE_PR,
        ACTION_MCP_TASK_MERGE,
        ACTION_MCP_TASK_RECOVER,
        ACTION_MCP_TASK_START,
        ACTION_MCP_TASK_STOP,
    )

    for action in [
        ACTION_MCP_PROJECT_CREATE,
        ACTION_MCP_TASK_APPROVE_PLAN,
        ACTION_MCP_TASK_CREATE_AND_RUN,
        ACTION_MCP_TASK_CREATE_PR,
        ACTION_MCP_TASK_MERGE,
        ACTION_MCP_TASK_RECOVER,
        ACTION_MCP_TASK_START,
        ACTION_MCP_TASK_STOP,
    ]:
        assert action.startswith("mcp."), (
            f"Expected mcp.* namespace for Epic #50 audit constant, got {action!r}"
        )


# ── Plan routes (#694) ───────────────────────────────────────────────
#
# The plan MCP tools had nowhere on this proxy to write, which is why they used
# a private in-process store. These routes are that destination; they must be
# scope-gated like every other route here, and the write routes must audit.


def test_plan_write_scope_is_a_named_scope():
    """``plan:write`` must be in ALL_SCOPES or no acw_ key can ever hold it."""
    from server.mcp_stdio.auth import ALL_SCOPES, PLAN_WRITE_SCOPE

    assert PLAN_WRITE_SCOPE == "plan:write"
    assert PLAN_WRITE_SCOPE in ALL_SCOPES


def test_plan_audit_constants_use_mcp_namespace():
    from server.services.audit_service import (
        ACTION_MCP_PLAN_APPROVE,
        ACTION_MCP_PLAN_INGEST,
        ACTION_MCP_PLAN_PROCESS,
    )

    assert ACTION_MCP_PLAN_INGEST == "mcp.plan.ingest"
    assert ACTION_MCP_PLAN_PROCESS == "mcp.plan.process"
    assert ACTION_MCP_PLAN_APPROVE == "mcp.plan.approve"


def _proxy_module():
    """The router MODULE — the package __init__ re-exports the APIRouter as
    ``router``, which shadows it on a normal import."""
    import sys

    import server.mcp_stdio.router  # noqa: F401  (load into sys.modules)

    return sys.modules["server.mcp_stdio.router"]


def _scopes_of(route) -> set[str]:
    """The scope strings closed over by this route's ``require_acw_scope`` dep."""
    scopes: set[str] = set()
    dependant = getattr(route, "dependant", None)
    for dep in getattr(dependant, "dependencies", []) or []:
        for cell in getattr(dep.call, "__closure__", None) or ():
            value = cell.cell_contents
            if isinstance(value, str):
                scopes.add(value)
    return scopes


def _plan_routes():
    return {
        (sorted(r.methods - {"HEAD", "OPTIONS"})[0], r.path)
        for r in _proxy_module().router.routes
        if "/plan" in getattr(r, "path", "")
    }


def test_every_plan_tool_has_a_proxy_route():
    """One route per plan MCP tool — a tool with no route cannot reach the server."""
    assert _plan_routes() == {
        ("GET", "/api/mcp-stdio/plan/sessions"),
        ("GET", "/api/mcp-stdio/plan/sessions/{session_id}"),
        ("GET", "/api/mcp-stdio/plan/sessions/{session_id}/audit-pack"),
        ("GET", "/api/mcp-stdio/plan/meta/categories"),
        ("POST", "/api/mcp-stdio/plan/sessions/ingest"),
        ("POST", "/api/mcp-stdio/plan/sessions/ingest-text"),
        ("POST", "/api/mcp-stdio/plan/sessions/ingest-approved"),
        ("POST", "/api/mcp-stdio/plan/sessions/{session_id}/process"),
        ("POST", "/api/mcp-stdio/plan/sessions/{session_id}/approve"),
        ("POST", "/api/mcp-stdio/plan/sessions/{session_id}/emit-contract"),
    }


def test_plan_writes_need_plan_write_and_reads_need_mcp_read():
    """A write gated only by ``mcp:read`` would let a read-only key mutate plans.

    Reads the dependency each route actually declares, rather than trusting that
    the right decorator was pasted in.
    """
    writes = {
        "/api/mcp-stdio/plan/sessions/ingest",
        "/api/mcp-stdio/plan/sessions/ingest-text",
        "/api/mcp-stdio/plan/sessions/ingest-approved",
        "/api/mcp-stdio/plan/sessions/{session_id}/process",
        "/api/mcp-stdio/plan/sessions/{session_id}/emit-contract",
        "/api/mcp-stdio/plan/sessions/{session_id}/approve",
    }
    seen = {
        r.path: _scopes_of(r)
        for r in _proxy_module().router.routes
        if "/plan" in getattr(r, "path", "")
    }
    assert seen, "no plan routes found — the check examined nothing"
    for path, scopes in seen.items():
        expected = "plan:write" if path in writes else "mcp:read"
        assert expected in scopes, f"{path} not gated by {expected} (got {scopes})"


def test_headerless_proxy_live_emit_refuses_shared_token_without_approval(monkeypatch):
    async def no_guard(*_args):
        pass

    class Service:
        def get(self, _session_id):
            return SimpleNamespace(project_id=None, repo=None)

        def emit_contract(self, *_args, **kwargs):
            assert kwargs["require_user_approval"] is True
            raise pp.UserApprovalRequiredError("private detail")

    monkeypatch.setattr(config.get_settings(), "API_TOKEN", "legacy-secret-token-for-test")
    monkeypatch.setattr(pp, "SERVICE", Service())
    monkeypatch.setattr(pp, "_guard_session", no_guard)
    monkeypatch.setattr(_proxy_module().PlanAccessChecker, "check", no_guard)
    app = FastAPI()
    app.include_router(_proxy_module().router)
    app.dependency_overrides[get_db] = lambda: None
    response = TestClient(app).post(
        "/api/mcp-stdio/plan/sessions/PLAN-1/emit-contract",
        headers={"Authorization": "Bearer legacy-secret-token-for-test"},
        json={"dry_run": False},
    )
    assert response.status_code == 409
    assert "private detail" not in response.text


def test_approved_artifact_bounds_and_blob_mismatch(monkeypatch):
    from tests.test_ingest_approved import _artifacts

    proxy = _proxy_module()
    artifacts = _artifacts()
    body = {
        "slug": "2026-11-23-3865-factory-cli", "provider": "github",
        "repo": "owner/repo", "base_ref": "main", "branch": "feat/x",
        "head_sha": "b" * 40, "artifacts": artifacts,
    }
    with pytest.raises(ValidationError):
        proxy._IngestApprovedBody.model_validate({**body, "artifacts": artifacts[:2]})
    with pytest.raises(ValidationError):
        proxy._IngestApprovedBody.model_validate({**body, "head_sha": "not-a-sha"})
    with pytest.raises(ValidationError):
        proxy._IngestApprovedBody.model_validate({**body, "artifacts": [
            {**artifacts[0], "approve_commit_author": "x" * 201}, *artifacts[1:]
        ]})
    with pytest.raises(ValidationError):
        proxy._IngestApprovedBody.model_validate({**body, "artifacts": [
            {**artifacts[0], "path": "../intent.md"}, *artifacts[1:]
        ]})
    for field, value in (
        ("slug", "Bad Slug"), ("provider", "GitHub"), ("repo", "x" * 257),
        ("base_ref", "bad..ref"), ("branch", "bad~ref"),
        ("branch", "x" * 256),
    ):
        with pytest.raises(ValidationError):
            proxy._IngestApprovedBody.model_validate({**body, field: value})
    with pytest.raises(ValidationError):
        proxy._IngestApprovedBody.model_validate({**body, "artifacts": [
            {**artifacts[0], "content": "é" * 131073}, *artifacts[1:]
        ]})

    async def no_guard(*_args):
        pass

    monkeypatch.setattr(config.get_settings(), "API_TOKEN", "legacy-secret-token-for-test")
    monkeypatch.setattr(proxy.PlanAccessChecker, "check", no_guard)
    monkeypatch.setattr(
        proxy, "require_repo_project_role", AsyncMock(return_value=SimpleNamespace(
            project_id="project-1", org_id="org-1"
        )),
    )
    service = SimpleNamespace(ingest_approved=lambda *_a, **_kw: pytest.fail("ingested bad blob"))
    monkeypatch.setattr(proxy, "SERVICE", service)
    app = FastAPI()
    app.include_router(proxy.router)
    app.dependency_overrides[get_db] = lambda: None
    response = TestClient(app).post(
        "/api/mcp-stdio/plan/sessions/ingest-approved",
        headers={"Authorization": "Bearer legacy-secret-token-for-test"},
        json={**body, "artifacts": [{**artifacts[0], "blob_sha": "0" * 40}, *artifacts[1:]]},
    )
    assert response.status_code == 422
    assert "blob SHA mismatch" in response.text
    bad_path = TestClient(app).post(
        "/api/mcp-stdio/plan/sessions/ingest-approved",
        headers={"Authorization": "Bearer legacy-secret-token-for-test"},
        json={**body, "artifacts": [{**artifacts[0], "path": "../intent.md"}, *artifacts[1:]]},
    )
    assert bad_path.status_code == 422


def test_approved_artifact_unknown_and_invisible_project_match(monkeypatch):
    from tests.test_ingest_approved import _artifacts

    proxy = _proxy_module()
    async def no_guard(*_args):
        pass

    async def not_found(*_args, **_kwargs):
        raise HTTPException(status_code=404, detail="Project not found")

    monkeypatch.setattr(config.get_settings(), "API_TOKEN", "legacy-secret-token-for-test")
    monkeypatch.setattr(proxy.PlanAccessChecker, "check", no_guard)
    monkeypatch.setattr(proxy, "require_repo_project_role", not_found)
    app = FastAPI()
    app.include_router(proxy.router)
    app.dependency_overrides[get_db] = lambda: None
    body = {
        "slug": "2026-11-23-3865-factory-cli", "provider": "github",
        "repo": "owner/repo", "base_ref": "main", "branch": "feat/x",
        "head_sha": "b" * 40, "artifacts": _artifacts(),
    }
    client = TestClient(app)
    responses = [client.post(
        "/api/mcp-stdio/plan/sessions/ingest-approved",
        headers={"Authorization": "Bearer legacy-secret-token-for-test"}, json=body,
    ) for _ in range(2)]
    assert all(response.status_code == 404 for response in responses)
    assert responses[0].json() == responses[1].json() == {"detail": "Project not found"}


def test_scoped_ingest_text_invalid_body_is_422(monkeypatch):
    proxy = _proxy_module()

    async def no_guard(*_args):
        pass

    monkeypatch.setattr(config.get_settings(), "API_TOKEN", "legacy-secret-token-for-test")
    monkeypatch.setattr(proxy.PlanAccessChecker, "check", no_guard)
    app = FastAPI()
    app.include_router(proxy.router)
    app.dependency_overrides[get_db] = lambda: None
    response = TestClient(app).post(
        "/api/mcp-stdio/plan/sessions/ingest-text",
        headers={"Authorization": "Bearer legacy-secret-token-for-test"},
        json={"not_text": "invalid"},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("route", ["ingest-text", "ingest-approved"])
def test_plan_ingest_array_body_is_422(monkeypatch, route):
    proxy = _proxy_module()

    async def no_guard(*_args):
        pass

    monkeypatch.setattr(config.get_settings(), "API_TOKEN", "legacy-secret-token-for-test")
    monkeypatch.setattr(proxy.PlanAccessChecker, "check", no_guard)
    app = FastAPI()
    app.include_router(proxy.router)
    app.dependency_overrides[get_db] = lambda: None
    response = TestClient(app).post(
        f"/api/mcp-stdio/plan/sessions/{route}",
        headers={"Authorization": "Bearer legacy-secret-token-for-test"}, json=[],
    )
    assert response.status_code == 422


def test_registered_repo_project_resolution_hides_other_org(monkeypatch):
    from server.routes import project_authz

    projects = {
        "visible": {"repo": "owner/repo", "org_id": "org-1"},
        "visible-2": {"repo": "owner/repo", "org_id": "org-1"},
        "foreign": {"repo": "owner/repo", "org_id": "org-2"},
    }
    monkeypatch.setattr(project_authz.project_paths, "load_projects", lambda: projects)
    principal = {"id": None, "org_id": "org-1", "api_key_id": "key-1",
                 "scopes": ["plan:write"]}

    async def resolve(project_id):
        return await project_authz.require_repo_project_role(
            principal, "owner/repo", "member", None, project_id=project_id,
        )

    assert asyncio.run(resolve("visible")).project_id == "visible"
    assert asyncio.run(resolve("visible-2")).project_id == "visible-2"
    failures = []
    for project_id in ("foreign", "unknown", "visible"):
        try:
            repo = "other/repo" if project_id == "visible" else "owner/repo"
            asyncio.run(project_authz.require_repo_project_role(
                principal, repo, "member", None, project_id=project_id,
            ))
        except HTTPException as exc:
            failures.append((exc.status_code, exc.detail))
    assert failures == [(404, "Project not found")] * 3


def test_artifact_approval_looks_up_display_name(monkeypatch):
    from plan.service import PlanService
    from tests.test_ingest_approved import _ready_artifacts

    service = PlanService()
    session, _ = service.ingest_approved(
        _ready_artifacts(), provider="github", repo="owner/repo", base_ref="main",
        project_id="p1", external_runner=lambda _p, _e: [],
    )
    assert session.review.gates_passed

    async def guard(*_args):
        return session

    async def audit(*_args, **_kwargs):
        pass

    class DB:
        async def get(self, _model, user_id):
            assert user_id == "user-1"
            return SimpleNamespace(name="A User", email="user@example.com")

        async def commit(self):
            pass

    monkeypatch.setattr(pp, "SERVICE", service)
    monkeypatch.setattr(pp, "_guard_session", guard)
    monkeypatch.setattr(pp, "log_audit_event", audit)
    request = SimpleNamespace(
        state=SimpleNamespace(user={"id": "user-1", "org_id": "org-1"}), client=None,
    )
    result = asyncio.run(pp.approve(
        session.session_id, pp.ApproveBody(approver="client label"), request, DB(),
    ))
    assert result["review"]["human_approval"]["approver_display"] == "A User"


def test_artifact_emit_route_ignores_body_project_and_reports_missing_binding(monkeypatch):
    from plan.service import PlanService
    from tests.test_ingest_approved import _ready_artifacts

    service = PlanService()
    session, _ = service.ingest_approved(
        _ready_artifacts(), provider="github", repo="owner/repo", base_ref="main",
        project_id="p1", external_runner=lambda _p, _e: [],
    )
    session.project_id = None
    service._save(session)

    async def guard(*_args):
        return session

    monkeypatch.setattr(pp, "SERVICE", service)
    monkeypatch.setattr(pp, "_guard_session", guard)
    request = SimpleNamespace(state=SimpleNamespace(user={"id": "user-1"}), client=None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(pp.emit_contract(
            session.session_id,
            pp.EmitContractBody(dry_run=True, repo="attacker/repo", project_id="attacker"),
            request, None,
        ))
    assert exc.value.status_code == 422


def test_legacy_key_can_ingest_and_dry_run_before_approval(monkeypatch):
    from plan.service import PlanService
    from tests.test_ingest_approved import _ready_artifacts

    proxy = _proxy_module()
    service = PlanService()

    async def guard(*_args):
        return service.get(_args[0])

    async def no_guard(*_args):
        pass

    async def registered(*_args, **_kwargs):
        return SimpleNamespace(project_id="p1", org_id="org-1")

    monkeypatch.setattr(config.get_settings(), "API_TOKEN", "legacy-secret-token-for-test")
    monkeypatch.setattr(proxy.PlanAccessChecker, "check", no_guard)
    monkeypatch.setattr(proxy, "require_repo_project_role", registered)
    monkeypatch.setattr(proxy, "SERVICE", service)
    monkeypatch.setattr(pp, "SERVICE", service)
    monkeypatch.setattr(pp, "_guard_session", guard)
    monkeypatch.setattr(PlanService, "_reconnoiter", lambda _self, _session, plan: plan)
    app = FastAPI()
    app.include_router(proxy.router)
    app.dependency_overrides[get_db] = lambda: None
    client = TestClient(app)
    headers = {"Authorization": "Bearer legacy-secret-token-for-test"}
    created = client.post("/api/mcp-stdio/plan/sessions/ingest-approved", headers=headers,
                          json={"slug": "rename-label", "provider": "github",
                                "repo": "owner/repo", "base_ref": "main", "branch": "feat/x",
                                "head_sha": "a" * 40, "artifacts": _ready_artifacts()})
    assert created.status_code == 200
    session_id = created.json()["session_id"]
    dry = client.post(f"/api/mcp-stdio/plan/sessions/{session_id}/emit-contract",
                      headers=headers, json={"dry_run": True})
    assert dry.status_code == 200
    assert dry.json()["contract_result"]["ok"] is True
    assert "human_approval" not in dry.json()["contract_result"]["contract"]["provenance"]


@pytest.mark.parametrize("query", ["", "?tail=50"])
def test_task_logs_proxy_returns_200_with_and_without_tail(monkeypatch, query):
    """#906: the proxy passed ``tail=`` to ``get_task_logs``, which takes none (500)."""
    from server import config
    from server.routes import project_authz, tasks as tasks_routes

    async def _logs(task_id):
        return {"task_id": task_id, "phases": {}}

    monkeypatch.setattr(config.get_settings(), "API_TOKEN", "legacy-secret-token-for-test")
    monkeypatch.setattr(
        project_authz.project_paths, "load_projects", lambda: {"p1": {"org_id": "default"}}
    )
    monkeypatch.setattr(tasks_routes, "get_task_logs", _logs)
    app = FastAPI()
    app.include_router(_proxy_module().router)
    r = TestClient(app).get(
        f"/api/mcp-stdio/tasks/p1:t1/logs{query}",
        headers={"Authorization": "Bearer legacy-secret-token-for-test"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"task_id": "p1:t1", "phases": {}}
