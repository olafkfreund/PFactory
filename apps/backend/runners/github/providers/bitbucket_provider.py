"""
Bitbucket Cloud code provider (RFC-0022 Phase 5, Factory#3776)
==============================================================

Implements :class:`~.protocol.CodeProvider` -- and only that -- over the
Bitbucket Cloud REST 2.0 API. Bitbucket issues are a work-item system and out
of scope, so this class has no work-item members and is NOT a ``GitProvider``;
hand it out through ``factory.get_code_provider``.

Credential: one opaque string, as for every other provider. Atlassian has
retired app passwords, so the supported kinds are

* a Bitbucket **API token**: pass the account email as ``_username`` (HTTP Basic
  ``email:token``);
* a repository/workspace **access token**: no ``_username`` (``Bearer``).

The credential is built into one header in ``__post_init__`` and is never logged,
never in ``repr`` and never in an exception message.

Redirects: the client never follows them (Factory#825). Two Bitbucket behaviours
need a redirect or an absolute URL -- ``/diff`` answers 302 and list endpoints
return absolute ``next`` links -- so :meth:`BitbucketProvider._get` follows
those by hand and only when the target is on the configured host. A foreign
host raises, so the credential never leaves ``_base_url``'s origin.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import httpx

from .protocol import (
    IssueComment,
    PRData,
    PRFilters,
    ProviderCommentError,
    ProviderType,
    ReviewData,
    oldest_first,
    to_utc,
)

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.bitbucket.org/2.0"
_PAGE_SIZE = 50  # Bitbucket's pagelen ceiling for these endpoints
_MAX_PAGES = 20  # bounds every paged read; hitting it is an error, not a truncation
_MAX_REDIRECTS = 3
_BAD_REQUEST, _FORBIDDEN, _NOT_FOUND, _CONFLICT, _ACCEPTED = 400, 403, 404, 409, 202
_MERGE_POLL_ATTEMPTS = 5
_MERGE_POLL_DELAY = 1.0  # seconds; tests set it to 0

_MERGE_STRATEGIES = {
    "merge": "merge_commit",
    "squash": "squash",
    "rebase": "rebase_fast_forward",
}
_STATES = {
    "open": ["OPEN"],
    "merged": ["MERGED"],
    "closed": ["DECLINED", "SUPERSEDED"],
    "all": ["OPEN", "MERGED", "DECLINED", "SUPERSEDED"],
}
_STATE_MAP = {
    "OPEN": "open",
    "MERGED": "merged",
    "DECLINED": "closed",
    "SUPERSEDED": "closed",
}


class BitbucketMergePending(RuntimeError):  # noqa: N818 - a state, not a fault
    """A merge was accepted (HTTP 202) but had not finished when polling stopped.

    Distinct from a ``False`` return so a caller never reads "still running" as
    "failed" and retries a merge that is about to land.
    """


def _origin(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return parts.scheme.lower(), parts.netloc.lower()


def _q_literal(value: str) -> str:
    """Quote ``value`` for a Bitbucket ``q`` filter string literal."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _parse_datetime(val: str | None) -> datetime:
    if not val:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC)


def _user_name(user: dict[str, Any] | None) -> str:
    user = user or {}
    return str(user.get("nickname") or user.get("display_name") or "")


@dataclass
class BitbucketProvider:
    """Bitbucket Cloud implementation of :class:`CodeProvider` only."""

    _repo: str  # "workspace/repo_slug"
    # repr=False on every credential-bearing field (Factory#372): a dataclass
    # repr is what tracebacks, f-string logs and error reporters render.
    _token: str | None = field(default=None, repr=False)
    _username: str | None = field(default=None, repr=False)
    _base_url: str = _DEFAULT_BASE_URL
    _transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        workspace, _, slug = self._repo.partition("/")
        if not workspace or not slug or "/" in slug:
            raise ValueError("Bitbucket repo must be 'workspace/repo_slug'")
        # A pasted or mounted secret often carries a trailing newline; httpx then
        # fails with an error that quotes the whole header value. Strip the edges
        # and refuse anything still unusable, naming no part of the value.
        self._token = self._token.strip() if self._token else self._token
        self._username = self._username.strip() if self._username else self._username
        for secret in (self._token, self._username):
            if secret and not (secret.isascii() and secret.isprintable() and " " not in secret):
                raise ValueError("Bitbucket credential contains whitespace or control characters")
        self._base_url = self._base_url.rstrip("/")
        self._repo_path = f"/repositories/{quote(workspace, safe='')}/{quote(slug, safe='')}"
        self._headers: dict[str, str] = {}
        if self._token and self._username:
            basic = base64.b64encode(f"{self._username}:{self._token}".encode()).decode()
            self._headers["Authorization"] = f"Basic {basic}"
        elif self._token:
            self._headers["Authorization"] = f"Bearer {self._token}"

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.BITBUCKET

    @property
    def repo(self) -> str:
        return self._repo

    # -------------------------------------------------------------------------
    # Transport
    # -------------------------------------------------------------------------
    def _client(self) -> httpx.AsyncClient:
        # Factory#825: explicit, never inherited from httpx's default. The
        # Authorization header must not be chased across a redirect; _get()
        # follows same-origin redirects itself.
        return httpx.AsyncClient(
            headers=self._headers,
            timeout=30.0,
            follow_redirects=False,
            transport=self._transport,
        )

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    def _same_origin(self, url: str) -> bool:
        return _origin(url) == _origin(self._base_url)

    async def _get(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: Any = None,
    ) -> httpx.Response:
        """GET, following up to ``_MAX_REDIRECTS`` redirects on the same origin only."""
        for _ in range(_MAX_REDIRECTS + 1):
            resp = await client.get(url, params=params)
            if not resp.is_redirect:
                resp.raise_for_status()
                return resp
            target = urljoin(str(resp.url), resp.headers.get("location", ""))
            if not self._same_origin(target):
                raise ValueError("Bitbucket redirect to a foreign host refused")
            url, params = target, None
        raise ValueError("Bitbucket redirect limit exceeded")

    async def _paged(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: Any = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield every ``values`` entry, following ``next`` links on this host only."""
        url: str | None = self._url(path)
        for _ in range(_MAX_PAGES):
            if url is None:
                return
            body = (await self._get(client, url, params)).json()
            for item in body.get("values") or []:
                yield item
            url, params = body.get("next"), None
            if url is not None and not self._same_origin(url):
                raise ValueError("Bitbucket 'next' link to a foreign host refused")
        if url is not None:
            raise RuntimeError(f"Bitbucket read exceeded {_MAX_PAGES} pages for {path}")

    # -------------------------------------------------------------------------
    # Repository
    # -------------------------------------------------------------------------
    async def get_repository_info(self) -> dict[str, Any]:
        """Repository JSON; clone URLs are in ``links.clone``."""
        async with self._client() as client:
            return (await self._get(client, self._url(self._repo_path))).json()

    async def get_default_branch(self) -> str:
        info = await self.get_repository_info()
        return str((info.get("mainbranch") or {}).get("name") or "main")

    async def check_permissions(self, username: str) -> str:
        """Explicit repository permission of ``username`` (account id or ``{uuid}``).

        Needs repo admin, and the endpoint lists EXPLICIT grants only: access
        inherited through a group reads as ``none``. Fails closed on 403/404.
        """
        path = f"{self._repo_path}/permissions-config/users/{quote(username, safe='')}"
        async with self._client() as client:
            resp = await client.get(self._url(path))
            if resp.status_code in (_FORBIDDEN, _NOT_FOUND):
                return "none"
            resp.raise_for_status()
            permission = resp.json().get("permission")
            return permission if permission in ("admin", "write", "read") else "none"

    # -------------------------------------------------------------------------
    # Pull requests
    # -------------------------------------------------------------------------
    def _parse_pr(
        self,
        pr: dict[str, Any],
        diff: str = "",
        files: list[dict[str, Any]] | None = None,
    ) -> PRData:
        raw_state = pr.get("state")
        state = _STATE_MAP.get(raw_state or "", "unknown")
        if state == "unknown":
            logger.warning(
                "Bitbucket returned an unmapped PR state %r for PR %s; "
                "reporting state='unknown' rather than assuming 'open'.",
                raw_state,
                pr.get("id"),
            )
        files = files or []
        reviewers = [
            _user_name(p.get("user"))
            for p in pr.get("participants") or []
            if p.get("role") == "REVIEWER"
        ]
        return PRData(
            number=pr["id"],
            title=pr.get("title") or "",
            body=pr.get("description") or "",
            author=_user_name(pr.get("author")),
            state=state,
            source_branch=((pr.get("source") or {}).get("branch") or {}).get("name", ""),
            target_branch=((pr.get("destination") or {}).get("branch") or {}).get("name", ""),
            additions=sum(f["additions"] for f in files),
            deletions=sum(f["deletions"] for f in files),
            changed_files=len(files),
            files=files,
            diff=diff,
            url=(((pr.get("links") or {}).get("html")) or {}).get("href", ""),
            created_at=_parse_datetime(pr.get("created_on")),
            updated_at=_parse_datetime(pr.get("updated_on")),
            reviewers=reviewers,
            is_draft=bool(pr.get("draft")),
            provider=ProviderType.BITBUCKET,
            raw_data=pr,
        )

    async def fetch_pr(self, number: int) -> PRData:
        """Fetch one PR with its diffstat and unified diff."""
        base = f"{self._repo_path}/pullrequests/{number}"
        async with self._client() as client:
            pr = (await self._get(client, self._url(base))).json()
            files = [
                {
                    "path": (e.get("new") or e.get("old") or {}).get("path", ""),
                    "additions": int(e.get("lines_added") or 0),
                    "deletions": int(e.get("lines_removed") or 0),
                    "type": e.get("status", "modified"),
                }
                async for e in self._paged(client, f"{base}/diffstat")
            ]
            diff = (await self._get(client, self._url(f"{base}/diff"))).text
        return self._parse_pr(pr, diff=diff, files=files)

    async def fetch_prs(self, filters: PRFilters | None = None) -> list[PRData]:
        """List PRs, newest-updated first. Entries carry no diff; use ``fetch_pr``.

        Bitbucket PRs have no labels, so a non-empty ``labels`` filter can match
        nothing and returns ``[]`` instead of silently widening the result.
        """
        filters = filters or PRFilters()
        if filters.labels:
            return []
        states = _STATES.get(filters.state)
        if states is None:
            raise ValueError(f"Unsupported PR state filter: {filters.state!r}")
        clauses = []
        if filters.author:
            clauses.append(f"author.nickname={_q_literal(filters.author)}")
        if filters.base_branch:
            clauses.append(f"destination.branch.name={_q_literal(filters.base_branch)}")
        if filters.head_branch:
            clauses.append(f"source.branch.name={_q_literal(filters.head_branch)}")
        params: list[tuple[str, Any]] = [("state", s) for s in states]
        params += [("pagelen", _PAGE_SIZE), ("sort", "-updated_on")]
        if clauses:
            params.append(("q", " AND ".join(clauses)))
        cutoff = to_utc(filters.since) if filters.since else None
        results: list[PRData] = []
        async with self._client() as client:
            async for pr in self._paged(client, f"{self._repo_path}/pullrequests", params):
                if cutoff is not None and to_utc(_parse_datetime(pr.get("updated_on"))) <= cutoff:
                    break  # newest-updated first, so everything after is older
                results.append(self._parse_pr(pr))
                if len(results) >= filters.limit:
                    break
        return results

    async def fetch_pr_diff(self, number: int) -> str:
        path = f"{self._repo_path}/pullrequests/{number}/diff"
        async with self._client() as client:
            return (await self._get(client, self._url(path))).text

    async def fetch_pr_comments(
        self, pr_number: int, since: datetime | None = None
    ) -> list[IssueComment]:
        """Discussion comments on a PR, oldest first (inline comments included)."""
        cutoff = to_utc(since) if since is not None else None
        collected: list[IssueComment] = []
        try:
            async with self._client() as client:
                path = f"{self._repo_path}/pullrequests/{pr_number}/comments"
                async for item in self._paged(client, path, {"pagelen": _PAGE_SIZE}):
                    if item.get("deleted"):
                        continue
                    created = item.get("created_on")
                    updated = _parse_datetime(item.get("updated_on") or created)
                    if cutoff is not None and to_utc(updated) <= cutoff:
                        continue
                    user = item.get("user") or {}
                    collected.append(
                        IssueComment(
                            id=str(item.get("id", "")),
                            issue_number=str(pr_number),
                            author=_user_name(user),
                            body=(item.get("content") or {}).get("raw") or "",
                            created_at=_parse_datetime(created),
                            updated_at=updated,
                            url=((item.get("links") or {}).get("html") or {}).get("href", ""),
                            provider=ProviderType.BITBUCKET,
                            raw_data=item,
                            avatar_url=str(
                                ((user.get("links") or {}).get("avatar") or {}).get("href") or ""
                            ),
                        )
                    )
        except Exception as exc:
            raise ProviderCommentError(
                f"Bitbucket comment read failed for PR {pr_number}: {exc}"
            ) from exc
        return oldest_first(collected)

    async def _comment(
        self,
        client: httpx.AsyncClient,
        pr_number: int,
        body: str,
        inline: dict[str, Any] | None = None,
    ) -> int:
        payload: dict[str, Any] = {"content": {"raw": body}}
        if inline:
            payload["inline"] = inline
        resp = await client.post(
            self._url(f"{self._repo_path}/pullrequests/{pr_number}/comments"), json=payload
        )
        resp.raise_for_status()
        return int(resp.json()["id"])

    async def post_review(self, pr_number: int, review: ReviewData) -> int:
        """Post the body, inline findings, then approve/request changes per ``event``.

        Returns the body comment's id. An inline finding Bitbucket rejects (400,
        usually a line outside the diff) is posted as a plain comment instead.
        """
        async with self._client() as client:
            comment_id = await self._comment(
                client, pr_number, review.body or f"Review: {review.event}"
            )
            for f in review.findings:
                text = (
                    f"**[{f.severity.upper()}] {f.title}**\n{f.description}\n\n"
                    f"*Category: {f.category}*"
                )
                if not (f.file and f.line):
                    await self._comment(client, pr_number, text)
                    continue
                try:
                    await self._comment(
                        client, pr_number, text, inline={"path": f.file, "to": f.line}
                    )
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != _BAD_REQUEST:
                        raise
                    await self._comment(client, pr_number, f"{text}\n\n`{f.file}:L{f.line}`")
            action = {"approve": "approve", "request_changes": "request-changes"}.get(review.event)
            if action:
                resp = await client.post(
                    self._url(f"{self._repo_path}/pullrequests/{pr_number}/{action}")
                )
                resp.raise_for_status()
        return comment_id

    async def merge_pr(
        self, pr_number: int, merge_method: str = "merge", commit_title: str | None = None
    ) -> bool:
        """Merge. ``False`` on a conflict or failed merge check (409/400)."""
        strategy = _MERGE_STRATEGIES.get(merge_method)
        if strategy is None:
            raise ValueError(f"Unsupported merge method for Bitbucket: {merge_method!r}")
        payload: dict[str, Any] = {"type": "pullrequest", "merge_strategy": strategy}
        if commit_title:
            payload["message"] = commit_title
        async with self._client() as client:
            resp = await client.post(
                self._url(f"{self._repo_path}/pullrequests/{pr_number}/merge"), json=payload
            )
            if resp.status_code in (_BAD_REQUEST, _CONFLICT):
                return False
            if resp.status_code == _ACCEPTED:
                return await self._await_merge(client, pr_number, resp)
            resp.raise_for_status()
            return True

    async def _await_merge(
        self, client: httpx.AsyncClient, pr_number: int, accepted: httpx.Response
    ) -> bool:
        """Poll the merge task named by the 202 ``Location``, same origin only."""
        target = urljoin(str(accepted.url), accepted.headers.get("location", ""))
        if not accepted.headers.get("location") or not self._same_origin(target):
            raise BitbucketMergePending(
                f"PR {pr_number}: merge accepted but no pollable task location"
            )
        for attempt in range(_MERGE_POLL_ATTEMPTS):
            if attempt:
                await asyncio.sleep(_MERGE_POLL_DELAY)
            resp = await client.get(target)
            if resp.status_code in (_BAD_REQUEST, _CONFLICT):
                return False  # the task reports a merge conflict / failed check
            resp.raise_for_status()
            status = resp.json().get("task_status")
            if status == "SUCCESS":
                return True
            if status != "PENDING":
                return False
        raise BitbucketMergePending(
            f"PR {pr_number}: merge still pending after {_MERGE_POLL_ATTEMPTS} polls"
        )

    async def close_pr(self, pr_number: int, comment: str | None = None) -> bool:
        """Decline the PR, after an optional closing comment."""
        async with self._client() as client:
            if comment:
                await self._comment(client, pr_number, comment)
            resp = await client.post(
                self._url(f"{self._repo_path}/pullrequests/{pr_number}/decline")
            )
            if resp.status_code in (_BAD_REQUEST, _CONFLICT):
                return False
            resp.raise_for_status()
            return True

    async def enable_auto_merge(self, pr_number: int, merge_method: str = "squash") -> bool:
        raise NotImplementedError(
            "enable_auto_merge is not available for Bitbucket Cloud: it has no "
            "auto-merge API. Use the merge_policy decision and merge_pr."
        )
