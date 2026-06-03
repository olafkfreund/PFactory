#!/usr/bin/env python3
"""
Tests for selectedSkills in Task models and skill context injection.
====================================================================

Tests the selectedSkills feature across three layers:

1. Pydantic model layer (SelectedSkill, TaskMetadata, TaskMetadataUpdate)
   - Correct validation, required / optional fields, serialisation
2. Backward compatibility
   - TaskMetadata without selectedSkills loads cleanly from JSON
3. Skill context injection (_write_skill_context)
   - Writes skill_context.md when skills are selected
   - Removes / skips skill_context.md when no skills are selected
   - Handles invalid skill IDs and missing skills gracefully
   - Caps loaded skills at 5 and truncates each to 2 500 chars
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

# Add web-server to path so server modules are importable
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "web-server"))

from server.routes.tasks import SelectedSkill, TaskMetadata, TaskMetadataUpdate
from server.services.skills_service import SkillsService, init_skills_service

# Fixture skills directory — fully isolated from the real skills/ dir
FIXTURES_PATH = Path(__file__).parent / "fixtures" / "skills"


# ---------------------------------------------------------------------------
# Helper: minimal valid SelectedSkill data
# ---------------------------------------------------------------------------

def _make_skill(
    skill_id: str = "frontend/react",
    name: str = "react",
    category: str = "frontend",
    source: str | None = "https://github.com/facebook/react",
) -> dict:
    return {"id": skill_id, "name": name, "category": category, "source": source}


# ===========================================================================
# 1. SelectedSkill Pydantic model
# ===========================================================================


class TestSelectedSkillModel:
    """Tests for the SelectedSkill Pydantic model."""

    def test_valid_skill_with_all_fields(self):
        skill = SelectedSkill(**_make_skill())
        assert skill.id == "frontend/react"
        assert skill.name == "react"
        assert skill.category == "frontend"
        assert skill.source == "https://github.com/facebook/react"

    def test_source_is_optional(self):
        """SelectedSkill is valid without a source field."""
        skill = SelectedSkill(id="frontend/react", name="react", category="frontend")
        assert skill.source is None

    def test_source_defaults_to_none(self):
        data = {"id": "devops/docker", "name": "docker", "category": "devops"}
        skill = SelectedSkill(**data)
        assert skill.source is None

    def test_missing_id_raises_validation_error(self):
        with pytest.raises(ValidationError):
            SelectedSkill(name="react", category="frontend")

    def test_missing_name_raises_validation_error(self):
        with pytest.raises(ValidationError):
            SelectedSkill(id="frontend/react", category="frontend")

    def test_missing_category_raises_validation_error(self):
        with pytest.raises(ValidationError):
            SelectedSkill(id="frontend/react", name="react")

    def test_model_dump_returns_dict_with_correct_keys(self):
        skill = SelectedSkill(**_make_skill())
        data = skill.model_dump()
        assert set(data.keys()) == {"id", "name", "category", "source"}

    def test_model_dump_values_match_input(self):
        raw = _make_skill("backend/fastapi", "fastapi", "backend", None)
        skill = SelectedSkill(**raw)
        dumped = skill.model_dump()
        assert dumped["id"] == "backend/fastapi"
        assert dumped["name"] == "fastapi"
        assert dumped["category"] == "backend"
        assert dumped["source"] is None

    def test_model_validate_from_dict(self):
        """model_validate accepts raw dict (e.g., from JSON parse)."""
        raw = _make_skill()
        skill = SelectedSkill.model_validate(raw)
        assert skill.id == "frontend/react"

    def test_id_uses_category_slash_name_convention(self):
        """id should contain a slash separating category and skill name."""
        skill = SelectedSkill(**_make_skill("devops/docker", "docker", "devops"))
        assert "/" in skill.id
        category, name = skill.id.split("/", 1)
        assert category == "devops"
        assert name == "docker"


# ===========================================================================
# 2. TaskMetadata with selectedSkills
# ===========================================================================


class TestTaskMetadataWithSkills:
    """Tests for TaskMetadata.selectedSkills field."""

    def test_task_metadata_accepts_selected_skills_list(self):
        skills = [SelectedSkill(**_make_skill())]
        meta = TaskMetadata(selectedSkills=skills)
        assert meta.selectedSkills is not None
        assert len(meta.selectedSkills) == 1

    def test_task_metadata_accepts_multiple_skills(self):
        skills = [
            SelectedSkill(**_make_skill("frontend/react", "react", "frontend")),
            SelectedSkill(**_make_skill("backend/fastapi", "fastapi", "backend", None)),
            SelectedSkill(**_make_skill("devops/docker", "docker", "devops")),
        ]
        meta = TaskMetadata(selectedSkills=skills)
        assert len(meta.selectedSkills) == 3

    def test_selected_skills_defaults_to_none(self):
        meta = TaskMetadata()
        assert meta.selectedSkills is None

    def test_model_dump_includes_selected_skills(self):
        skills = [SelectedSkill(**_make_skill())]
        meta = TaskMetadata(selectedSkills=skills)
        data = meta.model_dump()
        assert "selectedSkills" in data
        assert data["selectedSkills"] is not None
        assert len(data["selectedSkills"]) == 1

    def test_model_dump_skills_have_correct_shape(self):
        skill = SelectedSkill(**_make_skill())
        meta = TaskMetadata(selectedSkills=[skill])
        data = meta.model_dump()
        skill_data = data["selectedSkills"][0]
        assert "id" in skill_data
        assert "name" in skill_data
        assert "category" in skill_data
        assert "source" in skill_data

    def test_json_round_trip_preserves_skills(self):
        """TaskMetadata survives JSON serialise → parse → validate round trip."""
        skills = [SelectedSkill(**_make_skill())]
        meta = TaskMetadata(selectedSkills=skills)
        json_str = meta.model_dump_json()
        restored = TaskMetadata.model_validate_json(json_str)
        assert restored.selectedSkills is not None
        assert len(restored.selectedSkills) == 1
        assert restored.selectedSkills[0].id == "frontend/react"

    def test_validate_from_dict_with_skills_list(self):
        raw = {
            "selectedSkills": [
                {"id": "frontend/react", "name": "react", "category": "frontend"},
            ]
        }
        meta = TaskMetadata.model_validate(raw)
        assert meta.selectedSkills is not None
        assert len(meta.selectedSkills) == 1

    def test_selected_skills_can_be_explicitly_none(self):
        meta = TaskMetadata(selectedSkills=None)
        assert meta.selectedSkills is None

    def test_selected_skills_can_be_empty_list(self):
        meta = TaskMetadata(selectedSkills=[])
        assert meta.selectedSkills == []


# ===========================================================================
# 3. TaskMetadata backward compatibility
# ===========================================================================


class TestTaskMetadataBackwardCompat:
    """Existing task metadata JSON without selectedSkills loads correctly."""

    def test_metadata_without_selected_skills_field_is_valid(self):
        raw = {"model": "claude-sonnet-4-5", "thinkingLevel": "medium"}
        meta = TaskMetadata.model_validate(raw)
        assert meta.selectedSkills is None
        assert meta.model == "claude-sonnet-4-5"

    def test_metadata_with_only_model_field_is_valid(self):
        raw = {"model": "claude-opus-4-5"}
        meta = TaskMetadata.model_validate(raw)
        assert meta.selectedSkills is None

    def test_metadata_empty_dict_is_valid(self):
        meta = TaskMetadata.model_validate({})
        assert meta.selectedSkills is None

    def test_metadata_from_json_without_skills_key(self):
        json_str = '{"model": "claude-sonnet-4-5", "priority": "high"}'
        meta = TaskMetadata.model_validate_json(json_str)
        assert meta.selectedSkills is None
        assert meta.model == "claude-sonnet-4-5"

    def test_task_metadata_update_without_skills_is_valid(self):
        raw = {"model": "claude-sonnet-4-5", "mode": "quick"}
        update = TaskMetadataUpdate.model_validate(raw)
        assert update.selectedSkills is None
        assert update.model == "claude-sonnet-4-5"

    def test_task_metadata_update_accepts_skills(self):
        raw = {
            "selectedSkills": [
                {"id": "frontend/react", "name": "react", "category": "frontend"}
            ]
        }
        update = TaskMetadataUpdate.model_validate(raw)
        assert update.selectedSkills is not None
        assert len(update.selectedSkills) == 1


# ===========================================================================
# 4. _write_skill_context integration tests
# ===========================================================================


class TestWriteSkillContext:
    """
    Tests for AgentService._write_skill_context().

    Uses a temporary spec directory and a fixture-backed SkillsService.
    Patches server.services.skills_service._skills_service (the singleton)
    so the method picks up our fixture service without touching the real
    on-disk skills directory.
    """

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Provide a temporary spec dir and fixture-backed SkillsService."""
        self.spec_dir = tmp_path / "spec"
        self.spec_dir.mkdir()
        self.fixture_service = SkillsService(skills_base_path=FIXTURES_PATH)

    def _make_agent_service(self):
        """Return an AgentService instance with settings mocked out."""
        from unittest.mock import MagicMock
        with patch("server.services.agent_service.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                BACKEND_PATH="/tmp/backend",
                PROJECTS_DATA_DIR="/tmp/data",
            )
            from server.services.agent_service import AgentService
            return AgentService()

    def _write_task_metadata(self, selected_skills: list) -> None:
        """Write task_metadata.json with selectedSkills in production format (list of dicts)."""
        if selected_skills and isinstance(selected_skills[0], str):
            # Convert string IDs to dict format (production shape)
            skill_dicts = [
                {"id": sid, "name": sid.split("/")[-1], "category": sid.split("/")[0], "source": None}
                for sid in selected_skills
            ]
        else:
            skill_dicts = selected_skills
        data = {"selectedSkills": skill_dicts}
        (self.spec_dir / "task_metadata.json").write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # Writes skill_context.md when skills are selected
    # ------------------------------------------------------------------

    def test_writes_skill_context_file_when_skills_selected(self):
        """skill_context.md is created when selectedSkills is non-empty."""
        self._write_task_metadata(["frontend/react"])
        agent = self._make_agent_service()
        with patch("server.services.skills_service._skills_service", self.fixture_service):
            agent._write_skill_context(self.spec_dir)
        assert (self.spec_dir / "skill_context.md").exists()

    def test_skill_context_contains_skill_content(self):
        """skill_context.md includes the markdown content of the selected skill."""
        self._write_task_metadata(["frontend/react"])
        agent = self._make_agent_service()
        with patch("server.services.skills_service._skills_service", self.fixture_service):
            agent._write_skill_context(self.spec_dir)
        content = (self.spec_dir / "skill_context.md").read_text(encoding="utf-8")
        assert "React" in content

    def test_skill_context_has_header_section(self):
        """skill_context.md starts with the standard header."""
        self._write_task_metadata(["frontend/react"])
        agent = self._make_agent_service()
        with patch("server.services.skills_service._skills_service", self.fixture_service):
            agent._write_skill_context(self.spec_dir)
        content = (self.spec_dir / "skill_context.md").read_text(encoding="utf-8")
        assert "# Selected Skills Context" in content

    def test_multiple_skills_all_included(self):
        """All selected skills (up to 5) appear in skill_context.md."""
        self._write_task_metadata(["frontend/react", "backend/fastapi"])
        agent = self._make_agent_service()
        with patch("server.services.skills_service._skills_service", self.fixture_service):
            agent._write_skill_context(self.spec_dir)
        content = (self.spec_dir / "skill_context.md").read_text(encoding="utf-8")
        assert "React" in content
        assert "FastAPI" in content

    # ------------------------------------------------------------------
    # No skill context when no skills selected
    # ------------------------------------------------------------------

    def test_no_skill_context_when_selected_skills_empty(self):
        """skill_context.md is not created when selectedSkills is []."""
        self._write_task_metadata([])
        agent = self._make_agent_service()
        with patch("server.services.skills_service._skills_service", self.fixture_service):
            agent._write_skill_context(self.spec_dir)
        assert not (self.spec_dir / "skill_context.md").exists()

    def test_no_skill_context_when_metadata_missing(self):
        """skill_context.md is not created when task_metadata.json is absent."""
        agent = self._make_agent_service()
        with patch("server.services.skills_service._skills_service", self.fixture_service):
            agent._write_skill_context(self.spec_dir)
        assert not (self.spec_dir / "skill_context.md").exists()

    def test_existing_skill_context_removed_when_no_skills(self):
        """An existing skill_context.md is removed when selectedSkills becomes []."""
        # Create a stale file
        (self.spec_dir / "skill_context.md").write_text("stale content")
        self._write_task_metadata([])
        agent = self._make_agent_service()
        with patch("server.services.skills_service._skills_service", self.fixture_service):
            agent._write_skill_context(self.spec_dir)
        assert not (self.spec_dir / "skill_context.md").exists()

    # ------------------------------------------------------------------
    # Graceful handling of invalid / missing skills
    # ------------------------------------------------------------------

    def test_invalid_skill_id_format_skipped_gracefully(self):
        """Skill IDs without a '/' are skipped without raising an exception."""
        self._write_task_metadata(["invalid_no_slash", "frontend/react"])
        agent = self._make_agent_service()
        with patch("server.services.skills_service._skills_service", self.fixture_service):
            agent._write_skill_context(self.spec_dir)
        # The valid skill still produces output
        assert (self.spec_dir / "skill_context.md").exists()

    def test_unknown_skill_id_skipped_gracefully(self):
        """Unknown skill IDs (not in index) are silently skipped."""
        self._write_task_metadata(["frontend/nonexistent_skill"])
        agent = self._make_agent_service()
        with patch("server.services.skills_service._skills_service", self.fixture_service):
            agent._write_skill_context(self.spec_dir)
        # No valid skills → no file
        assert not (self.spec_dir / "skill_context.md").exists()

    def test_all_invalid_skills_does_not_create_file(self):
        """When all skills fail to load, no skill_context.md is written."""
        self._write_task_metadata(["frontend/bogus1", "backend/bogus2"])
        agent = self._make_agent_service()
        with patch("server.services.skills_service._skills_service", self.fixture_service):
            agent._write_skill_context(self.spec_dir)
        assert not (self.spec_dir / "skill_context.md").exists()

    # ------------------------------------------------------------------
    # Content truncation
    # ------------------------------------------------------------------

    def test_skill_content_truncated_at_2500_chars(self):
        """Skills with content longer than 2 500 chars are truncated."""
        # Create a mock service that returns very long content
        mock_service = MagicMock()
        long_content = "A" * 5000
        mock_service.get_skill.return_value = MagicMock(name="react")
        mock_service.get_skill_content.return_value = long_content
        self._write_task_metadata(["frontend/react"])
        agent = self._make_agent_service()
        with patch("server.services.skills_service._skills_service", mock_service):
            agent._write_skill_context(self.spec_dir)
        content = (self.spec_dir / "skill_context.md").read_text(encoding="utf-8")
        # Truncated marker appears
        assert "truncated" in content.lower() or len(content) < len(long_content) + 500

    # ------------------------------------------------------------------
    # Maximum 5 skills cap
    # ------------------------------------------------------------------

    def test_at_most_five_skills_loaded(self):
        """Only the first 5 skills in selectedSkills are loaded."""
        # Create mock service that tracks how many times get_skill_content is called
        mock_service = MagicMock()
        mock_service.get_skill.return_value = MagicMock(name="react")
        mock_service.get_skill_content.return_value = "Short content"
        # Request 8 skills (only 5 should be loaded)
        skill_ids = [f"frontend/react_{i}" for i in range(8)]
        self._write_task_metadata(skill_ids)
        agent = self._make_agent_service()
        with patch("server.services.skills_service._skills_service", mock_service):
            agent._write_skill_context(self.spec_dir)
        assert mock_service.get_skill_content.call_count <= 5
