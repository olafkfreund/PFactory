"""PFactory MCP server — JSON-RPC planning-context tools (epic #87 / #89).

Drives the POST /mcp router with a FastAPI TestClient against a seeded in-memory
plan session. Requires FastAPI (web-server venv); skipped where it is absent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

_ROOT = Path(__file__).parent.parent
_BACKEND = _ROOT / "apps" / "backend"
_WEBSERVER = _ROOT / "apps" / "web-server"
for p in (str(_BACKEND), str(_WEBSERVER)):
    if p not in sys.path:
        sys.path.insert(0, p)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from plan.decompose.models import ChildIssue, EpicPlan  # noqa: E402
from plan.models import Criterion, NormalizedPlan  # noqa: E402
from plan.review.models import LensScore, PlanReview  # noqa: E402
from plan.service import SERVICE, PlanSession  # noqa: E402
from server.routes import mcp_rpc  # noqa: E402

ISSUE = 4242
SESSION_ID = "plan-mcp-test"


@pytest.fixture()
def seeded_session():
    """Insert a fully-processed session into the shared SERVICE store."""
    plan = NormalizedPlan(
        plan_id=SESSION_ID,
        title="Add SSO",
        description="Single sign-on for the portal",
        source_format="markdown",
        criteria=[Criterion(id="AC1", text="Users can log in via OIDC")],
        target_kind="software",
    )
    epic = EpicPlan(
        plan_id=SESSION_ID,
        epic_title="Add SSO",
        epic_body="Epic body",
        summary="SSO epic",
        children=[
            ChildIssue(key="C1", title="OIDC callback", acceptance_criteria=["redirects"]),
            ChildIssue(key="C2", title="Session cookie", depends_on=["C1"]),
        ],
    )
    review = PlanReview(
        plan_id=SESSION_ID,
        lenses=[LensScore(lens="security", score=0.9)],
        aggregate_score=0.9,
        gates_passed=True,
    )
    session = PlanSession(session_id=SESSION_ID, plan=plan)
    session.epic = epic
    session.review = review
    session.emitted_issue_number = ISSUE
    session.contract_result = {"ok": True, "contract": {"schema": "task-contract/v2", "plan_id": SESSION_ID}}
    SERVICE._sessions[SESSION_ID] = session
    yield session
    SERVICE._sessions.pop(SESSION_ID, None)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("PFACTORY_MCP_SECRET", raising=False)
    app = FastAPI()
    app.include_router(mcp_rpc.router)
    return TestClient(app)


def _call(client, method, params=None, req_id=1, headers=None):
    body = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=headers or {})


# ---------------------------------------------------------------------------
# Protocol surface
# ---------------------------------------------------------------------------


def test_initialize(client):
    r = _call(client, "initialize")
    assert r.status_code == 200
    data = r.json()
    assert data["result"]["serverInfo"]["name"] == "pfactory"
    assert "capabilities" in data["result"]


def test_tools_list_returns_five_tools(client):
    r = _call(client, "tools/list")
    tools = r.json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {
        "pfactory_get_epic",
        "pfactory_get_requirements",
        "pfactory_get_decomposition",
        "pfactory_get_task_contract",
        "pfactory_get_review_status",
    }


def test_initialized_notification_is_202(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202


def test_unknown_method(client):
    r = _call(client, "does/not/exist")
    assert r.json()["error"]["code"] == -32601


# ---------------------------------------------------------------------------
# Tool calls
# ---------------------------------------------------------------------------


def _tool_payload(resp):
    import json as _json
    result = resp.json()["result"]
    assert result["isError"] is False, result
    return _json.loads(result["content"][0]["text"])


def test_get_task_contract_by_issue_number(client, seeded_session):
    r = _call(client, "tools/call", {
        "name": "pfactory_get_task_contract",
        "arguments": {"issue_number": ISSUE},
    })
    payload = _tool_payload(r)
    assert payload["contract"]["schema"] == "task-contract/v2"
    assert payload["contract"]["plan_id"] == SESSION_ID


def test_get_requirements_by_session_id(client, seeded_session):
    r = _call(client, "tools/call", {
        "name": "pfactory_get_requirements",
        "arguments": {"session_id": SESSION_ID},
    })
    payload = _tool_payload(r)
    assert payload["title"] == "Add SSO"
    assert payload["acceptance_criteria"][0]["text"] == "Users can log in via OIDC"


def test_get_decomposition(client, seeded_session):
    r = _call(client, "tools/call", {
        "name": "pfactory_get_decomposition",
        "arguments": {"issue_number": ISSUE},
    })
    payload = _tool_payload(r)
    assert [c["key"] for c in payload["children"]] == ["C1", "C2"]
    assert payload["children"][1]["depends_on"] == ["C1"]


def test_get_review_status(client, seeded_session):
    r = _call(client, "tools/call", {
        "name": "pfactory_get_review_status",
        "arguments": {"issue_number": ISSUE},
    })
    payload = _tool_payload(r)
    assert payload["gates_passed"] is True
    assert payload["aggregate_score"] == 0.9


def test_get_epic(client, seeded_session):
    r = _call(client, "tools/call", {
        "name": "pfactory_get_epic",
        "arguments": {"issue_number": ISSUE},
    })
    payload = _tool_payload(r)
    assert payload["epic_title"] == "Add SSO"


def test_unknown_issue_is_tool_error(client):
    r = _call(client, "tools/call", {
        "name": "pfactory_get_epic",
        "arguments": {"issue_number": 999999},
    })
    result = r.json()["result"]
    assert result["isError"] is True
    assert "no plan session" in result["content"][0]["text"]


def test_missing_selector_is_tool_error(client):
    r = _call(client, "tools/call", {
        "name": "pfactory_get_requirements",
        "arguments": {},
    })
    result = r.json()["result"]
    assert result["isError"] is True
    assert "issue_number" in result["content"][0]["text"]


def test_unknown_tool(client):
    r = _call(client, "tools/call", {"name": "pfactory_nope", "arguments": {}})
    assert r.json()["error"]["code"] == -32601


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_auth_rejects_missing_token(monkeypatch, seeded_session):
    monkeypatch.setenv("PFACTORY_MCP_SECRET", "s3cret")
    app = FastAPI()
    app.include_router(mcp_rpc.router)
    c = TestClient(app)
    r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401


def test_auth_accepts_valid_token(monkeypatch, seeded_session):
    monkeypatch.setenv("PFACTORY_MCP_SECRET", "s3cret")
    app = FastAPI()
    app.include_router(mcp_rpc.router)
    c = TestClient(app)
    r = c.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": "Bearer s3cret"},
    )
    assert r.status_code == 200
    assert len(r.json()["result"]["tools"]) == 5


def test_auth_rejects_wrong_token(monkeypatch, seeded_session):
    monkeypatch.setenv("PFACTORY_MCP_SECRET", "s3cret")
    app = FastAPI()
    app.include_router(mcp_rpc.router)
    c = TestClient(app)
    r = c.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 401
