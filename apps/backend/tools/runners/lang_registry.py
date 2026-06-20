"""Per-language, per-lane tool registry — Task 4 (#5).

Lookup table that maps ``(language, lane)`` → the tool the runner should
invoke. Used by per-lane Generators (Tasks 6+) and the Executor (Task 8)
to pick the right tooling per project at plan-time.

Lit today: Python ``unit`` (pytest) and TypeScript ``unit`` (jest) +
``browser`` (playwright). The remaining cells are wired tooling that lights
up per the roadmap; Java/.NET (v0.3) and Go/Rust/Ruby (v0.4) are ``None``.
Security scanning is out of scope (delegated to dedicated pipelines), so the
v0.1 sast/dast/fuzz lanes were dropped — see ``test_plan.enums`` Decision 2.

Single source of truth so the design-plan tooling table doesn't drift
from the code that actually invokes it (v0.2 modality spine):

    Lane         | Python                  | TypeScript
    -------------+-------------------------+-------------------------
    unit         | pytest                  | jest
    browser      | playwright-python       | @playwright/test
    api          | httpx + pytest          | supertest + jest
    integration  | testcontainers-python   | testcontainers-node
    mutation     | mutmut                  | stryker
"""

from __future__ import annotations

from dataclasses import dataclass


class UnsupportedLanguageError(KeyError):
    """Raised when a language has no registry row at all."""


@dataclass(frozen=True)
class ToolSpec:
    """A single (language, lane) entry."""

    binary: str
    description: str
    available_at_mvp: bool = False
    phase: str = "n/a"  # phase 1/2/3/4/5/6 — when this lane lights up


# Lane keys mirror test_plan.enums.Lane but stay as strings so this module
# stays import-cheap (no SDK / dataclass machinery at module-load time).
# Restructured in v0.2 Task 0 to match the new modality-based spine
# (Browser · API · Integration · Unit · Mutation). The deprecated v0.1
# names (functional/sast/deps/secrets/dast/fuzz) collapse to UNIT or are
# out of scope — see test_plan.enums._V01_LANE_ALIASES.
_LANE_KEYS = ("unit", "browser", "api", "integration", "mutation")


_REGISTRY: dict[str, dict[str, ToolSpec | None]] = {
    # ── Python (v0.1 anchor, v0.2 = pytest still primary) ───────────────
    "python": {
        "unit": ToolSpec("pytest", "pytest + pytest-cov", True, "1"),
        "browser": ToolSpec("playwright-python", "Playwright Python bindings", False, "2"),
        "api": ToolSpec("httpx+pytest", "httpx + pytest fixtures", False, "2"),
        "integration": ToolSpec("testcontainers-python", "testcontainers-python", False, "2"),
        "mutation": ToolSpec("mutmut", "mutmut (default) / cosmic-ray", False, "2"),
    },
    # ── TypeScript / Node (v0.2 ramp — Jest unit + Playwright browser) ──
    "typescript": {
        "unit": ToolSpec("jest", "jest + nyc (preferred) / vitest", True, "2"),
        "browser": ToolSpec("playwright", "@playwright/test (primary)", True, "2"),
        "api": ToolSpec("supertest", "supertest + jest", False, "3"),
        "integration": ToolSpec("testcontainers-node", "testcontainers-node", False, "3"),
        "mutation": ToolSpec("stryker", "stryker-mutator/core", False, "3"),
    },
    # ── Java (v0.3+) ────────────────────────────────────────────────────
    "java": dict.fromkeys(_LANE_KEYS),
    # ── C# / .NET (v0.3+) ───────────────────────────────────────────────
    "csharp": dict.fromkeys(_LANE_KEYS),
    # ── Go (v0.4+) ──────────────────────────────────────────────────────
    "go": dict.fromkeys(_LANE_KEYS),
    # ── Rust (v0.4+) ────────────────────────────────────────────────────
    "rust": dict.fromkeys(_LANE_KEYS),
    # ── Ruby (v0.4+) ────────────────────────────────────────────────────
    "ruby": dict.fromkeys(_LANE_KEYS),
}


def get_tool_for_lane(language: str, lane: str) -> ToolSpec | None:
    """Return the ToolSpec for ``(language, lane)`` or None if unimpl.

    Raises:
        UnsupportedLanguageError: if ``language`` isn't in the registry
            at all (e.g. C++, Elixir).
    """
    lang = language.lower()
    if lang not in _REGISTRY:
        raise UnsupportedLanguageError(
            f"language {language!r} not in pfactory registry; supported: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[lang].get(lane)


def languages_supporting_lane(lane: str, *, mvp_only: bool = False) -> list[str]:
    """Return languages that have a non-None entry for ``lane``.

    When ``mvp_only=True``, also require ``available_at_mvp=True``.
    """
    out = []
    for lang, lanes in _REGISTRY.items():
        spec = lanes.get(lane)
        if spec is None:
            continue
        if mvp_only and not spec.available_at_mvp:
            continue
        out.append(lang)
    return sorted(out)
