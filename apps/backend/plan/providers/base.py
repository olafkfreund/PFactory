"""Provider MCP layer — execute cloud/IaC best-practice MCP servers (#23/#24).

PFactory *uses* external best-practice MCP servers (AWS / Azure / GCP / Terraform)
during Enrich and Review to auto-evaluate a plan against provider best practices.
A :class:`ProviderMCP` adapter wraps one such server: it reports whether it's
reachable and runs a read-only best-practice check that returns review
:class:`~plan.review.models.Finding` objects — so provider output flows straight
into the review gates via the rules engine's external-policy seam (#16).

Read-only by contract: providers only call the server's analyse/assess/scan tools.
"""

from __future__ import annotations

import abc

from plan.review.models import Finding
from pydantic import BaseModel, Field


class BestPracticeCheck(BaseModel):
    """Result of running a provider's best-practice analysis."""

    provider: str
    target: str = ""
    available: bool = True
    findings: list[Finding] = Field(default_factory=list)
    summary: str = ""
    raw: dict = Field(default_factory=dict)
    error: str | None = None


class ProviderMCP(abc.ABC):
    """Base class for a best-practice MCP provider adapter."""

    name: str = "base"
    capabilities: list[str] = []

    def __init__(self, *, config: dict | None = None, client: object | None = None) -> None:
        self.config = config or {}
        self.client = client  # injectable MCP client (mock in tests)

    @abc.abstractmethod
    def available(self) -> bool:
        """True if the MCP server is configured/reachable."""

    @abc.abstractmethod
    def check(self, context: dict) -> BestPracticeCheck:
        """Run the provider's read-only best-practice analysis over ``context``."""

    def to_findings(self, context: dict) -> list[Finding]:
        """Run :meth:`check` and return findings; never raises."""
        try:
            if not self.available():
                return []
            return self.check(context).findings
        except Exception:
            return []


# ── registry ───────────────────────────────────────────────────────────

_REGISTRY: dict[str, type[ProviderMCP]] = {}


def register_provider(cls: type[ProviderMCP]) -> type[ProviderMCP]:
    _REGISTRY[cls.name] = cls
    return cls


def available_providers() -> list[str]:
    return sorted(_REGISTRY)


def get_provider(name: str, **kwargs: object) -> ProviderMCP:
    try:
        cls = _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown provider '{name}'; registered: {available_providers()}") from None
    return cls(**kwargs)


def run_all(context: dict, *, names: list[str] | None = None) -> list[Finding]:
    """Run every available registered provider and collect all findings.

    Suitable as the ``run_external_policy`` runner in the review rules engine.
    """
    findings: list[Finding] = []
    for name in names or available_providers():
        try:
            findings.extend(get_provider(name).to_findings(context))
        except Exception:
            continue
    return findings
