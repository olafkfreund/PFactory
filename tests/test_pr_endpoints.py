"""
Tests for PR endpoints in the web-server.

Validates all PR-related routes in github.py project_router:
- GET  /api/projects/{projectId}/github/prs          - List PRs
- POST /api/projects/{projectId}/github/prs/{n}/review  - Trigger review (202)
- GET  /api/projects/{projectId}/github/prs/{n}/review  - Get stored review
- DELETE /api/projects/{projectId}/github/prs/{n}/review - Delete review
- POST /api/projects/{projectId}/github/prs/{n}/post-review - Post findings
- POST /api/projects/{projectId}/github/prs/{n}/comment  - Post comment
- POST /api/projects/{projectId}/github/prs/{n}/merge    - Merge PR
- POST /api/projects/{projectId}/github/prs/{n}/assign   - Assign user
- POST /api/projects/{projectId}/github/prs/{n}/cancel   - Cancel review
- GET  /api/projects/{projectId}/github/prs/{n}/new-commits - Check commits
- GET  /api/projects/{projectId}/github/prs/{n}/logs     - Get logs

All tests mock the gh CLI subprocess calls and use a TestClient
with auth bypassed to isolate endpoint logic.
"""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure server package is importable
# ---------------------------------------------------------------------------
_ws = Path(__file__).resolve().parent.parent / "apps" / "web-server"
if str(_ws) not in sys.path:
    sys.path.insert(0, str(_ws))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_singletons():
    """Reset singleton service instances between tests."""
    import server.services.pr_data_service as pds
    import server.services.pr_review_service as prs

    pds._pr_data_service = None
    prs._pr_review_service = None
    yield
    pds._pr_data_service = None
    prs._pr_review_service = None


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    """Create a temporary project directory with .pfactory structure."""
    proj = tmp_path / "my-project"
    proj.mkdir()
    (proj / ".git").mkdir()
    (proj / ".pfactory" / "github" / "pr").mkdir(parents=True)
    return proj


@pytest.fixture()
def _mock_load_projects(project_dir: Path):
    """Patch load_projects so that 'test-proj' resolves to project_dir."""
    projects_dict = {
        "test-proj": {"path": str(project_dir), "name": "Test Project"}
    }
    with patch(
        "server.routes.github._resolve_project_path",
        side_effect=lambda pid: Path(projects_dict[pid]["path"]) if pid in projects_dict else None,
    ):
        yield


@pytest.fixture()
def client(_mock_load_projects):
    """FastAPI TestClient with auth disabled via settings."""
    from fastapi.testclient import TestClient
    from server.config import get_settings

    settings = get_settings()
    original_disable = settings.DISABLE_AUTH
    settings.DISABLE_AUTH = True

    from server.main import create_app

    app = create_app()

    yield TestClient(app)

    settings.DISABLE_AUTH = original_disable


# ---------------------------------------------------------------------------
# Sample gh CLI response data
# ---------------------------------------------------------------------------

_SAMPLE_PR_RAW = {
    "number": 42,
    "title": "Add feature X",
    "body": "Description of the PR",
    "state": "OPEN",
    "author": {"login": "octocat"},
    "headRefName": "feature-x",
    "baseRefName": "main",
    "additions": 10,
    "deletions": 3,
    "changedFiles": 2,
    "files": [
        {"path": "src/app.py", "additions": 8, "deletions": 2, "status": "modified"},
        {"path": "tests/test_app.py", "additions": 2, "deletions": 1, "status": "modified"},
    ],
    "assignees": [{"login": "reviewer1"}],
    "createdAt": "2026-01-15T10:00:00Z",
    "updatedAt": "2026-01-16T12:00:00Z",
    "url": "https://github.com/org/repo/pull/42",
}

_SAMPLE_REVIEW = {
    "prNumber": 42,
    "overallStatus": "comment",
    "summary": "Generally clean, minor issues.",
    "findings": [
        {
            "id": "f1",
            "severity": "warning",
            "title": "Unused import",
            "description": "os is imported but unused",
            "file": "src/app.py",
            "line": 3,
            "suggestedFix": "Remove the import",
        },
        {
            "id": "f2",
            "severity": "info",
            "title": "Add docstring",
            "description": "Missing docstring for function",
            "file": "src/app.py",
            "line": 10,
            "suggestedFix": "",
        },
    ],
    "reviewed_commit_sha": "abc123",
}


# ===================================================================
# GET /api/projects/{projectId}/github/prs
# ===================================================================


class TestListPRs:
    """Tests for the PR list endpoint."""

    def test_list_prs_success(self, client, project_dir):
        """Returns mapped PRData list on success."""
        with patch(
            "server.services.pr_data_service._run_gh",
            return_value={"success": True, "output": json.dumps([_SAMPLE_PR_RAW])},
        ):
            resp = client.get("/api/projects/test-proj/github/prs")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        prs = data["data"]
        assert len(prs) == 1
        pr = prs[0]
        assert pr["number"] == 42
        assert pr["title"] == "Add feature X"
        assert pr["state"] == "open"
        assert pr["author"]["login"] == "octocat"
        assert pr["headRefName"] == "feature-x"
        assert pr["htmlUrl"] == "https://github.com/org/repo/pull/42"

    def test_list_prs_with_state_filter(self, client, project_dir):
        """Passes state filter to gh CLI."""
        with patch(
            "server.services.pr_data_service._run_gh",
            return_value={"success": True, "output": "[]"},
        ) as mock_gh:
            resp = client.get("/api/projects/test-proj/github/prs?state=closed")

        assert resp.status_code == 200
        # Verify state was forwarded
        call_args = mock_gh.call_args[0][0]
        assert "--state" in call_args
        assert "closed" in call_args

    def test_list_prs_gh_failure(self, client, project_dir):
        """Returns error when gh CLI fails."""
        with patch(
            "server.services.pr_data_service._run_gh",
            return_value={"success": False, "error": "Not authenticated"},
        ):
            resp = client.get("/api/projects/test-proj/github/prs")

        data = resp.json()
        assert data["success"] is False
        assert "Not authenticated" in data.get("error", "")

    def test_list_prs_project_not_found(self, client):
        """Returns error for unknown project ID."""
        resp = client.get("/api/projects/nonexistent/github/prs")
        data = resp.json()
        assert data["success"] is False
        assert "not found" in data.get("error", "").lower()

    def test_list_prs_empty_list(self, client, project_dir):
        """Returns empty array when no PRs exist."""
        with patch(
            "server.services.pr_data_service._run_gh",
            return_value={"success": True, "output": "[]"},
        ):
            resp = client.get("/api/projects/test-proj/github/prs")

        data = resp.json()
        assert data["success"] is True
        assert data["data"] == []

    def test_list_prs_invalid_json(self, client, project_dir):
        """Returns empty list for unparseable gh output."""
        with patch(
            "server.services.pr_data_service._run_gh",
            return_value={"success": True, "output": "not-json"},
        ):
            resp = client.get("/api/projects/test-proj/github/prs")

        data = resp.json()
        assert data["success"] is True
        assert data["data"] == []


# ===================================================================
# POST /api/projects/{projectId}/github/prs/{prNumber}/review
# ===================================================================


class TestTriggerPRReview:
    """Tests for the PR review trigger endpoint (202 Accepted)."""

    @pytest.mark.anyio
    def test_trigger_review_returns_202(self, client, project_dir):
        """Returns 202 Accepted when review starts."""
        with patch(
            "server.services.pr_review_service.PRReviewService.is_running",
            return_value=False,
        ), patch(
            "server.services.pr_review_service.PRReviewService.start_review",
            new_callable=AsyncMock,
            return_value=True,
        ):
            resp = client.post(
                "/api/projects/test-proj/github/prs/42/review",
                json={},
            )

        assert resp.status_code == 202
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["prNumber"] == 42
        assert data["data"]["followup"] is False

    @pytest.mark.anyio
    def test_trigger_followup_review(self, client, project_dir):
        """Passes followup=True to service."""
        with patch(
            "server.services.pr_review_service.PRReviewService.is_running",
            return_value=False,
        ), patch(
            "server.services.pr_review_service.PRReviewService.start_review",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_start:
            resp = client.post(
                "/api/projects/test-proj/github/prs/42/review",
                json={"followup": True},
            )

        assert resp.status_code == 202
        assert resp.json()["data"]["followup"] is True
        _, kwargs = mock_start.call_args
        assert kwargs.get("followup") is True

    @pytest.mark.anyio
    def test_trigger_review_duplicate_returns_409(self, client, project_dir):
        """Returns 409 Conflict if review already running."""
        with patch(
            "server.services.pr_review_service.PRReviewService.is_running",
            return_value=True,
        ):
            resp = client.post(
                "/api/projects/test-proj/github/prs/42/review",
                json={},
            )

        assert resp.status_code == 409
        data = resp.json()
        assert data["success"] is False
        assert "already running" in data["error"].lower()

    def test_trigger_review_project_not_found(self, client):
        """Returns 404 for unknown project."""
        resp = client.post(
            "/api/projects/nonexistent/github/prs/42/review",
            json={},
        )
        assert resp.status_code == 404

    @pytest.mark.anyio
    def test_trigger_review_start_fails_returns_500(self, client, project_dir):
        """Returns 500 if the service fails to start."""
        with patch(
            "server.services.pr_review_service.PRReviewService.is_running",
            return_value=False,
        ), patch(
            "server.services.pr_review_service.PRReviewService.start_review",
            new_callable=AsyncMock,
            return_value=False,
        ):
            resp = client.post(
                "/api/projects/test-proj/github/prs/42/review",
                json={},
            )

        assert resp.status_code == 500


# ===================================================================
# GET /api/projects/{projectId}/github/prs/{prNumber}/review
# ===================================================================


class TestGetPRReview:
    """Tests for reading stored PR review results."""

    def test_get_review_returns_data(self, client, project_dir):
        """Returns review data from disk."""
        review_file = project_dir / ".pfactory" / "github" / "pr" / "review_42.json"
        review_file.write_text(json.dumps(_SAMPLE_REVIEW))

        resp = client.get("/api/projects/test-proj/github/prs/42/review")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["prNumber"] == 42
        assert len(data["data"]["findings"]) == 2

    def test_get_review_no_review_returns_null(self, client, project_dir):
        """Returns null data when no review file exists."""
        resp = client.get("/api/projects/test-proj/github/prs/99/review")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"] is None

    def test_get_review_corrupt_json(self, client, project_dir):
        """Returns error for corrupt JSON file."""
        review_file = project_dir / ".pfactory" / "github" / "pr" / "review_42.json"
        review_file.write_text("{corrupt-json")

        resp = client.get("/api/projects/test-proj/github/prs/42/review")

        assert resp.status_code == 500
        data = resp.json()
        assert data["success"] is False

    def test_get_review_project_not_found(self, client):
        """Returns 404 for unknown project."""
        resp = client.get("/api/projects/nonexistent/github/prs/42/review")
        assert resp.status_code == 404


# ===================================================================
# DELETE /api/projects/{projectId}/github/prs/{prNumber}/review
# ===================================================================


class TestDeletePRReview:
    """Tests for deleting stored PR review results."""

    def test_delete_review_success(self, client, project_dir):
        """Deletes review file and returns success."""
        review_file = project_dir / ".pfactory" / "github" / "pr" / "review_42.json"
        review_file.write_text(json.dumps(_SAMPLE_REVIEW))

        resp = client.delete("/api/projects/test-proj/github/prs/42/review")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["deleted"] is True
        assert not review_file.exists()

    def test_delete_review_not_found(self, client, project_dir):
        """Returns deleted=False when no review exists."""
        resp = client.delete("/api/projects/test-proj/github/prs/99/review")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["deleted"] is False

    def test_delete_review_updates_index(self, client, project_dir):
        """Removes entry from index.json when present."""
        pr_dir = project_dir / ".pfactory" / "github" / "pr"
        review_file = pr_dir / "review_42.json"
        review_file.write_text(json.dumps(_SAMPLE_REVIEW))

        index_file = pr_dir / "index.json"
        index_file.write_text(json.dumps({
            "reviews": [
                {"pr_number": 42, "status": "completed"},
                {"pr_number": 99, "status": "completed"},
            ]
        }))

        resp = client.delete("/api/projects/test-proj/github/prs/42/review")

        assert resp.status_code == 200
        updated_index = json.loads(index_file.read_text())
        assert len(updated_index["reviews"]) == 1
        assert updated_index["reviews"][0]["pr_number"] == 99

    def test_delete_review_project_not_found(self, client):
        """Returns 404 for unknown project."""
        resp = client.delete("/api/projects/nonexistent/github/prs/42/review")
        assert resp.status_code == 404


# ===================================================================
# POST /api/projects/{projectId}/github/prs/{prNumber}/post-review
# ===================================================================


class TestPostPRReviewToGitHub:
    """Tests for posting review findings as GitHub comments."""

    def test_post_review_all_findings(self, client, project_dir):
        """Posts all findings when no selection specified."""
        review_file = project_dir / ".pfactory" / "github" / "pr" / "review_42.json"
        review_file.write_text(json.dumps(_SAMPLE_REVIEW))

        with patch(
            "server.services.pr_data_service._run_gh",
            return_value={"success": True, "output": "Comment posted"},
        ):
            resp = client.post("/api/projects/test-proj/github/prs/42/post-review")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["posted"] is True
        assert data["data"]["findingsPosted"] == 2

        # Verify review metadata was updated
        updated = json.loads(review_file.read_text())
        assert updated["has_posted_findings"] is True
        assert "f1" in updated["posted_finding_ids"]
        assert "f2" in updated["posted_finding_ids"]

    def test_post_review_selected_findings(self, client, project_dir):
        """Posts only selected findings."""
        review_file = project_dir / ".pfactory" / "github" / "pr" / "review_42.json"
        review_file.write_text(json.dumps(_SAMPLE_REVIEW))

        with patch(
            "server.services.pr_data_service._run_gh",
            return_value={"success": True, "output": "Comment posted"},
        ):
            resp = client.post(
                "/api/projects/test-proj/github/prs/42/post-review",
                json={"selectedFindingIds": ["f1"]},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["findingsPosted"] == 1

    def test_post_review_no_review_404(self, client, project_dir):
        """Returns 404 when no review file exists."""
        resp = client.post("/api/projects/test-proj/github/prs/99/post-review")

        assert resp.status_code == 404
        data = resp.json()
        assert "No review found" in data["error"]

    def test_post_review_empty_findings_400(self, client, project_dir):
        """Returns 400 when selected findings list matches nothing."""
        review_file = project_dir / ".pfactory" / "github" / "pr" / "review_42.json"
        review_file.write_text(json.dumps(_SAMPLE_REVIEW))

        resp = client.post(
            "/api/projects/test-proj/github/prs/42/post-review",
            json={"selectedFindingIds": ["nonexistent-id"]},
        )

        assert resp.status_code == 400
        data = resp.json()
        assert "No findings to post" in data["error"]

    def test_post_review_gh_failure(self, client, project_dir):
        """Returns 500 when gh pr comment fails."""
        review_file = project_dir / ".pfactory" / "github" / "pr" / "review_42.json"
        review_file.write_text(json.dumps(_SAMPLE_REVIEW))

        with patch(
            "server.services.pr_data_service._run_gh",
            return_value={"success": False, "error": "Permission denied"},
        ):
            resp = client.post("/api/projects/test-proj/github/prs/42/post-review")

        assert resp.status_code == 500

    def test_post_review_project_not_found(self, client):
        """Returns 404 for unknown project."""
        resp = client.post("/api/projects/nonexistent/github/prs/42/post-review")
        assert resp.status_code == 404


# ===================================================================
# POST /api/projects/{projectId}/github/prs/{prNumber}/comment
# ===================================================================


class TestPostPRComment:
    """Tests for posting general PR comments."""

    def test_post_comment_success(self, client, project_dir):
        """Posts comment and returns success."""
        with patch(
            "server.services.pr_data_service._run_gh",
            return_value={"success": True, "output": "Comment posted"},
        ):
            resp = client.post(
                "/api/projects/test-proj/github/prs/42/comment",
                json={"body": "Great work on this PR!"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["posted"] is True

    def test_post_comment_empty_body_400(self, client, project_dir):
        """Returns 400 for empty comment body."""
        resp = client.post(
            "/api/projects/test-proj/github/prs/42/comment",
            json={"body": "   "},
        )

        assert resp.status_code == 400
        data = resp.json()
        assert "cannot be empty" in data["error"].lower()

    def test_post_comment_gh_failure(self, client, project_dir):
        """Returns 500 when gh CLI fails."""
        with patch(
            "server.services.pr_data_service._run_gh",
            return_value={"success": False, "error": "Network error"},
        ):
            resp = client.post(
                "/api/projects/test-proj/github/prs/42/comment",
                json={"body": "A comment"},
            )

        assert resp.status_code == 500

    def test_post_comment_project_not_found(self, client):
        """Returns 404 for unknown project."""
        resp = client.post(
            "/api/projects/nonexistent/github/prs/42/comment",
            json={"body": "Hello"},
        )
        assert resp.status_code == 404


# ===================================================================
# POST /api/projects/{projectId}/github/prs/{prNumber}/merge
# ===================================================================


class TestMergePR:
    """Tests for merging PRs."""

    def test_merge_squash_success(self, client, project_dir):
        """Merges PR with squash method."""
        with patch(
            "server.services.pr_data_service._run_gh",
            return_value={"success": True, "output": "Merged"},
        ) as mock_gh:
            resp = client.post(
                "/api/projects/test-proj/github/prs/42/merge",
                json={"mergeMethod": "squash"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["merged"] is True
        assert data["data"]["method"] == "squash"

        # Verify --squash was passed to gh
        call_args = mock_gh.call_args[0][0]
        assert "--squash" in call_args

    def test_merge_rebase_method(self, client, project_dir):
        """Merges PR with rebase method."""
        with patch(
            "server.services.pr_data_service._run_gh",
            return_value={"success": True, "output": "Merged"},
        ) as mock_gh:
            resp = client.post(
                "/api/projects/test-proj/github/prs/42/merge",
                json={"mergeMethod": "rebase"},
            )

        assert resp.status_code == 200
        call_args = mock_gh.call_args[0][0]
        assert "--rebase" in call_args

    def test_merge_default_method_squash(self, client, project_dir):
        """Defaults to squash when no method specified."""
        with patch(
            "server.services.pr_data_service._run_gh",
            return_value={"success": True, "output": "Merged"},
        ):
            resp = client.post(
                "/api/projects/test-proj/github/prs/42/merge",
                json={},
            )

        assert resp.status_code == 200
        assert resp.json()["data"]["method"] == "squash"

    def test_merge_invalid_method_400(self, client, project_dir):
        """Returns 400 for invalid merge method."""
        resp = client.post(
            "/api/projects/test-proj/github/prs/42/merge",
            json={"mergeMethod": "yolo"},
        )

        assert resp.status_code == 400
        data = resp.json()
        assert "Invalid merge method" in data["error"]

    def test_merge_gh_failure(self, client, project_dir):
        """Returns 500 when gh merge fails."""
        with patch(
            "server.services.pr_data_service._run_gh",
            return_value={"success": False, "error": "Merge conflict"},
        ):
            resp = client.post(
                "/api/projects/test-proj/github/prs/42/merge",
                json={"mergeMethod": "merge"},
            )

        assert resp.status_code == 500

    def test_merge_project_not_found(self, client):
        """Returns 404 for unknown project."""
        resp = client.post(
            "/api/projects/nonexistent/github/prs/42/merge",
            json={"mergeMethod": "squash"},
        )
        assert resp.status_code == 404


# ===================================================================
# POST /api/projects/{projectId}/github/prs/{prNumber}/assign
# ===================================================================


class TestAssignPR:
    """Tests for assigning users to PRs."""

    def test_assign_success(self, client, project_dir):
        """Assigns user and returns success."""
        with patch(
            "server.services.pr_data_service._run_gh",
            return_value={"success": True, "output": "Assigned"},
        ) as mock_gh:
            resp = client.post(
                "/api/projects/test-proj/github/prs/42/assign",
                json={"username": "octocat"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["assigned"] is True
        assert data["data"]["username"] == "octocat"

        # Verify --add-assignee was passed
        call_args = mock_gh.call_args[0][0]
        assert "--add-assignee" in call_args
        assert "octocat" in call_args

    def test_assign_empty_username_400(self, client, project_dir):
        """Returns 400 for empty username."""
        resp = client.post(
            "/api/projects/test-proj/github/prs/42/assign",
            json={"username": "   "},
        )

        assert resp.status_code == 400
        data = resp.json()
        assert "cannot be empty" in data["error"].lower()

    def test_assign_gh_failure(self, client, project_dir):
        """Returns 500 when gh CLI fails."""
        with patch(
            "server.services.pr_data_service._run_gh",
            return_value={"success": False, "error": "User not found"},
        ):
            resp = client.post(
                "/api/projects/test-proj/github/prs/42/assign",
                json={"username": "ghost"},
            )

        assert resp.status_code == 500

    def test_assign_project_not_found(self, client):
        """Returns 404 for unknown project."""
        resp = client.post(
            "/api/projects/nonexistent/github/prs/42/assign",
            json={"username": "octocat"},
        )
        assert resp.status_code == 404


# ===================================================================
# POST /api/projects/{projectId}/github/prs/{prNumber}/cancel
# ===================================================================


class TestCancelPRReview:
    """Tests for cancelling ongoing PR reviews."""

    @pytest.mark.anyio
    def test_cancel_running_review(self, client, project_dir):
        """Cancels a running review and returns success."""
        with patch(
            "server.services.pr_review_service.PRReviewService.is_running",
            return_value=True,
        ), patch(
            "server.services.pr_review_service.PRReviewService.cancel_review",
            new_callable=AsyncMock,
            return_value=True,
        ):
            resp = client.post("/api/projects/test-proj/github/prs/42/cancel")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["cancelled"] is True

    def test_cancel_no_running_review(self, client, project_dir):
        """Returns cancelled=False when no review is running."""
        with patch(
            "server.services.pr_review_service.PRReviewService.is_running",
            return_value=False,
        ):
            resp = client.post("/api/projects/test-proj/github/prs/42/cancel")

        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["cancelled"] is False

    @pytest.mark.anyio
    def test_cancel_failure_returns_500(self, client, project_dir):
        """Returns 500 when cancel operation fails."""
        with patch(
            "server.services.pr_review_service.PRReviewService.is_running",
            return_value=True,
        ), patch(
            "server.services.pr_review_service.PRReviewService.cancel_review",
            new_callable=AsyncMock,
            return_value=False,
        ):
            resp = client.post("/api/projects/test-proj/github/prs/42/cancel")

        assert resp.status_code == 500

    def test_cancel_project_not_found(self, client):
        """Returns 404 for unknown project."""
        resp = client.post("/api/projects/nonexistent/github/prs/42/cancel")
        assert resp.status_code == 404


# ===================================================================
# GET /api/projects/{projectId}/github/prs/{prNumber}/new-commits
# ===================================================================


class TestCheckNewCommits:
    """Tests for checking new commits since last review."""

    def test_no_prior_review(self, client, project_dir):
        """Returns hasNewCommits=False when no prior review exists."""
        with patch(
            "server.services.pr_data_service._run_gh",
            return_value={"success": True, "output": "def456"},
        ):
            resp = client.get("/api/projects/test-proj/github/prs/42/new-commits")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["hasNewCommits"] is False
        assert data["newCommitCount"] == 0
        assert data["lastReviewedCommit"] is None
        assert data["currentHeadCommit"] == "def456"

    def test_has_new_commits(self, client, project_dir):
        """Returns hasNewCommits=True with commit count."""
        review_file = project_dir / ".pfactory" / "github" / "pr" / "review_42.json"
        review_file.write_text(json.dumps(_SAMPLE_REVIEW))

        def mock_gh(args, **kwargs):
            if "pr" in args and "view" in args:
                return {"success": True, "output": "def456"}
            if "api" in args:
                return {"success": True, "output": "3"}
            return {"success": False, "error": "unexpected"}

        with patch("server.services.pr_data_service._run_gh", side_effect=mock_gh):
            resp = client.get("/api/projects/test-proj/github/prs/42/new-commits")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["hasNewCommits"] is True
        assert data["newCommitCount"] == 3
        assert data["lastReviewedCommit"] == "abc123"
        assert data["currentHeadCommit"] == "def456"

    def test_no_new_commits(self, client, project_dir):
        """Returns hasNewCommits=False when SHAs match."""
        review_data = {**_SAMPLE_REVIEW, "reviewed_commit_sha": "abc123"}
        review_file = project_dir / ".pfactory" / "github" / "pr" / "review_42.json"
        review_file.write_text(json.dumps(review_data))

        with patch(
            "server.services.pr_data_service._run_gh",
            return_value={"success": True, "output": "abc123"},
        ):
            resp = client.get("/api/projects/test-proj/github/prs/42/new-commits")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["hasNewCommits"] is False
        assert data["newCommitCount"] == 0

    def test_new_commits_project_not_found(self, client):
        """Returns 404 for unknown project."""
        resp = client.get("/api/projects/nonexistent/github/prs/42/new-commits")
        assert resp.status_code == 404


# ===================================================================
# GET /api/projects/{projectId}/github/prs/{prNumber}/logs
# ===================================================================


class TestGetPRReviewLogs:
    """Tests for reading PR review execution logs."""

    def test_get_logs_success(self, client, project_dir):
        """Returns log data from disk."""
        logs_data = {
            "prNumber": 42,
            "createdAt": "2026-01-15T10:00:00",
            "status": "completed",
            "phases": {
                "fetching": {
                    "phase": "fetching",
                    "status": "completed",
                    "entries": [{"message": "Fetched PR data", "progress": 15}],
                },
            },
        }
        logs_file = project_dir / ".pfactory" / "github" / "pr" / "review_42_logs.json"
        logs_file.write_text(json.dumps(logs_data))

        resp = client.get("/api/projects/test-proj/github/prs/42/logs")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["prNumber"] == 42
        assert "fetching" in data["data"]["phases"]

    def test_get_logs_no_logs_returns_null(self, client, project_dir):
        """Returns null data when no logs file exists."""
        resp = client.get("/api/projects/test-proj/github/prs/99/logs")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"] is None

    def test_get_logs_corrupt_json(self, client, project_dir):
        """Returns error for corrupt logs file."""
        logs_file = project_dir / ".pfactory" / "github" / "pr" / "review_42_logs.json"
        logs_file.write_text("{bad-json")

        resp = client.get("/api/projects/test-proj/github/prs/42/logs")

        assert resp.status_code == 500
        data = resp.json()
        assert data["success"] is False

    def test_get_logs_project_not_found(self, client):
        """Returns 404 for unknown project."""
        resp = client.get("/api/projects/nonexistent/github/prs/42/logs")
        assert resp.status_code == 404


# ===================================================================
# PRDataService unit tests (service layer)
# ===================================================================


class TestPRDataServiceUnit:
    """Direct unit tests for PRDataService methods."""

    def test_map_gh_pr_handles_missing_fields(self):
        """_map_gh_pr handles partial/missing data gracefully."""
        from server.services.pr_data_service import _map_gh_pr

        minimal = {"number": 1}
        mapped = _map_gh_pr(minimal)
        assert mapped["number"] == 1
        assert mapped["title"] == ""
        assert mapped["state"] == "open"
        assert mapped["author"]["login"] == ""
        assert mapped["files"] == []
        assert mapped["assignees"] == []

    def test_map_gh_pr_lowercases_state(self):
        """_map_gh_pr lowercases the state field."""
        from server.services.pr_data_service import _map_gh_pr

        pr = {"number": 1, "state": "MERGED"}
        mapped = _map_gh_pr(pr)
        assert mapped["state"] == "merged"

    def test_build_review_comment_body(self):
        """_build_review_comment_body formats findings correctly."""
        from server.services.pr_data_service import PRDataService

        service = PRDataService()
        body = service._build_review_comment_body(42, _SAMPLE_REVIEW, _SAMPLE_REVIEW["findings"])

        assert "PR #42" in body
        assert "[WARNING]" in body
        assert "[INFO]" in body
        assert "Unused import" in body
        assert "src/app.py:3" in body

    def test_review_file_paths(self):
        """Canonical file paths are correct."""
        from server.services.pr_data_service import (
            _review_file_path,
            _review_index_path,
            _review_logs_path,
        )

        p = Path("/project")
        assert _review_file_path(p, 42) == p / ".pfactory" / "github" / "pr" / "review_42.json"
        assert _review_index_path(p) == p / ".pfactory" / "github" / "pr" / "index.json"
        assert _review_logs_path(p, 42) == p / ".pfactory" / "github" / "pr" / "review_42_logs.json"


# ===================================================================
# PRReviewService unit tests
# ===================================================================


class TestPRReviewServiceUnit:
    """Direct unit tests for PRReviewService."""

    def test_review_key(self):
        """Generates correct composite key."""
        from server.services.pr_review_service import PRReviewService

        service = PRReviewService()
        assert service._review_key("proj-1", 42) == "proj-1:42"

    def test_is_running_false_by_default(self):
        """Returns False when no reviews are active."""
        from server.services.pr_review_service import PRReviewService

        service = PRReviewService()
        assert service.is_running("proj-1", 42) is False

    def test_get_status_idle(self):
        """Returns idle status when nothing is running."""
        from server.services.pr_review_service import PRReviewService

        service = PRReviewService()
        status = service.get_status("proj-1", 42)
        assert status["isRunning"] is False
        assert status["status"] == "idle"
        assert status["progress"] == 0

    def test_parse_progress_valid(self):
        """Parses valid progress lines from runner stdout."""
        from server.services.pr_review_service import PRReviewPhase, PRReviewService

        service = PRReviewService()

        phase, progress, msg = service._parse_progress("[PR #42] [ 25%] Fetching PR data...")
        assert phase == PRReviewPhase.ANALYZING
        assert progress == 25
        assert msg == "Fetching PR data..."

    def test_parse_progress_complete(self):
        """Parses 100% progress as COMPLETE phase."""
        from server.services.pr_review_service import PRReviewPhase, PRReviewService

        service = PRReviewService()

        phase, progress, msg = service._parse_progress("[PR #42] [100%] Review complete")
        assert phase == PRReviewPhase.COMPLETE
        assert progress == 100

    def test_parse_progress_invalid_line(self):
        """Returns None for non-progress lines."""
        from server.services.pr_review_service import PRReviewService

        service = PRReviewService()

        phase, progress, msg = service._parse_progress("Some random log line")
        assert phase is None
        assert progress == 0
        assert msg == ""

    def test_parse_progress_low_percentage(self):
        """Low percentages map to FETCHING phase."""
        from server.services.pr_review_service import PRReviewPhase, PRReviewService

        service = PRReviewService()

        phase, _, _ = service._parse_progress("[PR #1] [  5%] Loading data")
        assert phase == PRReviewPhase.FETCHING

    def test_parse_progress_high_percentage(self):
        """High percentages map to GENERATING phase."""
        from server.services.pr_review_service import PRReviewPhase, PRReviewService

        service = PRReviewService()

        phase, _, _ = service._parse_progress("[PR #1] [ 80%] Building report")
        assert phase == PRReviewPhase.GENERATING


# ===================================================================
# PRReviewLogWriter unit tests
# ===================================================================


class TestPRReviewLogWriter:
    """Tests for the review execution log writer."""

    def test_log_writer_creates_file(self, tmp_path):
        """Log writer creates the JSON file."""
        from server.services.pr_review_service import PRReviewLogWriter, PRReviewPhase

        log_file = tmp_path / "review_42_logs.json"
        writer = PRReviewLogWriter(log_file, 42)
        writer.start_phase(PRReviewPhase.FETCHING)

        assert log_file.exists()
        data = json.loads(log_file.read_text())
        assert data["prNumber"] == 42
        assert "fetching" in data["phases"]
        assert data["phases"]["fetching"]["status"] == "active"

    def test_log_writer_add_entry(self, tmp_path):
        """Adds entries to phases."""
        from server.services.pr_review_service import PRReviewLogWriter, PRReviewPhase

        log_file = tmp_path / "review_42_logs.json"
        writer = PRReviewLogWriter(log_file, 42)
        writer.start_phase(PRReviewPhase.ANALYZING)
        writer.add_entry(PRReviewPhase.ANALYZING, "Analyzing code patterns", 40)

        data = json.loads(log_file.read_text())
        entries = data["phases"]["analyzing"]["entries"]
        assert len(entries) == 1
        assert entries[0]["message"] == "Analyzing code patterns"
        assert entries[0]["progress"] == 40

    def test_log_writer_complete_phase(self, tmp_path):
        """Completes a phase with status."""
        from server.services.pr_review_service import PRReviewLogWriter, PRReviewPhase

        log_file = tmp_path / "review_42_logs.json"
        writer = PRReviewLogWriter(log_file, 42)
        writer.start_phase(PRReviewPhase.FETCHING)
        writer.complete_phase(PRReviewPhase.FETCHING, "completed")

        data = json.loads(log_file.read_text())
        assert data["phases"]["fetching"]["status"] == "completed"
        assert data["phases"]["fetching"]["completedAt"] is not None

    def test_log_writer_finalize(self, tmp_path):
        """Finalizes the log with overall status."""
        from server.services.pr_review_service import PRReviewLogWriter, PRReviewPhase

        log_file = tmp_path / "review_42_logs.json"
        writer = PRReviewLogWriter(log_file, 42)
        writer.start_phase(PRReviewPhase.STARTING)
        writer.finalize("completed")

        data = json.loads(log_file.read_text())
        assert data["status"] == "completed"
        assert data["completedAt"] is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
