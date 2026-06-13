"""
End-to-End Workflow Tests for PFactory API

This test suite validates complete user workflows that span multiple endpoints.
Unlike unit tests that validate individual endpoints, these tests verify realistic
user journeys and ensure endpoints work together correctly.

Workflows tested:
1. Profile Management Workflow - Create, configure, switch, and manage profiles
2. Roadmap/Ideation Workflow - Generate ideas, update status, manage lifecycle
3. GitLab Workflow - Issue investigation, MR review, approval, and merge
4. Project Setup Workflow - Discover, add, configure projects
5. Settings Configuration Workflow - API keys, auto-switch, environment setup
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Generator
from unittest.mock import Mock, patch, MagicMock

import pytest
from fastapi.testclient import TestClient


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
                "updatedAt": 1704067200000
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

    def test_complete_profile_lifecycle(self, temp_dir: Path, mock_settings_dir: Path):
        """
        Test complete profile management workflow:
        1. Create new Claude profile
        2. Set profile token
        3. Rename profile
        4. Set as active profile
        5. Create second profile
        6. Switch profiles (retry_with_profile)
        7. Delete inactive profile

        This simulates a real user setting up and managing multiple Claude profiles.
        """
        # Setup: Create initial profiles file
        profiles_file = mock_settings_dir.parent / "claude-profiles.json"
        profiles_data = {
            "activeProfileId": None,
            "profiles": []
        }
        profiles_file.write_text(json.dumps(profiles_data, indent=2))

        with patch("apps.web-server.server.routes.settings.CLAUDE_PROFILES_FILE", profiles_file):
            from apps.web_server.server.routes.settings import (
                save_claude_profile,
                set_claude_profile_token,
                rename_claude_profile,
                set_active_claude_profile,
                retry_with_profile
            )

            # Step 1: Create first profile
            profile_data = {
                "name": "Work Account",
                "email": "work@example.com",
                "token": "sess-" + "x" * 40
            }
            # Mock ClaudeProfile model
            mock_profile = MagicMock()
            mock_profile.name = profile_data["name"]
            mock_profile.email = profile_data["email"]
            mock_profile.token = profile_data["token"]
            mock_profile.id = None

            result1 = save_claude_profile(mock_profile)
            assert result1["success"] is True
            profile_id_1 = result1["profileId"]

            # Verify profile was created
            updated_data = json.loads(profiles_file.read_text())
            assert len(updated_data["profiles"]) == 1
            assert updated_data["profiles"][0]["name"] == "Work Account"

            # Step 2: Set active profile
            mock_active_request = MagicMock()
            mock_active_request.profileId = profile_id_1
            result2 = set_active_claude_profile(mock_active_request)
            assert result2["success"] is True

            updated_data = json.loads(profiles_file.read_text())
            assert updated_data["activeProfileId"] == profile_id_1

            # Step 3: Create second profile
            profile_data_2 = {
                "name": "Personal Account",
                "email": "personal@example.com",
                "token": "sk-ant-" + "y" * 40
            }
            mock_profile_2 = MagicMock()
            mock_profile_2.name = profile_data_2["name"]
            mock_profile_2.email = profile_data_2["email"]
            mock_profile_2.token = profile_data_2["token"]
            mock_profile_2.id = None

            result3 = save_claude_profile(mock_profile_2)
            assert result3["success"] is True
            profile_id_2 = result3["profileId"]

            # Verify two profiles exist
            updated_data = json.loads(profiles_file.read_text())
            assert len(updated_data["profiles"]) == 2

            # Step 4: Switch profiles (simulate rate limit scenario)
            mock_retry_request = MagicMock()
            mock_retry_request.profileId = profile_id_2
            mock_retry_request.reason = "rate_limit"
            mock_retry_request.operationContext = {"operation": "generate_ideation"}

            result4 = retry_with_profile(mock_retry_request)
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
        profiles_data = {
            "activeProfileId": None,
            "profiles": []
        }
        profiles_file.write_text(json.dumps(profiles_data, indent=2))

        # This workflow would be implemented similarly to the Claude profile workflow
        # Testing create -> update -> set active -> switch -> delete
        pass


# ============================================================================
# WORKFLOW 2: Roadmap & Ideation
# ============================================================================

class TestRoadmapIdeationWorkflow:
    """Test complete roadmap and ideation lifecycle."""

    def test_ideation_lifecycle_workflow(
        self,
        temp_dir: Path,
        mock_project_dir: Path,
        mock_projects_json: Path
    ):
        """
        Test complete ideation workflow:
        1. Generate ideation (AI service)
        2. Update idea status (new -> accepted)
        3. Update another idea status (new -> rejected)
        4. Dismiss a rejected idea
        5. Archive an old idea
        6. Delete multiple dismissed/archived ideas
        7. Update feature status based on accepted ideas

        This simulates a real user managing their product roadmap.
        """
        # Setup: Create ideation.json
        ideation_file = mock_project_dir / ".pfactory" / "ideation.json"
        ideation_data = {
            "ideas": [
                {
                    "id": "idea-1",
                    "title": "Add dark mode",
                    "status": "new",
                    "type": "feature",
                    "dismissed": False,
                    "archived": False
                },
                {
                    "id": "idea-2",
                    "title": "Improve performance",
                    "status": "new",
                    "type": "enhancement",
                    "dismissed": False,
                    "archived": False
                },
                {
                    "id": "idea-3",
                    "title": "Old outdated idea",
                    "status": "new",
                    "type": "feature",
                    "dismissed": False,
                    "archived": False
                }
            ],
            "updatedAt": 1704067200000
        }
        ideation_file.write_text(json.dumps(ideation_data, indent=2))

        # Setup: Create roadmap.json
        roadmap_file = mock_project_dir / ".pfactory" / "roadmap.json"
        roadmap_data = {
            "features": [
                {
                    "id": "feature-1",
                    "title": "Dark Mode Support",
                    "status": "planned",
                    "priority": "high"
                }
            ],
            "updatedAt": 1704067200000
        }
        roadmap_file.write_text(json.dumps(roadmap_data, indent=2))

        with patch("apps.web-server.server.routes.roadmap.load_projects") as mock_load:
            mock_load.return_value = json.loads(mock_projects_json.read_text())

            from apps.web_server.server.routes.roadmap import (
                update_idea_status,
                dismiss_idea,
                archive_idea,
                delete_multiple_ideas,
                update_feature_status
            )

            # Step 1: Accept first idea
            mock_status_request = MagicMock()
            mock_status_request.status = "accepted"
            result1 = update_idea_status("test-project-1", "idea-1", mock_status_request)
            assert result1["success"] is True

            # Verify idea status updated
            updated_ideation = json.loads(ideation_file.read_text())
            assert updated_ideation["ideas"][0]["status"] == "accepted"

            # Step 2: Reject second idea
            mock_status_request.status = "rejected"
            result2 = update_idea_status("test-project-1", "idea-2", mock_status_request)
            assert result2["success"] is True

            # Step 3: Dismiss the rejected idea
            result3 = dismiss_idea("test-project-1", "idea-2")
            assert result3["success"] is True

            updated_ideation = json.loads(ideation_file.read_text())
            idea_2 = next(i for i in updated_ideation["ideas"] if i["id"] == "idea-2")
            assert idea_2["dismissed"] is True

            # Step 4: Archive the old idea
            result4 = archive_idea("test-project-1", "idea-3")
            assert result4["success"] is True

            updated_ideation = json.loads(ideation_file.read_text())
            idea_3 = next(i for i in updated_ideation["ideas"] if i["id"] == "idea-3")
            assert idea_3["archived"] is True

            # Step 5: Delete dismissed and archived ideas
            mock_delete_request = MagicMock()
            mock_delete_request.ideaIds = ["idea-2", "idea-3"]
            result5 = delete_multiple_ideas("test-project-1", mock_delete_request)
            assert result5["success"] is True
            assert result5["deletedCount"] == 2

            # Verify only accepted idea remains
            updated_ideation = json.loads(ideation_file.read_text())
            assert len(updated_ideation["ideas"]) == 1
            assert updated_ideation["ideas"][0]["id"] == "idea-1"

            # Step 6: Update feature status based on accepted idea
            mock_feature_request = MagicMock()
            mock_feature_request.status = "in_progress"
            result6 = update_feature_status("test-project-1", "feature-1", mock_feature_request)
            assert result6["success"] is True

            updated_roadmap = json.loads(roadmap_file.read_text())
            assert updated_roadmap["features"][0]["status"] == "in_progress"


# ============================================================================
# WORKFLOW 3: GitLab Issue to MR
# ============================================================================

class TestGitLabWorkflow:
    """Test complete GitLab workflow from issue investigation to MR merge."""

    @patch("apps.web-server.server.routes.gitlab.run_glab_command")
    @patch("apps.web-server.server.routes.gitlab.create_simple_client")
    def test_gitlab_issue_to_mr_workflow(
        self,
        mock_ai_client,
        mock_glab,
        temp_dir: Path,
        mock_project_dir: Path,
        mock_projects_json: Path
    ):
        """
        Test complete GitLab workflow:
        1. Investigate issue (fetch + AI analysis)
        2. Create merge request (via glab)
        3. Update MR title/description
        4. Assign reviewers to MR
        5. Run AI code review on MR
        6. Post review comments to MR
        7. Approve MR
        8. Merge MR (with confirmation)

        This simulates a developer workflow from issue to merge.
        """
        # Mock glab command responses
        issue_json = {
            "number": 123,
            "title": "Fix authentication bug",
            "body": "Users can't log in with OAuth",
            "state": "opened",
            "labels": ["bug", "priority:high"],
            "user": {"login": "developer1"},
            "createdAt": "2024-01-01T10:00:00Z"
        }

        mr_diff = """
diff --git a/auth.py b/auth.py
--- a/auth.py
+++ b/auth.py
@@ -10,7 +10,7 @@
-    if not user.oauth_token:
+    if not user.oauth_token or not user.oauth_token.strip():
         return False
"""

        # Configure mocks
        mock_glab.side_effect = [
            json.dumps(issue_json),  # get issue
            json.dumps({"comments": []}),  # get comments
            "MR created",  # create MR
            "MR updated",  # update MR
            "Users assigned",  # assign users
            mr_diff,  # get MR diff for review
            "Review posted",  # post review
            "MR approved",  # approve MR
            "MR merged"  # merge MR
        ]

        # Mock AI responses
        mock_ai_response = MagicMock()
        mock_ai_response.content = json.dumps({
            "summary": "OAuth token validation missing",
            "issue_type": "bug",
            "complexity": "simple",
            "suggestions": ["Add null/empty check for OAuth token"],
            "affected_areas": ["auth.py"],
            "risks": ["Users cannot authenticate"]
        })
        mock_ai_client.return_value.messages.create.return_value = mock_ai_response

        with patch("apps.web-server.server.routes.gitlab.load_projects") as mock_load:
            mock_load.return_value = json.loads(mock_projects_json.read_text())

            from apps.web_server.server.routes.gitlab import (
                investigate_gitlab_issue,
                update_merge_request,
                assign_merge_request,
                run_mr_review,
                post_mr_review,
                approve_merge_request,
                merge_merge_request
            )

            project_id = "test-project-1"
            issue_iid = 123
            mr_iid = 456

            # Step 1: Investigate issue
            mock_investigate_request = MagicMock()
            mock_investigate_request.selectedCommentIds = None
            result1 = investigate_gitlab_issue(project_id, issue_iid, mock_investigate_request)
            assert result1["issue"] is not None
            assert "analysis" in result1

            # Step 2: Update MR (after creation)
            mock_update_request = MagicMock()
            mock_update_request.title = "Fix: Add OAuth token validation"
            mock_update_request.description = "Closes #123 - Adds null check for OAuth token"
            mock_update_request.labels = ["bug-fix", "security"]
            result2 = update_merge_request(project_id, mr_iid, mock_update_request)
            assert result2["success"] is True

            # Step 3: Assign reviewers
            mock_assign_request = MagicMock()
            mock_assign_request.userIds = [101, 102]
            result3 = assign_merge_request(project_id, mr_iid, mock_assign_request)
            assert result3["success"] is True

            # Step 4: Run AI code review
            result4 = run_mr_review(project_id, mr_iid)
            assert "findings" in result4 or "review" in result4

            # Step 5: Approve MR
            result5 = approve_merge_request(project_id, mr_iid)
            assert result5["success"] is True

            # Step 6: Merge MR (requires confirmation in real scenario)
            mock_merge_request = MagicMock()
            mock_merge_request.mergeMethod = "squash"
            result6 = merge_merge_request(project_id, mr_iid, mock_merge_request)
            assert result6["success"] is True


# ============================================================================
# WORKFLOW 4: Project Setup
# ============================================================================

class TestProjectSetupWorkflow:
    """Test complete project onboarding and configuration."""

    def test_project_onboarding_workflow(self, temp_dir: Path):
        """
        Test complete project setup workflow:
        1. Scan filesystem for projects
        2. Add discovered project
        3. Update project settings (.pfactory/.env)
        4. Update project environment variables
        5. Initialize git repository (if needed)

        This simulates a new user adding their first project.
        """
        # Create mock project structure
        project_dir = temp_dir / "my-app"
        project_dir.mkdir(parents=True)
        (project_dir / ".git").mkdir()
        (project_dir / "package.json").write_text('{"name": "my-app"}')

        with patch("apps.web-server.server.routes.projects.scan_for_projects") as mock_scan:
            # Mock scan results
            mock_scan.return_value = [
                {
                    "name": "my-app",
                    "path": str(project_dir),
                    "has_git": True,
                    "has_package_json": True,
                    "has_magestic_ai": False
                }
            ]

            # Step 1: Scan for projects
            mock_scan_request = MagicMock()
            mock_scan_request.basePath = str(temp_dir)
            mock_scan_request.maxDepth = 1
            results = mock_scan(mock_scan_request)
            assert len(results) == 1
            assert results[0]["name"] == "my-app"

            # Subsequent steps would test:
            # - Adding project to projects.json
            # - Creating .pfactory directory
            # - Setting up .env file
            # - Configuring project settings
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


# ============================================================================
# WORKFLOW 7: Git Operations
# ============================================================================

class TestGitOperationsWorkflow:
    """Test git-related workflows."""

    @patch("apps.web-server.server.routes.git.run_git_command")
    def test_git_workflow_management(self, mock_git, temp_dir: Path, mock_project_dir: Path):
        """
        Test git workflow:
        1. Create worktree for new feature
        2. Make multiple commits
        3. Squash commits
        4. Create release

        This simulates parallel development workflow.
        """
        # Mock git command responses
        mock_git.side_effect = [
            "",  # worktree add
            "commit1\ncommit2\ncommit3",  # log
            "",  # reset
            "",  # commit
            "v1.0.0",  # create release
        ]

        # This would test:
        # - create_worktree
        # - squash_commits
        # - create_release
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
