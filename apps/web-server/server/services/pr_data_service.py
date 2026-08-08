"""
PR data service.

Wraps gh CLI commands for PR listing, merging, assigning, commenting,
posting reviews, and checking new commits. Provides a clean service layer
between the FastAPI routes and the GitHub CLI, following the same singleton
pattern as pr_review_service.py and changelog_service.py.

All operations accept a project_path parameter so that `gh` runs with
the correct working directory (and therefore the correct GitHub remote).
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ============================================================================
# Key conversion helpers
# ============================================================================


def _snake_to_camel(key: str) -> str:
    """Convert a snake_case string to camelCase."""
    parts = key.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _convert_keys(obj: Any) -> Any:
    """Recursively convert dict keys from snake_case to camelCase."""
    if isinstance(obj, dict):
        return {_snake_to_camel(k): _convert_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_keys(item) for item in obj]
    return obj


# ============================================================================
# gh CLI helper (shared with routes/github.py)
# ============================================================================


def _run_gh(args: list[str], cwd: str | None = None, timeout: int = 30) -> dict:
    """Run a gh CLI command and return the result.

    Kept as a module-level function so it can be used by both the service
    class and any other callers that need raw gh access.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
        )
        if result.returncode != 0:
            return {"success": False, "error": result.stderr.strip()}
        return {"success": True, "output": result.stdout.strip()}
    except FileNotFoundError:
        return {"success": False, "error": "GitHub CLI (gh) not installed"}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Command timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================================
# PR field mapping
# ============================================================================

# Fields fetched from gh CLI for PR listing
_PR_JSON_FIELDS = (
    "number,title,body,state,author,headRefName,baseRefName,"
    "additions,deletions,changedFiles,files,assignees,createdAt,updatedAt,url"
)


def _map_gh_pr(pr: dict) -> dict:
    """Map gh CLI PR JSON to the frontend PRData shape."""
    author = pr.get("author", {}) or {}
    assignees = pr.get("assignees", []) or []
    files = pr.get("files", []) or []

    return {
        "number": pr.get("number", 0),
        "title": pr.get("title", ""),
        "body": pr.get("body", ""),
        "state": (pr.get("state", "OPEN") or "OPEN").lower(),
        "author": {
            "login": author.get("login", "") if isinstance(author, dict) else str(author),
        },
        "headRefName": pr.get("headRefName", ""),
        "baseRefName": pr.get("baseRefName", ""),
        "additions": pr.get("additions", 0),
        "deletions": pr.get("deletions", 0),
        "changedFiles": pr.get("changedFiles", 0),
        "assignees": [
            {
                "login": a.get("login", "") if isinstance(a, dict) else str(a),
            }
            for a in assignees
        ],
        "files": [
            {
                "path": f.get("path", ""),
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
                "status": f.get("status", ""),
            }
            for f in files
        ],
        "createdAt": pr.get("createdAt", ""),
        "updatedAt": pr.get("updatedAt", ""),
        "htmlUrl": pr.get("url", ""),
    }


# ============================================================================
# Review file helpers
# ============================================================================


def _review_file_path(project_path: Path, pr_number: int) -> Path:
    """Canonical path for a stored PR review result."""
    return project_path / ".pfactory" / "github" / "pr" / f"review_{pr_number}.json"


def _review_index_path(project_path: Path) -> Path:
    """Canonical path for the PR review index."""
    return project_path / ".pfactory" / "github" / "pr" / "index.json"


def _review_logs_path(project_path: Path, pr_number: int) -> Path:
    """Canonical path for PR review execution logs."""
    return project_path / ".pfactory" / "github" / "pr" / f"review_{pr_number}_logs.json"


# ============================================================================
# Service class
# ============================================================================


class PRDataService:
    """Service class wrapping gh CLI commands for PR operations.

    Provides a clean API surface for:
    - Listing PRs
    - Posting review findings to GitHub
    - Posting general PR comments
    - Merging PRs
    - Assigning users to PRs
    - Checking for new commits since last review
    - Reading / writing stored review results

    All methods are synchronous (they call subprocess.run) except where
    noted.  The FastAPI route handlers can call them directly since the
    gh CLI calls are fast (< 30 s timeout).
    """

    # ------------------------------------------------------------------
    # PR listing
    # ------------------------------------------------------------------

    def list_prs(
        self,
        project_path: Path,
        state: str | None = None,
    ) -> dict[str, Any]:
        """List pull requests for the project's GitHub repo.

        Args:
            project_path: Filesystem path to the project root.
            state: Filter by state (open, closed, merged, all).

        Returns:
            ``{"success": True, "data": [PRData, ...]}`` on success, or
            ``{"success": False, "error": "..."}`` on failure.
        """
        args = [
            "pr",
            "list",
            "--json",
            _PR_JSON_FIELDS,
            "--limit",
            "100",
        ]
        if state and state in ("open", "closed", "merged", "all"):
            args.extend(["--state", state])

        result = _run_gh(args, cwd=str(project_path))
        if not result["success"]:
            return {"success": False, "error": result.get("error", "Failed to fetch pull requests")}

        try:
            prs_raw = json.loads(result["output"])
        except json.JSONDecodeError:
            return {"success": True, "data": []}

        prs = [_map_gh_pr(pr) for pr in prs_raw]
        return {"success": True, "data": prs}

    # ------------------------------------------------------------------
    # Post review findings to GitHub
    # ------------------------------------------------------------------

    def post_review_to_github(
        self,
        project_path: Path,
        pr_number: int,
        selected_finding_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Post stored review findings as a formatted GitHub PR comment.

        Reads the review result JSON from disk, optionally filters findings
        by *selected_finding_ids*, builds a formatted markdown body, and
        posts it via ``gh pr comment``.

        Also updates the review metadata on disk (hasPostedFindings,
        postedFindingIds, postedAt).

        Args:
            project_path: Filesystem path to the project root.
            pr_number: The PR number.
            selected_finding_ids: Optional list of finding IDs to post.
                If ``None``, all findings are posted.

        Returns:
            ``{"success": True, "data": {"posted": True, ...}}`` on
            success, or ``{"success": False, "error": "..."}`` on failure.
        """
        review_file = _review_file_path(project_path, pr_number)
        if not review_file.exists():
            return {"success": False, "error": f"No review found for PR #{pr_number}"}

        try:
            review_data = json.loads(review_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            return {"success": False, "error": f"Failed to read review data: {e}"}

        findings = review_data.get("findings", [])

        # Filter findings if specific IDs were provided
        if selected_finding_ids is not None:
            findings = [f for f in findings if f.get("id") in selected_finding_ids]

        if not findings:
            return {"success": False, "error": "No findings to post"}

        # Build formatted markdown body
        review_body = self._build_review_comment_body(
            pr_number,
            review_data,
            findings,
        )

        # Post via gh CLI
        result = _run_gh(
            ["pr", "comment", str(pr_number), "--body", review_body],
            cwd=str(project_path),
        )
        if not result["success"]:
            return {"success": False, "error": result.get("error", "Failed to post review")}

        # Update review metadata on disk (snake_case — matches backend format)
        posted_ids = [f.get("id") for f in findings if f.get("id")]
        review_data["has_posted_findings"] = True
        review_data.setdefault("posted_finding_ids", [])
        for fid in posted_ids:
            if fid not in review_data["posted_finding_ids"]:
                review_data["posted_finding_ids"].append(fid)
        review_data["posted_at"] = datetime.now().isoformat()

        try:
            review_file.write_text(json.dumps(review_data, indent=2))
        except OSError:
            pass  # Non-fatal: review was posted but metadata update failed

        return {"success": True, "data": {"posted": True, "findingsPosted": len(findings)}}

    # ------------------------------------------------------------------
    # Post general comment
    # ------------------------------------------------------------------

    def post_comment(
        self,
        project_path: Path,
        pr_number: int,
        body: str,
    ) -> dict[str, Any]:
        """Post a general comment on a PR via ``gh pr comment``.

        Args:
            project_path: Filesystem path to the project root.
            pr_number: The PR number.
            body: Comment body text.

        Returns:
            ``{"success": True, "data": {"posted": True}}`` on success,
            or ``{"success": False, "error": "..."}`` on failure.
        """
        if not body or not body.strip():
            return {"success": False, "error": "Comment body cannot be empty"}

        result = _run_gh(
            ["pr", "comment", str(pr_number), "--body", body],
            cwd=str(project_path),
        )
        if not result["success"]:
            return {"success": False, "error": result.get("error", "Failed to post comment")}

        return {"success": True, "data": {"posted": True}}

    # ------------------------------------------------------------------
    # Approve PR
    # ------------------------------------------------------------------

    def approve_pr(
        self,
        project_path: Path,
        pr_number: int,
        body: str = "",
    ) -> dict[str, Any]:
        """Approve a PR via ``gh pr review --approve``.

        This marks the PR as "Approved" in GitHub's review system (green
        checkmark), unlike ``post_comment()`` which only adds a plain text
        comment.

        Args:
            project_path: Filesystem path to the project root.
            pr_number: The PR number.
            body: Optional review body text.

        Returns:
            ``{"success": True, "data": {"approved": True}}`` on success,
            or ``{"success": False, "error": "..."}`` on failure.
        """
        args = ["pr", "review", str(pr_number), "--approve"]
        if body and body.strip():
            args.extend(["--body", body])

        result = _run_gh(args, cwd=str(project_path))
        if not result["success"]:
            return {"success": False, "error": result.get("error", "Failed to approve PR")}

        return {"success": True, "data": {"approved": True}}

    # ------------------------------------------------------------------
    # Merge PR
    # ------------------------------------------------------------------

    def merge_pr(
        self,
        project_path: Path,
        pr_number: int,
        method: str = "squash",
    ) -> dict[str, Any]:
        """Merge a PR with configurable merge method.

        Args:
            project_path: Filesystem path to the project root.
            pr_number: The PR number.
            method: One of ``merge``, ``squash``, ``rebase``.

        Returns:
            ``{"success": True, "data": {"merged": True, ...}}`` on
            success, or ``{"success": False, "error": "..."}`` on failure.
        """
        if method not in ("merge", "squash", "rebase"):
            return {
                "success": False,
                "error": f"Invalid merge method: {method}. Must be one of: merge, squash, rebase",
            }

        result = _run_gh(
            ["pr", "merge", str(pr_number), f"--{method}"],
            cwd=str(project_path),
        )
        if not result["success"]:
            return {"success": False, "error": result.get("error", "Failed to merge PR")}

        return {"success": True, "data": {"merged": True, "method": method}}

    # ------------------------------------------------------------------
    # Assign user to PR
    # ------------------------------------------------------------------

    def assign_pr(
        self,
        project_path: Path,
        pr_number: int,
        username: str,
    ) -> dict[str, Any]:
        """Assign a user to a PR via ``gh pr edit --add-assignee``.

        Args:
            project_path: Filesystem path to the project root.
            pr_number: The PR number.
            username: GitHub username to assign.

        Returns:
            ``{"success": True, "data": {"assigned": True, ...}}`` on
            success, or ``{"success": False, "error": "..."}`` on failure.
        """
        if not username or not username.strip():
            return {"success": False, "error": "Username cannot be empty"}

        result = _run_gh(
            ["pr", "edit", str(pr_number), "--add-assignee", username],
            cwd=str(project_path),
        )
        if not result["success"]:
            return {"success": False, "error": result.get("error", "Failed to assign user")}

        return {"success": True, "data": {"assigned": True, "username": username}}

    # ------------------------------------------------------------------
    # Check new commits since last review
    # ------------------------------------------------------------------

    def check_new_commits(
        self,
        project_path: Path,
        pr_number: int,
    ) -> dict[str, Any]:
        """Check if there are new commits since the last review.

        Compares the ``reviewed_commit_sha`` stored in the review result
        JSON against the current HEAD SHA of the PR (fetched via
        ``gh pr view``).

        Args:
            project_path: Filesystem path to the project root.
            pr_number: The PR number.

        Returns:
            ``{"success": True, "data": NewCommitsCheck}`` where
            NewCommitsCheck has keys: hasNewCommits, newCommitCount,
            lastReviewedCommit, currentHeadCommit.
        """
        # Read stored review to get the reviewed commit SHA
        last_reviewed_commit = None
        posted_at = None
        review_file = _review_file_path(project_path, pr_number)
        if review_file.exists():
            try:
                review_data = json.loads(review_file.read_text())
                last_reviewed_commit = review_data.get("reviewed_commit_sha")
                posted_at = review_data.get("posted_at")
            except (json.JSONDecodeError, OSError):
                pass

        # Get the current HEAD commit SHA of the PR
        # Use headRefOid instead of commits[-1].oid because the commits list
        # is paginated at 100 by the GitHub GraphQL API, which returns a stale
        # SHA for PRs with more than 100 commits.
        result = _run_gh(
            [
                "pr",
                "view",
                str(pr_number),
                "--json",
                "headRefOid",
                "--jq",
                ".headRefOid",
            ],
            cwd=str(project_path),
        )
        current_head_commit = result.get("output", "").strip() if result["success"] else None

        # If no prior review, there are no "new" commits relative to a review
        if not last_reviewed_commit:
            return {
                "success": True,
                "data": {
                    "hasNewCommits": False,
                    "newCommitCount": 0,
                    "lastReviewedCommit": None,
                    "currentHeadCommit": current_head_commit,
                    "hasCommitsAfterPosting": False,
                },
            }

        # Compare SHAs
        has_new_commits = (
            current_head_commit is not None and current_head_commit != last_reviewed_commit
        )

        new_commit_count = 0
        if has_new_commits and current_head_commit:
            count_result = _run_gh(
                [
                    "api",
                    f"repos/{{owner}}/{{repo}}/compare/{last_reviewed_commit}...{current_head_commit}",
                    "--jq",
                    ".total_commits",
                ],
                cwd=str(project_path),
            )
            if count_result["success"]:
                try:
                    new_commit_count = int(count_result["output"].strip())
                except (ValueError, TypeError):
                    new_commit_count = 1  # Fallback

        return {
            "success": True,
            "data": {
                "hasNewCommits": has_new_commits,
                "newCommitCount": new_commit_count,
                "lastReviewedCommit": last_reviewed_commit,
                "currentHeadCommit": current_head_commit,
                "hasCommitsAfterPosting": has_new_commits,
            },
        }

    # ------------------------------------------------------------------
    # Review result file operations
    # ------------------------------------------------------------------

    def get_review(
        self,
        project_path: Path,
        pr_number: int,
    ) -> dict[str, Any]:
        """Read stored PR review result from disk.

        Returns:
            ``{"success": True, "data": <review_data or None>}`` on success,
            or ``{"success": False, "error": "..."}`` on failure.
        """
        review_file = _review_file_path(project_path, pr_number)
        if not review_file.exists():
            return {"success": True, "data": None}

        try:
            result_data = json.loads(review_file.read_text())
            return {"success": True, "data": _convert_keys(result_data)}
        except json.JSONDecodeError:
            return {"success": False, "error": "Failed to parse stored review data"}
        except OSError as e:
            return {"success": False, "error": f"Failed to read review file: {e}"}

    def delete_review(
        self,
        project_path: Path,
        pr_number: int,
    ) -> dict[str, Any]:
        """Delete a stored PR review result.

        Removes the review JSON file and updates the index.

        Returns:
            ``{"success": True, "data": {"deleted": True/False, ...}}``
        """
        review_file = _review_file_path(project_path, pr_number)
        if not review_file.exists():
            return {"success": True, "data": {"deleted": False, "reason": "No review found"}}

        try:
            review_file.unlink()
        except OSError as e:
            return {"success": False, "error": f"Failed to delete review file: {e}"}

        # Update the index file to remove the entry
        index_file = _review_index_path(project_path)
        if index_file.exists():
            try:
                index_data = json.loads(index_file.read_text())
                reviews = index_data.get("reviews", [])
                index_data["reviews"] = [r for r in reviews if r.get("pr_number") != pr_number]
                index_file.write_text(json.dumps(index_data, indent=2))
            except (json.JSONDecodeError, OSError):
                pass  # Non-fatal: review was deleted, index update failed

        return {"success": True, "data": {"deleted": True}}

    def get_review_logs(
        self,
        project_path: Path,
        pr_number: int,
    ) -> dict[str, Any]:
        """Read PR review execution logs from disk.

        Returns:
            ``{"success": True, "data": <logs_data or None>}`` on success,
            or ``{"success": False, "error": "..."}`` on failure.
        """
        logs_file = _review_logs_path(project_path, pr_number)
        if not logs_file.exists():
            return {"success": True, "data": None}

        try:
            logs_data = json.loads(logs_file.read_text())
            return {"success": True, "data": logs_data}
        except json.JSONDecodeError:
            return {"success": False, "error": "Failed to parse review logs"}
        except OSError as e:
            return {"success": False, "error": f"Failed to read logs file: {e}"}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_review_comment_body(
        pr_number: int,
        review_data: dict,
        findings: list[dict],
    ) -> str:
        """Build formatted markdown body from review findings."""
        parts: list[str] = []
        parts.append(f"## AI Code Review - PR #{pr_number}\n")
        parts.append(f"**Overall Status:** {review_data.get('overall_status', 'comment')}\n")

        if review_data.get("summary"):
            parts.append(f"### Summary\n{review_data['summary']}\n")

        parts.append("### Findings\n")
        for finding in findings:
            severity = finding.get("severity", "info").upper()
            title = finding.get("title", "Untitled")
            description = finding.get("description", "")
            file_path = finding.get("file", "")
            line = finding.get("line", 0)
            suggested_fix = finding.get("suggested_fix", "")

            location = f"`{file_path}"
            if line:
                location += f":{line}"
            location += "`"

            parts.append(f"- **[{severity}]** {title} ({location})")
            if description:
                parts.append(f"  {description}")
            if suggested_fix:
                parts.append(f"  **Suggested fix:** {suggested_fix}")
            parts.append("")

        return "\n".join(parts)


# ============================================================================
# Singleton
# ============================================================================

_pr_data_service: PRDataService | None = None


def get_pr_data_service() -> PRDataService:
    """Get the singleton PRDataService instance."""
    global _pr_data_service
    if _pr_data_service is None:
        _pr_data_service = PRDataService()
    return _pr_data_service
