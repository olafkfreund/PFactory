"""
GitLab Provider Implementation
==============================

Implements the GitProvider protocol for GitLab using standard HTTP/REST APIs.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from .protocol import (
    IssueData,
    IssueFilters,
    LabelData,
    PRData,
    PRFilters,
    ProviderType,
    ReviewData,
)

logger = logging.getLogger(__name__)


@dataclass
class GitLabProvider:
    """
    GitLab implementation of the GitProvider protocol.
    Works with both gitlab.com and self-hosted GitLab CE/EE instances.
    """

    _repo: str  # Format: "owner/repo" or "group/subgroup/repo"
    _token: str | None = None
    _base_url: str = "https://gitlab.com"
    _project_dir: str | None = None

    def __post_init__(self):
        # Url-encode path with subgroup support
        self._project_id = self._repo.replace("/", "%2F")
        self._headers = {}
        if self._token:
            self._headers["PRIVATE-TOKEN"] = self._token

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.GITLAB

    @property
    def repo(self) -> str:
        return self._repo

    # -------------------------------------------------------------------------
    # Helper to construct clients
    # -------------------------------------------------------------------------
    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url, headers=self._headers, timeout=30.0
        )

    # -------------------------------------------------------------------------
    # Pull (Merge) Request Operations
    # -------------------------------------------------------------------------
    async def fetch_pr(self, number: int) -> PRData:
        """Fetch GitLab Merge Request details."""
        async with self._client() as client:
            # Fetch MR basic details
            mr_resp = await client.get(
                f"/api/v4/projects/{self._project_id}/merge_requests/{number}"
            )
            mr_resp.raise_for_status()
            mr_data = mr_resp.json()

            # Fetch MR diffs
            diff_resp = await client.get(
                f"/api/v4/projects/{self._project_id}/merge_requests/{number}/diffs"
            )
            diffs = []
            unified_diff = ""
            if diff_resp.status_code == 200:
                diffs = diff_resp.json()
                unified_diff = "\n".join([d.get("diff", "") for d in diffs])

            # Map changed files list
            files = []
            additions = 0
            deletions = 0
            for d in diffs:
                # Approximate additions/deletions from raw diff markers if needed
                file_diff = d.get("diff", "")
                file_adds = file_diff.count("\n+")
                file_dels = file_diff.count("\n-")
                additions += file_adds
                deletions += file_dels
                files.append(
                    {
                        "path": d.get("new_path") or d.get("old_path"),
                        "additions": file_adds,
                        "deletions": file_dels,
                        "type": "modified"
                        if not d.get("new_file") and not d.get("deleted_file")
                        else ("added" if d.get("new_file") else "deleted"),
                    }
                )

            return PRData(
                number=mr_data["iid"],
                title=mr_data["title"],
                body=mr_data.get("description") or "",
                author=mr_data.get("author", {}).get("username", ""),
                state="merged"
                if mr_data.get("state") == "merged"
                else ("open" if mr_data.get("state") == "opened" else "closed"),
                source_branch=mr_data.get("source_branch") or "",
                target_branch=mr_data.get("target_branch") or "",
                additions=additions,
                deletions=deletions,
                changed_files=len(files),
                files=files,
                diff=unified_diff,
                url=mr_data.get("web_url") or "",
                created_at=self._parse_datetime(mr_data.get("created_at")),
                updated_at=self._parse_datetime(mr_data.get("updated_at")),
                labels=mr_data.get("labels") or [],
                reviewers=[r.get("username", "") for r in mr_data.get("reviewers", [])]
                if mr_data.get("reviewers")
                else [],
                is_draft=mr_data.get("work_in_progress")
                or mr_data.get("draft")
                or False,
                mergeable=mr_data.get("merge_status") == "can_be_merged",
                provider=ProviderType.GITLAB,
                raw_data=mr_data,
            )

    async def fetch_prs(self, filters: PRFilters | None = None) -> list[PRData]:
        """Fetch merge requests list with optional filters."""
        filters = filters or PRFilters()
        params: dict[str, Any] = {"per_page": filters.limit}

        if filters.state == "open":
            params["state"] = "opened"
        elif filters.state in ("closed", "merged"):
            params["state"] = filters.state

        async with self._client() as client:
            resp = await client.get(
                f"/api/v4/projects/{self._project_id}/merge_requests", params=params
            )
            resp.raise_for_status()
            mr_list = resp.json()

            results = []
            for mr in mr_list:
                # Apply labels filter
                if filters.labels:
                    mr_labels = mr.get("labels") or []
                    if not all(label in mr_labels for label in filters.labels):
                        continue

                # Apply branch filter
                if (
                    filters.base_branch
                    and mr.get("target_branch") != filters.base_branch
                ):
                    continue
                if (
                    filters.head_branch
                    and mr.get("source_branch") != filters.head_branch
                ):
                    continue

                # Fetch full MR details (including diffs) for conformance
                try:
                    full_pr = await self.fetch_pr(mr["iid"])
                    results.append(full_pr)
                except Exception as e:
                    logger.error(
                        f"Error fetching GitLab MR details for iid {mr.get('iid')}: {e}"
                    )

            return results

    async def fetch_pr_diff(self, number: int) -> str:
        """Fetch the MR unified diff."""
        pr = await self.fetch_pr(number)
        return pr.diff

    async def post_review(self, pr_number: int, review: ReviewData) -> int:
        """Post a code review with inline findings to GitLab Merge Request."""
        async with self._client() as client:
            # Post main review body comment as a note
            note_payload = {"body": review.body}
            note_resp = await client.post(
                f"/api/v4/projects/{self._project_id}/merge_requests/{pr_number}/notes",
                json=note_payload,
            )
            note_resp.raise_for_status()
            note_id = note_resp.json()["id"]

            # Post each inline finding as a discussion thread
            for finding in review.findings:
                if finding.file and finding.line:
                    # In GitLab, position is needed for MR reviews
                    # To post discussion correctly, we can submit details
                    disc_payload = {
                        "body": f"**[{finding.severity.upper()}] {finding.title}**\n{finding.description}\n\n*Category: {finding.category}*",
                        "position": {
                            "position_type": "text",
                            "new_path": finding.file,
                            "new_line": finding.line,
                            "base_sha": "HEAD",  # Fallbacks
                            "start_sha": "HEAD",
                            "head_sha": "HEAD",
                        },
                    }
                    try:
                        disc_resp = await client.post(
                            f"/api/v4/projects/{self._project_id}/merge_requests/{pr_number}/discussions",
                            json=disc_payload,
                        )
                        # If complex position parameter fails due to SHA mismatches, fallback to a standard note comment
                        if disc_resp.status_code != 201:
                            fallback_payload = {
                                "body": f"**[{finding.severity.upper()}] {finding.title}** on `{finding.file}:L{finding.line}`\n{finding.description}"
                            }
                            await client.post(
                                f"/api/v4/projects/{self._project_id}/merge_requests/{pr_number}/notes",
                                json=fallback_payload,
                            )
                    except Exception as e:
                        logger.error(f"Error posting GitLab discussion: {e}")

            return note_id

    async def create_pr(
        self,
        source_branch: str,
        target_branch: str,
        title: str,
        body: str = "",
        draft: bool = False,
    ) -> dict[str, Any]:
        """Open a GitLab Merge Request and return its metadata.

        Returns a dict with at least: number (iid), web_url, source_branch,
        target_branch, draft. Matches the shape consumed by the
        create_pr_from_task route in apps/web-server/server/routes/tasks.py.
        """
        # GitLab uses "Draft: " title prefix to mark a draft MR (the dedicated
        # `draft` boolean works only on newer GitLab versions; the prefix is
        # universally supported and matches what the web UI does).
        effective_title = (
            f"Draft: {title}"
            if draft and not title.lower().startswith("draft:")
            else title
        )
        payload = {
            "source_branch": source_branch,
            "target_branch": target_branch,
            "title": effective_title,
            "description": body or "",
        }
        async with self._client() as client:
            resp = await client.post(
                f"/api/v4/projects/{self._project_id}/merge_requests",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "number": data.get("iid"),
                "web_url": data.get("web_url") or "",
                "source_branch": data.get("source_branch") or source_branch,
                "target_branch": data.get("target_branch") or target_branch,
                "draft": bool(data.get("draft"))
                or effective_title.lower().startswith("draft:"),
            }

    async def merge_pr(
        self,
        pr_number: int,
        merge_method: str = "merge",
        commit_title: str | None = None,
    ) -> bool:
        """Merge a GitLab merge request."""
        payload: dict[str, Any] = {}
        if commit_title:
            payload["merge_commit_message"] = commit_title
        if merge_method == "squash":
            payload["squash"] = True

        async with self._client() as client:
            resp = await client.put(
                f"/api/v4/projects/{self._project_id}/merge_requests/{pr_number}/merge",
                json=payload,
            )
            return resp.status_code in (200, 202)

    async def close_pr(self, pr_number: int, comment: str | None = None) -> bool:
        """Close GitLab Merge Request without merging."""
        if comment:
            await self.add_comment(pr_number, comment)

        async with self._client() as client:
            resp = await client.put(
                f"/api/v4/projects/{self._project_id}/merge_requests/{pr_number}",
                json={"state_event": "close"},
            )
            return resp.status_code == 200

    async def enable_auto_merge(
        self, pr_number: int, merge_method: str = "squash"
    ) -> bool:
        """Not yet implemented for GitLab (RFC-0011 #637).

        GitLab's "merge when pipeline succeeds" maps here, but is deferred — the
        low-tier auto-merge fast path is GitHub-first for now.
        """
        raise NotImplementedError(
            "enable_auto_merge is not implemented for the GitLab provider yet; "
            "use the merge_policy decision + manual/CI merge for GitLab MRs."
        )

    # -------------------------------------------------------------------------
    # Issue Operations
    # -------------------------------------------------------------------------
    async def fetch_issue(self, number: int) -> IssueData:
        """Fetch a GitLab issue by its IID."""
        async with self._client() as client:
            resp = await client.get(
                f"/api/v4/projects/{self._project_id}/issues/{number}"
            )
            resp.raise_for_status()
            issue = resp.json()
            return self._parse_issue_data(issue)

    async def fetch_issues(
        self, filters: IssueFilters | None = None
    ) -> list[IssueData]:
        """Fetch issues with optional filters."""
        filters = filters or IssueFilters()
        params: dict[str, Any] = {"per_page": filters.limit}

        if filters.state == "open":
            params["state"] = "opened"
        elif filters.state == "closed":
            params["state"] = "closed"

        async with self._client() as client:
            resp = await client.get(
                f"/api/v4/projects/{self._project_id}/issues", params=params
            )
            resp.raise_for_status()
            issues_list = resp.json()

            results = []
            for issue in issues_list:
                # Labels filter
                if filters.labels:
                    issue_labels = issue.get("labels") or []
                    if not all(label in issue_labels for label in filters.labels):
                        continue

                results.append(self._parse_issue_data(issue))
            return results

    async def create_issue(
        self,
        title: str,
        body: str,
        labels: list[str] | None = None,
        assignees: list[str] | None = None,
    ) -> IssueData:
        """Create a new GitLab issue."""
        payload: dict[str, Any] = {"title": title, "description": body}
        if labels:
            payload["labels"] = ",".join(labels)

        # GitLab requires assignee_ids, so we fetch usernames to resolve IDs if provided
        # For simplicity in testing, we send plain assignee_ids if we have them or fall back
        async with self._client() as client:
            if assignees:
                assignee_ids = []
                for username in assignees:
                    user_resp = await client.get(
                        "/api/v4/users", params={"username": username}
                    )
                    if user_resp.status_code == 200 and user_resp.json():
                        assignee_ids.append(user_resp.json()[0]["id"])
                if assignee_ids:
                    payload["assignee_ids"] = assignee_ids

            resp = await client.post(
                f"/api/v4/projects/{self._project_id}/issues", json=payload
            )
            resp.raise_for_status()
            return self._parse_issue_data(resp.json())

    async def close_issue(self, number: int, comment: str | None = None) -> bool:
        """Close GitLab issue."""
        if comment:
            await self.add_comment(number, comment)

        async with self._client() as client:
            resp = await client.put(
                f"/api/v4/projects/{self._project_id}/issues/{number}",
                json={"state_event": "close"},
            )
            return resp.status_code == 200

    async def add_comment(self, issue_or_pr_number: int, body: str) -> int:
        """Add a general comment to an issue or merge request."""
        # Try merge request notes first, if not found or failed, try issues notes
        async with self._client() as client:
            mr_resp = await client.post(
                f"/api/v4/projects/{self._project_id}/merge_requests/{issue_or_pr_number}/notes",
                json={"body": body},
            )
            if mr_resp.status_code == 201:
                return mr_resp.json()["id"]

            issue_resp = await client.post(
                f"/api/v4/projects/{self._project_id}/issues/{issue_or_pr_number}/notes",
                json={"body": body},
            )
            issue_resp.raise_for_status()
            return issue_resp.json()["id"]

    # GitLab Duo's "alias" for delegation. The runner passes ``["Copilot"]``
    # to every provider; we treat that as the GitLab-specific Duo trigger
    # since this is a GitLab project. ``"Duo"`` is also accepted (case-
    # insensitive) for callers that want explicit naming.
    _DUO_ALIASES = frozenset({"copilot", "duo", "gitlab-duo"})

    async def assign_to_user(self, issue_number: int, assignees: list[str]) -> None:
        """Trigger a GitLab Duo Workflow against the issue (#98, V1.5).

        Mirrors the GitHub provider's ``assign_to_user`` contract: when
        ``"Copilot"`` (or ``"Duo"``) is in ``assignees``, dispatch the
        delegation. Other assignees fall back to regular GitLab issue
        assignment (assignee_ids).

        The Duo Workflow API expects an OAuth-Bearer-authenticated POST
        to ``/api/v4/ai/duo_workflows/workflows`` with at minimum
        ``goal``, ``project_id``, and ``issue_id``. We verified the
        endpoint surface live during the V1 smoke test (see #92
        comment trail).

        Silently no-ops on auth/entitlement failures (401/403) so the
        tracker can detect the missed assignment by re-reading the issue.
        """
        if not assignees:
            return

        wants_duo = any(a.strip().lower() in self._DUO_ALIASES for a in assignees)
        regular_logins = [
            a for a in assignees if a.strip().lower() not in self._DUO_ALIASES
        ]

        if wants_duo:
            await self._trigger_duo_workflow(issue_number)

        # Regular GitLab assignment for any non-Duo usernames (mirrors the
        # behavior the GitHub provider has for non-Copilot users).
        if regular_logins:
            await self._assign_users(issue_number, regular_logins)

    async def _trigger_duo_workflow(self, issue_iid: int) -> None:
        """POST to /api/v4/ai/duo_workflows/workflows with the issue context.

        Uses ``Authorization: Bearer`` auth (the Duo endpoints reject
        ``PRIVATE-TOKEN`` — verified during V1 smoke testing). Caller
        must therefore have configured an OAuth-style token in the
        project's ``gitToken`` setting.
        """
        if not self._token:
            logger.warning(
                "[gitlab_provider] Duo Workflow requires a GitLab token; skipping assignment"
            )
            return

        # Resolve the GitLab project's numeric ID. The Duo endpoint wants
        # ``project_id`` as a string, but it'll accept the URL-encoded
        # path too — try the numeric form first since it's the canonical.
        project_numeric_id: str | None = None
        try:
            info = await self.get_repository_info()
            project_numeric_id = str(info.get("id"))
        except Exception as e:
            logger.warning(
                "[gitlab_provider] could not resolve project_id for Duo Workflow: %s",
                e,
            )
            # Fall back to the URL-encoded path; the API accepts both.
            project_numeric_id = self._project_id

        # Build a "goal" string the Duo agent can use as its prompt. The
        # per-service enrichment comment (posted by that service's
        # delegation_runner BEFORE this call) already lives on the issue, so a
        # short reference is sufficient — the Duo agent will read the issue
        # context itself. The service name is the one genuine per-service
        # identity in this otherwise-shared layer (Factory#157): each repo names
        # *itself* ("AIFactory"/"PFactory"/"TFactory"). Rather than fork the
        # file per service, it is parameterised via FACTORY_SERVICE_NAME so the
        # canonical stays byte-identical across the fleet and each service
        # supplies its own identity at runtime. The default is deliberately
        # neutral and behaviour-equivalent — the Duo agent reads the issue
        # regardless of the exact wording.
        service_name = os.environ.get("FACTORY_SERVICE_NAME", "the Factory")
        goal = (
            f"Implement the change requested in issue #{issue_iid}. "
            f"See the {service_name} enrichment comment on the issue for the "
            "structured implementation plan."
        )

        payload = {
            "goal": goal,
            "project_id": project_numeric_id,
            "issue_id": issue_iid,
            # Software development is the closest match to "go fix this
            # issue". Other definitions (`code_review`, `secret_detection_fp`)
            # are for narrower workflows.
            "workflow_definition": "software_development",
        }

        url = f"{self._base_url}/api/v4/ai/duo_workflows/workflows"
        bearer_headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=bearer_headers, json=payload)

        if resp.status_code in (200, 201):
            logger.info(
                "[gitlab_provider] Duo Workflow triggered for issue=%d (workflow id=%s)",
                issue_iid,
                resp.json().get("id", "?") if resp.content else "?",
            )
            return

        if resp.status_code in (401, 403):
            # Most likely no Duo seat on this token. Silent no-op so the
            # tracker can detect this by polling for the resulting MR.
            logger.warning(
                "[gitlab_provider] Duo Workflow returned %d for issue=%d — "
                "likely no Duo entitlement on the configured token",
                resp.status_code,
                issue_iid,
            )
            return

        # 4xx (other) / 5xx — log + return without raising. The runner's
        # post-call assignee re-check handles "did this actually fire?".
        logger.warning(
            "[gitlab_provider] Duo Workflow returned %d for issue=%d: %s",
            resp.status_code,
            issue_iid,
            (resp.text or "")[:200],
        )

    async def _assign_users(self, issue_iid: int, usernames: list[str]) -> None:
        """Assign a regular GitLab issue to one or more users by username."""
        async with self._client() as client:
            assignee_ids: list[int] = []
            for username in usernames:
                user_resp = await client.get(
                    "/api/v4/users", params={"username": username}
                )
                if user_resp.status_code == 200 and user_resp.json():
                    assignee_ids.append(user_resp.json()[0]["id"])
            if not assignee_ids:
                return
            await client.put(
                f"/api/v4/projects/{self._project_id}/issues/{issue_iid}",
                json={"assignee_ids": assignee_ids},
            )

    # -------------------------------------------------------------------------
    # Label Operations
    # -------------------------------------------------------------------------
    async def apply_labels(self, issue_or_pr_number: int, labels: list[str]) -> None:
        """Add labels to MR or Issue."""
        async with self._client() as client:
            # Try MR update first, then issue update
            mr_resp = await client.put(
                f"/api/v4/projects/{self._project_id}/merge_requests/{issue_or_pr_number}",
                json={"add_labels": ",".join(labels)},
            )
            if mr_resp.status_code != 200:
                await client.put(
                    f"/api/v4/projects/{self._project_id}/issues/{issue_or_pr_number}",
                    json={"add_labels": ",".join(labels)},
                )

    async def remove_labels(self, issue_or_pr_number: int, labels: list[str]) -> None:
        """Remove labels from MR or Issue."""
        async with self._client() as client:
            mr_resp = await client.put(
                f"/api/v4/projects/{self._project_id}/merge_requests/{issue_or_pr_number}",
                json={"remove_labels": ",".join(labels)},
            )
            if mr_resp.status_code != 200:
                await client.put(
                    f"/api/v4/projects/{self._project_id}/issues/{issue_or_pr_number}",
                    json={"remove_labels": ",".join(labels)},
                )

    async def create_label(self, label: LabelData) -> None:
        """Create a new label in the repository."""
        payload = {
            "name": label.name,
            "color": label.color,
            "description": label.description,
        }
        async with self._client() as client:
            await client.post(
                f"/api/v4/projects/{self._project_id}/labels", json=payload
            )

    async def list_labels(self) -> list[LabelData]:
        """List all project labels."""
        async with self._client() as client:
            resp = await client.get(f"/api/v4/projects/{self._project_id}/labels")
            resp.raise_for_status()
            labels_list = resp.json()
            return [
                LabelData(
                    name=lbl["name"],
                    color=lbl.get("color") or "#909090",
                    description=lbl.get("description") or "",
                )
                for lbl in labels_list
            ]

    # -------------------------------------------------------------------------
    # Repository operations
    # -------------------------------------------------------------------------
    async def get_repository_info(self) -> dict[str, Any]:
        """Fetch general repository/project information."""
        async with self._client() as client:
            resp = await client.get(f"/api/v4/projects/{self._project_id}")
            resp.raise_for_status()
            return resp.json()

    async def get_default_branch(self) -> str:
        """Get project's default branch name."""
        info = await self.get_repository_info()
        return info.get("default_branch") or "main"

    async def check_permissions(self, username: str) -> str:
        """Evaluate a user's access level in GitLab (admin, write, read, none)."""
        async with self._client() as client:
            # Query members endpoint to get access levels
            resp = await client.get(
                f"/api/v4/projects/{self._project_id}/members/all",
                params={"query": username},
            )
            if resp.status_code == 200 and resp.json():
                user_info = resp.json()[0]
                access_level = user_info.get("access_level", 0)
                # GitLab Access levels: Guest=10, Reporter=20, Developer=30, Maintainer=40, Owner=50
                if access_level >= 40:
                    return "admin"
                elif access_level >= 30:
                    return "write"
                elif access_level >= 10:
                    return "read"
            return "none"

    # -------------------------------------------------------------------------
    # Low-level REST Operations
    # -------------------------------------------------------------------------
    async def api_get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        async with self._client() as client:
            resp = await client.get(endpoint, params=params)
            resp.raise_for_status()
            return resp.json()

    async def api_post(self, endpoint: str, data: dict[str, Any] | None = None) -> Any:
        async with self._client() as client:
            resp = await client.post(endpoint, json=data)
            resp.raise_for_status()
            return resp.json()

    # -------------------------------------------------------------------------
    # Formatting utilities
    # -------------------------------------------------------------------------
    def _parse_issue_data(self, issue: dict[str, Any]) -> IssueData:
        return IssueData(
            number=issue["iid"],
            title=issue["title"],
            body=issue.get("description") or "",
            author=issue.get("author", {}).get("username", ""),
            state="closed" if issue.get("state") == "closed" else "open",
            labels=issue.get("labels") or [],
            created_at=self._parse_datetime(issue.get("created_at")),
            updated_at=self._parse_datetime(issue.get("updated_at")),
            url=issue.get("web_url") or "",
            assignees=[a.get("username", "") for a in issue.get("assignees", [])]
            if issue.get("assignees")
            else [],
            milestone=issue.get("milestone", {}).get("title")
            if issue.get("milestone")
            else None,
            provider=ProviderType.GITLAB,
            raw_data=issue,
        )

    def _parse_datetime(self, val: str | None) -> datetime:
        if not val:
            return datetime.now(UTC)
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except Exception:
            return datetime.now(UTC)
