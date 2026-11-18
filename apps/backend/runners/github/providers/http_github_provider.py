"""
GitHub over REST, authenticated by an EXPLICIT token (Factory#370)
=================================================================

:class:`GitHubProvider` drives the ``gh`` CLI through ``GHClient``. That is right
for the agent runners — they work inside a checkout on a machine where a
developer or a runner token has already logged ``gh`` in — and wrong twice over
for a hosted, multi-tenant service:

1. **A server image has no ``gh`` binary**, and adding one buys a subprocess per
   call for an API request httpx already makes.
2. **``gh`` uses whatever credential is ambient.** CFactory found this the hard
   way during RFC-0019 Phase 6: an ambient ``gh`` login silently switched
   issue-writing on and the board began filing real issues. ``CFACTORY_GITHUB_TOKEN``
   exists specifically to require an explicit, service-scoped credential, and a
   provider that reads whatever ``gh`` happens to be logged in as is the exact
   behaviour that guard was introduced to prevent.

The second reason is the one that generalises. **A per-tenant credential cannot
come from a process-wide login** — there is only one ``gh`` session per process
and there are many tenants. RFC-0020 phases 3 and 4 (the encrypted tenant
credential store, and the GitHub App install flow) make that concrete: the token
this provider is handed may be an installation token minted seconds ago for one
tenant, and the next call may carry a different tenant's.

So this class exists alongside ``GitHubProvider`` rather than replacing it. Both
implement the same protocol; they differ only in transport and in where the
credential comes from.

What this covers, and what it does not
--------------------------------------
This provider implements the **work-item** half of the protocol — issues,
comments, assignment, labels, repository info (complete since Factory#3773).
It does NOT implement the **code/VCS** half
(``fetch_pr``, ``fetch_pr_diff``, ``post_review``, ``merge_pr``,
``enable_auto_merge``, ``get_default_branch``), and that is deliberate rather
than unfinished.

Those operations are what the agent runners use, and the runners *do* have a
checkout and a logged-in ``gh``, so they are already served. Nobody has asked
for token-based PR review; adding it speculatively would mean translating ``gh``
argv (``["pr", "merge", "--squash", "--yes"]``) into REST calls, which is a lot
of fragile machinery for demand that does not exist yet.

That split is not an accident of this file. ``protocol.py`` conflates two
different seams — a code/VCS seam and a work-item seam — and a caller almost
never needs both. Factory#380 (Jira) is the same observation from the other
side: Jira has work items and no repositories at all. When that split is made
explicit, this class becomes "the work-item seam over REST" rather than "the
provider with holes in it".

Calling an unimplemented method raises :class:`NotImplementedError` with that
explanation, so a caller that needs the VCS half is told which provider to use
instead of getting an ``AttributeError`` from somewhere deep in a sync.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from ._github_json import (
    assign_actors,
    PAGE_SIZE as _GITHUB_PAGE_SIZE,
    collect_comment_pages as _collect_comment_pages,
    group_bulk_comments as _group_bulk_comments,
    milestone_title as _milestone_title,
    parse_comment as _shared_parse_comment,
    parse_datetime as _parse_datetime,
)
from .protocol import (
    IssueComment,
    IssueData,
    IssueFilters,
    IssueRef,
    LabelData,
    ProviderType,
    as_issue_int,
    oldest_first,
    to_iso_utc,
)

_TIMEOUT_SECONDS = 10.0
_NOT_FOUND = 404
_UNPROCESSABLE = 422


def _already_exists(resp: httpx.Response) -> bool:
    """True when a 422 says the label exists, as opposed to any other validation error."""
    try:
        errors = resp.json().get("errors") or []
    except ValueError:
        return False
    return any(isinstance(e, dict) and e.get("code") == "already_exists" for e in errors)


@dataclass
class HttpGitHubProvider:
    """GitHub's REST API, authenticated by a token this object is handed.

    Usage::

        provider = HttpGitHubProvider(_repo="owner/repo", _token=token)
        issues = await provider.fetch_issues()

    ``_base_url`` accepts a GitHub Enterprise host. ``_transport`` is for tests.
    """

    _repo: str
    # repr=False so the credential cannot ride out in a traceback, a log line or
    # a debugger dump of this object. A dataclass renders every field by default,
    # which for a token-carrying object is a leak waiting for the first
    # `logger.debug("provider=%s", provider)` (cf. Factory#372, which fixed
    # exactly this on the GitLab and Azure DevOps providers).
    _token: str | None = field(default=None, repr=False)
    _base_url: str = "https://api.github.com"
    _transport: httpx.AsyncBaseTransport | None = None

    @property
    def repo(self) -> str:
        return self._repo

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.GITHUB

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=_TIMEOUT_SECONDS,
            transport=self._transport,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Authorization": f"Bearer {self._token}",
            },
            # Factory#825: explicit no-redirect posture on a client that carries a
            # bearer token to an address derived from _base_url.
            follow_redirects=False,
        )

    # ── issues ───────────────────────────────────────────────────────────────

    async def create_issue(
        self,
        title: str,
        body: str,
        labels: list[str] | None = None,
        assignees: list[str] | None = None,
    ) -> IssueData:
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        if assignees:
            payload["assignees"] = assignees
        async with self._client() as client:
            resp = await client.post(f"/repos/{self._repo}/issues", json=payload)
            resp.raise_for_status()
            return self._parse_issue(resp.json())

    async def fetch_issue(self, number: IssueRef) -> IssueData:
        number = str(as_issue_int(number, "GitHub"))
        async with self._client() as client:
            resp = await client.get(f"/repos/{self._repo}/issues/{number}")
            resp.raise_for_status()
            return self._parse_issue(resp.json())

    async def fetch_issues(self, filters: IssueFilters | None = None) -> list[IssueData]:
        """List the repo's issues, **paging to completion** up to ``limit``.

        GitHub caps a page at 100, so a repo with 300 open issues needs three
        calls. Taking only the first page would silently import a third of a
        backlog — a "where are my issues?" failure that looks like success.

        ``/issues`` returns pull requests too (they carry a ``pull_request`` key),
        so they are dropped unless explicitly asked for.
        """
        filters = filters or IssueFilters()
        params: dict[str, Any] = {"state": filters.state, "per_page": _GITHUB_PAGE_SIZE}
        if filters.labels:
            params["labels"] = ",".join(filters.labels)
        if filters.assignee:
            params["assignee"] = filters.assignee
        if filters.author:
            params["creator"] = filters.author
        if filters.since is not None:
            # GitHub's `since` is "updated at or after" — exactly the watermark
            # semantics an incremental pass wants.
            params["since"] = filters.since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        issues: list[IssueData] = []
        async with self._client() as client:
            page = 1
            while len(issues) < filters.limit:
                resp = await client.get(
                    f"/repos/{self._repo}/issues", params={**params, "page": page}
                )
                resp.raise_for_status()
                batch = resp.json()
                if not isinstance(batch, list) or not batch:
                    break
                for item in batch:
                    if (
                        not filters.include_prs
                        and isinstance(item, dict)
                        and "pull_request" in item
                    ):
                        continue
                    issues.append(self._parse_issue(item))
                if len(batch) < _GITHUB_PAGE_SIZE:
                    break
                page += 1
        return issues[: filters.limit]

    async def close_issue(self, number: IssueRef, comment: str | None = None) -> bool:
        """Close an issue, optionally commenting first.

        Returns ``False`` on any HTTP or transport error rather than raising,
        matching :meth:`GitHubProvider.close_issue` — callers treat the result as
        "did it close", and the two transports must answer the same way.
        """
        number = str(as_issue_int(number, "GitHub"))  # a bad key is a caller error, not "False"
        try:
            if comment:
                await self.add_comment(number, comment)
            async with self._client() as client:
                resp = await client.patch(
                    f"/repos/{self._repo}/issues/{number}", json={"state": "closed"}
                )
                resp.raise_for_status()
        except httpx.HTTPError:
            return False
        return True

    async def assign_to_user(self, issue_number: IssueRef, assignees: list[str]) -> None:
        """Assign users or the Copilot agent; the algorithm is shared with the gh provider."""
        await assign_actors(
            self._graphql, repo=self._repo, issue_number=issue_number, assignees=assignees
        )

    async def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """One GraphQL request. GitHub Enterprise serves it at ``/api/graphql``."""
        base = self._base_url.rstrip("/")
        url = (base.removesuffix("/v3") if base.endswith("/api/v3") else base) + "/graphql"
        async with self._client() as client:
            resp = await client.post(url, json={"query": query, "variables": variables})
            resp.raise_for_status()
            payload = resp.json()
        result = dict(payload) if isinstance(payload, dict) else {}
        # GraphQL reports failures (FORBIDDEN, NOT_FOUND...) with HTTP 200 and an
        # `errors` array; `gh api graphql` exits non-zero on those, so the two
        # transports must both raise rather than let a failed mutation look done.
        if result.get("errors"):
            raise RuntimeError(f"GitHub GraphQL error: {result['errors']}")
        return result

    # ── labels ───────────────────────────────────────────────────────────────

    async def apply_labels(self, issue_or_pr_number: IssueRef, labels: list[str]) -> None:
        issue_or_pr_number = str(as_issue_int(issue_or_pr_number, "GitHub"))
        async with self._client() as client:
            resp = await client.post(
                f"/repos/{self._repo}/issues/{issue_or_pr_number}/labels", json={"labels": labels}
            )
            resp.raise_for_status()

    async def remove_labels(self, issue_or_pr_number: IssueRef, labels: list[str]) -> None:
        """One DELETE per label; a 404 means it was already absent, which is the goal."""
        issue_or_pr_number = str(as_issue_int(issue_or_pr_number, "GitHub"))
        async with self._client() as client:
            for label in labels:
                resp = await client.delete(
                    f"/repos/{self._repo}/issues/{issue_or_pr_number}/labels/"
                    f"{quote(label, safe='')}"
                )
                if resp.status_code != _NOT_FOUND:
                    resp.raise_for_status()

    async def create_label(self, label: LabelData) -> None:
        """Create a label, updating it when it already exists (gh ``--force`` semantics)."""
        payload = {"name": label.name, "color": label.color, "description": label.description}
        async with self._client() as client:
            resp = await client.post(f"/repos/{self._repo}/labels", json=payload)
            if resp.status_code == _UNPROCESSABLE and _already_exists(resp):
                resp = await client.patch(
                    f"/repos/{self._repo}/labels/{quote(label.name, safe='')}",
                    json={"color": label.color, "description": label.description},
                )
            resp.raise_for_status()

    async def list_labels(self) -> list[LabelData]:
        """Every label in the repository, paging to completion."""
        labels: list[LabelData] = []
        async with self._client() as client:
            page = 1
            while True:
                resp = await client.get(
                    f"/repos/{self._repo}/labels",
                    params={"per_page": _GITHUB_PAGE_SIZE, "page": page},
                )
                resp.raise_for_status()
                batch = resp.json()
                if not isinstance(batch, list) or not batch:
                    break
                labels.extend(
                    LabelData(
                        name=str(item.get("name") or ""),
                        color=str(item.get("color") or ""),
                        description=str(item.get("description") or ""),
                    )
                    for item in batch
                    if isinstance(item, dict)
                )
                if len(batch) < _GITHUB_PAGE_SIZE:
                    break
                page += 1
        return labels

    # ── comments ─────────────────────────────────────────────────────────────

    async def add_comment(self, issue_or_pr_number: IssueRef, body: str) -> int:
        """Comment on an issue or PR (both live on the issues endpoint); returns the id."""
        issue_or_pr_number = str(as_issue_int(issue_or_pr_number, "GitHub"))
        async with self._client() as client:
            resp = await client.post(
                f"/repos/{self._repo}/issues/{issue_or_pr_number}/comments", json={"body": body}
            )
            resp.raise_for_status()
            comment_id = resp.json().get("id")
        if not isinstance(comment_id, int):
            raise ValueError(f"{self._repo}: comment response carried no id")
        return comment_id

    async def fetch_comments(
        self, issue_number: IssueRef, since: datetime | None = None
    ) -> list[IssueComment]:
        """Read ONE issue's comment thread.

        GitHub takes ``since`` server-side on the per-issue endpoint, so an
        incremental read transfers only what changed.
        """
        issue_number = str(as_issue_int(issue_number, "GitHub"))
        params: dict[str, Any] = {}
        if since is not None:
            params["since"] = to_iso_utc(since)
        path = f"/repos/{self._repo}/issues/{issue_number}/comments"
        raw = await self._comment_pages(path, params)
        return oldest_first([self._parse_comment(item, issue_number) for item in raw])

    async def fetch_comments_bulk(
        self, issue_numbers: list[IssueRef], since: datetime | None = None
    ) -> dict[IssueRef, list[IssueComment]]:
        """Bulk read over REST. Grouping and the rationale: ``_github_json``."""
        wanted = sorted({str(as_issue_int(number, "GitHub")) for number in issue_numbers}, key=int)
        if not wanted:
            return {}
        if since is None:
            return {number: await self.fetch_comments(number, since=None) for number in wanted}
        raw = await self._comment_pages(
            f"/repos/{self._repo}/issues/comments",
            {"since": to_iso_utc(since), "sort": "updated", "direction": "asc"},
        )
        return _group_bulk_comments(raw, wanted)

    async def _comment_pages(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Transport half of the shared comment walk — see providers/_github_json.py."""

        async def fetch_page(page: int) -> Any:
            async with self._client() as client:
                resp = await client.get(
                    path, params={**params, "per_page": _GITHUB_PAGE_SIZE, "page": page}
                )
                resp.raise_for_status()
                return resp.json()

        return await _collect_comment_pages(fetch_page, path)

    # ── repository ───────────────────────────────────────────────────────────

    async def get_repository_info(self) -> dict[str, Any]:
        """One cheap authenticated read of the repository.

        The right call for a connection check: it proves in a single round trip
        that the base URL resolves, the credential is accepted, and the project
        exists and is visible to it.
        """
        async with self._client() as client:
            resp = await client.get(f"/repos/{self._repo}")
            resp.raise_for_status()
            info = resp.json()
            return dict(info) if isinstance(info, dict) else {}

    # ── the VCS half, deliberately absent ────────────────────────────────────

    def _vcs_unsupported(self, operation: str) -> NotImplementedError:
        return NotImplementedError(
            f"HttpGitHubProvider does not implement {operation}: it covers the "
            "work-item seam (issues, comments, repository info) over REST. The "
            "code/VCS seam is served by GitHubProvider via the gh CLI, which the "
            "agent runners already have. See Factory#370 and Factory#380."
        )

    async def fetch_pr(self, number: int) -> Any:
        raise self._vcs_unsupported("fetch_pr")

    async def fetch_pr_diff(self, number: int) -> Any:
        raise self._vcs_unsupported("fetch_pr_diff")

    async def fetch_pr_comments(self, *args: Any, **kwargs: Any) -> Any:
        raise self._vcs_unsupported("fetch_pr_comments")

    async def post_review(self, *args: Any, **kwargs: Any) -> Any:
        raise self._vcs_unsupported("post_review")

    async def merge_pr(self, *args: Any, **kwargs: Any) -> Any:
        raise self._vcs_unsupported("merge_pr")

    async def enable_auto_merge(self, *args: Any, **kwargs: Any) -> Any:
        raise self._vcs_unsupported("enable_auto_merge")

    async def get_default_branch(self) -> Any:
        raise self._vcs_unsupported("get_default_branch")

    # ── parsing ──────────────────────────────────────────────────────────────

    def _parse_issue(self, issue: Any) -> IssueData:
        """GitHub's issue JSON as provider-neutral :class:`IssueData`.

        The number is the one field with no safe default: without it the caller
        cannot name the issue it just created, so a response missing it is an
        error rather than a record pointing at nothing.
        """
        issue = dict(issue) if isinstance(issue, dict) else {}
        number = issue.get("number")
        if not isinstance(number, int):
            raise ValueError(f"{self._repo}: response carried no issue number")
        return IssueData(
            number=str(number),
            title=str(issue.get("title") or ""),
            body=str(issue.get("body") or ""),
            author=str((issue.get("user") or {}).get("login") or ""),
            state=str(issue.get("state") or ""),
            labels=[
                name
                for label in issue.get("labels") or []
                if isinstance(
                    name := (label.get("name") if isinstance(label, dict) else label), str
                )
            ],
            created_at=_parse_datetime(issue.get("created_at")),
            updated_at=_parse_datetime(issue.get("updated_at")),
            url=str(issue.get("html_url") or ""),
            assignees=[
                login
                for user in issue.get("assignees") or []
                if isinstance(login := (user.get("login") if isinstance(user, dict) else user), str)
            ],
            milestone=_milestone_title(issue.get("milestone")),
            provider=ProviderType.GITHUB,
            raw_data=issue,
        )

    def _parse_comment(self, data: dict[str, Any], issue_number: str) -> IssueComment:
        """Shared with the gh-CLI provider — ``gh api`` returns this payload verbatim."""
        return _shared_parse_comment(data, issue_number)
