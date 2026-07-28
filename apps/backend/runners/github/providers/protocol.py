"""
Git Provider Protocol
=====================

Defines the abstract interface that all git hosting providers must implement.
Enables support for GitHub, Bitbucket, and other providers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable


class ProviderType(str, Enum):
    """Supported git hosting providers."""

    GITHUB = "github"
    GITLAB = "gitlab"
    BITBUCKET = "bitbucket"
    GITEA = "gitea"
    AZURE_DEVOPS = "azure_devops"


# ============================================================================
# DATA MODELS
# ============================================================================


@dataclass
class PRData:
    """
    Pull/Merge Request data structure.

    Provider-agnostic representation of a pull request.
    """

    number: int
    title: str
    body: str
    author: str
    state: str  # open, closed, merged
    source_branch: str
    target_branch: str
    additions: int
    deletions: int
    changed_files: int
    files: list[dict[str, Any]]
    diff: str
    url: str
    created_at: datetime
    updated_at: datetime
    labels: list[str] = field(default_factory=list)
    reviewers: list[str] = field(default_factory=list)
    is_draft: bool = False
    mergeable: bool = True
    provider: ProviderType = ProviderType.GITHUB

    # Provider-specific raw data (for debugging)
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class IssueData:
    """
    Issue/Ticket data structure.

    Provider-agnostic representation of an issue.
    """

    number: int
    title: str
    body: str
    author: str
    state: str  # open, closed
    labels: list[str]
    created_at: datetime
    updated_at: datetime
    url: str
    assignees: list[str] = field(default_factory=list)
    milestone: str | None = None
    provider: ProviderType = ProviderType.GITHUB

    # Provider-specific raw data
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class IssueComment:
    """
    A single comment on an issue.

    Provider-agnostic representation of a discussion entry (Factory#375). The
    body of an issue is only half the story — the decision usually lives in the
    thread — so the board needs to read comments without ever branching on
    ``provider``. Every field below is populated identically by all three
    providers; ``id`` keeps the provider-native identifier (stringified, because
    GitHub/GitLab/ADO all number them independently) so a consumer can dedupe an
    incremental re-fetch against what it already stored.
    """

    id: str
    issue_number: int
    author: str
    body: str
    created_at: datetime
    updated_at: datetime
    url: str
    provider: ProviderType = ProviderType.GITHUB

    # Provider-specific raw data
    raw_data: dict[str, Any] = field(default_factory=dict)


class ProviderCommentError(RuntimeError):
    """A comment read failed or could not be completed.

    Raised instead of returning what was collected so far. A partial thread is
    indistinguishable from a short one once stored, and "this issue has no
    discussion" is exactly the wrong thing to persist for an issue whose
    discussion simply failed to download.
    """


def to_utc(dt: datetime) -> datetime:
    """Treat a naive datetime as UTC so ``since`` comparisons never raise."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def to_iso_utc(dt: datetime) -> str:
    """Render a datetime as the ISO-8601 UTC form the provider APIs expect."""
    return to_utc(dt).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def oldest_first(comments: list[IssueComment]) -> list[IssueComment]:
    """Normalise comment order.

    Providers return threads in whatever order their pagination needed (GitHub
    ascending, GitLab and ADO newest-first for the ``since`` early stop). The
    protocol promises one order to the caller, so every implementation ends on
    this rather than each sorting for itself.
    """
    return sorted(comments, key=lambda comment: comment.created_at)


@runtime_checkable
class SupportsFetchComments(Protocol):
    """The single method :func:`fanout_comments` actually needs.

    Narrower than :class:`GitProvider` on purpose. FanoutCommentsMixin passes
    ``self`` here, and a mixin is not a GitProvider — it supplies one method to
    a class that is one. Typing the parameter as the full provider made that
    call a strict-mypy arg-type error and forced the mixin to claim a contract
    it does not implement. Stating the real requirement fixes it without a cast
    or an ignore, and every GitProvider still satisfies it structurally.
    """

    async def fetch_comments(
        self,
        issue_number: int,
        since: datetime | None = None,
    ) -> list[IssueComment]: ...


async def fanout_comments(
    provider: SupportsFetchComments,
    issue_numbers: list[int],
    since: datetime | None = None,
) -> dict[int, list[IssueComment]]:
    """Per-issue fallback for providers with no bulk comment endpoint.

    Costs exactly ``len(set(issue_numbers))`` API calls — bounded and
    predictable, which is the point: the alternative failure mode is an
    unbounded walk of a whole repository's comment history. Sequential on
    purpose; concurrency here would spend the rate-limit budget faster, not save
    any calls. Deduping and ordering live here so callers stay one line.
    """
    return {
        number: await provider.fetch_comments(number, since=since)
        for number in sorted({int(number) for number in issue_numbers})
    }


class FanoutCommentsMixin:
    """``fetch_comments_bulk`` for providers whose API has no batch endpoint.

    GitLab exposes notes only per resource (there is no project-wide notes
    endpoint) and Azure DevOps has no batch comments API (``workitemsbatch``
    expands fields, relations and links, not comments). Both therefore answer a
    bulk read the only way their API allows — one call per issue — so the
    fallback lives here once instead of being pasted into each provider.
    """

    if TYPE_CHECKING:
        # Type-only declaration of what this mixin REQUIRES of its host class.
        # It is a mixin, so it never implements fetch_comments itself - but it
        # calls it, and without saying so `self` satisfies no protocol and the
        # fanout_comments call below is a strict-mypy arg-type error. Declaring
        # the requirement is also the honest documentation: a class that mixes
        # this in must provide fetch_comments. No runtime effect.
        async def fetch_comments(
            self,
            issue_number: int,
            since: datetime | None = None,
        ) -> list[IssueComment]: ...

    async def fetch_comments_bulk(
        self, issue_numbers: list[int], since: datetime | None = None
    ) -> dict[int, list[IssueComment]]:
        """One call per issue; single-paged per issue when ``since`` is set."""
        return await fanout_comments(self, issue_numbers, since)


@dataclass
class ReviewFinding:
    """
    Individual finding in a code review.
    """

    id: str
    severity: str  # critical, high, medium, low, info
    category: str  # security, bug, performance, style, etc.
    title: str
    description: str
    file: str | None = None
    line: int | None = None
    end_line: int | None = None
    suggested_fix: str | None = None
    confidence: float = 0.8  # P3-4: Confidence scoring
    evidence: list[str] = field(default_factory=list)
    fixable: bool = False


@dataclass
class ReviewData:
    """
    Code review data structure.

    Provider-agnostic representation of a review.
    """

    pr_number: int
    event: str  # approve, request_changes, comment
    body: str
    findings: list[ReviewFinding] = field(default_factory=list)
    inline_comments: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class IssueFilters:
    """
    Filters for listing issues.
    """

    state: str = "open"
    labels: list[str] = field(default_factory=list)
    author: str | None = None
    assignee: str | None = None
    since: datetime | None = None
    limit: int = 100
    include_prs: bool = False


@dataclass
class PRFilters:
    """
    Filters for listing pull requests.
    """

    state: str = "open"
    labels: list[str] = field(default_factory=list)
    author: str | None = None
    base_branch: str | None = None
    head_branch: str | None = None
    since: datetime | None = None
    limit: int = 100


@dataclass
class LabelData:
    """
    Label data structure.
    """

    name: str
    color: str
    description: str = ""


# ============================================================================
# PROVIDER PROTOCOL
# ============================================================================


@runtime_checkable
class GitProvider(Protocol):
    """
    Abstract protocol for git hosting providers.

    All provider implementations must implement these methods.
    This enables the system to work with GitHub, Bitbucket, etc.
    """

    @property
    def provider_type(self) -> ProviderType:
        """Get the provider type."""
        ...

    @property
    def repo(self) -> str:
        """Get the repository in owner/repo format."""
        ...

    # -------------------------------------------------------------------------
    # Pull Request Operations
    # -------------------------------------------------------------------------

    async def fetch_pr(self, number: int) -> PRData:
        """
        Fetch a pull request by number.

        Args:
            number: PR/MR number

        Returns:
            PRData with full PR details including diff
        """
        ...

    async def fetch_prs(self, filters: PRFilters | None = None) -> list[PRData]:
        """
        Fetch pull requests with optional filters.

        Args:
            filters: Optional filters (state, labels, etc.)

        Returns:
            List of PRData
        """
        ...

    async def fetch_pr_diff(self, number: int) -> str:
        """
        Fetch the diff for a pull request.

        Args:
            number: PR number

        Returns:
            Unified diff string
        """
        ...

    async def post_review(
        self,
        pr_number: int,
        review: ReviewData,
    ) -> int:
        """
        Post a review to a pull request.

        Args:
            pr_number: PR number
            review: Review data with findings and comments

        Returns:
            Review ID
        """
        ...

    async def merge_pr(
        self,
        pr_number: int,
        merge_method: str = "merge",
        commit_title: str | None = None,
    ) -> bool:
        """
        Merge a pull request.

        Args:
            pr_number: PR number
            merge_method: merge, squash, or rebase
            commit_title: Optional commit title

        Returns:
            True if merged successfully
        """
        ...

    async def close_pr(
        self,
        pr_number: int,
        comment: str | None = None,
    ) -> bool:
        """
        Close a pull request without merging.

        Args:
            pr_number: PR number
            comment: Optional closing comment

        Returns:
            True if closed successfully
        """
        ...

    async def enable_auto_merge(
        self,
        pr_number: int,
        merge_method: str = "squash",
    ) -> bool:
        """
        Enable auto-merge on a PR (RFC-0011 low tier: auto-merge-when-green).

        The PR merges automatically once the host's required checks pass — the
        provider-native equivalent of the RFC-0009 merge gate. Implementations
        that don't support auto-merge should raise ``NotImplementedError`` with
        a clear message.

        Args:
            pr_number: PR number
            merge_method: merge, squash, or rebase

        Returns:
            True if auto-merge was enabled
        """
        ...

    # -------------------------------------------------------------------------
    # Issue Operations
    # -------------------------------------------------------------------------

    async def fetch_issue(self, number: int) -> IssueData:
        """
        Fetch an issue by number.

        Args:
            number: Issue number

        Returns:
            IssueData with full issue details
        """
        ...

    async def fetch_issues(
        self, filters: IssueFilters | None = None
    ) -> list[IssueData]:
        """
        Fetch issues with optional filters.

        Args:
            filters: Optional filters

        Returns:
            List of IssueData
        """
        ...

    async def create_issue(
        self,
        title: str,
        body: str,
        labels: list[str] | None = None,
        assignees: list[str] | None = None,
    ) -> IssueData:
        """
        Create a new issue.

        Args:
            title: Issue title
            body: Issue body
            labels: Optional labels
            assignees: Optional assignees

        Returns:
            Created IssueData
        """
        ...

    async def close_issue(
        self,
        number: int,
        comment: str | None = None,
    ) -> bool:
        """
        Close an issue.

        Args:
            number: Issue number
            comment: Optional closing comment

        Returns:
            True if closed successfully
        """
        ...

    async def add_comment(
        self,
        issue_or_pr_number: int,
        body: str,
    ) -> int:
        """
        Add a comment to an issue or PR.

        Args:
            issue_or_pr_number: Issue/PR number
            body: Comment body

        Returns:
            Comment ID
        """
        ...

    async def fetch_comments(
        self,
        issue_number: int,
        since: datetime | None = None,
    ) -> list[IssueComment]:
        """
        Read the comment thread on an issue (Factory#375).

        The read counterpart to :meth:`add_comment`. Returns a normalised
        :class:`IssueComment` list ordered oldest-first, so a caller never
        branches on ``provider`` to render a discussion.

        ``since`` mirrors :class:`IssueFilters.since`: only comments created or
        updated strictly after that instant are returned. It is a real narrowing
        of the request, not a client-side filter — implementations either pass it
        to the API (GitHub) or fetch newest-first and stop at the cutoff
        (GitLab, Azure DevOps), so a poll that finds nothing new costs one page.

        Storage is the consumer's decision: this returns the same thing whether
        it is called once at import or on every card open.

        Args:
            issue_number: Issue number (GitHub) / IID (GitLab) / work item ID (ADO)
            since: Only return comments updated after this instant

        Returns:
            Comments, oldest first. Empty when the issue has no discussion.

        Raises:
            ProviderCommentError: The thread could not be read in full. Never
                returns a truncated list — a partial thread presented as
                complete is worse than a visible failure.
        """
        ...

    async def fetch_comments_bulk(
        self,
        issue_numbers: list[int],
        since: datetime | None = None,
    ) -> dict[int, list[IssueComment]]:
        """
        Read comment threads for several issues at once (Factory#375).

        Comments are one API call PER ISSUE on the naive path, and Factory#373
        multiplies that per repository — a 46-card backfill is 46 extra calls
        every poll. Implementations must use a bulk endpoint where the provider
        has one (GitHub's repository-wide issue-comments endpoint does, with
        server-side ``since``) and fall back to a bounded per-issue fan-out
        (:func:`fanout_comments`) where it does not.

        Args:
            issue_numbers: Issues to read. Explicit rather than "all", so the
                call cost is always bounded by what the caller asked for.
            since: Only return comments updated after this instant

        Returns:
            Mapping of issue number to its comments, oldest first. Every
            requested number is present; issues with no comments map to ``[]``.

        Raises:
            ProviderCommentError: As :meth:`fetch_comments`.
        """
        ...

    async def assign_to_user(
        self,
        issue_number: int,
        assignees: list[str],
    ) -> None:
        """
        Assign an issue to one or more users or autonomous agents.

        Special aliases recognized by GitHub provider:
            - "Copilot" — maps to GitHub Copilot Coding Agent
              (login: copilot-swe-agent). The agent is assigned via the
              GraphQL replaceActorsForAssignable mutation.

        Silent no-op semantics: if a requested assignee cannot be assigned
        because the agent is disabled at the org/repo level, the call
        returns without raising. Callers should verify success by
        re-fetching the issue and checking IssueData.assignees.

        Other providers (GitLab, Azure DevOps) raise NotImplementedError
        until their equivalent autonomous-agent APIs are wired up
        (Duo Workflow lands in V1.5 — see issue #98).

        Args:
            issue_number: Issue number (GitHub) or IID (GitLab/ADO)
            assignees: List of usernames or aliases. The string "Copilot"
                is recognized as the Copilot Coding Agent alias.
        """
        ...

    # -------------------------------------------------------------------------
    # Label Operations
    # -------------------------------------------------------------------------

    async def apply_labels(
        self,
        issue_or_pr_number: int,
        labels: list[str],
    ) -> None:
        """
        Apply labels to an issue or PR.

        Args:
            issue_or_pr_number: Issue/PR number
            labels: Labels to apply
        """
        ...

    async def remove_labels(
        self,
        issue_or_pr_number: int,
        labels: list[str],
    ) -> None:
        """
        Remove labels from an issue or PR.

        Args:
            issue_or_pr_number: Issue/PR number
            labels: Labels to remove
        """
        ...

    async def create_label(
        self,
        label: LabelData,
    ) -> None:
        """
        Create a label in the repository.

        Args:
            label: Label data
        """
        ...

    async def list_labels(self) -> list[LabelData]:
        """
        List all labels in the repository.

        Returns:
            List of LabelData
        """
        ...

    # -------------------------------------------------------------------------
    # Repository Operations
    # -------------------------------------------------------------------------

    async def get_repository_info(self) -> dict[str, Any]:
        """
        Get repository information.

        Returns:
            Repository metadata
        """
        ...

    async def get_default_branch(self) -> str:
        """
        Get the default branch name.

        Returns:
            Default branch name (e.g., "main", "master")
        """
        ...

    async def check_permissions(self, username: str) -> str:
        """
        Check a user's permission level on the repository.

        Args:
            username: GitHub username

        Returns:
            Permission level (admin, write, read, none)
        """
        ...

    # -------------------------------------------------------------------------
    # API Operations (Low-level)
    # -------------------------------------------------------------------------

    async def api_get(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """
        Make a GET request to the provider API.

        Args:
            endpoint: API endpoint
            params: Query parameters

        Returns:
            API response data
        """
        ...

    async def api_post(
        self,
        endpoint: str,
        data: dict[str, Any] | None = None,
    ) -> Any:
        """
        Make a POST request to the provider API.

        Args:
            endpoint: API endpoint
            data: Request body

        Returns:
            API response data
        """
        ...
