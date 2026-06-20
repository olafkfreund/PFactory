"""
Azure DevOps Provider Implementation
====================================

Implements the GitProvider protocol for Azure DevOps using standard REST APIs.
"""

from __future__ import annotations

import base64
import logging
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
class AzureDevOpsProvider:
    """
    Azure DevOps implementation of the GitProvider protocol.
    Works with dev.azure.com.
    """

    _repo: str  # Repository ID or Name
    _pat: str | None = None
    _organization: str | None = None
    _project: str | None = None
    _base_url: str = "https://dev.azure.com"
    _project_dir: str | None = None

    def __post_init__(self):
        # Fallback parsing organization and project from repo string if needed
        # (For example, if repo matches "org/proj/repo-name")
        self._headers = {}
        if self._pat:
            # Basic Authentication with PAT
            encoded_pat = base64.b64encode(f":{self._pat}".encode()).decode()
            self._headers["Authorization"] = f"Basic {encoded_pat}"

        # Resolve organization and project
        self._org = self._organization or "default-org"
        self._proj = self._project or "default-project"
        self._repo_id = self._repo

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.AZURE_DEVOPS

    @property
    def repo(self) -> str:
        return self._repo

    # -------------------------------------------------------------------------
    # Helper to construct clients
    # -------------------------------------------------------------------------
    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(headers=self._headers, timeout=30.0)

    # -------------------------------------------------------------------------
    # Pull Request Operations
    # -------------------------------------------------------------------------
    async def fetch_pr(self, number: int) -> PRData:
        """Fetch Azure DevOps Pull Request details."""
        url = f"{self._base_url}/{self._org}/{self._proj}/_apis/git/repositories/{self._repo_id}/pullrequests/{number}?api-version=7.1"
        async with self._client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            pr_data = resp.json()

            # Attempt to retrieve iterative changes to count files/lines
            files = []
            unified_diff = ""
            additions = 0
            deletions = 0
            try:
                # Fetch PR iterations to get latest iteration changes
                iter_url = f"{self._base_url}/{self._org}/{self._proj}/_apis/git/repositories/{self._repo_id}/pullrequests/{number}/iterations?api-version=7.1"
                iter_resp = await client.get(iter_url)
                if iter_resp.status_code == 200:
                    iterations = iter_resp.json().get("value", [])
                    if iterations:
                        latest_iter = iterations[-1]["id"]
                        changes_url = f"{self._base_url}/{self._org}/{self._proj}/_apis/git/repositories/{self._repo_id}/pullrequests/{number}/iterations/{latest_iter}/changes?api-version=7.1"
                        changes_resp = await client.get(changes_url)
                        if changes_resp.status_code == 200:
                            change_entries = changes_resp.json().get(
                                "changeEntries", []
                            )
                            for entry in change_entries:
                                path = entry.get("item", {}).get("path", "")
                                change_type = entry.get("changeType", "edit")
                                files.append(
                                    {
                                        "path": path,
                                        "additions": 0,
                                        "deletions": 0,
                                        "type": change_type,
                                    }
                                )
            except Exception as e:
                logger.error(f"Error fetching ADO PR changes: {e}")

            # Map ADO status (active, completed, abandoned) to standard (open, merged, closed)
            status_map = {
                "active": "open",
                "completed": "merged",
                "abandoned": "closed",
            }
            raw_status = pr_data.get("status", "active")
            state = status_map.get(raw_status, "open")

            return PRData(
                number=pr_data["pullRequestId"],
                title=pr_data["title"],
                body=pr_data.get("description") or "",
                author=pr_data.get("createdBy", {}).get("uniqueName")
                or pr_data.get("createdBy", {}).get("displayName")
                or "",
                state=state,
                source_branch=pr_data.get("sourceRefName", "").replace(
                    "refs/heads/", ""
                ),
                target_branch=pr_data.get("targetRefName", "").replace(
                    "refs/heads/", ""
                ),
                additions=additions,
                deletions=deletions,
                changed_files=len(files),
                files=files,
                diff=unified_diff,
                url=pr_data.get("_links", {}).get("web", {}).get("href") or "",
                created_at=self._parse_datetime(pr_data.get("creationDate")),
                updated_at=self._parse_datetime(pr_data.get("creationDate")),
                labels=[lbl["name"] for lbl in pr_data.get("labels", [])]
                if pr_data.get("labels")
                else [],
                reviewers=[
                    r.get("uniqueName") or r.get("displayName")
                    for r in pr_data.get("reviewers", [])
                ]
                if pr_data.get("reviewers")
                else [],
                is_draft=pr_data.get("isDraft") or False,
                mergeable=pr_data.get("mergeStatus") == "succeeded",
                provider=ProviderType.AZURE_DEVOPS,
                raw_data=pr_data,
            )

    async def fetch_prs(self, filters: PRFilters | None = None) -> list[PRData]:
        """Fetch list of Azure DevOps pull requests."""
        filters = filters or PRFilters()

        # Map state filter
        status = "active"
        if filters.state == "merged":
            status = "completed"
        elif filters.state == "closed":
            status = "abandoned"
        elif filters.state == "all":
            status = "all"

        url = f"{self._base_url}/{self._org}/{self._proj}/_apis/git/repositories/{self._repo_id}/pullrequests?searchCriteria.status={status}&api-version=7.1"
        async with self._client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            prs_list = resp.json().get("value", [])

            results = []
            for pr in prs_list[: filters.limit]:
                # Filter by branches
                source = pr.get("sourceRefName", "").replace("refs/heads/", "")
                target = pr.get("targetRefName", "").replace("refs/heads/", "")
                if filters.base_branch and target != filters.base_branch:
                    continue
                if filters.head_branch and source != filters.head_branch:
                    continue

                full_pr = await self.fetch_pr(pr["pullRequestId"])
                results.append(full_pr)
            return results

    async def fetch_pr_diff(self, number: int) -> str:
        """Diff retrieval stub for Azure DevOps."""
        # ADO does not have a single plain text unified diff API endpoint.
        # We return empty or parsed diff entries.
        pr = await self.fetch_pr(number)
        return pr.diff

    async def post_review(self, pr_number: int, review: ReviewData) -> int:
        """Submit a review review thread with comments to ADO Pull Request."""
        url = f"{self._base_url}/{self._org}/{self._proj}/_apis/git/repositories/{self._repo_id}/pullrequests/{pr_number}/threads?api-version=7.1"

        # In ADO, threads contain a list of comments.
        # Let's post the main review comments.
        main_thread = {
            "comments": [
                {
                    "parentCommentId": 0,
                    "content": review.body,
                    "commentType": "system" if review.event == "approve" else "text",
                }
            ],
            "status": "fixed" if review.event == "approve" else "active",
        }

        async with self._client() as client:
            resp = await client.post(url, json=main_thread)
            resp.raise_for_status()
            thread_id = resp.json()["id"]

            # Submit each inline code finding as a separate conversation thread
            for finding in review.findings:
                if finding.file and finding.line:
                    inline_thread = {
                        "comments": [
                            {
                                "parentCommentId": 0,
                                "content": f"**[{finding.severity.upper()}] {finding.title}**\n{finding.description}\n\n*Category: {finding.category}*",
                                "commentType": "text",
                            }
                        ],
                        "threadContext": {
                            "filePath": finding.file,
                            "rightFileStart": {"line": finding.line, "offset": 1},
                            "rightFileEnd": {"line": finding.line, "offset": 50},
                        },
                        "status": "active",
                    }
                    try:
                        await client.post(url, json=inline_thread)
                    except Exception as e:
                        logger.error(f"Error posting ADO inline finding: {e}")

            return thread_id

    async def merge_pr(
        self,
        pr_number: int,
        merge_method: str = "merge",
        commit_title: str | None = None,
    ) -> bool:
        """Complete/Merge Azure DevOps PR."""
        url = f"{self._base_url}/{self._org}/{self._proj}/_apis/git/repositories/{self._repo_id}/pullrequests/{pr_number}?api-version=7.1"
        payload = {
            "status": "completed",
            "lastMergeSourceCommit": {
                "commitId": ""  # Should be filled if doing strict optimistic locking
            },
            "completionOptions": {
                "mergeStrategy": merge_method,
                "bypassPolicy": True,
                "deleteSourceBranch": True,
            },
        }
        # First retrieve the PR to get the correct lastMergeSourceCommit ID
        pr = await self.fetch_pr(pr_number)
        last_commit = pr.raw_data.get("lastMergeSourceCommit", {}).get("commitId")
        if last_commit:
            payload["lastMergeSourceCommit"]["commitId"] = last_commit

        async with self._client() as client:
            resp = await client.patch(url, json=payload)
            return resp.status_code == 200

    async def close_pr(self, pr_number: int, comment: str | None = None) -> bool:
        """Abandon/Close Azure DevOps PR without merging."""
        if comment:
            await self.add_comment(pr_number, comment)

        url = f"{self._base_url}/{self._org}/{self._proj}/_apis/git/repositories/{self._repo_id}/pullrequests/{pr_number}?api-version=7.1"
        async with self._client() as client:
            resp = await client.patch(url, json={"status": "abandoned"})
            return resp.status_code == 200

    async def enable_auto_merge(
        self, pr_number: int, merge_method: str = "squash"
    ) -> bool:
        """Not yet implemented for Azure DevOps (RFC-0011 #637).

        ADO's auto-complete (completionOptions on the PR) maps here, but is
        deferred — the low-tier auto-merge fast path is GitHub-first for now.
        """
        raise NotImplementedError(
            "enable_auto_merge is not implemented for the Azure DevOps provider "
            "yet; use the merge_policy decision + manual/CI completion for ADO PRs."
        )

    # -------------------------------------------------------------------------
    # Issue (Work Item) Operations
    # -------------------------------------------------------------------------
    async def fetch_issue(self, number: int) -> IssueData:
        """Fetch an Azure DevOps work item."""
        url = f"{self._base_url}/{self._org}/{self._proj}/_apis/wit/workitems/{number}?api-version=7.1"
        async with self._client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            wi = resp.json()
            return self._parse_work_item(wi)

    async def fetch_issues(
        self, filters: IssueFilters | None = None
    ) -> list[IssueData]:
        """Fetch issues using WIQL (Work Item Query Language) queries."""
        filters = filters or IssueFilters()
        url = (
            f"{self._base_url}/{self._org}/{self._proj}/_apis/wit/wiql?api-version=7.1"
        )

        # Build WIQL state filter
        state_condition = ""
        if filters.state == "open":
            state_condition = "AND [System.State] <> 'Closed'"
        elif filters.state == "closed":
            state_condition = "AND [System.State] = 'Closed'"

        wiql_query = f"""
        SELECT [System.Id], [System.Title], [System.State]
        FROM workitems
        WHERE [System.TeamProject] = '{self._proj}'
        {state_condition}
        ORDER BY [System.CreatedDate] DESC
        """

        async with self._client() as client:
            resp = await client.post(url, json={"query": wiql_query})
            resp.raise_for_status()
            wi_refs = resp.json().get("workItems", [])

            results = []
            for ref in wi_refs[: filters.limit]:
                try:
                    full_wi = await self.fetch_issue(ref["id"])
                    results.append(full_wi)
                except Exception as e:
                    logger.error(f"Error fetching full ADO workitem details: {e}")
            return results

    async def create_issue(
        self,
        title: str,
        body: str,
        labels: list[str] | None = None,
        assignees: list[str] | None = None,
    ) -> IssueData:
        """Create a new Azure DevOps work item (Issue)."""
        # Creation uses JSON Patch (Content-Type: application/json-patch+json)
        url = f"{self._base_url}/{self._org}/{self._proj}/_apis/wit/workitems/$Issue?api-version=7.1"
        patch = [
            {"op": "add", "path": "/fields/System.Title", "value": title},
            {"op": "add", "path": "/fields/System.Description", "value": body},
        ]
        if assignees:
            patch.append(
                {
                    "op": "add",
                    "path": "/fields/System.AssignedTo",
                    "value": assignees[0],
                }
            )

        if labels:
            patch.append(
                {"op": "add", "path": "/fields/System.Tags", "value": "; ".join(labels)}
            )

        headers = self._headers.copy()
        headers["Content-Type"] = "application/json-patch+json"

        async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
            resp = await client.post(url, json=patch)
            resp.raise_for_status()
            return self._parse_work_item(resp.json())

    async def close_issue(self, number: int, comment: str | None = None) -> bool:
        """Close an ADO work item."""
        if comment:
            await self.add_comment(number, comment)

        url = f"{self._base_url}/{self._org}/{self._proj}/_apis/wit/workitems/{number}?api-version=7.1"
        patch = [{"op": "add", "path": "/fields/System.State", "value": "Closed"}]
        headers = self._headers.copy()
        headers["Content-Type"] = "application/json-patch+json"

        async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
            resp = await client.patch(url, json=patch)
            return resp.status_code == 200

    async def add_comment(self, issue_or_pr_number: int, body: str) -> int:
        """Add comment to ADO pull request or work item."""
        # Try PR comment first, if fails then try work item comment
        url_pr = f"{self._base_url}/{self._org}/{self._proj}/_apis/git/repositories/{self._repo_id}/pullrequests/{issue_or_pr_number}/threads?api-version=7.1"
        thread = {
            "comments": [
                {"parentCommentId": 0, "content": body, "commentType": "text"}
            ],
            "status": "active",
        }
        async with self._client() as client:
            resp_pr = await client.post(url_pr, json=thread)
            if resp_pr.status_code == 200 or resp_pr.status_code == 201:
                return resp_pr.json()["id"]

            # Fallback to workitem comments API
            url_wi = f"{self._base_url}/{self._org}/{self._proj}/_apis/wit/workitems/{issue_or_pr_number}/comments?api-version=7.1"
            resp_wi = await client.post(url_wi, json={"text": body})
            resp_wi.raise_for_status()
            return resp_wi.json()["id"]

    async def assign_to_user(self, issue_number: int, assignees: list[str]) -> None:
        """Permanent stub — ADO has no autonomous coding agent."""
        raise NotImplementedError(
            "Azure DevOps has no autonomous coding agent equivalent to "
            "GitHub Copilot Coding Agent or GitLab Duo Workflow."
        )

    # -------------------------------------------------------------------------
    # Label Operations
    # -------------------------------------------------------------------------
    async def apply_labels(self, issue_or_pr_number: int, labels: list[str]) -> None:
        """Update tags on a work item or labels on a PR."""
        # For simplicity, we implement tag updating on the work item
        try:
            full_wi = await self.fetch_issue(issue_or_pr_number)
            existing_tags = full_wi.labels
            all_tags = list(set(existing_tags + labels))

            url = f"{self._base_url}/{self._org}/{self._proj}/_apis/wit/workitems/{issue_or_pr_number}?api-version=7.1"
            patch = [
                {
                    "op": "add",
                    "path": "/fields/System.Tags",
                    "value": "; ".join(all_tags),
                }
            ]
            headers = self._headers.copy()
            headers["Content-Type"] = "application/json-patch+json"
            async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
                await client.patch(url, json=patch)
        except Exception as e:
            logger.error(f"Error applying labels: {e}")

    async def remove_labels(self, issue_or_pr_number: int, labels: list[str]) -> None:
        """Remove tags from work item."""
        try:
            full_wi = await self.fetch_issue(issue_or_pr_number)
            existing_tags = full_wi.labels
            filtered_tags = [t for t in existing_tags if t not in labels]

            url = f"{self._base_url}/{self._org}/{self._proj}/_apis/wit/workitems/{issue_or_pr_number}?api-version=7.1"
            patch = [
                {
                    "op": "add",
                    "path": "/fields/System.Tags",
                    "value": "; ".join(filtered_tags),
                }
            ]
            headers = self._headers.copy()
            headers["Content-Type"] = "application/json-patch+json"
            async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
                await client.patch(url, json=patch)
        except Exception as e:
            logger.error(f"Error removing labels: {e}")

    async def create_label(self, label: LabelData) -> None:
        """Mock create label for ADO."""
        pass

    async def list_labels(self) -> list[LabelData]:
        """Mock list labels for ADO."""
        return []

    # -------------------------------------------------------------------------
    # Repository operations
    # -------------------------------------------------------------------------
    async def get_repository_info(self) -> dict[str, Any]:
        """Fetch general repository information."""
        url = f"{self._base_url}/{self._org}/{self._proj}/_apis/git/repositories/{self._repo_id}?api-version=7.1"
        async with self._client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()

    async def get_default_branch(self) -> str:
        """Get repository default branch name."""
        info = await self.get_repository_info()
        ref = info.get("defaultBranch") or "refs/heads/main"
        return ref.replace("refs/heads/", "")

    async def check_permissions(self, username: str) -> str:
        """Stub check permissions for ADO (admin, write, read, none)."""
        return "write"

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
    def _parse_work_item(self, wi: dict[str, Any]) -> IssueData:
        fields = wi.get("fields", {})
        tags_str = fields.get("System.Tags") or ""
        labels = [t.strip() for t in tags_str.split(";")] if tags_str else []
        assignee = (
            fields.get("System.AssignedTo", {}).get("uniqueName")
            or fields.get("System.AssignedTo", {}).get("displayName")
            or ""
        )

        return IssueData(
            number=wi["id"],
            title=fields.get("System.Title", ""),
            body=fields.get("System.Description") or "",
            author=fields.get("System.CreatedBy", {}).get("uniqueName")
            or fields.get("System.CreatedBy", {}).get("displayName")
            or "",
            state="closed" if fields.get("System.State") == "Closed" else "open",
            labels=labels,
            created_at=self._parse_datetime(fields.get("System.CreatedDate")),
            updated_at=self._parse_datetime(fields.get("System.ChangedDate")),
            url=wi.get("_links", {}).get("html", {}).get("href") or "",
            assignees=[assignee] if assignee else [],
            milestone=fields.get("System.IterationPath"),
            provider=ProviderType.AZURE_DEVOPS,
            raw_data=wi,
        )

    def _parse_datetime(self, val: str | None) -> datetime:
        if not val:
            return datetime.now(UTC)
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except Exception:
            return datetime.now(UTC)
