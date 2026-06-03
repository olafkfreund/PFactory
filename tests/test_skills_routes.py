#!/usr/bin/env python3
"""
Tests for Skills API Routes
============================

Tests the /api/skills/* FastAPI endpoints using a minimal TestClient that
patches the SkillsService singleton so it never touches the real skills
directory on disk.

Endpoints under test:
  GET /api/skills/categories
  GET /api/skills/list?category=<name>
  GET /api/skills/search?q=<query>
  GET /api/skills/suggest?task_description=<text>
  GET /api/skills/{category}/{skill_name}
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Add web-server to path so server modules are importable
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "web-server"))

from server.routes.skills import router as skills_router
from server.services.skills_service import SkillsService

# Fixture skills directory — fully isolated from the real skills/ dir
FIXTURES_PATH = Path(__file__).parent / "fixtures" / "skills"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fixture_service() -> SkillsService:
    """A SkillsService instance backed by the test fixtures directory."""
    return SkillsService(skills_base_path=FIXTURES_PATH)


@pytest.fixture(scope="module")
def _app() -> FastAPI:
    """Minimal FastAPI application containing only the skills router."""
    app = FastAPI(title="Skills Test App")
    app.include_router(skills_router, prefix="/api/skills")
    return app


@pytest.fixture
def client(_app: FastAPI, fixture_service: SkillsService):
    """
    TestClient whose skills endpoints use the fixture-backed service.

    Patches ``get_skills_service`` in the routes module so the real
    on-disk skills singleton is never touched.
    """
    with patch(
        "server.routes.skills.get_skills_service",
        return_value=fixture_service,
    ):
        with TestClient(_app, raise_server_exceptions=True) as c:
            yield c


# ---------------------------------------------------------------------------
# GET /api/skills/categories
# ---------------------------------------------------------------------------


class TestGetCategories:
    """Tests for GET /api/skills/categories."""

    def test_returns_200(self, client):
        resp = client.get("/api/skills/categories")
        assert resp.status_code == 200

    def test_returns_list(self, client):
        data = client.get("/api/skills/categories").json()
        assert isinstance(data, list)

    def test_returns_three_fixture_categories(self, client):
        data = client.get("/api/skills/categories").json()
        assert len(data) == 3

    def test_contains_expected_category_names(self, client):
        data = client.get("/api/skills/categories").json()
        names = {cat["name"] for cat in data}
        assert "frontend" in names
        assert "backend" in names
        assert "devops" in names

    def test_category_has_count_field(self, client):
        data = client.get("/api/skills/categories").json()
        for cat in data:
            assert "count" in cat
            assert isinstance(cat["count"], int)
            assert cat["count"] >= 0

    def test_category_fields_present(self, client):
        data = client.get("/api/skills/categories").json()
        for cat in data:
            assert "name" in cat
            assert "count" in cat


# ---------------------------------------------------------------------------
# GET /api/skills/list
# ---------------------------------------------------------------------------


class TestListSkills:
    """Tests for GET /api/skills/list?category=<name>."""

    def test_list_frontend_returns_200(self, client):
        resp = client.get("/api/skills/list?category=frontend")
        assert resp.status_code == 200

    def test_list_backend_returns_200(self, client):
        resp = client.get("/api/skills/list?category=backend")
        assert resp.status_code == 200

    def test_list_devops_returns_200(self, client):
        resp = client.get("/api/skills/list?category=devops")
        assert resp.status_code == 200

    def test_response_is_paginated(self, client):
        data = client.get("/api/skills/list?category=frontend").json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "limit" in data
        assert "has_next" in data

    def test_frontend_contains_react(self, client):
        data = client.get("/api/skills/list?category=frontend").json()
        names = [item["name"] for item in data["items"]]
        assert "react" in names

    def test_backend_contains_fastapi(self, client):
        data = client.get("/api/skills/list?category=backend").json()
        names = [item["name"] for item in data["items"]]
        assert "fastapi" in names

    def test_devops_contains_docker(self, client):
        data = client.get("/api/skills/list?category=devops").json()
        names = [item["name"] for item in data["items"]]
        assert "docker" in names

    def test_unknown_category_returns_404(self, client):
        resp = client.get("/api/skills/list?category=nonexistent_category")
        assert resp.status_code == 404

    def test_missing_category_param_returns_422(self, client):
        """category query param is required."""
        resp = client.get("/api/skills/list")
        assert resp.status_code == 422

    def test_page_defaults_to_1(self, client):
        data = client.get("/api/skills/list?category=frontend").json()
        assert data["page"] == 1

    def test_custom_limit_respected(self, client):
        data = client.get("/api/skills/list?category=frontend&limit=5").json()
        assert data["limit"] == 5

    def test_skill_items_have_required_fields(self, client):
        data = client.get("/api/skills/list?category=frontend").json()
        for item in data["items"]:
            assert "id" in item
            assert "name" in item
            assert "category" in item
            assert "description" in item


# ---------------------------------------------------------------------------
# GET /api/skills/search
# ---------------------------------------------------------------------------


class TestSearchSkills:
    """Tests for GET /api/skills/search?q=<query>."""

    def test_search_react_returns_200(self, client):
        resp = client.get("/api/skills/search?q=react")
        assert resp.status_code == 200

    def test_search_returns_paginated_response(self, client):
        data = client.get("/api/skills/search?q=react").json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "limit" in data
        assert "has_next" in data

    def test_search_react_finds_react_skill(self, client):
        data = client.get("/api/skills/search?q=react").json()
        names = [item["name"] for item in data["items"]]
        assert "react" in names

    def test_search_fastapi_finds_fastapi_skill(self, client):
        data = client.get("/api/skills/search?q=fastapi").json()
        names = [item["name"] for item in data["items"]]
        assert "fastapi" in names

    def test_search_docker_finds_docker_skill(self, client):
        data = client.get("/api/skills/search?q=docker").json()
        names = [item["name"] for item in data["items"]]
        assert "docker" in names

    def test_search_with_category_filter(self, client):
        data = client.get("/api/skills/search?q=docker&category=devops").json()
        for item in data["items"]:
            assert item["category"] == "devops"

    def test_search_nonexistent_category_returns_404(self, client):
        resp = client.get("/api/skills/search?q=react&category=bogus_category")
        assert resp.status_code == 404

    def test_missing_q_param_returns_422(self, client):
        """q query param is required."""
        resp = client.get("/api/skills/search")
        assert resp.status_code == 422

    def test_limit_param_applied(self, client):
        data = client.get("/api/skills/search?q=a&limit=1").json()
        assert len(data["items"]) <= 1

    def test_no_match_returns_empty_items(self, client):
        data = client.get("/api/skills/search?q=xxxxxxnoexist").json()
        assert data["items"] == []
        assert data["total"] == 0


# ---------------------------------------------------------------------------
# GET /api/skills/suggest
# ---------------------------------------------------------------------------


class TestSuggestSkills:
    """Tests for GET /api/skills/suggest?task_description=<text>."""

    def test_suggest_returns_200(self, client):
        resp = client.get(
            "/api/skills/suggest?task_description=Build a React frontend app"
        )
        assert resp.status_code == 200

    def test_suggest_returns_list(self, client):
        data = client.get(
            "/api/skills/suggest?task_description=Build a React frontend app"
        ).json()
        assert isinstance(data, list)

    def test_suggest_react_task_includes_react(self, client):
        data = client.get(
            "/api/skills/suggest?task_description=Build a React frontend app"
        ).json()
        names = [s["skill"]["name"] for s in data]
        assert "react" in names

    def test_suggest_fastapi_task_includes_fastapi(self, client):
        data = client.get(
            "/api/skills/suggest?task_description=Create a FastAPI REST API backend"
        ).json()
        names = [s["skill"]["name"] for s in data]
        assert "fastapi" in names

    def test_suggest_docker_task_includes_docker(self, client):
        data = client.get(
            "/api/skills/suggest?task_description=Containerize app with Docker"
        ).json()
        names = [s["skill"]["name"] for s in data]
        assert "docker" in names

    def test_suggestion_has_relevance_score(self, client):
        data = client.get(
            "/api/skills/suggest?task_description=Build a React frontend app"
        ).json()
        for s in data:
            assert "relevance_score" in s
            score = s["relevance_score"]
            assert 0.0 <= score <= 1.0

    def test_suggestion_has_reason(self, client):
        data = client.get(
            "/api/skills/suggest?task_description=Build a React frontend app"
        ).json()
        for s in data:
            assert "reason" in s
            assert isinstance(s["reason"], str)
            assert len(s["reason"]) > 0

    def test_suggestion_has_nested_skill(self, client):
        data = client.get(
            "/api/skills/suggest?task_description=Build a React frontend app"
        ).json()
        for s in data:
            skill = s["skill"]
            assert "id" in skill
            assert "name" in skill
            assert "category" in skill
            assert "description" in skill

    def test_missing_task_description_returns_422(self, client):
        resp = client.get("/api/skills/suggest")
        assert resp.status_code == 422

    def test_limit_param_caps_results(self, client):
        data = client.get(
            "/api/skills/suggest"
            "?task_description=Build a React FastAPI Docker app&limit=1"
        ).json()
        assert len(data) <= 1


# ---------------------------------------------------------------------------
# GET /api/skills/{category}/{skill_name}
# ---------------------------------------------------------------------------


class TestGetSkillDetail:
    """Tests for GET /api/skills/{category}/{skill_name}."""

    def test_get_react_returns_200(self, client):
        resp = client.get("/api/skills/frontend/react")
        assert resp.status_code == 200

    def test_get_fastapi_returns_200(self, client):
        resp = client.get("/api/skills/backend/fastapi")
        assert resp.status_code == 200

    def test_get_docker_returns_200(self, client):
        resp = client.get("/api/skills/devops/docker")
        assert resp.status_code == 200

    def test_response_contains_content_field(self, client):
        data = client.get("/api/skills/frontend/react").json()
        assert "content" in data
        assert isinstance(data["content"], str)
        assert len(data["content"]) > 0

    def test_content_contains_skill_text(self, client):
        data = client.get("/api/skills/frontend/react").json()
        assert "React" in data["content"]

    def test_response_contains_summary_fields(self, client):
        data = client.get("/api/skills/frontend/react").json()
        assert "id" in data
        assert "name" in data
        assert "category" in data
        assert "description" in data

    def test_id_matches_category_and_name(self, client):
        data = client.get("/api/skills/backend/fastapi").json()
        assert data["id"] == "backend/fastapi"
        assert data["name"] == "fastapi"
        assert data["category"] == "backend"

    def test_source_field_present(self, client):
        """source field is present (may be None for skills without it)."""
        data = client.get("/api/skills/frontend/react").json()
        assert "source" in data

    def test_nonexistent_skill_returns_404(self, client):
        resp = client.get("/api/skills/frontend/nonexistent_skill")
        assert resp.status_code == 404

    def test_nonexistent_category_returns_404(self, client):
        resp = client.get("/api/skills/nonexistent_category/react")
        assert resp.status_code == 404

    def test_404_response_has_detail(self, client):
        data = client.get("/api/skills/frontend/bogus").json()
        assert "detail" in data
