"""GitHub JSON shapes shared by both GitHub providers (Factory#370).

The fleet has two GitHub providers — :class:`~providers.github_provider.GitHubProvider`
over the ``gh`` CLI, and :class:`~providers.http_github_provider.HttpGitHubProvider`
over REST with an explicit token. This module holds the parsing they genuinely
share, so adding the second one did not paste the first one's helpers.

**What is shared, and why only this much.** Comments are identical between the
two because the gh provider reads them with ``gh api``, which returns the REST
payload verbatim — same ``user``/``created_at``/``html_url`` keys. So the comment
parser, the issue-number-from-URL helper and the timestamp parser are one
implementation.

**What is deliberately NOT shared: the issue parser.** The two wire formats
differ, and they differ silently:

======================  =========================  ==========================
field                   ``gh`` CLI JSON            REST JSON
======================  =========================  ==========================
author                  ``author``                 ``user``
created / updated       ``createdAt``/``updatedAt``  ``created_at``/``updated_at``
web link                ``url``                    ``html_url``
======================  =========================  ==========================

A single "unified" issue parser would therefore read ``None`` for half its
fields against whichever format it was not written for, and still return a
populated-looking ``IssueData``. That is a data-loss bug that no type checker
catches, so the two parsers stay separate on purpose. **Do not merge them
because a clone detector says they look alike** — the similarity is structural,
not semantic.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from .protocol import IssueComment, ProviderCommentError, ProviderType, oldest_first

# GitHub caps both the issue and the comment endpoints at 100 per page.
PAGE_SIZE = 100

# The page cap bounds a repository-wide comment fetch (50 x 100 = 5000 comments)
# rather than paging forever against an unknown history size. Hitting it is an
# error, never a silent truncation.
COMMENT_MAX_PAGES = 50


def parse_datetime(value: Any) -> datetime:
    """Tolerant ISO parse: a missing or malformed timestamp is not worth failing
    a sync over, since neither provider treats timestamps as authoritative."""
    if not isinstance(value, str) or not value:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.now(UTC)


def issue_number_from_url(url: str) -> int | None:
    """Recover the issue number from a comment's ``issue_url``.

    The repository-wide comments endpoint does not carry the issue number as a
    field; the only link back is the URL the comment was made against.
    """
    tail = (url or "").rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def parse_comment(data: dict[str, Any], issue_number: int) -> IssueComment:
    """A GitHub comment payload as the provider-neutral :class:`IssueComment`.

    Shared by both providers because ``gh api`` returns the REST payload
    unchanged — unlike the issue endpoints, where the CLI reshapes the keys.
    """
    user = data.get("user") or {}
    author = user.get("login", "") if isinstance(user, dict) else str(user)
    created = data.get("created_at")
    return IssueComment(
        id=str(data.get("id", "")),
        issue_number=issue_number,
        author=author,
        body=data.get("body") or "",
        created_at=parse_datetime(created),
        updated_at=parse_datetime(data.get("updated_at") or created),
        url=data.get("html_url") or "",
        provider=ProviderType.GITHUB,
        raw_data=data,
    )


def milestone_title(milestone: Any) -> str | None:
    """GitHub's milestone object as the plain title a caller stores."""
    if isinstance(milestone, dict):
        title = milestone.get("title")
        return title if isinstance(title, str) else None
    return milestone if isinstance(milestone, str) else None


async def collect_comment_pages(
    fetch_page: Callable[[int], Awaitable[Any]],
    path: str,
) -> list[dict[str, Any]]:
    """Page a GitHub comments endpoint to completion, or FAIL — never truncate.

    Shared by both providers (Factory#370). They differ only in transport —
    ``gh api`` versus httpx — so ``fetch_page`` takes a 1-based page number and
    returns the decoded body; everything about *when to stop* and *what counts
    as a failure* lives here, once.

    That policy is the part worth having in one place:

    * a short page ends the walk (fewer than ``PAGE_SIZE`` items means the last
      one);
    * a non-list body is an error, not an empty result;
    * exhausting ``COMMENT_MAX_PAGES`` is an error too.

    The last two matter because **a truncated thread stored as a whole one is
    indistinguishable from a short one**. Returning what was collected so far
    would silently corrupt the caller's copy; raising lets it keep the complete
    copy it already had.
    """
    collected: list[dict[str, Any]] = []
    for page in range(1, COMMENT_MAX_PAGES + 1):
        try:
            batch = await fetch_page(page)
        except ProviderCommentError:
            raise
        except Exception as exc:
            raise ProviderCommentError(
                f"GitHub comment read failed for {path} (page {page}): {exc}"
            ) from exc
        if not isinstance(batch, list):
            raise ProviderCommentError(
                f"GitHub returned a non-list comment page for {path}: {type(batch).__name__}"
            )
        collected.extend(item for item in batch if isinstance(item, dict))
        if len(batch) < PAGE_SIZE:
            return collected
    raise ProviderCommentError(
        f"GitHub comment read for {path} exceeded {COMMENT_MAX_PAGES} pages; "
        "narrow the `since` window rather than accept a truncated thread"
    )


def group_bulk_comments(
    raw: list[dict[str, Any]], wanted: list[int]
) -> dict[int, list[IssueComment]]:
    """Fan a repository-wide comment stream back out per issue (Factory#370).

    ``GET /repos/{repo}/issues/comments`` returns every issue comment in the
    repository in one paginated stream and honours ``since`` server-side. That
    is what makes an incremental poll affordable — one call covers a whole board
    instead of one call per card — and it is why both providers have a bulk
    method rather than a loop at the call site.

    The stream answers for issues the caller does not track, and for pull-request
    comments, which share the endpoint. Those are DROPPED rather than stored
    against nothing: ``wanted`` is the caller's list, and an issue in it with no
    comments must still come back as an empty list, or the caller cannot tell
    "no comments" from "not asked about".

    Note what is NOT here: the ``since is None`` case. Without a window to narrow,
    the repository-wide stream would page over the project's entire comment
    history to answer a question about a handful of issues, so each provider
    fans out per issue instead — bounded by the caller's list rather than by the
    size of the repository. That fan-out is transport-specific, so it stays with
    each provider.
    """
    grouped: dict[int, list[IssueComment]] = {number: [] for number in wanted}
    for item in raw:
        number = issue_number_from_url(item.get("issue_url", ""))
        if number in grouped:
            grouped[number].append(parse_comment(item, number))
    return {number: oldest_first(comments) for number, comments in grouped.items()}
