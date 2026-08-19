"""One SSRF check for the per-project ``gitBaseUrl`` setting (#610).

Its own module, rather than a private helper in ``routes/github.py``, because
both readers of the setting need it and the repo's strict bar bans the relative
and function-local imports that sharing it across those two packages would
otherwise need (TID252 / PLC0415). A leaf module they can both import at the top
also keeps ``auto_fix_service`` from importing a routes module for one function.

This adds no SSRF logic: it calls ``url_safety.assert_safe_outbound_url``, the
guard every live web-server call site here already uses. Growing a second
dialect of the address check is the failure mode, not the fix.

Ported from TFactory's ``server/services/git_base_url.py`` (TFactory#1116),
which closed the identical defect there.
"""

from __future__ import annotations

from server.services.url_safety import assert_safe_outbound_url


def safe_git_base_url(base_url: str | None) -> str | None:
    """SSRF-check the per-project ``gitBaseUrl`` before a credential rides on it (#610).

    ``gitBaseUrl`` is stored per project and settable through
    ``PATCH /api/projects/{project_id}/settings``, a route with no ``Depends`` of
    its own: ``TokenAuthMiddleware`` admits any JWT access token, an ``acw_`` API
    key carrying the ``api`` scope, or the legacy ``API_TOKEN``, and there is no
    role or ownership check beyond that. The stored value becomes the provider's
    ``_base_url``, and the provider attaches a real credential to the requests it
    addresses -- a GitLab ``PRIVATE-TOKEN``, an Azure DevOps Basic-auth PAT. So a
    non-operator can steer a credentialed outbound request.

    The check sits here, where the untrusted value is read, and not inside
    ``runners/github/providers/factory.py``: that file is the vendored
    factory-github canonical, byte-identical across TFactory, PFactory, AIFactory
    and CFactory and byte-gated by a blocking drift check. Guarding at PFactory's
    own trust boundary keeps the canonical undrifted and puts the check next to
    the input it distrusts.

    ``allow_private=True``: a self-hosted GitLab CE/EE or Azure DevOps Server on
    a LAN is a legitimate target, so refusing RFC-1918 would break real
    deployments. Both postures still refuse the cloud-metadata range, which is
    the one with no legitimate use and a credential-harvesting payoff.

    Returns the checked URL, so the caller is forced to use the value the check
    actually saw rather than re-reading the setting.
    """
    if not base_url:
        return base_url
    assert_safe_outbound_url(base_url, allow_private=True)
    return base_url
