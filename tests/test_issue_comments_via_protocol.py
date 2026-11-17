"""The issue-comment route reads through the provider protocol (Factory#3771).

``_get_provider_issue_comments`` used to branch on ``provider_type`` and call the
raw ``provider.api_get`` for GitLab notes, Azure DevOps comments and GitHub. It
now asks the protocol: ``fetch_comments`` for the issue thread and
``fetch_pr_comments`` when the number names a pull request, so the route holds no
provider-specific URL.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))
_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from runners.github.providers.protocol import IssueComment  # noqa: E402
from server.routes.github import _get_provider_issue_comments  # noqa: E402


def _comment(comment_id: str, avatar: str = "") -> IssueComment:
    return IssueComment(
        id=comment_id,
        issue_number=7,
        author="alice",
        body="ship it",
        created_at=datetime(2026, 7, 20, 9, 0, tzinfo=UTC),
        updated_at=datetime(2026, 7, 21, 9, 0, tzinfo=UTC),
        url="",
        avatar_url=avatar,
    )


def _provider(**methods) -> MagicMock:
    provider = MagicMock()
    provider.api_get = AsyncMock(side_effect=AssertionError("raw api_get must not be used"))
    for name, value in methods.items():
        setattr(provider, name, value)
    return provider


@pytest.mark.asyncio
async def test_issue_thread_is_mapped_to_the_frontend_shape():
    provider = _provider(
        fetch_comments=AsyncMock(return_value=[_comment("12", "https://a/a.png")]),
        fetch_pr_comments=AsyncMock(side_effect=AssertionError("not needed")),
    )

    result = await _get_provider_issue_comments(provider, 7)

    assert result == [
        {
            "id": 12,
            "body": "ship it",
            "user": {"login": "alice", "avatar_url": "https://a/a.png"},
            "created_at": "2026-07-20T09:00:00Z",
            "updated_at": "2026-07-21T09:00:00Z",
        }
    ]
    provider.fetch_comments.assert_awaited_once_with("7")


@pytest.mark.asyncio
async def test_a_failed_issue_read_falls_back_to_the_pull_request_thread():
    provider = _provider(
        fetch_comments=AsyncMock(side_effect=RuntimeError("404")),
        fetch_pr_comments=AsyncMock(return_value=[_comment("5")]),
    )

    result = await _get_provider_issue_comments(provider, 7)

    assert [c["id"] for c in result] == [5]
    provider.fetch_pr_comments.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_two_failures_yield_no_comments_instead_of_an_error():
    provider = _provider(
        fetch_comments=AsyncMock(side_effect=RuntimeError("boom")),
        fetch_pr_comments=AsyncMock(side_effect=RuntimeError("boom")),
    )

    assert await _get_provider_issue_comments(provider, 7) == []


@pytest.mark.asyncio
async def test_an_empty_issue_thread_does_not_trigger_the_pr_fallback():
    provider = _provider(
        fetch_comments=AsyncMock(return_value=[]),
        fetch_pr_comments=AsyncMock(side_effect=AssertionError("not needed")),
    )

    assert await _get_provider_issue_comments(provider, 7) == []
