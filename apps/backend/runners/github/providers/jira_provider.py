"""
Jira Cloud over REST v3, as a WORK-ITEM provider only (RFC-0022 Phase 3, Factory#3775)
=======================================================================================

Jira has issues and no repositories, pull requests or diffs, so this class
implements :class:`~.protocol.WorkItemProvider` and nothing else. It deliberately
has no ``fetch_pr`` / ``merge_pr`` stubs: a caller that needs the code seam must
hold a :class:`~.protocol.CodeProvider`, and a type checker now says so.

The RFC's decisions, in the order they bite:

* **Closed means status category Done** (RFC section 3, option 1). Every Jira
  status belongs to ``new`` / ``indeterminate`` / ``done``; that is instance-wide
  and not configurable. A category this code has never seen reads as ``open``:
  the failure of the other direction (finished work looking open) is the one
  that re-plans a finished item, but the failure of calling unfinished work
  closed is silent data loss, so unknown stays open.
* **Keys are strings.** ``PROJ-123`` is never turned into an integer.
* **A PR cannot close a Jira issue.** :meth:`close_issue` performs the workflow
  transition itself and raises :class:`JiraTransitionError` when the issue has no
  transition into a Done-category status, rather than reporting success.
* **Labels degrade instead of crashing** (RFC section 9, RFC-0011 intake). Jira
  creates a label on first use, so ``create_label`` is a no-op. Applying one can
  be refused on a locked-down project (400/403); that is logged and skipped, so a
  board write is not lost over a label. 401, 404, 429 and 5xx still raise.
* **Never log the credential.** Email and token are ``repr=False``, and every
  error message is built from Jira's own error text, never from the request.

Not proven against a real Jira yet: the owner supplies a test site (Factory#3775).
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

import httpx

from .protocol import (
    FanoutCommentsMixin,
    IssueComment,
    IssueData,
    IssueFilters,
    IssueRef,
    LabelData,
    ProviderCommentError,
    ProviderType,
    oldest_first,
    to_utc,
)

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10.0
_PAGE_SIZE = 100
# Pages a search or a comment walk will take before giving up. Reaching it is an
# error, never a silent truncation.
_MAX_PAGES = 50
_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*-[0-9]+$")
_PROJECT_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
# What the parser reads. The enhanced search returns ids only without it.
_FIELDS = "summary,description,status,labels,assignee,reporter,created,updated"
_DONE = "done"
_Names = list[str] | None
_DEGRADED_LABEL_STATUSES = (HTTPStatus.BAD_REQUEST, HTTPStatus.FORBIDDEN)


class JiraError(httpx.HTTPStatusError):
    """A Jira call that failed, with a message that cannot contain the credential.

    A subclass of ``httpx.HTTPStatusError`` so callers that already branch on
    ``exc.response.status_code`` (CFactory's credential-rejection check) keep working.
    """

    def __init__(self, message: str, response: httpx.Response) -> None:
        super().__init__(message, request=response.request, response=response)
        self.status = response.status_code
        self.retry_after = response.headers.get("Retry-After")


class JiraTransitionError(RuntimeError):
    """The issue cannot be moved to Done (no such transition, or it needs fields)."""


def _redact(text: str, secrets: tuple[str, ...]) -> str:
    """Jira can echo a request back in an error; never let that carry the credential."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


def _detail(resp: httpx.Response) -> str:
    """Jira's own explanation (``errorMessages`` and per-field ``errors``), bounded."""
    try:
        body = resp.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    parts = [str(m) for m in body.get("errorMessages") or []]
    parts += [f"{name}: {msg}" for name, msg in (body.get("errors") or {}).items()]
    return "; ".join(parts)[:500]


def _raise_for_status(resp: httpx.Response, what: str, secrets: tuple[str, ...] = ()) -> None:
    if resp.is_success:
        return
    code = resp.status_code
    detail = _redact(_detail(resp), secrets)
    if code == HTTPStatus.UNAUTHORIZED:
        message = f"Jira rejected the credential (401) on {what}"
    elif code == HTTPStatus.FORBIDDEN:
        message = f"Jira denied permission (403) on {what}"
    elif code == HTTPStatus.NOT_FOUND:
        message = f"Jira: not found, or not visible to this credential (404) on {what}"
    elif code == HTTPStatus.TOO_MANY_REQUESTS:
        message = f"Jira rate limit (429) on {what}; Retry-After={resp.headers.get('Retry-After')}"
    else:
        message = f"Jira error {code} on {what}"
    raise JiraError(f"{message}: {detail}" if detail else message, resp)


def _jql_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _label(name: str) -> str:
    """Jira rejects whitespace in a label."""
    return re.sub(r"\s+", "-", name.strip())


# ── Atlassian Document Format ────────────────────────────────────────────────


def to_adf(text: str) -> dict[str, Any]:
    """Plain text as an ADF document: one paragraph per line, blank lines dropped."""
    paragraphs = [
        {"type": "paragraph", "content": [{"type": "text", "text": line}]}
        for line in text.splitlines()
        if line.strip()
    ]
    return {"type": "doc", "version": 1, "content": paragraphs}


def adf_to_text(node: Any) -> str:  # noqa: PLR0911
    """Flatten ADF (or a plain string, which older payloads carry) to text."""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    kind = node.get("type")
    if kind == "text":
        text = str(node.get("text") or "")
        for mark in node.get("marks") or []:
            href = (mark.get("attrs") or {}).get("href") if isinstance(mark, dict) else None
            if mark.get("type") == "link" and href and href != text:
                return f"{text} ({href})"
        return text
    if kind == "hardBreak":
        return "\n"
    if kind == "mention":
        return str((node.get("attrs") or {}).get("text") or "")
    if kind == "inlineCard":
        return str((node.get("attrs") or {}).get("url") or "")
    children = node.get("content") or []
    if kind == "listItem":
        inner = "\n".join(adf_to_text(c).rstrip("\n") for c in children)
        return "- " + inner.replace("\n", "\n  ")
    if kind in {"bulletList", "orderedList"}:
        return "\n".join(adf_to_text(c).rstrip("\n") for c in children) + "\n"
    inner = "".join(adf_to_text(c) for c in children)
    if kind in {"paragraph", "heading", "codeBlock", "blockquote", "bulletList", "orderedList"}:
        return inner.rstrip("\n") + "\n" if kind in {"paragraph", "heading", "codeBlock"} else inner
    return inner


def _parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        return datetime.now(UTC)
    try:
        return to_utc(datetime.fromisoformat(value))
    except ValueError:
        return datetime.now(UTC)


def _state(fields: dict[str, Any]) -> str:
    category = ((fields.get("status") or {}).get("statusCategory") or {}).get("key")
    return "closed" if category == _DONE else "open"


@dataclass
class JiraProvider(FanoutCommentsMixin):
    """Jira Cloud work items, authenticated by an email and an API token.

    Usage::

        provider = JiraProvider(_repo="PROJ", _email=email, _api_token=token,
                                _base_url="https://acme.atlassian.net")

    ``_repo`` is the Jira project key. ``_transport`` is for tests.
    """

    _repo: str
    # repr=False: a dataclass renders every field, and these end up in tracebacks.
    _email: str | None = field(default=None, repr=False)
    _api_token: str | None = field(default=None, repr=False)
    _base_url: str = ""
    _issue_type: str = "Task"
    _transport: httpx.AsyncBaseTransport | None = None

    def __post_init__(self) -> None:
        if not _PROJECT_RE.match(self._repo):
            raise ValueError(f"not a Jira project key: {self._repo!r}")
        if not self._base_url:
            raise ValueError("a Jira site base URL is required (https://<site>.atlassian.net)")

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.JIRA

    @property
    def repo(self) -> str:
        return self._repo

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={"Accept": "application/json"},
            auth=httpx.BasicAuth(self._email or "", self._api_token or ""),
            base_url=self._base_url,
            timeout=_TIMEOUT_SECONDS,
            transport=self._transport,
            # The Basic credential must not follow a redirect to another host.
            follow_redirects=False,
        )

    def _secrets(self) -> tuple[str, ...]:
        basic = base64.b64encode(f"{self._email}:{self._api_token}".encode()).decode()
        return (self._api_token or "", self._email or "", basic)

    async def _request(
        self, method: str, path: str, what: str, *, expect_json: bool = True, **kwargs: Any
    ) -> Any:
        async with self._client() as client:
            resp = await client.request(method, f"/rest/api/3{path}", **kwargs)
        _raise_for_status(resp, what, self._secrets())
        if not expect_json or not resp.content:
            return None
        return resp.json()

    @staticmethod
    def _key(ref: IssueRef | int) -> str:
        key = str(ref)
        if not _KEY_RE.match(key):
            raise ValueError(f"not a Jira issue key: {ref!r} (expected e.g. PROJ-123)")
        return key

    # ── issues ───────────────────────────────────────────────────────────────

    async def fetch_issue(self, number: IssueRef) -> IssueData:
        key = self._key(number)
        data = await self._request(
            "GET", f"/issue/{key}", f"fetch_issue {key}", params={"fields": _FIELDS}
        )
        return self._parse_issue(data)

    async def fetch_issues(self, filters: IssueFilters | None = None) -> list[IssueData]:
        """List the project's issues, paging with ``nextPageToken`` up to ``limit``."""
        filters = filters or IssueFilters()
        clauses = [f"project = {_jql_quote(self._repo)}"]
        if filters.state == "open":
            clauses.append("statusCategory != Done")
        elif filters.state == "closed":
            clauses.append("statusCategory = Done")
        clauses += [f"labels = {_jql_quote(_label(name))}" for name in filters.labels]
        if filters.assignee:
            clauses.append(f"assignee = {_jql_quote(filters.assignee)}")
        if filters.author:
            clauses.append(f"reporter = {_jql_quote(filters.author)}")
        if filters.since is not None:
            # Epoch milliseconds: a formatted date is read in the ACCOUNT's timezone.
            millis = int(to_utc(filters.since).timestamp() * 1000)
            clauses.append(f"updated >= {millis}")
        jql = " AND ".join(clauses) + " ORDER BY created ASC"

        issues: list[IssueData] = []
        token: str | None = None
        for _ in range(_MAX_PAGES):
            params: dict[str, Any] = {
                "jql": jql,
                "fields": _FIELDS,
                "maxResults": min(_PAGE_SIZE, filters.limit - len(issues)),
            }
            if token:
                params["nextPageToken"] = token
            page = await self._request("GET", "/search/jql", "fetch_issues", params=params)
            issues += [self._parse_issue(item) for item in page.get("issues") or []]
            token = page.get("nextPageToken")
            if not token or len(issues) >= filters.limit:
                return issues[: filters.limit]
        raise _too_many_pages()

    async def create_issue(
        self,
        title: str,
        body: str,
        labels: _Names = None,
        assignees: _Names = None,
    ) -> IssueData:
        """Create in this project, then read it back so the caller gets the canonical record."""
        fields: dict[str, Any] = {
            "project": {"key": self._repo},
            "summary": title,
            "description": to_adf(body),
            "issuetype": {"name": self._issue_type},
        }
        if labels:
            fields["labels"] = [_label(name) for name in labels]
        if assignees:
            fields["assignee"] = {"accountId": self._single_assignee(assignees)}
        created = await self._request("POST", "/issue", "create_issue", json={"fields": fields})
        return await self.fetch_issue(created["key"])

    async def close_issue(self, number: IssueRef, comment: str | None = None) -> bool:
        """Transition to a Done-category status. Raises rather than pretending."""
        key = self._key(number)
        current = await self.fetch_issue(key)
        if current.state == "closed":
            if comment:
                await self.add_comment(key, comment)
            return True
        listing = await self._request("GET", f"/issue/{key}/transitions", f"transitions {key}")
        transitions = listing.get("transitions") or []
        chosen = next(
            (
                t
                for t in transitions
                if ((t.get("to") or {}).get("statusCategory") or {}).get("key") == _DONE
            ),
            None,
        )
        if chosen is None:
            names = ", ".join(str(t.get("name")) for t in transitions) or "none"
            raise JiraTransitionError(
                f"{key} has no transition into a Done-category status (available: {names})"
            )
        payload: dict[str, Any] = {"transition": {"id": str(chosen["id"])}}
        if comment:
            payload["update"] = {"comment": [{"add": {"body": to_adf(comment)}}]}
        try:
            await self._request(
                "POST", f"/issue/{key}/transitions", f"close {key}", expect_json=False, json=payload
            )
        except JiraError as exc:
            if exc.status == HTTPStatus.BAD_REQUEST:
                raise JiraTransitionError(
                    f"{key}: transition {chosen.get('name')!r} was refused, likely because it "
                    f"requires fields the factory cannot supply: {exc}"
                ) from exc
            raise
        return True

    # ── comments ─────────────────────────────────────────────────────────────

    async def add_comment(self, issue_or_pr_number: IssueRef, body: str) -> int:
        key = self._key(issue_or_pr_number)
        data = await self._request(
            "POST", f"/issue/{key}/comment", f"add_comment {key}", json={"body": to_adf(body)}
        )
        return int(data["id"])

    async def fetch_comments(
        self, issue_number: IssueRef, since: datetime | None = None
    ) -> list[IssueComment]:
        """The whole thread, oldest first. ``since`` is applied here: Jira has no such filter."""
        key = self._key(issue_number)
        raw: list[dict[str, Any]] = []
        start = 0
        try:
            for _ in range(_MAX_PAGES):
                page = await self._request(
                    "GET",
                    f"/issue/{key}/comment",
                    f"fetch_comments {key}",
                    params={"startAt": start, "maxResults": _PAGE_SIZE, "orderBy": "created"},
                )
                batch = page.get("comments") or []
                raw += batch
                start += len(batch)
                if not batch or start >= int(page.get("total") or 0):
                    break
            else:
                raise ProviderCommentError(f"{key}: comment thread exceeds {_MAX_PAGES} pages")
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise ProviderCommentError(f"{key}: could not read the comment thread: {exc}") from exc
        comments = [self._parse_comment(item, key) for item in raw]
        if since is not None:
            cutoff = to_utc(since)
            comments = [c for c in comments if c.updated_at > cutoff]
        return oldest_first(comments)

    # ── assignment and labels ────────────────────────────────────────────────

    @staticmethod
    def _single_assignee(assignees: list[str]) -> str:
        if len(assignees) != 1:
            raise ValueError("Jira issues have one assignee")
        if assignees[0].lower() == "copilot":
            raise NotImplementedError("the Copilot agent alias is GitHub-specific")
        return assignees[0]

    async def assign_to_user(self, issue_number: IssueRef, assignees: list[str]) -> None:
        """Assign by account id (Jira identity is opaque, not a username)."""
        key = self._key(issue_number)
        if not assignees:
            return
        await self._request(
            "PUT",
            f"/issue/{key}/assignee",
            f"assign {key}",
            expect_json=False,
            json={"accountId": self._single_assignee(assignees)},
        )

    async def _edit_labels(self, ref: IssueRef, op: str, labels: list[str]) -> None:
        key = self._key(ref)
        if not labels:
            return
        update = {"labels": [{op: _label(name)} for name in labels]}
        try:
            await self._request(
                "PUT",
                f"/issue/{key}",
                f"{op} labels {key}",
                expect_json=False,
                json={"update": update},
            )
        except JiraError as exc:
            if exc.status not in _DEGRADED_LABEL_STATUSES:
                raise
            logger.warning("jira: labels not changed on %s (%s)", key, exc)

    async def apply_labels(self, issue_or_pr_number: IssueRef, labels: list[str]) -> None:
        await self._edit_labels(issue_or_pr_number, "add", labels)

    async def remove_labels(self, issue_or_pr_number: IssueRef, labels: list[str]) -> None:
        await self._edit_labels(issue_or_pr_number, "remove", labels)

    async def create_label(self, label: LabelData) -> None:
        """No-op: Jira has no label entity, a label exists once an issue carries it."""

    async def list_labels(self) -> list[LabelData]:
        names: list[str] = []
        start = 0
        for _ in range(_MAX_PAGES):
            page = await self._request(
                "GET", "/label", "list_labels", params={"startAt": start, "maxResults": 1000}
            )
            values = page.get("values") or []
            names += [str(v) for v in values]
            start += len(values)
            if page.get("isLast", True) or not values:
                return [LabelData(name=n, color="") for n in names]
        raise _too_many_pages()

    # ── project ──────────────────────────────────────────────────────────────

    async def get_repository_info(self) -> dict[str, Any]:
        """One authenticated read of the project: proves host, credential and key."""
        info = await self._request("GET", f"/project/{self._repo}", "get_repository_info")
        return dict(info) if isinstance(info, dict) else {}

    # ── parsing ──────────────────────────────────────────────────────────────

    def _parse_issue(self, issue: Any) -> IssueData:
        issue = dict(issue) if isinstance(issue, dict) else {}
        key = issue.get("key")
        if not isinstance(key, str) or not _KEY_RE.match(key):
            raise ValueError(f"{self._repo}: response carried no issue key")
        fields = issue.get("fields") or {}
        reporter = fields.get("reporter") or {}
        return IssueData(
            number=key,
            title=str(fields.get("summary") or ""),
            body=adf_to_text(fields.get("description")).strip(),
            author=str(reporter.get("displayName") or reporter.get("accountId") or ""),
            state=_state(fields),
            labels=[str(x) for x in fields.get("labels") or []],
            created_at=_parse_datetime(fields.get("created")),
            updated_at=_parse_datetime(fields.get("updated")),
            url=f"{self._base_url.rstrip('/')}/browse/{key}",
            assignees=[a for a in [(fields.get("assignee") or {}).get("accountId")] if a],
            milestone=None,
            provider=ProviderType.JIRA,
            raw_data=issue,
        )

    def _parse_comment(self, data: dict[str, Any], key: str) -> IssueComment:
        author = data.get("author") or {}
        comment_id = str(data.get("id") or "")
        return IssueComment(
            id=comment_id,
            issue_number=key,
            author=str(author.get("displayName") or author.get("accountId") or ""),
            body=adf_to_text(data.get("body")).strip(),
            created_at=_parse_datetime(data.get("created")),
            updated_at=_parse_datetime(data.get("updated") or data.get("created")),
            url=f"{self._base_url.rstrip('/')}/browse/{key}?focusedCommentId={comment_id}",
            provider=ProviderType.JIRA,
            raw_data=data,
            avatar_url=str((author.get("avatarUrls") or {}).get("48x48") or ""),
        )


def _too_many_pages() -> RuntimeError:
    return RuntimeError(f"Jira listing exceeded {_MAX_PAGES} pages; refusing to truncate")
