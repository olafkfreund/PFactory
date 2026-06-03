#!/usr/bin/env python3
"""
Test Utilities and Fixtures for Endpoint Testing
=================================================

Provides shared utilities and fixtures for testing FastAPI endpoint implementations,
specifically focused on the 46 stub endpoints being implemented.

This module provides:
- FastAPI TestClient fixtures
- Mock file system utilities for JSON file operations
- Mock CLI command utilities (glab, gh, git, claude)
- Mock AI/background service utilities
- Request/response test data factories
- Helper assertions for endpoint testing
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

# =============================================================================
# FASTAPI TEST CLIENT FIXTURES
# =============================================================================


@pytest.fixture
def test_app():
    """
    Create a FastAPI test application instance.

    Returns the configured FastAPI app with all routers.
    """
    # Import here to avoid circular imports.
    # Insert apps/web-server (parent of server/) so that `server.main` loads
    # as a package — required for the `from .auth import ...` relative
    # imports inside server/main.py to resolve.
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "web-server"))

    from server.main import create_app
    return create_app()


@pytest.fixture
def client(test_app):
    """
    Create a FastAPI TestClient for endpoint testing.

    Usage:
        def test_endpoint(client):
            response = client.post("/api/settings/api-key", json={"key": "test"})
            assert response.status_code == 200
    """
    return TestClient(test_app)


@pytest.fixture
def authenticated_client(client):
    """
    Create an authenticated TestClient with auth token.

    Usage:
        def test_protected_endpoint(authenticated_client):
            response = authenticated_client.get("/api/protected")
            assert response.status_code == 200
    """
    # Add authentication headers
    client.headers.update({"Authorization": "Bearer test-token"})
    return client


# =============================================================================
# FILE SYSTEM MOCK FIXTURES
# =============================================================================


@pytest.fixture
def mock_file_system(temp_dir: Path):
    """
    Create a mock file system for testing file-based operations.

    Provides:
    - Temporary directory structure mimicking project layout
    - Pre-populated config files
    - Helper methods for file assertions

    Usage:
        def test_file_operation(mock_file_system):
            fs = mock_file_system
            fs.write_json("test.json", {"key": "value"})
            assert fs.read_json("test.json")["key"] == "value"
    """
    class MockFileSystem:
        def __init__(self, base_path: Path):
            self.base_path = base_path
            self.magestic_ai_dir = base_path / ".pfactory"
            self.magestic_ai_dir.mkdir(parents=True, exist_ok=True)

        def write_json(self, filename: str, data: dict) -> Path:
            """Write JSON data to file."""
            filepath = self.magestic_ai_dir / filename
            filepath.write_text(json.dumps(data, indent=2))
            return filepath

        def read_json(self, filename: str) -> dict:
            """Read JSON data from file."""
            filepath = self.magestic_ai_dir / filename
            if not filepath.exists():
                raise FileNotFoundError(f"{filename} not found")
            return json.loads(filepath.read_text())

        def exists(self, filename: str) -> bool:
            """Check if file exists."""
            return (self.magestic_ai_dir / filename).exists()

        def delete(self, filename: str):
            """Delete a file."""
            filepath = self.magestic_ai_dir / filename
            if filepath.exists():
                filepath.unlink()

        def list_files(self) -> list[str]:
            """List all files in .pfactory directory."""
            return [f.name for f in self.magestic_ai_dir.iterdir() if f.is_file()]

    return MockFileSystem(temp_dir)


@pytest.fixture
def mock_claude_profiles(mock_file_system):
    """
    Create mock claude-profiles.json file with sample data.

    Returns:
        tuple: (file_system, profiles_data)
    """
    profiles_data = {
        "profiles": [
            {
                "id": "profile-1",
                "name": "Default",
                "api_token": "sk-ant-test-token-1",
                "active": True,
            },
            {
                "id": "profile-2",
                "name": "Secondary",
                "api_token": "sk-ant-test-token-2",
                "active": False,
            },
        ],
        "active_profile_id": "profile-1",
    }
    mock_file_system.write_json("claude-profiles.json", profiles_data)
    return mock_file_system, profiles_data


@pytest.fixture
def mock_api_profiles(mock_file_system):
    """
    Create mock api-profiles.json file with sample data.

    Returns:
        tuple: (file_system, api_profiles_data)
    """
    api_profiles_data = {
        "profiles": [
            {
                "id": "api-profile-1",
                "name": "Production API",
                "base_url": "https://api.anthropic.com",
                "active": True,
            },
            {
                "id": "api-profile-2",
                "name": "Test API",
                "base_url": "https://test.anthropic.com",
                "active": False,
            },
        ],
        "active_profile_id": "api-profile-1",
    }
    mock_file_system.write_json("api-profiles.json", api_profiles_data)
    return mock_file_system, api_profiles_data


@pytest.fixture
def mock_roadmap_json(mock_file_system):
    """
    Create mock roadmap.json file with sample data.

    Returns:
        tuple: (file_system, roadmap_data)
    """
    roadmap_data = {
        "features": [
            {
                "id": "feature-1",
                "title": "User Authentication",
                "status": "in_progress",
                "priority": "high",
            },
            {
                "id": "feature-2",
                "title": "API Integration",
                "status": "planned",
                "priority": "medium",
            },
        ],
    }
    mock_file_system.write_json("roadmap.json", roadmap_data)
    return mock_file_system, roadmap_data


@pytest.fixture
def mock_ideation_json(mock_file_system):
    """
    Create mock ideation.json file with sample data.

    Returns:
        tuple: (file_system, ideation_data)
    """
    ideation_data = {
        "ideas": [
            {
                "id": "idea-1",
                "title": "Add dark mode",
                "status": "active",
                "dismissed": False,
                "archived": False,
            },
            {
                "id": "idea-2",
                "title": "Implement caching",
                "status": "active",
                "dismissed": False,
                "archived": False,
            },
        ],
    }
    mock_file_system.write_json("ideation.json", ideation_data)
    return mock_file_system, ideation_data


# =============================================================================
# CLI COMMAND MOCK FIXTURES
# =============================================================================


@pytest.fixture
def mock_subprocess():
    """
    Mock subprocess.run for CLI command testing.

    Usage:
        def test_git_command(mock_subprocess):
            mock = mock_subprocess.configure(
                commands={
                    "git status": {"returncode": 0, "stdout": "On branch main"}
                }
            )
            result = subprocess.run(["git", "status"])
            assert result.returncode == 0
    """
    class MockSubprocess:
        def __init__(self):
            self.commands = {}
            self.call_history = []

        def configure(self, commands: dict[str, dict[str, Any]]):
            """
            Configure mock responses for commands.

            Args:
                commands: Dict mapping command strings to response dicts
                         {"git status": {"returncode": 0, "stdout": "..."}}
            """
            self.commands = commands
            return self

        def run(self, cmd, **kwargs):
            """Mock subprocess.run implementation."""
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            self.call_history.append(cmd_str)

            if cmd_str in self.commands:
                response = self.commands[cmd_str]
                result = MagicMock()
                result.returncode = response.get("returncode", 0)
                result.stdout = response.get("stdout", "")
                result.stderr = response.get("stderr", "")
                return result

            # Default response
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

    return MockSubprocess()


@pytest.fixture
def mock_glab_cli(mock_subprocess):
    """
    Mock GitLab CLI (glab) commands.

    Usage:
        def test_gitlab_mr(mock_glab_cli):
            response = mock_glab_cli.mock_command(
                "mr view 123",
                output='{"title": "Test MR"}'
            )
            result = subprocess.run(["glab", "mr", "view", "123"])
            assert "Test MR" in result.stdout
    """
    class MockGlabCLI:
        def __init__(self, subprocess_mock):
            self.subprocess_mock = subprocess_mock

        def mock_command(self, subcommand: str, output: str = "", returncode: int = 0):
            """Mock a glab command."""
            self.subprocess_mock.configure({
                f"glab {subcommand}": {
                    "returncode": returncode,
                    "stdout": output,
                }
            })
            return self

        def mock_mr_view(self, mr_id: int, mr_data: dict):
            """Mock glab mr view command."""
            return self.mock_command(
                f"mr view {mr_id} --json",
                output=json.dumps(mr_data)
            )

        def mock_mr_update(self, mr_id: int, success: bool = True):
            """Mock glab mr update command."""
            return self.mock_command(
                f"mr update {mr_id}",
                output=f"MR !{mr_id} updated",
                returncode=0 if success else 1
            )

    return MockGlabCLI(mock_subprocess)


@pytest.fixture
def mock_gh_cli(mock_subprocess):
    """
    Mock GitHub CLI (gh) commands.

    Usage:
        def test_github_pr(mock_gh_cli):
            mock_gh_cli.mock_pr_view(123, {"title": "Test PR"})
            result = subprocess.run(["gh", "pr", "view", "123"])
            assert "Test PR" in result.stdout
    """
    class MockGhCLI:
        def __init__(self, subprocess_mock):
            self.subprocess_mock = subprocess_mock

        def mock_command(self, subcommand: str, output: str = "", returncode: int = 0):
            """Mock a gh command."""
            self.subprocess_mock.configure({
                f"gh {subcommand}": {
                    "returncode": returncode,
                    "stdout": output,
                }
            })
            return self

        def mock_pr_view(self, pr_number: int, pr_data: dict):
            """Mock gh pr view command."""
            return self.mock_command(
                f"pr view {pr_number} --json",
                output=json.dumps(pr_data)
            )

        def mock_issue_view(self, issue_number: int, issue_data: dict):
            """Mock gh issue view command."""
            return self.mock_command(
                f"issue view {issue_number} --json",
                output=json.dumps(issue_data)
            )

    return MockGhCLI(mock_subprocess)


# =============================================================================
# AI/BACKGROUND SERVICE MOCK FIXTURES
# =============================================================================


@pytest.fixture
def mock_ai_service():
    """
    Mock AI service for testing AI-powered endpoints.

    Usage:
        def test_ai_endpoint(mock_ai_service):
            mock_ai_service.configure_response(
                prompt="Generate ideas",
                response={"ideas": ["idea1", "idea2"]}
            )
            result = ai_service.generate(prompt="Generate ideas")
            assert len(result["ideas"]) == 2
    """
    class MockAIService:
        def __init__(self):
            self.responses = {}
            self.call_history = []

        def configure_response(self, prompt: str, response: Any):
            """Configure mock response for a prompt."""
            self.responses[prompt] = response

        async def generate(self, prompt: str, **kwargs):
            """Mock AI generation."""
            self.call_history.append({"prompt": prompt, "kwargs": kwargs})
            return self.responses.get(prompt, {"generated": "default response"})

    return MockAIService()


@pytest.fixture
def mock_background_task():
    """
    Mock background task service for testing async operations.

    Usage:
        def test_background_task(mock_background_task):
            task_id = mock_background_task.start("generate_ideas")
            status = mock_background_task.get_status(task_id)
            assert status == "running"
    """
    class MockBackgroundTask:
        def __init__(self):
            self.tasks = {}
            self.task_counter = 0

        def start(self, task_name: str, **kwargs) -> str:
            """Start a mock background task."""
            self.task_counter += 1
            task_id = f"task-{self.task_counter}"
            self.tasks[task_id] = {
                "id": task_id,
                "name": task_name,
                "status": "running",
                "kwargs": kwargs,
            }
            return task_id

        def get_status(self, task_id: str) -> str:
            """Get task status."""
            return self.tasks.get(task_id, {}).get("status", "not_found")

        def complete(self, task_id: str, result: Any = None):
            """Mark task as completed."""
            if task_id in self.tasks:
                self.tasks[task_id]["status"] = "completed"
                self.tasks[task_id]["result"] = result

        def cancel(self, task_id: str):
            """Cancel a task."""
            if task_id in self.tasks:
                self.tasks[task_id]["status"] = "cancelled"

    return MockBackgroundTask()


# =============================================================================
# REQUEST/RESPONSE TEST DATA FACTORIES
# =============================================================================


class EndpointTestDataFactory:
    """
    Factory for creating test request/response data for endpoints.
    """

    @staticmethod
    def settings_api_key_request(api_key: str = "sk-ant-test-key") -> dict:
        """Create API key update request."""
        return {"api_key": api_key}

    @staticmethod
    def settings_profile_request(profile_id: str = "profile-1") -> dict:
        """Create profile selection request."""
        return {"profile_id": profile_id}

    @staticmethod
    def settings_profile_token_request(
        profile_id: str = "profile-1",
        token: str = "sk-ant-test-token"
    ) -> dict:
        """Create profile token update request."""
        return {"profile_id": profile_id, "token": token}

    @staticmethod
    def roadmap_feature_status_request(
        feature_id: str = "feature-1",
        status: str = "in_progress"
    ) -> dict:
        """Create feature status update request."""
        return {"feature_id": feature_id, "status": status}

    @staticmethod
    def roadmap_idea_status_request(
        idea_id: str = "idea-1",
        status: str = "dismissed"
    ) -> dict:
        """Create idea status update request."""
        return {"idea_id": idea_id, "status": status}

    @staticmethod
    def gitlab_mr_update_request(
        mr_id: int = 123,
        title: str | None = None,
        description: str | None = None
    ) -> dict:
        """Create GitLab MR update request."""
        return {
            "mr_id": mr_id,
            "title": title,
            "description": description,
        }

    @staticmethod
    def github_issue_request(issue_number: int = 456) -> dict:
        """Create GitHub issue investigation request."""
        return {"issue_number": issue_number}

    @staticmethod
    def success_response(data: dict | None = None) -> dict:
        """Create standard success response."""
        response = {"success": True}
        if data:
            response.update(data)
        return response

    @staticmethod
    def error_response(message: str, code: str = "error") -> dict:
        """Create standard error response."""
        return {
            "success": False,
            "error": {
                "code": code,
                "message": message,
            }
        }


@pytest.fixture
def test_data_factory():
    """
    Provide test data factory for endpoint testing.

    Usage:
        def test_endpoint(client, test_data_factory):
            request_data = test_data_factory.settings_api_key_request()
            response = client.post("/api/settings/api-key", json=request_data)
            assert response.json() == test_data_factory.success_response()
    """
    return EndpointTestDataFactory()


# =============================================================================
# ASSERTION HELPERS
# =============================================================================


class EndpointAssertions:
    """
    Helper assertions for endpoint testing.
    """

    @staticmethod
    def assert_success_response(response, expected_data: dict | None = None):
        """Assert response is successful."""
        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True
        if expected_data:
            for key, value in expected_data.items():
                assert data.get(key) == value

    @staticmethod
    def assert_error_response(
        response,
        expected_status: int = 400,
        expected_message: str | None = None
    ):
        """Assert response is an error."""
        assert response.status_code == expected_status
        data = response.json()
        if "success" in data:
            assert data["success"] is False
        if expected_message:
            error_msg = data.get("detail") or data.get("error", {}).get("message", "")
            assert expected_message in error_msg

    @staticmethod
    def assert_file_updated(
        file_system,
        filename: str,
        expected_content: dict | None = None
    ):
        """Assert file was updated correctly."""
        assert file_system.exists(filename)
        if expected_content:
            actual_content = file_system.read_json(filename)
            for key, value in expected_content.items():
                assert actual_content.get(key) == value

    @staticmethod
    def assert_cli_called(mock_subprocess, command: str):
        """Assert CLI command was called."""
        assert any(command in call for call in mock_subprocess.call_history), \
            f"Expected command '{command}' not found in: {mock_subprocess.call_history}"


@pytest.fixture
def assert_endpoint():
    """
    Provide assertion helpers for endpoint testing.

    Usage:
        def test_endpoint(client, assert_endpoint):
            response = client.post("/api/test", json={"key": "value"})
            assert_endpoint.assert_success_response(response)
    """
    return EndpointAssertions()


# =============================================================================
# INTEGRATION TEST HELPERS
# =============================================================================


@pytest.fixture
def endpoint_integration_helper(
    client,
    mock_file_system,
    mock_subprocess,
    mock_ai_service,
    test_data_factory,
    assert_endpoint
):
    """
    Comprehensive helper combining all endpoint test utilities.

    Provides a single fixture with all testing utilities configured
    for integration testing of endpoints.

    Usage:
        def test_full_workflow(endpoint_integration_helper):
            helper = endpoint_integration_helper

            # Setup
            helper.file_system.write_json("config.json", {...})

            # Execute
            response = helper.client.post("/api/test", json={...})

            # Assert
            helper.assert_endpoint.assert_success_response(response)
            helper.assert_endpoint.assert_file_updated(
                helper.file_system, "config.json"
            )
    """
    class IntegrationHelper:
        def __init__(
            self,
            client,
            file_system,
            subprocess_mock,
            ai_service,
            data_factory,
            assertions
        ):
            self.client = client
            self.file_system = file_system
            self.subprocess = subprocess_mock
            self.ai_service = ai_service
            self.data_factory = data_factory
            self.assert_endpoint = assertions

    return IntegrationHelper(
        client,
        mock_file_system,
        mock_subprocess,
        mock_ai_service,
        test_data_factory,
        assert_endpoint
    )
