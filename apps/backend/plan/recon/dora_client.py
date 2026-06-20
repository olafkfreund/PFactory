"""DORA delivery context client (RFC-0013, #189).

Populates ``deployment.dora_context`` from the provider-agnostic
deployment-metrics MCP (``apis/deployment-metrics.mcp.md`` in the Factory hub).
The MCP is **stubbed now, real provider later**; the defining property is
HONESTY: with no backend it returns ``available=False`` with a ``reason`` and
NULL metrics — it degrades, it never fabricates a healthy-looking record.

This module vendors the dependency-free stub behavior (Factory hub
``scripts/deployment_metrics_stub.py``) so PFactory can attach a well-formed,
honest ``dora_context`` today. When a real MCP client is wired in, pass it as
``mcp_client``; absent or failing, we degrade to the stub default. The returned
envelope maps 1:1 onto ``$defs.deployment.dora_context`` in the contract schema
(that schema is the source of truth).
"""

from __future__ import annotations

from typing import Any, Protocol

_SOURCE_STUB = "stub"
_UNAVAILABLE_REASON = "no provider configured (stub default)"
_DEFAULT_WINDOW_DAYS = 30


class DoraMcpClient(Protocol):
    """The single tool we call on a real deployment-metrics MCP.

    A real client (GitHub Deployments / Argo CD / Datadog) implements this and
    returns the same envelope shape the stub produces, so PFactory needs no
    change to adopt it.
    """

    def dora_metrics(
        self, repo: str, env: str | None = ..., window_days: int = ...
    ) -> dict[str, Any]: ...


def _unavailable(repo: str | None, env: str | None, reason: str) -> dict[str, Any]:
    """A well-formed ``available=False`` envelope (honest absence).

    The schema (``$defs.deployment.dora_context``) types the number metrics
    nullable but the object metrics (``deploy_success_rate``/``last_deploy``) as
    objects, so when unavailable we emit the nullable numbers as ``null`` and
    OMIT the object metrics entirely — omission is honest absence and conforms to
    the schema. ``available: false`` already means "metrics unreachable, health
    UNKNOWN" (never fabricated).
    """
    return {
        "source": _SOURCE_STUB,
        "repo": repo,
        "env": env,
        "available": False,
        "reason": reason,
        "lead_time_p50_hours": None,
        "change_fail_rate": None,
    }


def _normalize(
    envelope: dict[str, Any], repo: str | None, env: str | None, window_days: int
) -> dict[str, Any]:
    """Normalize a provider/fixture envelope, enforcing the honesty rules.

    A malformed or ``available=False`` envelope can never smuggle in non-null
    metrics: when not available, every numeric field is forced to ``null``.
    """
    if not isinstance(envelope, dict) or not envelope.get("available"):
        reason = (
            str(envelope.get("reason"))
            if isinstance(envelope, dict) and envelope.get("reason")
            else _UNAVAILABLE_REASON
        )
        return _unavailable(repo, env, reason)

    success_rate = envelope.get("deploy_success_rate")
    if isinstance(success_rate, dict) and "window_days" not in success_rate:
        success_rate = {"window_days": window_days, **success_rate}

    out: dict[str, Any] = {
        "source": str(envelope.get("source", _SOURCE_STUB)),
        "repo": repo,
        "env": env,
        "available": True,
        "reason": None,
        "lead_time_p50_hours": envelope.get("lead_time_p50_hours"),
        "change_fail_rate": envelope.get("change_fail_rate"),
    }
    # Object-typed metrics are non-nullable in the schema: include only when the
    # provider/fixture actually supplied an object (omitted == honest absence).
    if isinstance(success_rate, dict):
        out["deploy_success_rate"] = success_rate
    last_deploy = envelope.get("last_deploy")
    if isinstance(last_deploy, dict):
        out["last_deploy"] = last_deploy
    return out


def dora_context(
    repo: str | None,
    env: str | None = None,
    *,
    window_days: int = _DEFAULT_WINDOW_DAYS,
    mcp_client: DoraMcpClient | None = None,
    fixture: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the ``deployment.dora_context`` block for ``repo``/``env``.

    Resolution order, each degrading honestly to the next:

      1. ``mcp_client`` — a real deployment-metrics MCP (when wired in). Any
         error degrades to unavailable, never raises.
      2. ``fixture`` — well-formed sample data for tests/demos (the stub path).
      3. default — ``available=False`` with a reason and null metrics.

    No ``repo`` (greenfield / no target repo) => unavailable: there is nothing to
    ask the metrics provider about.
    """
    if not repo:
        return _unavailable(repo, env, "no target repo to query for delivery metrics")

    if mcp_client is not None:
        try:
            envelope = mcp_client.dora_metrics(repo, env, window_days)
        except Exception as exc:  # noqa: BLE001 - any client failure degrades
            return _unavailable(repo, env, f"deployment-metrics MCP unreachable: {exc}")
        return _normalize(envelope, repo, env, window_days)

    if fixture is not None:
        envelope = {"available": True, "source": _SOURCE_STUB, **fixture}
        return _normalize(envelope, repo, env, window_days)

    return _unavailable(repo, env, _UNAVAILABLE_REASON)
