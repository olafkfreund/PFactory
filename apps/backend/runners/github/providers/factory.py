"""
Provider Factory
================

Factory functions for creating git provider instances.
Supports dynamic provider registration for extensibility.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from .github_provider import GitHubProvider
from .protocol import CodeProvider, GitProvider, ProviderType, WorkItemProvider

# Provider registry for dynamic registration
_PROVIDER_REGISTRY: dict[ProviderType, Callable[..., GitProvider]] = {}


def _bitbucket_enabled() -> bool:
    return os.getenv("FACTORY_BITBUCKET_ENABLED", "").lower() in {"1", "true", "yes"}


def register_provider(
    provider_type: ProviderType,
    factory: Callable[..., GitProvider],
) -> None:
    """
    Register a provider factory.

    Args:
        provider_type: The provider type to register
        factory: Factory function that creates provider instances

    Example:
        def create_custom(repo: str, **kwargs) -> GitProvider:
            return CustomProvider(repo=repo, **kwargs)

        register_provider(ProviderType.BITBUCKET, create_custom)
    """
    _PROVIDER_REGISTRY[provider_type] = factory


def get_provider(
    provider_type: ProviderType | str,
    repo: str,
    **kwargs: Any,
) -> GitProvider:
    """
    Get a provider instance by type.

    Args:
        provider_type: The provider type (github, bitbucket, etc.)
        repo: Repository in owner/repo format
        **kwargs: Additional provider-specific arguments

    Returns:
        GitProvider instance

    Raises:
        ValueError: If provider type is not supported

    Example:
        provider = get_provider("github", "owner/repo")
        pr = await provider.fetch_pr(123)
    """
    # Convert string to enum if needed
    if isinstance(provider_type, str):
        try:
            provider_type = ProviderType(provider_type.lower())
        except ValueError:
            raise ValueError(
                f"Unknown provider type: {provider_type}. "
                f"Supported: {[p.value for p in ProviderType]}"
            )

    # Check registry first
    if provider_type in _PROVIDER_REGISTRY:
        return _PROVIDER_REGISTRY[provider_type](repo=repo, **kwargs)

    if provider_type == ProviderType.BITBUCKET:
        raise NotImplementedError(
            "Bitbucket work items are not implemented; use get_code_provider with "
            "FACTORY_BITBUCKET_ENABLED=1 for code operations."
        )

    # Built-in providers
    if provider_type == ProviderType.GITHUB:
        # Factory#370: an EXPLICIT token selects the REST provider, and the
        # selection is made here rather than left to the caller so that supplying
        # a credential can never silently produce a gh-CLI provider that ignores
        # it and uses the ambient login instead. That substitution is not a
        # degraded result — it is a request made as the wrong identity, which is
        # precisely the failure CFactory's explicit-token rule exists to prevent.
        #
        # `token=None` is NOT "no token given": a caller resolving a per-tenant
        # credential that came back empty must not be handed an ambient-auth
        # provider, so the branch turns on whether the key was PASSED.
        if "token" in kwargs:
            from .http_github_provider import HttpGitHubProvider

            token = kwargs.pop("token")
            return HttpGitHubProvider(
                _repo=repo,
                _token=token,
                **{k: v for k, v in kwargs.items() if k in {"_base_url", "_transport"}},
            )
        return GitHubProvider(_repo=repo, **kwargs)

    if provider_type == ProviderType.GITLAB:
        from .gitlab_provider import GitLabProvider

        return GitLabProvider(_repo=repo, **kwargs)

    if provider_type == ProviderType.AZURE_DEVOPS:
        from .azure_devops_provider import AzureDevOpsProvider

        return AzureDevOpsProvider(_repo=repo, **kwargs)

    if provider_type == ProviderType.JIRA:
        # Jira has no code seam, so it cannot be returned as a GitProvider.
        raise ValueError("jira is a work-item provider only: use get_work_item_provider()")

    # Future providers (not yet implemented)
    if provider_type == ProviderType.GITEA:
        raise NotImplementedError(
            "Gitea provider not yet implemented. "
            "See providers/gitea_provider.py.stub for interface."
        )

    raise ValueError(f"Unsupported provider type: {provider_type}")


def get_code_provider(
    provider_type: ProviderType | str,
    repo: str,
    **kwargs: Any,
) -> CodeProvider:
    """Get a provider for repository and pull-request operations."""
    kind = provider_type.lower() if isinstance(provider_type, str) else provider_type
    if kind == ProviderType.BITBUCKET:
        if not _bitbucket_enabled():
            raise NotImplementedError(
                "Bitbucket code provider requires FACTORY_BITBUCKET_ENABLED=1"
            )
        from .bitbucket_provider import BitbucketProvider

        return BitbucketProvider(_repo=repo, **kwargs)
    if kind == ProviderType.GITHUB and "token" in kwargs:
        raise NotImplementedError(
            "GitHub code operations with an explicit token are not implemented"
        )
    return get_provider(provider_type, repo, **kwargs)


def get_work_item_provider(
    provider_type: ProviderType | str,
    repo: str,
    **kwargs: Any,
) -> WorkItemProvider:
    """A provider typed as the work-item seam only (RFC-0022 Phase 3).

    ``jira`` is served here; every other type is a ``GitProvider`` and therefore
    already a ``WorkItemProvider``. ``repo`` is the Jira project key.
    """
    if str(getattr(provider_type, "value", provider_type)).lower() == ProviderType.JIRA.value:
        from .jira_provider import JiraProvider

        return JiraProvider(_repo=repo, **kwargs)
    return get_provider(provider_type, repo, **kwargs)


def list_available_providers() -> list[ProviderType]:
    """
    List all available provider types.

    Returns:
        List of available ProviderType values
    """
    available = [
        ProviderType.GITHUB,
        ProviderType.GITLAB,
        ProviderType.AZURE_DEVOPS,
        ProviderType.JIRA,
    ]  # Built-in

    if _bitbucket_enabled():
        available.append(ProviderType.BITBUCKET)

    # Add registered providers
    for provider_type in _PROVIDER_REGISTRY:
        if provider_type not in available and (
            provider_type != ProviderType.BITBUCKET or _bitbucket_enabled()
        ):
            available.append(provider_type)

    return available


def is_provider_available(provider_type: ProviderType | str) -> bool:
    """
    Check if a provider is available.

    Args:
        provider_type: The provider type to check

    Returns:
        True if the provider is available
    """
    if isinstance(provider_type, str):
        try:
            provider_type = ProviderType(provider_type.lower())
        except ValueError:
            return False

    # GitHub is always available
    if provider_type == ProviderType.GITHUB:
        return True

    if provider_type == ProviderType.BITBUCKET:
        return _bitbucket_enabled()
    if provider_type == ProviderType.JIRA:
        return True

    # Check registry
    return provider_type in _PROVIDER_REGISTRY


# Register default providers
# (Future implementations can be registered here or by external packages)
