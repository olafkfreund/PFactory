"""The task contract's provider-qualified repo reference (RFC-0020 3.5, Factory#366).

**The bug this closes.** A GitLab tenant's PARR run reconnoitred github.com.
``plan/recon/clone.py`` built its clone URL from
``PFACTORY_RECON_GIT_HOST``, defaulting to ``github.com``, so the tenant's
declaration in CFactory's Settings panel had no effect on which host PFactory
actually read the code from — it degraded to greenfield against a repo that was
never there, and planned accordingly.

The contract already carried a repo reference (``provenance.repo``). Since phase
5 that reference may be **provider-qualified**::

    owner/repo                              -> ("github", "owner/repo")
    gitlab:group/subgroup/project           -> ("gitlab", "group/subgroup/project")
    azure_devops:org/project/repo           -> ("azure_devops", "org/project/repo")

Three rules, and they are the whole contract:

1. **GitHub is the unqualified default.** An unqualified reference reads as
   ``github``. Every pre-phase-5 contract and every GitHub contract is unchanged
   and nothing needs backfilling, which is what makes this safe to deploy.
2. **Only a KNOWN provider is a qualification.** That matters more here than
   anywhere else in the fleet: :func:`plan.recon.clone._git_url` accepts a full
   clone URL, and ``https://gitlab.example/g/p`` must not be read as a project
   on a host called ``https``. A parser that split on the first colon regardless
   would break the one caller that already worked.
3. **The reference is not a credential.** It says WHERE the code lives. The
   recon token still comes from the environment.

Deliberately not in ``runners/github/`` — that tree is a byte-for-byte vendored
copy of the hub's canonical provider layer behind a drift gate, this is
contract-reading code rather than a VCS client, and putting it there would mean
a hub change plus four re-vendors plus four pinned-SHA bumps to ship a
twelve-line parser.
"""

from __future__ import annotations

# The providers this fleet implements. Bitbucket and Gitea are declared in the
# canonical ProviderType and unimplemented, so treating one as a qualification
# would point a clone at a host nothing can serve.
GITHUB = "github"
GITLAB = "gitlab"
AZURE_DEVOPS = "azure_devops"
SUPPORTED_PROVIDERS: tuple[str, ...] = (GITHUB, GITLAB, AZURE_DEVOPS)

# Where each provider's public instance lives, for building a clone URL. A
# self-hosted host is named by PFACTORY_RECON_GIT_HOST, which stays the override
# it always was — it simply stops being the only answer.
PROVIDER_GIT_HOST: dict[str, str] = {
    GITHUB: "github.com",
    GITLAB: "gitlab.com",
    AZURE_DEVOPS: "dev.azure.com",
}


def parse_repo_ref(ref: str | None) -> tuple[str, str] | None:
    """``(provider, project)`` for a repo reference, or ``None`` if there is none."""
    value = (ref or "").strip()
    if not value:
        return None
    head, sep, tail = value.partition(":")
    if sep and head.strip().lower() in SUPPORTED_PROVIDERS and tail.strip():
        return head.strip().lower(), tail.strip()
    return GITHUB, value


def provider_of(ref: str | None) -> str:
    """Which host ``ref`` names. Unqualified, absent or a URL all read GitHub."""
    parsed = parse_repo_ref(ref)
    return parsed[0] if parsed else GITHUB


def project_of(ref: str | None) -> str:
    """The bare project path, with any qualification stripped."""
    parsed = parse_repo_ref(ref)
    return parsed[1] if parsed else ""


def qualify_repo(provider: str | None, project: str | None) -> str:
    """``project`` tagged with its host — the inverse of :func:`parse_repo_ref`."""
    if not project:
        return ""
    kind = (provider or GITHUB).strip().lower()
    return project if kind == GITHUB else f"{kind}:{project}"
