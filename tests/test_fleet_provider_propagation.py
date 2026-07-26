"""PFactory reconnoitres the tenant's host (RFC-0020 3.5, Factory#366).

The bug: a GitLab tenant's PARR run reconnoitred github.com. ``_git_url`` built
its clone URL from ``PFACTORY_RECON_GIT_HOST``, defaulting to ``github.com``, so
the tenant's declaration in CFactory's Settings panel had no effect on which
host PFactory read the code from. Recon then failed against a repo that was
never there and degraded silently to greenfield — the plan was built as if the
codebase did not exist.

Covered here:

* the clone URL's host comes from the reference's qualification;
* the qualification is STRIPPED from the path, never glued into the URL;
* a GitHub tenant's URL is byte-for-byte what it was;
* ``PFACTORY_RECON_GIT_HOST`` still overrides, for a self-hosted instance;
* a full clone URL is passed through untouched — the case a naive colon-split
  would have broken;
* the token is still the environment's; a reference cannot carry one;
* a tracked project registered from a plan session records the declared host.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _seam in (_ROOT / "apps" / "backend", _ROOT / "apps" / "web-server"):
    if str(_seam) not in sys.path:
        sys.path.insert(0, str(_seam))

from plan.recon.clone import _git_url  # noqa: E402
from plan.repo_ref import parse_repo_ref, project_of, provider_of, qualify_repo  # noqa: E402
from server.routes import projects as projects_mod  # noqa: E402

_GL_REF = "gitlab:platform/pipelines"
_GH_REF = "acme/widgets"

# Not a credential: an opaque string standing in for a recon token, so the
# assertions about where it does and does not appear are meaningful.
_FAKE_TOKEN = "recon-token-placeholder"  # noqa: S105 - a literal, not a credential


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """No ambient host or token — every test states what it needs."""
    for var in (
        "PFACTORY_RECON_GIT_HOST",
        "PFACTORY_RECON_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)


# ── the reference ────────────────────────────────────────────────────────────


def test_the_contract_round_trips_a_gitlab_reference_unchanged():
    assert parse_repo_ref(_GL_REF) == ("gitlab", "platform/pipelines")
    assert qualify_repo(*parse_repo_ref(_GL_REF)) == _GL_REF


def test_github_is_the_unqualified_default():
    assert parse_repo_ref(_GH_REF) == ("github", _GH_REF)
    assert qualify_repo(*parse_repo_ref(_GH_REF)) == _GH_REF
    assert provider_of(_GH_REF) == "github"
    assert provider_of(None) == "github"


def test_azure_devops_qualifies_with_its_three_part_path():
    assert parse_repo_ref("azure_devops:org/proj/repo") == ("azure_devops", "org/proj/repo")


def test_an_unimplemented_host_is_not_a_qualification():
    assert parse_repo_ref("bitbucket:team/repo") == ("github", "bitbucket:team/repo")


# ── the clone URL ────────────────────────────────────────────────────────────


def test_a_gitlab_reference_clones_from_gitlab_not_github():
    """The bug, asserted at the single line that produced it."""
    url = _git_url(_GL_REF)
    assert url == "https://gitlab.com/platform/pipelines.git"
    # On the parsed host, not a substring: "gitlab.com" appears inside
    # "gitlab.com.evil.test" too, and a check that cannot tell those apart is
    # not worth writing even in a test.
    assert urlsplit(url).hostname == "gitlab.com"


def test_the_qualification_never_reaches_the_url():
    """A stray "gitlab:" in the path is a 404 that reads like a missing repo.

    Worse than a plain failure, because recon degrades to greenfield rather than
    raising — so the plan comes out wrong and nothing says why.
    """
    assert "gitlab:" not in _git_url(_GL_REF)
    assert project_of(_GL_REF) == "platform/pipelines"


def test_a_github_reference_is_byte_for_byte_what_it_was():
    assert _git_url(_GH_REF) == "https://github.com/acme/widgets.git"


def test_azure_devops_clones_from_its_own_host():
    assert _git_url("azure_devops:org/proj/repo") == "https://dev.azure.com/org/proj/repo.git"


def test_the_env_host_still_overrides_for_a_self_hosted_instance(monkeypatch):
    """A self-hosted GitLab has a host no table can know."""
    monkeypatch.setenv("PFACTORY_RECON_GIT_HOST", "gitlab.internal.example")
    assert _git_url(_GL_REF) == "https://gitlab.internal.example/platform/pipelines.git"


def test_an_empty_env_host_does_not_blank_the_provider_default(monkeypatch):
    """An env var set to "" must not produce https:///path."""
    monkeypatch.setenv("PFACTORY_RECON_GIT_HOST", "   ")
    assert _git_url(_GL_REF) == "https://gitlab.com/platform/pipelines.git"


def test_a_full_clone_url_is_passed_through_untouched():
    """The caller a naive colon-split would have broken.

    "https://gitlab.example.com/g/p.git" contains a colon, and splitting on it
    regardless yields a provider called "https". The parser only treats a KNOWN
    provider name as a qualification, and _git_url short-circuits on "://"
    before it even asks.
    """
    url = "https://gitlab.example.com/platform/pipelines.git"
    assert _git_url(url) == url
    assert parse_repo_ref(url) == ("github", url)


def test_the_token_is_the_environments_and_a_reference_cannot_carry_one(monkeypatch):
    """A qualification says WHERE the code is. It is not a credential."""
    monkeypatch.setenv("PFACTORY_RECON_TOKEN", _FAKE_TOKEN)
    url = _git_url(_GL_REF)
    assert url == f"https://x-access-token:{_FAKE_TOKEN}@gitlab.com/platform/pipelines.git"
    # Still the tenant's host, not the token's provider — checked on the parsed
    # host, since a credential-bearing URL is exactly where a substring test
    # gives the wrong answer (the userinfo half can carry anything).
    assert urlsplit(url).hostname == "gitlab.com"


# ── the tracked project a plan session registers ─────────────────────────────


def test_a_tracked_project_records_the_declared_host(monkeypatch):
    """W4/#218 registration used to store the qualified string as the repo path.

    That left the project id as "gitlab:platform-pipelines" and its provider
    reading as the "github" default — so the project PFactory created from a
    GitLab contract described itself as a GitHub project.
    """
    store: dict = {}
    monkeypatch.setattr(projects_mod, "load_projects", lambda: store)
    monkeypatch.setattr(projects_mod, "save_projects", lambda data: store.update(data))

    pid = projects_mod.ensure_tracked_project(_GL_REF)
    assert pid == "platform-pipelines"
    assert store[pid]["repo"] == "platform/pipelines"
    assert store[pid]["settings"]["gitProvider"] == "gitlab"


def test_a_tracked_github_project_is_unchanged(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(projects_mod, "load_projects", lambda: store)
    monkeypatch.setattr(projects_mod, "save_projects", lambda data: store.update(data))

    pid = projects_mod.ensure_tracked_project(_GH_REF)
    assert pid == "acme-widgets"
    assert store[pid]["repo"] == _GH_REF
    assert store[pid]["settings"]["gitProvider"] == "github"
