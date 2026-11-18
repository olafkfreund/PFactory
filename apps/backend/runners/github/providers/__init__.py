"""
Git Provider Abstraction
========================

Abstracts git hosting providers (GitHub, GitLab, Bitbucket) behind a common interface.

Usage:
    from providers import GitProvider, get_provider

    # Get provider based on config
    provider = get_provider(config)

    # Fetch PR data
    pr = await provider.fetch_pr(123)

    # Post review
    await provider.post_review(123, review)
"""

from .azure_devops_provider import AzureDevOpsProvider
from .factory import get_provider, get_work_item_provider, register_provider
from .github_provider import GitHubProvider
from .gitlab_provider import GitLabProvider
from .http_github_provider import HttpGitHubProvider
from .jira_provider import JiraProvider
from .protocol import (
    CodeProvider,
    FanoutCommentsMixin,
    GitProvider,
    IssueComment,
    IssueData,
    IssueFilters,
    IssueRef,
    PRData,
    PRFilters,
    ProviderCommentError,
    ProviderType,
    ReviewData,
    ReviewFinding,
    WorkItemProvider,
    as_issue_int,
    fanout_comments,
    oldest_first,
)

__all__ = [
    # Protocol
    "GitProvider",
    "WorkItemProvider",
    "CodeProvider",
    "PRData",
    "IssueData",
    "IssueComment",
    "ReviewData",
    "ReviewFinding",
    "IssueFilters",
    "PRFilters",
    "ProviderType",
    "ProviderCommentError",
    "FanoutCommentsMixin",
    "IssueRef",
    "as_issue_int",
    "fanout_comments",
    "oldest_first",
    # Implementations
    "GitHubProvider",
    "HttpGitHubProvider",
    "JiraProvider",
    "GitLabProvider",
    "AzureDevOpsProvider",
    # Factory
    "get_provider",
    "get_work_item_provider",
    "register_provider",
]
