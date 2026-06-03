#!/usr/bin/env python3
"""
Tests for SkillsService
=======================

Tests the skills_service.py module functionality including:
- In-memory index construction from a skills directory tree
- Category listing
- Skill listing by category
- Keyword search across name and description
- Individual skill retrieval (summary and full content)
- Auto-suggest based on task description with synonym expansion
- Graceful handling of missing/invalid paths
"""

import sys
from pathlib import Path

import pytest

# Add web-server to path so we can import server modules
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "web-server"))

from server.services.skills_service import (
    SkillCategory,
    SkillDetail,
    SkillsService,
    SkillSuggestion,
    SkillSummary,
)

# Fixture skills directory — fully isolated from the real skills/ dir
FIXTURES_PATH = Path(__file__).parent / "fixtures" / "skills"


class TestSkillsServiceInit:
    """Tests for SkillsService initialisation and index building."""

    def setup_method(self):
        self.service = SkillsService(skills_base_path=FIXTURES_PATH)

    def test_builds_index_from_fixtures(self):
        """Index is populated from the fixture directory."""
        assert self.service._built is True
        assert len(self.service._index) == 3

    def test_missing_skills_path_returns_empty_index(self):
        """Service initialises gracefully when skills path does not exist."""
        service = SkillsService(skills_base_path=Path("/nonexistent/skills/path"))
        assert service._built is True
        assert service._index == {}

    def test_missing_skills_path_all_queries_return_empty(self):
        """All query methods return empty results when path is missing."""
        service = SkillsService(skills_base_path=Path("/nonexistent/skills/path"))
        assert service.list_categories() == []
        assert service.list_skills("frontend") == []
        assert service.search_skills("react") == []
        assert service.get_skill("frontend", "react") is None
        assert service.get_skill_content("frontend", "react") is None
        assert service.get_skill_detail("frontend", "react") is None
        assert service.suggest_skills("Build a React app") == []


class TestListCategories:
    """Tests for list_categories()."""

    def setup_method(self):
        self.service = SkillsService(skills_base_path=FIXTURES_PATH)

    def test_returns_all_fixture_categories(self):
        """All three fixture categories are returned."""
        categories = self.service.list_categories()
        names = [c.name for c in categories]
        assert "frontend" in names
        assert "backend" in names
        assert "devops" in names

    def test_returns_correct_count(self):
        """Each category reports one skill (one fixture file per dir)."""
        categories = self.service.list_categories()
        assert len(categories) == 3
        for cat in categories:
            assert cat.count == 1

    def test_returns_skill_category_instances(self):
        """Items are SkillCategory dataclass instances."""
        categories = self.service.list_categories()
        for cat in categories:
            assert isinstance(cat, SkillCategory)

    def test_categories_sorted_alphabetically(self):
        """Categories are returned in sorted order."""
        categories = self.service.list_categories()
        names = [c.name for c in categories]
        assert names == sorted(names)


class TestListSkills:
    """Tests for list_skills(category)."""

    def setup_method(self):
        self.service = SkillsService(skills_base_path=FIXTURES_PATH)

    def test_list_frontend_skills(self):
        """Returns the react skill from the frontend category."""
        skills = self.service.list_skills("frontend")
        assert len(skills) == 1
        assert skills[0].name == "react"

    def test_list_backend_skills(self):
        """Returns the fastapi skill from the backend category."""
        skills = self.service.list_skills("backend")
        assert len(skills) == 1
        assert skills[0].name == "fastapi"

    def test_list_devops_skills(self):
        """Returns the docker skill from the devops category."""
        skills = self.service.list_skills("devops")
        assert len(skills) == 1
        assert skills[0].name == "docker"

    def test_unknown_category_returns_empty_list(self):
        """Returns [] for a category that doesn't exist."""
        assert self.service.list_skills("nonexistent_category") == []

    def test_returns_skill_summary_instances(self):
        """Items are SkillSummary dataclass instances."""
        skills = self.service.list_skills("frontend")
        for skill in skills:
            assert isinstance(skill, SkillSummary)

    def test_skill_id_format(self):
        """Skill id follows '{category}/{name}' format."""
        skills = self.service.list_skills("frontend")
        assert skills[0].id == "frontend/react"

    def test_skill_has_category_set(self):
        """Skill summary has correct category field."""
        skills = self.service.list_skills("backend")
        assert skills[0].category == "backend"


class TestSearchSkills:
    """Tests for search_skills(query, category, limit)."""

    def setup_method(self):
        self.service = SkillsService(skills_base_path=FIXTURES_PATH)

    def test_search_returns_react_for_react_query(self):
        """Searching 'react' returns the react skill."""
        results = self.service.search_skills("react")
        assert len(results) > 0
        assert results[0].name == "react"

    def test_search_returns_fastapi_for_fastapi_query(self):
        """Searching 'fastapi' returns the fastapi skill."""
        results = self.service.search_skills("fastapi")
        assert len(results) > 0
        assert results[0].name == "fastapi"

    def test_search_returns_docker_for_docker_query(self):
        """Searching 'docker' returns the docker skill."""
        results = self.service.search_skills("docker")
        assert len(results) > 0
        assert results[0].name == "docker"

    def test_search_with_category_filter(self):
        """Category filter restricts results to that category."""
        results = self.service.search_skills("docker", category="devops")
        assert all(s.category == "devops" for s in results)

    def test_search_category_filter_excludes_other_categories(self):
        """Searching in 'frontend' category does not return backend skills."""
        results = self.service.search_skills("fastapi", category="frontend")
        assert all(s.category == "frontend" for s in results)

    def test_empty_query_returns_empty_list(self):
        """Empty or blank query returns empty list."""
        assert self.service.search_skills("") == []
        assert self.service.search_skills("   ") == []

    def test_search_returns_summary_instances(self):
        """Results are SkillSummary instances."""
        results = self.service.search_skills("react")
        for result in results:
            assert isinstance(result, SkillSummary)

    def test_limit_parameter_respected(self):
        """Result list is capped at the supplied limit."""
        results = self.service.search_skills("a", limit=1)
        assert len(results) <= 1

    def test_no_matching_results_returns_empty_list(self):
        """Query with no matches returns empty list."""
        results = self.service.search_skills("xxxxxxxxxxxxxxnoexist")
        assert results == []

    def test_case_insensitive_search(self):
        """Search is case-insensitive."""
        lower = self.service.search_skills("react")
        upper = self.service.search_skills("REACT")
        assert len(lower) > 0
        assert len(upper) > 0


class TestGetSkill:
    """Tests for get_skill(category, name)."""

    def setup_method(self):
        self.service = SkillsService(skills_base_path=FIXTURES_PATH)

    def test_get_react_skill_summary(self):
        """Returns SkillSummary for the react fixture."""
        skill = self.service.get_skill("frontend", "react")
        assert skill is not None
        assert isinstance(skill, SkillSummary)
        assert skill.name == "react"
        assert skill.category == "frontend"

    def test_get_fastapi_skill_summary(self):
        """Returns SkillSummary for the fastapi fixture."""
        skill = self.service.get_skill("backend", "fastapi")
        assert skill is not None
        assert skill.name == "fastapi"

    def test_get_docker_skill_summary(self):
        """Returns SkillSummary for the docker fixture."""
        skill = self.service.get_skill("devops", "docker")
        assert skill is not None
        assert skill.name == "docker"

    def test_unknown_skill_returns_none(self):
        """Returns None when skill name doesn't exist."""
        assert self.service.get_skill("frontend", "angular") is None

    def test_unknown_category_returns_none(self):
        """Returns None when category doesn't exist."""
        assert self.service.get_skill("unknown_cat", "react") is None

    def test_skill_description_non_empty(self):
        """Extracted description is a non-empty string."""
        skill = self.service.get_skill("frontend", "react")
        assert skill is not None
        assert skill.description
        assert len(skill.description) > 10

    def test_skill_source_extracted(self):
        """Source URL is extracted from the blockquote."""
        skill = self.service.get_skill("frontend", "react")
        assert skill is not None
        assert skill.source is not None
        assert "github.com" in skill.source


class TestGetSkillContent:
    """Tests for get_skill_content(category, name)."""

    def setup_method(self):
        self.service = SkillsService(skills_base_path=FIXTURES_PATH)

    def test_returns_react_markdown_content(self):
        """Full markdown content is returned for react fixture."""
        content = self.service.get_skill_content("frontend", "react")
        assert content is not None
        assert "React" in content

    def test_returns_fastapi_markdown_content(self):
        """Full markdown content is returned for fastapi fixture."""
        content = self.service.get_skill_content("backend", "fastapi")
        assert content is not None
        assert "FastAPI" in content

    def test_returns_docker_markdown_content(self):
        """Full markdown content is returned for docker fixture."""
        content = self.service.get_skill_content("devops", "docker")
        assert content is not None
        assert "Docker" in content

    def test_unknown_skill_returns_none(self):
        """Returns None for non-existent skill."""
        assert self.service.get_skill_content("frontend", "vue") is None

    def test_content_starts_with_heading(self):
        """Raw content starts with a Markdown heading."""
        content = self.service.get_skill_content("frontend", "react")
        assert content is not None
        assert content.startswith("# ")

    def test_content_contains_source_line(self):
        """Content includes the '> Source:' blockquote."""
        content = self.service.get_skill_content("backend", "fastapi")
        assert content is not None
        assert "> Source:" in content


class TestGetSkillDetail:
    """Tests for get_skill_detail(category, name)."""

    def setup_method(self):
        self.service = SkillsService(skills_base_path=FIXTURES_PATH)

    def test_returns_skill_detail_instance(self):
        """Returns a SkillDetail dataclass instance."""
        detail = self.service.get_skill_detail("frontend", "react")
        assert isinstance(detail, SkillDetail)

    def test_detail_includes_content(self):
        """SkillDetail has non-empty content field."""
        detail = self.service.get_skill_detail("frontend", "react")
        assert detail is not None
        assert detail.content
        assert "React" in detail.content

    def test_detail_fields_match_summary(self):
        """SkillDetail has same id, name, category as SkillSummary."""
        summary = self.service.get_skill("frontend", "react")
        detail = self.service.get_skill_detail("frontend", "react")
        assert detail is not None
        assert summary is not None
        assert detail.id == summary.id
        assert detail.name == summary.name
        assert detail.category == summary.category
        assert detail.description == summary.description

    def test_unknown_skill_returns_none(self):
        """Returns None for non-existent skill."""
        assert self.service.get_skill_detail("devops", "kubernetes") is None


class TestSuggestSkills:
    """Tests for suggest_skills(task_description, max_results)."""

    def setup_method(self):
        self.service = SkillsService(skills_base_path=FIXTURES_PATH)

    def test_suggests_react_for_react_task(self):
        """'Build a React frontend app' suggests the react skill."""
        suggestions = self.service.suggest_skills("Build a React frontend app")
        assert len(suggestions) > 0
        skill_names = [s.skill.name for s in suggestions]
        assert "react" in skill_names

    def test_suggests_fastapi_for_api_task(self):
        """'Create a FastAPI REST API' suggests the fastapi skill."""
        suggestions = self.service.suggest_skills("Create a FastAPI REST API backend")
        assert len(suggestions) > 0
        skill_names = [s.skill.name for s in suggestions]
        assert "fastapi" in skill_names

    def test_suggests_docker_for_containerization_task(self):
        """'Containerize the application with Docker' suggests docker."""
        suggestions = self.service.suggest_skills(
            "Containerize the application with Docker and deploy"
        )
        assert len(suggestions) > 0
        skill_names = [s.skill.name for s in suggestions]
        assert "docker" in skill_names

    def test_multi_skill_suggestion(self):
        """Task mentioning both FastAPI and Docker suggests both skills."""
        suggestions = self.service.suggest_skills(
            "Create a FastAPI backend with Docker containerization"
        )
        skill_names = [s.skill.name for s in suggestions]
        assert "fastapi" in skill_names
        assert "docker" in skill_names

    def test_returns_skill_suggestion_instances(self):
        """Items are SkillSuggestion dataclass instances."""
        suggestions = self.service.suggest_skills("Build a React frontend app")
        for s in suggestions:
            assert isinstance(s, SkillSuggestion)

    def test_relevance_score_in_valid_range(self):
        """Relevance scores are between 0.0 and 1.0 inclusive."""
        suggestions = self.service.suggest_skills("Build a React frontend app")
        for s in suggestions:
            assert 0.0 <= s.relevance_score <= 1.0

    def test_reason_is_non_empty_string(self):
        """Each suggestion has a non-empty human-readable reason."""
        suggestions = self.service.suggest_skills("Build a React frontend app")
        for s in suggestions:
            assert isinstance(s.reason, str)
            assert len(s.reason) > 0

    def test_short_description_returns_empty(self):
        """Task descriptions shorter than 10 chars return no suggestions."""
        suggestions = self.service.suggest_skills("React")
        assert suggestions == []

    def test_max_results_respected(self):
        """Result list is capped at max_results."""
        suggestions = self.service.suggest_skills(
            "Build a React FastAPI Docker app", max_results=2
        )
        assert len(suggestions) <= 2

    def test_empty_description_returns_empty(self):
        """Empty task description returns empty list."""
        assert self.service.suggest_skills("") == []

    def test_suggestions_ordered_by_relevance(self):
        """Suggestions are ordered from highest to lowest relevance score."""
        suggestions = self.service.suggest_skills(
            "Build a React frontend app with components"
        )
        scores = [s.relevance_score for s in suggestions]
        assert scores == sorted(scores, reverse=True)


class TestMetadataExtraction:
    """Tests for _extract_metadata() – the internal markdown parser."""

    def test_extract_source_from_markdown_link(self):
        """Markdown link syntax in Source line is parsed to bare URL."""
        content = (
            "# react\n\n"
            "> Source: [facebook/react](https://github.com/facebook/react) | Stars: 200k\n\n"
            "---\n\n"
            "# React\n\n"
            "React is a JavaScript library.\n"
        )
        description, source = SkillsService._extract_metadata(content)
        assert source == "https://github.com/facebook/react"

    def test_extract_description_after_divider(self):
        """First prose paragraph after '---' divider is used as description."""
        content = (
            "# react\n\n"
            "> Source: https://github.com/facebook/react\n\n"
            "---\n\n"
            "# React\n\n"
            "This is the description paragraph with more than ten characters.\n"
        )
        description, source = SkillsService._extract_metadata(content)
        assert "description paragraph" in description

    def test_description_skips_headings(self):
        """Heading lines are not used as the description."""
        content = (
            "# react\n\n"
            "> Source: https://example.com\n\n"
            "---\n\n"
            "# Section Heading\n\n"
            "Actual description text follows here.\n"
        )
        description, source = SkillsService._extract_metadata(content)
        assert not description.startswith("#")
        assert "Actual description" in description

    def test_missing_source_returns_none(self):
        """Returns None for source when no '> Source:' line exists."""
        content = "# skill\n\n---\n\nSome description text here.\n"
        description, source = SkillsService._extract_metadata(content)
        assert source is None

    def test_no_divider_falls_back_to_full_content(self):
        """When no '---' divider exists, description is taken from the whole content."""
        content = "# skill\n\nSome description text without a divider.\n"
        description, source = SkillsService._extract_metadata(content)
        assert "description text" in description
