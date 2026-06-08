"""PFactory plan-review-from-PR endpoint (epic #87 / #90, Component 4).

Drives POST /api/github/prs/{pr}/plan-review with a stubbed gh and the real
deterministic plan pipeline. Requires FastAPI (web-server venv).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

_ROOT = Path(__file__).parent.parent
for p in (str(_ROOT / "apps" / "backend"), str(_ROOT / "apps" / "web-server")):
    if p not in sys.path:
        sys.path.insert(0, p)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from server.routes import github as gh_routes  # noqa: E402

_PR_JSON = (
    '{"title": "Add OIDC SSO", "body": "Implement single sign-on.\\n\\n'
    "## Acceptance Criteria\\n- Users can log in via OIDC\\n- Sessions persist\"}"
)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("PFACTORY_PLAN_REVIEW_COMMENT", raising=False)
    app = FastAPI()
    app.include_router(gh_routes.router, prefix="/api/github")
    return TestClient(app)


def test_plan_review_runs_gates_and_returns_summary(client, monkeypatch):
    calls = []

    def fake_gh(args, cwd=None):
        calls.append(args)
        if args[:2] == ["pr", "view"]:
            return {"success": True, "output": _PR_JSON}
        return {"success": True, "output": ""}

    monkeypatch.setattr(gh_routes, "run_gh_command", fake_gh)

    r = client.post(
        "/api/github/prs/12/plan-review",
        json={"repo": "owner/repo", "pr_number": 12},
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["gates_passed"] in (True, False)  # gates ran
    assert "PFactory plan review" in data["comment_body"]
    assert data["comment_posted"] is False  # opt-in env not set
    # gh pr view was called; gh pr comment was NOT (comment posting opt-in)
    assert any(a[:2] == ["pr", "view"] for a in calls)
    assert not any(a[:2] == ["pr", "comment"] for a in calls)


def test_plan_review_posts_comment_when_opted_in(client, monkeypatch):
    monkeypatch.setenv("PFACTORY_PLAN_REVIEW_COMMENT", "1")
    posted = {}

    def fake_gh(args, cwd=None):
        if args[:2] == ["pr", "view"]:
            return {"success": True, "output": _PR_JSON}
        if args[:2] == ["pr", "comment"]:
            posted["body"] = args
            return {"success": True, "output": ""}
        return {"success": True, "output": ""}

    monkeypatch.setattr(gh_routes, "run_gh_command", fake_gh)
    r = client.post(
        "/api/github/prs/12/plan-review",
        json={"repo": "owner/repo", "pr_number": 12},
    )
    assert r.status_code == 200
    assert r.json()["data"]["comment_posted"] is True
    assert posted, "expected gh pr comment to be invoked"


def test_plan_review_502_when_pr_fetch_fails(client, monkeypatch):
    def fake_gh(args, cwd=None):
        return {"success": False, "error": "no such PR"}

    monkeypatch.setattr(gh_routes, "run_gh_command", fake_gh)
    r = client.post(
        "/api/github/prs/999/plan-review",
        json={"repo": "owner/repo", "pr_number": 999},
    )
    assert r.status_code == 502
    assert r.json()["success"] is False
