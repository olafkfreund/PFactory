"""Tests for the declarative lens gate in default_lenses (PFactory#676).

The ``order`` list reads like an allowlist but is only an ordering hint: the
registry tail admits any registered lens. Before #676 the sole thing keeping
the gated red-team lens out was a hardcoded ``if n == "red-team"`` line — a
second gated lens would have RUN while the extension registry still reported
``enabled: false``. Fail-open is the wrong direction for a gate.

The rule now: the tail admits only lenses whose ``<name>-review`` registry
entry is enabled, or that have no registry entry at all (mandatory built-ins,
test-injected lenses). The key test here is the negative control that stayed
GREEN under the old code: a registry-disabled lens, registered and absent from
``order``, must not come back through the tail.

Run: apps/backend/.venv/bin/pytest tests/test_lens_gating.py
"""

from __future__ import annotations

import inspect
import json

import pytest

from plan.review import extension_registry
from plan.review.lenses import base
from plan.review.lenses.base import default_lenses, register_lens


class _FakeLens:
    """A lens that must never actually run."""

    def __init__(self, name: str) -> None:
        self.name = name

    def evaluate(self, plan, epic):  # noqa: ARG002 - Lens protocol signature; pragma: no cover
        raise AssertionError(f"gated lens {self.name!r} should not have run")


def _registry(tmp_path, extensions):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"extensions": extensions}), encoding="utf-8")
    return str(path)


@pytest.fixture
def gated_lens(monkeypatch, tmp_path):
    """Register a lens whose registry entry says enabled: false."""
    name = "expensive-experimental-lens"
    register_lens(_FakeLens(name))
    monkeypatch.setenv(
        "PFACTORY_EXTENSION_REGISTRY",
        _registry(
            tmp_path,
            [
                {
                    "name": f"{name}-review",
                    "category": "review",
                    "effect": "read-only",
                    "enabled": False,
                    "owner_service": "pfactory",
                }
            ],
        ),
    )
    extension_registry.reset_cache()
    yield name
    del base._REGISTRY[name]
    extension_registry.reset_cache()


def test_registry_disabled_lens_never_leaks_through_the_tail(gated_lens) -> None:
    """The #676 reproduction: registered + disabled + absent from order.

    Under the pre-#676 code this test stays GREEN only for red-team (the
    hardcoded exception) and FAILS for any other gated lens — which is exactly
    the negative control that exposed the fail-open tail. Break the _gated_off
    guard and this goes red.
    """
    names = [lens.name for lens in default_lenses()]
    assert gated_lens not in names


def test_env_override_still_admits_a_registry_disabled_lens(gated_lens, monkeypatch) -> None:
    monkeypatch.setenv("PFACTORY_EXPENSIVE_EXPERIMENTAL_LENS_REVIEW", "1")
    extension_registry.reset_cache()
    names = [lens.name for lens in default_lenses()]
    assert gated_lens in names


def test_ungated_injected_lens_still_tails_in(monkeypatch, tmp_path) -> None:
    """A lens with NO registry entry has no gate to fail — it is admitted."""
    name = "test-injected-lens"
    register_lens(_FakeLens(name))
    try:
        monkeypatch.setenv("PFACTORY_EXTENSION_REGISTRY", _registry(tmp_path, []))
        extension_registry.reset_cache()
        names = [lens.name for lens in default_lenses()]
        assert name in names
    finally:
        del base._REGISTRY[name]
        extension_registry.reset_cache()


def test_no_hardcoded_red_team_special_case_remains() -> None:
    """The general rule replaced the bespoke exception — keep it deleted.

    A special case beside a general rule invites the next person to add a
    second special case instead of a registry entry.
    """
    source = inspect.getsource(base.default_lenses)
    assert 'n == "red-team"' not in source
