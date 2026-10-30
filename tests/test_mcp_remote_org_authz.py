"""Org-level authorization coverage for remote MCP tools (#946)."""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_WEB_SERVER_DIR = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))

READ_TASK_TOOLS = (
    "pfactory.get_task",
    "pfactory.get_worktree_diff",
    "pfactory.get_qa_report",
    "pfactory.tail_agent_console",
)

WRITE_TASK_TOOLS = (
    "pfactory.start_task",
    "pfactory.stop_task",
    "pfactory.approve_plan",
    "pfactory.merge_pr",
    "pfactory.reject_plan",
    "pfactory.recover_task",
)

ALL_TASK_TOOLS = (*READ_TASK_TOOLS, *WRITE_TASK_TOOLS)


@pytest.fixture
def org_env(tmp_path, monkeypatch):
    models = importlib.import_module("server.database.models")
    pa = importlib.import_module("server.routes.project_authz")
    tools = importlib.import_module("server.mcp_remote.tools")
    auth = importlib.import_module("server.mcp_remote.auth")

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    session_local = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(models.Base.metadata.create_all)
        async with session_local() as db:
            for uid in ("viewer-a", "member-a", "outsider", "owner"):
                db.add(models.User(id=uid, email=f"{uid}@x.test", password_hash=uid))
            await db.flush()
            db.add(models.Organization(id="A", name="A", slug="A", owner_id="owner"))
            db.add(models.Organization(id="B", name="B", slug="B", owner_id="owner"))
            await db.flush()
            db.add(models.OrgMember(org_id="A", user_id="viewer-a", role="viewer"))
            db.add(models.OrgMember(org_id="A", user_id="member-a", role="member"))
            await db.commit()

    asyncio.run(_init())
    audits = []

    async def _capture(**kw):
        audits.append(kw)

    async def _db():
        async with session_local() as db:
            yield db

    monkeypatch.setattr(tools, "get_db", _db)
    monkeypatch.setattr(pa.audit_service, "log_audit_event_bg", _capture)
    monkeypatch.setattr(
        pa.project_paths,
        "load_projects",
        lambda: {"pa": {"org_id": "A"}, "pb": {"org_id": "B"}},
    )
    settings = SimpleNamespace(DISABLE_AUTH=False, RBAC_ENFORCE_TASK_ROUTES=False)
    monkeypatch.setattr(pa, "get_settings", lambda: settings)

    call_internal = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(tools, "_call_internal", call_internal)

    scopes = frozenset({auth.MCP_READ_SCOPE, auth.MCP_WRITE_SCOPE})
    keys = {
        "member_a": auth.AuthenticatedKey(
            key_id="key-ma", scopes=scopes, user_id="member-a", org_id="A"
        ),
        "viewer_a": auth.AuthenticatedKey(
            key_id="key-va", scopes=scopes, user_id="viewer-a", org_id="A"
        ),
        "outsider": auth.AuthenticatedKey(
            key_id="key-out", scopes=scopes, user_id="outsider", org_id="A"
        ),
        "service": auth.AuthenticatedKey(
            key_id="key-svc", scopes=scopes, user_id=None, org_id=None
        ),
    }

    yield tools, keys, call_internal, audits
    asyncio.run(engine.dispose())


@pytest.mark.parametrize("tool_name", ALL_TASK_TOOLS)
async def test_member_a_denied_every_task_tool_on_org_b(org_env, tool_name):
    tools, keys, call_internal, audits = org_env
    result = await tools.dispatch_tool_call(tool_name, {"task_id": "pb:001"}, keys["member_a"])
    assert result.get("isError") is True
    assert "denied" in result["content"][0]["text"]
    call_internal.assert_not_awaited()
    assert len(audits) == 1


async def test_member_a_denied_list_tasks_on_org_b(org_env):
    tools, keys, call_internal, audits = org_env
    result = await tools.dispatch_tool_call(
        "pfactory.list_tasks", {"project_id": "pb"}, keys["member_a"]
    )
    assert result.get("isError") is True
    assert "denied" in result["content"][0]["text"]
    call_internal.assert_not_awaited()
    assert len(audits) == 1


@pytest.mark.parametrize("tool_name", ALL_TASK_TOOLS)
async def test_member_a_allowed_every_task_tool_on_org_a(org_env, tool_name):
    tools, keys, call_internal, audits = org_env
    result = await tools.dispatch_tool_call(tool_name, {"task_id": "pa:001"}, keys["member_a"])
    assert not result.get("isError")
    if tool_name != "pfactory.tail_agent_console":
        call_internal.assert_awaited_once()
    assert audits == []


async def test_member_a_allowed_list_tasks_on_org_a(org_env):
    tools, keys, call_internal, audits = org_env
    result = await tools.dispatch_tool_call(
        "pfactory.list_tasks", {"project_id": "pa"}, keys["member_a"]
    )
    assert not result.get("isError")
    call_internal.assert_awaited_once_with("GET", "/api/projects/pa/tasks", keys["member_a"])
    assert audits == []


async def test_list_projects_hides_other_orgs(org_env):
    tools, keys, call_internal, _ = org_env
    call_internal.return_value = [{"id": "pa"}, {"id": "pb"}, {"id": "unassigned"}]

    res_a = await tools.dispatch_tool_call("pfactory.list_projects", {}, keys["member_a"])
    assert json.loads(res_a["content"][0]["text"]) == [{"id": "pa"}]

    res_out = await tools.dispatch_tool_call("pfactory.list_projects", {}, keys["outsider"])
    assert json.loads(res_out["content"][0]["text"]) == []

    res_svc = await tools.dispatch_tool_call("pfactory.list_projects", {}, keys["service"])
    assert json.loads(res_svc["content"][0]["text"]) == [
        {"id": "pa"},
        {"id": "pb"},
        {"id": "unassigned"},
    ]


@pytest.mark.parametrize("tool_name", WRITE_TASK_TOOLS)
async def test_viewer_with_write_scope_denied_write_tools(org_env, tool_name):
    tools, keys, call_internal, audits = org_env
    result = await tools.dispatch_tool_call(tool_name, {"task_id": "pa:001"}, keys["viewer_a"])
    assert result.get("isError") is True
    assert "denied" in result["content"][0]["text"]
    call_internal.assert_not_awaited()
    assert len(audits) == 1


@pytest.mark.parametrize("tool_name", READ_TASK_TOOLS)
async def test_viewer_with_write_scope_allowed_read_tools(org_env, tool_name):
    tools, keys, call_internal, audits = org_env
    result = await tools.dispatch_tool_call(tool_name, {"task_id": "pa:001"}, keys["viewer_a"])
    assert not result.get("isError")
    if tool_name != "pfactory.tail_agent_console":
        call_internal.assert_awaited_once()
    assert audits == []


async def test_viewer_allowed_list_tasks(org_env):
    tools, keys, call_internal, audits = org_env
    result = await tools.dispatch_tool_call(
        "pfactory.list_tasks", {"project_id": "pa"}, keys["viewer_a"]
    )
    assert not result.get("isError")
    call_internal.assert_awaited_once()
    assert audits == []


@pytest.mark.parametrize(
    "bad_task_id",
    [
        "a:1/../b:2",
        "pa",
        ".pa:1",
        "pa:1?x=1",
        "pa:1#frag",
        "pa:1%2f",
        "",
        123,
        None,
        ["pa:1"],
    ],
)
async def test_invalid_task_ids_refused(org_env, bad_task_id):
    tools, keys, call_internal, _ = org_env
    result = await tools.dispatch_tool_call(
        "pfactory.get_task",
        {"task_id": bad_task_id, "project_id": "pa"},
        keys["member_a"],
    )
    assert result.get("isError") is True
    assert "invalid task_id" in result["content"][0]["text"]
    call_internal.assert_not_awaited()


@pytest.mark.parametrize("bad_project_id", ["../pb", ".pa", "pa/b", "pa?x=1", "", 123, None])
async def test_invalid_project_ids_refused(org_env, bad_project_id):
    tools, keys, call_internal, _ = org_env
    result = await tools.dispatch_tool_call(
        "pfactory.list_tasks",
        {"project_id": bad_project_id},
        keys["member_a"],
    )
    assert result.get("isError") is True
    assert "invalid project_id" in result["content"][0]["text"]
    call_internal.assert_not_awaited()
