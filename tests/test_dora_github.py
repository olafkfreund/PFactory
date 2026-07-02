"""Tests for the GitHub Deployments DORA provider (issue #252).

All tests are hermetic — no network calls. The GitHub API is mocked at the
httpx.Client level so the tests verify the provider's metric-computation logic
and degradation behaviour without any external dependencies.

Covers:
  * :class:`plan.recon.dora_github.GitHubDeploymentsDoraProvider`
    — success path (all successes, mixed success/failure, no window deployments)
  * :func:`plan.recon.dora_github.make_github_dora_client`
    — no token → None; token present → provider instance; env precedence
  * Integration with :func:`plan.recon.dora_client.dora_context`
    — provider success is normalised, provider error degrades to available:false
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("httpx")

from plan.recon.dora_client import dora_context  # noqa: E402
from plan.recon.dora_github import (  # noqa: E402
    GitHubDeploymentsDoraProvider,
    make_github_dora_client,
)

# Placeholder token value used across all tests. S105 is suppressed because
# this is an intentional test fixture, not a real credential.
_FAKE_TOKEN = "fake-token"  # noqa: S105


# ---------------------------------------------------------------------------
# Helpers — build mock responses that look like the GitHub Deployments API
# ---------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    """Format a datetime as a GitHub-style ISO timestamp."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _recent(days_ago: float = 0.0) -> str:
    """Return an ISO timestamp *days_ago* days in the past from now."""
    return _iso(datetime.now(UTC) - timedelta(days=days_ago))


def _dep(dep_id: int, created_at: str, env: str = "production") -> dict:
    return {"id": dep_id, "sha": f"abc{dep_id}", "created_at": created_at, "environment": env}


def _status(state: str) -> list[dict]:
    return [{"state": state, "created_at": _recent(0)}]


def _make_mock_client(deps_json: list[dict], statuses_by_id: dict[int, list[dict]]) -> MagicMock:
    """Build an httpx.Client mock returning *deps_json* for the deployments
    endpoint and the matching *statuses_by_id* list for each status endpoint."""

    def _get(url: str, **_kwargs: object) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        if "/statuses" in url:
            dep_id = int(url.rstrip("/").split("/")[-2])
            resp.json.return_value = statuses_by_id.get(dep_id, [])
        else:
            resp.json.return_value = deps_json
        resp.raise_for_status = MagicMock()
        return resp

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.side_effect = _get
    return mock_client


# ---------------------------------------------------------------------------
# GitHubDeploymentsDoraProvider — success paths
# ---------------------------------------------------------------------------


class TestProviderSuccess:
    """Provider correctly computes metrics from mocked API responses."""

    def test_all_successful_deployments(self) -> None:
        deps = [_dep(1, _recent(1)), _dep(2, _recent(3)), _dep(3, _recent(7))]
        statuses = {1: _status("success"), 2: _status("success"), 3: _status("success")}
        mock_client = _make_mock_client(deps, statuses)

        provider = GitHubDeploymentsDoraProvider(token=_FAKE_TOKEN)
        with patch("httpx.Client", return_value=mock_client):
            result = provider.dora_metrics("owner/repo", "production", window_days=30)

        assert result["available"] is True
        assert result["source"] == "github-deployments"
        assert result["deploy_success_rate"]["value"] == 1.0
        assert result["deploy_success_rate"]["sample"] == 3
        assert result["deploy_success_rate"]["window_days"] == 30
        assert result["change_fail_rate"] == 0.0
        # last_deploy reflects the most recent deployment
        assert result["last_deploy"]["env"] == "production"
        assert result["last_deploy"]["status"] == "success"
        # lead_time is deferred to v2 — null honest absence
        assert result["lead_time_p50_hours"] is None

    def test_mixed_success_and_failure(self) -> None:
        deps = [_dep(10, _recent(1)), _dep(11, _recent(5)), _dep(12, _recent(10))]
        statuses = {10: _status("success"), 11: _status("failure"), 12: _status("error")}
        mock_client = _make_mock_client(deps, statuses)

        provider = GitHubDeploymentsDoraProvider(token=_FAKE_TOKEN)
        with patch("httpx.Client", return_value=mock_client):
            result = provider.dora_metrics("owner/repo", window_days=30)

        assert result["available"] is True
        # 1 success / 3 total
        assert result["deploy_success_rate"]["value"] == pytest.approx(1 / 3, abs=0.001)
        # 2 failures (failure + error) / 3 total
        assert result["change_fail_rate"] == pytest.approx(2 / 3, abs=0.001)
        assert result["deploy_success_rate"]["sample"] == 3

    def test_no_deployments_in_window_returns_unavailable(self) -> None:
        # Deployment is older than window_days=7
        old = _iso(datetime.now(UTC) - timedelta(days=10))
        deps = [_dep(20, old)]
        mock_client = _make_mock_client(deps, {})

        provider = GitHubDeploymentsDoraProvider(token=_FAKE_TOKEN)
        with patch("httpx.Client", return_value=mock_client):
            result = provider.dora_metrics("owner/repo", window_days=7)

        assert result["available"] is False
        assert "no deployments" in result["reason"]

    def test_last_deploy_uses_most_recent(self) -> None:
        # Deployments API returns most recent first
        deps = [_dep(30, _recent(1)), _dep(31, _recent(5))]
        statuses = {30: _status("success"), 31: _status("failure")}
        mock_client = _make_mock_client(deps, statuses)

        provider = GitHubDeploymentsDoraProvider(token=_FAKE_TOKEN)
        with patch("httpx.Client", return_value=mock_client):
            result = provider.dora_metrics("owner/repo", "staging", window_days=30)

        assert result["last_deploy"]["status"] == "success"  # dep 30 is most recent
        assert result["last_deploy"]["env"] == "production"  # from dep environment field

    def test_env_parameter_forwarded_to_api(self) -> None:
        """The env filter is passed as a query param to the deployments endpoint."""
        deps = [_dep(40, _recent(1), env="staging")]
        statuses = {40: _status("success")}
        mock_client = _make_mock_client(deps, statuses)

        provider = GitHubDeploymentsDoraProvider(token=_FAKE_TOKEN)
        with patch("httpx.Client", return_value=mock_client):
            provider.dora_metrics("owner/repo", "staging", window_days=30)

        calls = mock_client.get.call_args_list
        deployments_call = next(
            c for c in calls if "/statuses" not in str(c.args[0] if c.args else "")
        )
        assert deployments_call.kwargs.get("params", {}).get("environment") == "staging"

    def test_no_statuses_resolved_still_returns_available(self) -> None:
        """Zero-sample deploy_success_rate is honest absence (n=0), not unavailable."""
        deps = [_dep(50, _recent(1))]
        mock_client = _make_mock_client(deps, statuses_by_id={50: []})

        provider = GitHubDeploymentsDoraProvider(token=_FAKE_TOKEN)
        with patch("httpx.Client", return_value=mock_client):
            result = provider.dora_metrics("owner/repo", window_days=30)

        assert result["available"] is True
        assert result["deploy_success_rate"]["sample"] == 0
        assert result["deploy_success_rate"]["value"] == 0.0
        assert result["change_fail_rate"] is None  # n == 0 → None


# ---------------------------------------------------------------------------
# Provider — error / degradation paths
# ---------------------------------------------------------------------------


class TestProviderDegradation:
    """Any exception from the provider degrades to available:false via dora_context."""

    def test_http_error_propagates(self) -> None:
        """raise_for_status raises → exception propagates to caller (dora_context)."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401 Unauthorized",
            request=MagicMock(),
            response=MagicMock(),
        )
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp

        provider = GitHubDeploymentsDoraProvider(token="bad-token")  # noqa: S106
        with (
            patch("httpx.Client", return_value=mock_client),
            pytest.raises(httpx.HTTPStatusError),
        ):
            provider.dora_metrics("owner/repo")

    def test_network_error_degrades_to_unavailable_via_dora_context(self) -> None:
        """When the provider raises, dora_context catches and returns available:false."""
        provider = GitHubDeploymentsDoraProvider(token=_FAKE_TOKEN)
        with patch.object(provider, "dora_metrics", side_effect=RuntimeError("network down")):
            result = dora_context("owner/repo", mcp_client=provider)

        assert result["available"] is False
        assert "unreachable" in result["reason"]

    def test_http_error_degrades_via_dora_context(self) -> None:
        """HTTP 401 also degrades gracefully via dora_context."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401",
            request=MagicMock(),
            response=MagicMock(),
        )
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp

        provider = GitHubDeploymentsDoraProvider(token="bad-token")  # noqa: S106
        with patch("httpx.Client", return_value=mock_client):
            result = dora_context("owner/repo", mcp_client=provider)

        assert result["available"] is False


# ---------------------------------------------------------------------------
# make_github_dora_client — factory
# ---------------------------------------------------------------------------


class TestMakeGithubDoraClient:
    """Factory returns None (no-op) when unconfigured, provider when configured."""

    def test_no_token_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PFACTORY_RECON_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        assert make_github_dora_client() is None

    def test_pfactory_recon_token_returns_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setenv("PFACTORY_RECON_TOKEN", "pfactory-secret")

        client = make_github_dora_client()
        assert isinstance(client, GitHubDeploymentsDoraProvider)
        assert client._token == "pfactory-secret"  # noqa: S105

    def test_gh_token_fallback_returns_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PFACTORY_RECON_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setenv("GH_TOKEN", "gh-secret")

        client = make_github_dora_client()
        assert isinstance(client, GitHubDeploymentsDoraProvider)
        assert client._token == "gh-secret"  # noqa: S105

    def test_github_token_lowest_priority(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PFACTORY_RECON_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.setenv("GITHUB_TOKEN", "github-secret")

        client = make_github_dora_client()
        assert isinstance(client, GitHubDeploymentsDoraProvider)
        assert client._token == "github-secret"  # noqa: S105

    def test_pfactory_token_takes_precedence_over_gh_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PFACTORY_RECON_TOKEN", "primary")
        monkeypatch.setenv("GH_TOKEN", "secondary")
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        client = make_github_dora_client()
        assert isinstance(client, GitHubDeploymentsDoraProvider)
        assert client._token == "primary"  # noqa: S105

    def test_custom_api_base_is_forwarded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GH_TOKEN", "tok")
        monkeypatch.setenv("PFACTORY_GITHUB_API_BASE", "https://ghe.example.com/api/v3")
        monkeypatch.delenv("PFACTORY_RECON_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        client = make_github_dora_client()
        assert isinstance(client, GitHubDeploymentsDoraProvider)
        assert client._api_base == "https://ghe.example.com/api/v3"

    def test_whitespace_only_token_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GH_TOKEN", "   ")
        monkeypatch.delenv("PFACTORY_RECON_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        assert make_github_dora_client() is None


# ---------------------------------------------------------------------------
# Integration: provider feeds through dora_context normalisation
# ---------------------------------------------------------------------------


class TestProviderIntegrationWithDoraContext:
    """The provider result is correctly normalised by dora_context._normalize."""

    def test_success_envelope_passes_through_normalisation(self) -> None:
        deps = [_dep(60, _recent(2))]
        statuses = {60: _status("success")}
        mock_client = _make_mock_client(deps, statuses)

        provider = GitHubDeploymentsDoraProvider(token=_FAKE_TOKEN)
        with patch("httpx.Client", return_value=mock_client):
            result = dora_context("owner/repo", "production", mcp_client=provider)

        assert result["available"] is True
        assert result["source"] == "github-deployments"
        assert result["repo"] == "owner/repo"
        assert result["env"] == "production"
        assert "deploy_success_rate" in result
        assert result["deploy_success_rate"]["window_days"] == 30

    def test_no_token_config_returns_stub_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With no token configured, make_github_dora_client() returns None → available:false."""
        monkeypatch.delenv("PFACTORY_RECON_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        result = dora_context("owner/repo", mcp_client=make_github_dora_client())
        assert result["available"] is False
        assert result["reason"]  # non-empty reason
