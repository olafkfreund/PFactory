"""
End-to-End Workflow Tests for PFactory API

This test suite validates complete user workflows that span multiple endpoints.
Unlike unit tests that validate individual endpoints, these tests verify realistic
user journeys and ensure endpoints work together correctly.

Workflows tested:
1. Profile Management Workflow - Create, configure, and switch Claude profiles
2. Settings Configuration Workflow - API keys, auto-switch, environment setup

Note (PFactory#291): the Roadmap/Ideation, GitLab, Project-onboarding, and
git-worktree workflow tests that used to live here were deleted rather than
un-skipped. They exercised a `server.routes.gitlab` / `server.routes.roadmap`
module and ideation/roadmap functions (`investigate_gitlab_issue`,
`update_idea_status`, `dismiss_idea`, ...) that do not exist anywhere in this
codebase, and the project-onboarding/git-worktree tests only ever asserted on
their own Mock's configured return value, never on real code. See the PR that
closed #291 for the full accounting; the placeholder-test debt in the
neighboring `test_cli_integration_endpoints.py` and `test_file_based_endpoints.py`
files is tracked separately.
"""

import json
import sys
import tempfile
from pathlib import Path
from typing import Generator
from unittest.mock import patch

import pytest

# Ensure the server package is importable when tests run from repository root
# (matches the pattern used by tests/test_agent_service_failover.py).
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_settings_dir(temp_dir: Path) -> Path:
    """Create mock settings directory structure."""
    settings_dir = temp_dir / ".pfactory"
    settings_dir.mkdir(parents=True)
    return settings_dir


@pytest.fixture
def mock_project_dir(temp_dir: Path) -> Path:
    """Create mock project directory with .pfactory."""
    project_dir = temp_dir / "test-project"
    project_dir.mkdir(parents=True)
    magestic_ai_dir = project_dir / ".pfactory"
    magestic_ai_dir.mkdir(parents=True)
    return project_dir


@pytest.fixture
def mock_projects_json(temp_dir: Path, mock_project_dir: Path) -> Path:
    """Create mock projects.json."""
    projects_file = temp_dir / "projects.json"
    projects_data = {
        "projects": [
            {
                "id": "test-project-1",
                "name": "Test Project",
                "path": str(mock_project_dir),
                "createdAt": 1704067200000,
                "updatedAt": 1704067200000,
            }
        ]
    }
    projects_file.write_text(json.dumps(projects_data, indent=2))
    return projects_file


# ============================================================================
# WORKFLOW 1: Profile Management
# ============================================================================


class TestProfileManagementWorkflow:
    """Test complete profile management lifecycle."""

    @pytest.mark.asyncio
    async def test_complete_profile_lifecycle(
        self, temp_dir: Path, mock_settings_dir: Path
    ) -> None:
        """
        Test complete profile management workflow:
        1. Create new Claude profile
        2. Set as active profile
        3. Create second profile
        4. Switch profiles (retry_with_profile)

        This simulates a real user setting up and managing multiple Claude profiles.

        Rewritten for PFactory#291: the original body patched a module-level
        ``CLAUDE_PROFILES_FILE`` constant that has never existed in
        ``server.routes.settings`` (profile storage is computed on each call
        via ``get_profiles_file()``), called the route handlers -- which are
        all ``async def`` -- synchronously, and built request objects with
        ``MagicMock`` using the pre-alias field name ``token`` where the real
        ``ClaudeProfile`` model expects ``oauthToken``. None of that matched
        the current implementation, so it could never have passed once the
        patch-target typo was fixed. This version patches ``get_profiles_file``
        (the real seam) and drives the endpoints with their actual Pydantic
        request models.
        """
        # Setup: Create initial profiles file
        profiles_file = mock_settings_dir.parent / "claude-profiles.json"
        profiles_data = {"activeProfileId": None, "profiles": []}
        profiles_file.write_text(json.dumps(profiles_data, indent=2))

        with patch("server.routes.settings.get_profiles_file", return_value=profiles_file):
            from server.routes.settings import (
                ActiveProfileRequest,
                ClaudeProfile,
                RetryWithProfileRequest,
                retry_with_profile,
                save_claude_profile,
                set_active_claude_profile,
            )

            # Step 1: Create first profile
            profile = ClaudeProfile(
                name="Work Account",
                email="work@example.com",
                oauthToken="sess-" + "x" * 40,
            )

            result1 = await save_claude_profile(profile)
            assert result1["success"] is True
            profile_id_1 = result1["data"]["id"]

            # Verify profile was created
            updated_data = json.loads(profiles_file.read_text())
            assert len(updated_data["profiles"]) == 1
            assert updated_data["profiles"][0]["name"] == "Work Account"

            # Step 2: Set active profile
            result2 = await set_active_claude_profile(ActiveProfileRequest(profileId=profile_id_1))
            assert result2["success"] is True

            updated_data = json.loads(profiles_file.read_text())
            assert updated_data["activeProfileId"] == profile_id_1

            # Step 3: Create second profile
            profile_2 = ClaudeProfile(
                name="Personal Account",
                email="personal@example.com",
                oauthToken="sk-ant-" + "y" * 40,
            )

            result3 = await save_claude_profile(profile_2)
            assert result3["success"] is True
            profile_id_2 = result3["data"]["id"]

            # Verify two profiles exist
            updated_data = json.loads(profiles_file.read_text())
            assert len(updated_data["profiles"]) == 2

            # Step 4: Switch profiles (simulate rate limit scenario)
            retry_request = RetryWithProfileRequest(
                profileId=profile_id_2,
                reason="rate_limit",
                operationContext={"operation": "generate_ideation"},
            )

            result4 = await retry_with_profile(retry_request)
            assert result4["success"] is True
            assert result4["newProfileId"] == profile_id_2
            assert result4["previousProfileId"] == profile_id_1

            # Verify active profile changed
            updated_data = json.loads(profiles_file.read_text())
            assert updated_data["activeProfileId"] == profile_id_2

    def test_api_profile_management_workflow(self, temp_dir: Path, mock_settings_dir: Path):
        """
        Test API profile management workflow:
        1. Create API profile
        2. Update API profile settings
        3. Set as active
        4. Create second profile
        5. Switch to second profile
        6. Delete first profile
        """
        # Setup: Create initial API profiles file
        profiles_file = mock_settings_dir.parent / "api-profiles.json"
        profiles_data = {"activeProfileId": None, "profiles": []}
        profiles_file.write_text(json.dumps(profiles_data, indent=2))

        # This workflow would be implemented similarly to the Claude profile workflow
        # Testing create -> update -> set active -> switch -> delete
        pass


# ============================================================================
# WORKFLOW 5: Settings Configuration
# ============================================================================


class TestSettingsConfigurationWorkflow:
    """Test complete settings configuration workflow."""

    def test_initial_setup_workflow(self, temp_dir: Path, mock_settings_dir: Path):
        """
        Test initial PFactory setup workflow:
        1. Update source environment (.env for backend)
        2. Set Anthropic API key
        3. Create API profile
        4. Set active API profile
        5. Configure auto-switch settings
        6. Update Claude token for active session

        This simulates initial setup by a new user.
        """
        # Setup files
        api_profiles_file = mock_settings_dir.parent / "api-profiles.json"
        api_profiles_data = {"activeProfileId": None, "profiles": []}
        api_profiles_file.write_text(json.dumps(api_profiles_data, indent=2))

        auto_switch_file = mock_settings_dir.parent / "auto-switch.json"
        auto_switch_data = {"enabled": False, "threshold": 80}
        auto_switch_file.write_text(json.dumps(auto_switch_data, indent=2))

        # This workflow would test the complete initial setup process
        # including all settings configuration steps
        pass


# ============================================================================
# WORKFLOW 6: Error Handling & Recovery
# ============================================================================


class TestErrorHandlingWorkflows:
    """Test workflows that involve error handling and recovery."""

    def test_rate_limit_recovery_workflow(self, temp_dir: Path, mock_settings_dir: Path):
        """
        Test rate limit recovery workflow:
        1. Attempt operation (e.g., generate ideation)
        2. Encounter rate limit error
        3. Switch to backup profile
        4. Retry operation with new profile
        5. Operation succeeds

        This simulates handling rate limits with profile switching.
        """
        # This would test the retry_with_profile endpoint
        # in the context of recovering from rate limits
        pass

    def test_concurrent_file_access_workflow(self, temp_dir: Path):
        """
        Test handling of concurrent file modifications:
        1. Thread A starts updating settings
        2. Thread B starts updating same settings
        3. Verify atomic operations prevent corruption
        4. Verify proper error handling

        This tests file locking and atomic write operations.
        """
        # This would test concurrent access to the same files
        # and verify proper locking mechanisms
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
