"""Guard: a configured GitHub token must reach a REST provider, not the gh CLI.

Factory#365. Both places that build a provider from a project's settings passed
the tenant's GitHub token as ``kwargs["_token"]``::

    kwargs["_token"] = token
    return get_provider(ProviderType.GITHUB, repo=repo_name, **kwargs)

The vendored factory keys its REST-vs-gh-CLI choice on ``"token" in kwargs``, so
``_token`` missed the branch entirely and fell through to the gh-CLI
``GitHubProvider`` -- a dataclass with no ``_token`` field, so the call raised
``TypeError`` and every GitHub action on a token-configured project failed. The
GitLab and Azure DevOps branches keep ``_token``/``_pat`` because
``GitLabProvider`` and ``AzureDevOpsProvider`` really do declare those fields;
only the GitHub branch was wrong.

WHY THE SECOND ASSERTION IS THE ONE THAT MATTERS
    Asserting only "a provider comes back carrying the token" is satisfied by an
    implementation that quietly drops the credential and returns the gh-CLI
    provider, which then authenticates as whoever ``gh auth`` happens to be
    logged in as on the host. That is not a degraded result -- it is a request
    made as the WRONG IDENTITY, writing to a tenant's repo under the operator's
    account. So each test also asserts the gh-CLI ``GitHubProvider`` is NOT what
    came back: ambient auth must not be able to substitute for a configured
    credential.

The no-token case is pinned too, in the opposite direction: an unconfigured
tenant is *meant* to fall through to ambient ``gh`` auth, so a future "always
use REST" change cannot land silently.

ON THE SUPPRESSIONS BELOW
    ``S101`` (assert) and ``S105`` (hardcoded secret) are carved out for tests by
    ``ruff.toml``'s ``per-file-ignores``, but the lint RATCHET copies each changed
    file to a ``tmpXXXX__<name>.py`` temp path, whose basename no longer matches
    ``test_*.py`` -- so the carve-out silently does not apply there. Likewise
    ``standards/mypy.ini`` has no test carve-out, hence the explicit annotations
    and the import ignores for the vendored provider package (which is not on the
    ratchet's MYPYPATH). Tracked as a ratchet defect rather than worked around
    forever -- see PFactory#369; remove these suppressions when it is fixed.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

_WEB_SERVER = Path(__file__).resolve().parents[1]
_REPO_ROOT = _WEB_SERVER.parents[1]

sys.path.insert(0, str(_WEB_SERVER))
sys.path.insert(0, str(_REPO_ROOT / "apps" / "backend"))

from runners.github.providers.github_provider import (  # noqa: E402
    GitHubProvider,
)
from runners.github.providers.http_github_provider import (  # noqa: E402
    HttpGitHubProvider,
)
from server.routes.github import _get_project_provider  # noqa: E402
from server.services.auto_fix_service import _provider_for  # noqa: E402

_PROJECT_ID = "proj-365"
_TOKEN = "ghp_TENANT_TOKEN_NOT_THE_AMBIENT_LOGIN"  # noqa: S105

# Both resolvers take a project id and return a provider. They live in different
# modules and were fixed independently, so both are exercised -- patching one
# call site and leaving the other is exactly how half this bug survived.
_RESOLVERS = pytest.mark.parametrize(
    "resolve",
    [
        pytest.param(_get_project_provider, id="routes.github"),
        pytest.param(_provider_for, id="services.auto_fix"),
    ],
)

_InstallProject = Callable[[dict[str, str]], None]
_Resolver = Callable[[str], Any]


@pytest.fixture
def project(monkeypatch: pytest.MonkeyPatch) -> _InstallProject:
    """Install a fake project whose settings this test controls.

    ``gitRepo`` is set by every caller so the resolvers never fall back to
    shelling out to ``git``/``gh`` for repo auto-detection -- that would make the
    test depend on the host's checkout and its ambient login, the very thing
    under test.
    """

    def _install(settings: dict[str, str]) -> None:
        projects = {_PROJECT_ID: {"path": "/nonexistent/project", "settings": settings}}
        # Two readers, two bindings: routes/github imports the loader from
        # services.project_paths at module level, while services/auto_fix_service
        # still reaches for it through routes.projects inside the function.
        monkeypatch.setattr("server.routes.github.load_projects", lambda: projects, raising=True)
        monkeypatch.setattr("server.routes.projects.load_projects", lambda: projects, raising=True)

    return _install


@_RESOLVERS
def test_configured_token_reaches_a_rest_provider(
    project: _InstallProject, resolve: _Resolver
) -> None:
    """A configured token produces a REST provider actually carrying it."""
    project({"gitProvider": "github", "gitRepo": "acme/widgets", "gitToken": _TOKEN})

    provider = resolve(_PROJECT_ID)

    assert isinstance(provider, HttpGitHubProvider)  # noqa: S101
    # `_token` is repr=False, so read the field rather than the repr.
    assert provider._token == _TOKEN  # noqa: S101


@_RESOLVERS
def test_ambient_gh_auth_cannot_substitute_for_a_configured_token(
    project: _InstallProject, resolve: _Resolver
) -> None:
    """With a token configured, the gh-CLI provider must NOT come back.

    This is the assertion that has teeth. ``GitHubProvider`` shells out to ``gh``
    and therefore acts as the host's logged-in user; returning it here would send
    the tenant's requests under someone else's identity while every
    "did we get a provider?" check still passed.
    """
    project({"gitProvider": "github", "gitRepo": "acme/widgets", "gitToken": _TOKEN})

    provider = resolve(_PROJECT_ID)

    assert not isinstance(provider, GitHubProvider), (  # noqa: S101
        "a configured token was dropped in favour of ambient gh CLI auth"
    )


@_RESOLVERS
def test_no_token_still_falls_through_to_ambient_gh_auth(
    project: _InstallProject, resolve: _Resolver
) -> None:
    """No token configured is a real state, and ambient ``gh`` auth is correct.

    Pinned so the fix above cannot drift into forcing REST with an empty token,
    which would 401 every project that relies on the host's ``gh`` login.
    """
    project({"gitProvider": "github", "gitRepo": "acme/widgets"})

    provider = resolve(_PROJECT_ID)

    assert isinstance(provider, GitHubProvider)  # noqa: S101
