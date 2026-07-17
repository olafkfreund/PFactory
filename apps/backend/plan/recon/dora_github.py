"""GitHub Deployments provider for DORA context (RFC-0013, issue #252).

Implements :class:`DoraMcpClient` backed by the GitHub Deployments REST API,
computing basic DORA-ish signals (deployment success rate, change-fail rate,
last-deploy summary) over a configurable rolling window.

Auth follows the same token-resolution order as :mod:`plan.recon.clone` so
no new credential path is introduced:
  PFACTORY_RECON_TOKEN → GH_TOKEN → GITHUB_TOKEN.

When no token is present :func:`make_github_dora_client` returns ``None``
and planning degrades to ``available=False`` — the existing safe default.

Any runtime error inside :meth:`GitHubDeploymentsDoraProvider.dora_metrics`
propagates to :func:`plan.recon.dora_client.dora_context`, which catches all
exceptions and returns ``available=False``. The provider itself never silences
errors; the caller is responsible for degradation.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

_SOURCE = "github-deployments"
_DEFAULT_API_BASE = "https://api.github.com"
# Limit the per-deployment status API calls to keep latency bounded.
_MAX_STATUS_CALLS = 20
_HTTP_OK: int = 200


def _parse_iso(dt_str: str) -> datetime:
    """Parse a GitHub ISO-8601 timestamp (UTC, may end in 'Z') to an aware datetime."""
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))


class GitHubDeploymentsDoraProvider:
    """DORA metrics from the GitHub Deployments REST API.

    Satisfies the :class:`plan.recon.dora_client.DoraMcpClient` Protocol:
    one method, ``dora_metrics(repo, env, window_days)``, returns an envelope
    that :func:`plan.recon.dora_client.dora_context` normalizes before stamping
    the contract.

    The implementation is intentionally synchronous: DORA context is gathered
    once per plan run, not in a hot loop, so the extra simplicity of
    ``httpx.Client`` outweighs the benefits of async here.

    Computed signals
    ----------------
    * ``deploy_success_rate`` — deployments whose latest GitHub status is
      ``success`` divided by total resolved deployments in the window.
    * ``change_fail_rate`` — deployments whose latest status is ``failure`` or
      ``error`` divided by total.
    * ``last_deploy`` — environment, terminal state, and timestamp of the most
      recent deployment in the window.
    * ``lead_time_p50_hours`` — **not computed in v1** (requires pairing each
      deployment SHA with its commit author date, which adds N extra API calls
      and complexity disproportionate to the signal's current use). Returned as
      ``None`` (honest absence; schema allows nullable).

    Rate-limit notes
    ----------------
    One GET for the deployment list + up to :data:`_MAX_STATUS_CALLS` GETs for
    statuses. For repos with moderate deploy frequency this is well under the
    5 000 req/hour authenticated limit. The provider is called at most once per
    planning session.
    """

    def __init__(self, token: str, api_base: str = _DEFAULT_API_BASE) -> None:
        self._token = token
        self._api_base = api_base.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def dora_metrics(
        self, repo: str, env: str | None = None, window_days: int = 30
    ) -> dict[str, Any]:
        """Return a DORA envelope for *repo* / *env* over the last *window_days*.

        Raises on network or HTTP error — the caller (:func:`dora_context`) is
        responsible for catching and converting to ``available=False``.
        """
        import httpx  # noqa: PLC0415 — lazy import keeps cold-start cheap

        owner, _, name = repo.partition("/")
        since: datetime = datetime.now(UTC) - timedelta(days=window_days)
        headers = self._headers()
        params: dict[str, str] = {"per_page": "100"}
        if env:
            params["environment"] = env

        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                f"{self._api_base}/repos/{owner}/{name}/deployments",
                headers=headers,
                params=params,
            )
            resp.raise_for_status()
            all_deps: list[dict[str, Any]] = resp.json()

            # Filter to the rolling window.
            deps = [
                d
                for d in all_deps
                if _parse_iso(d.get("created_at", "1970-01-01T00:00:00Z")) >= since
            ]

            if not deps:
                return {
                    "available": False,
                    "reason": f"no deployments in the last {window_days} days",
                }

            # Fetch the latest status for each deployment (most recent first).
            # Cap at _MAX_STATUS_CALLS to keep latency bounded.
            statuses: list[tuple[dict[str, Any], str]] = []
            for dep in deps[:_MAX_STATUS_CALLS]:
                st_resp = client.get(
                    f"{self._api_base}/repos/{owner}/{name}/deployments/{dep['id']}/statuses",
                    headers=headers,
                    params={"per_page": "1"},
                )
                if st_resp.status_code == _HTTP_OK:
                    st_list: list[dict[str, Any]] = st_resp.json()
                    if st_list:
                        statuses.append((dep, st_list[0].get("state", "unknown")))

        n = len(statuses)
        success_n = sum(1 for _, st in statuses if st == "success")
        fail_n = sum(1 for _, st in statuses if st in ("failure", "error"))

        result: dict[str, Any] = {
            "available": True,
            "source": _SOURCE,
            # lead_time_p50_hours requires commit-date resolution; deferred to v2.
            "lead_time_p50_hours": None,
            "change_fail_rate": round(fail_n / n, 4) if n else None,
            "deploy_success_rate": {
                "window_days": window_days,
                "value": round(success_n / n, 4) if n else 0.0,
                "sample": n,
            },
        }

        if statuses:
            most_recent_dep, most_recent_state = statuses[0]
            result["last_deploy"] = {
                "env": most_recent_dep.get("environment") or env or "unknown",
                "status": most_recent_state,
                "at": most_recent_dep.get("created_at", ""),
            }

        return result


def make_github_dora_client() -> GitHubDeploymentsDoraProvider | None:
    """Return a :class:`GitHubDeploymentsDoraProvider` if a token is available.

    Token resolution (same order as :func:`plan.recon.clone._git_url`):
      1. ``PFACTORY_RECON_TOKEN``
      2. ``GH_TOKEN``
      3. ``GITHUB_TOKEN``

    Returns ``None`` when no token is found so planning degrades gracefully to
    ``available=False`` without any configuration required. Set
    ``PFACTORY_GITHUB_API_BASE`` to override the API base for GitHub Enterprise.
    """
    token = (
        os.environ.get("PFACTORY_RECON_TOKEN")
        or os.environ.get("GH_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
        or ""
    ).strip()
    if not token:
        return None
    api_base = os.environ.get("PFACTORY_GITHUB_API_BASE", _DEFAULT_API_BASE)
    return GitHubDeploymentsDoraProvider(token=token, api_base=api_base)
