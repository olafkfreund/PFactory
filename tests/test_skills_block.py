"""The skill lane is matched by capability, conservatively (#687).

The registry's `kind: skill` rows were inert: PFactory raised an obligation and
handed the coder a contract that never mentioned the skill this repo ships for
exactly that work. These pin the matcher — and, as much, what it must NOT
match: a wrongly attached skill points a coder at irrelevant guidance, while a
missed one is only the behaviour we had before.

Offline: the registry loader is injected/monkeypatched, no catalogue I/O.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.emit import skills_block  # noqa: E402
from plan.emit.skills_block import attach_skills, build_skills_block, derive_needs  # noqa: E402
from plan.registry.models import Registry, RegistryEntry  # noqa: E402


class _Plan:
    def __init__(self, plan_type: str | None = None):
        self.plan_type = plan_type


def _compliance(*, obligations=(), data_classes=()) -> dict:
    return {
        "compliance": {
            "available": True,
            "obligations": list(obligations),
            "data_classes": list(data_classes),
        }
    }


def _registry(*entries: RegistryEntry) -> Registry:
    return Registry(entries=list(entries))


def _skill(entry_id: str, caps: list[str], *, enabled: bool = True) -> RegistryEntry:
    return RegistryEntry(
        id=entry_id,
        kind="skill",
        title=entry_id,
        capabilities=caps,
        enabled=enabled,
        config={"path": f"skills/engineering/{entry_id}.md"},
    )


# ── derive_needs: one case per table row ──────────────────────────────────────


def test_obligations_ask_for_the_compliance_block_capability():
    assert derive_needs(_compliance(obligations=[{"title": "retention"}])) == {"compliance-block"}


@pytest.mark.parametrize(
    "data_class",
    ["account", "location", "personal-profile", "profile", "profiling", "user-contact"],
)
def test_personal_data_classes_ask_for_privacy(data_class):
    assert derive_needs(_compliance(data_classes=[data_class])) == {"privacy"}


def test_a_mobile_plan_asks_for_mobile():
    assert derive_needs({}, _Plan("mobile-app")) == {"mobile"}


def test_a_plain_software_plan_asks_for_nothing():
    assert derive_needs({}, _Plan("software-service")) == set()


def test_store_distribution_alone_asks_for_nothing():
    """The guard: `store-distribution` is about app stores, which a web plan can
    mention. Mapping it would pull the mobile skill into a non-mobile build."""
    assert derive_needs(_compliance(data_classes=["store-distribution"])) == set()


def test_a_compliance_block_that_found_nothing_asks_for_nothing():
    assert derive_needs(_compliance()) == set()


# ── matching ──────────────────────────────────────────────────────────────────


def _with_registry(monkeypatch, registry: Registry):
    monkeypatch.setattr("plan.registry.load_registry", lambda *_a, **_k: registry)


def test_an_intersecting_row_matches(monkeypatch):
    _with_registry(monkeypatch, _registry(_skill("skill:privacy", ["privacy", "gdpr"])))
    block = build_skills_block(_compliance(data_classes=["profile"]))
    assert [s["id"] for s in block["skills"]] == ["skill:privacy"]
    assert block["skills"][0]["path"] == "skills/engineering/skill:privacy.md"


def test_a_disabled_row_never_matches(monkeypatch):
    _with_registry(monkeypatch, _registry(_skill("skill:privacy", ["privacy"], enabled=False)))
    assert build_skills_block(_compliance(data_classes=["profile"]))["skills"] == []


def test_a_row_with_no_capabilities_never_matches(monkeypatch):
    _with_registry(monkeypatch, _registry(_skill("skill:empty", [])))
    assert build_skills_block(_compliance(data_classes=["profile"]))["skills"] == []


def test_a_row_that_does_not_intersect_never_matches(monkeypatch):
    _with_registry(monkeypatch, _registry(_skill("skill:mobile", ["mobile", "ios"])))
    assert build_skills_block(_compliance(data_classes=["profile"]))["skills"] == []


def test_looked_and_found_nothing_is_not_could_not_look(monkeypatch):
    _with_registry(monkeypatch, _registry(_skill("skill:mobile", ["mobile"])))
    block = build_skills_block({})
    assert block["available"] is True  # the catalogue WAS read
    assert block["skills"] == []
    assert block["matched_on"] == []


def test_an_unreadable_catalogue_degrades_and_never_raises(monkeypatch):
    def _boom(*_a, **_k):
        raise OSError("catalogue gone")

    monkeypatch.setattr("plan.registry.load_registry", _boom)
    block = build_skills_block(_compliance(data_classes=["profile"]))
    assert block["available"] is False
    assert block["skills"] == []
    assert block["matched_on"] == ["privacy"]  # what we would have looked for


# ── attach ────────────────────────────────────────────────────────────────────


def test_attach_writes_the_block_into_epic_context(monkeypatch):
    _with_registry(monkeypatch, _registry(_skill("skill:mobile", ["mobile"])))
    contract: dict = {}
    out = attach_skills(contract, _Plan("mobile-app"))
    assert out is contract
    assert [s["id"] for s in contract["epic_context"]["skills"]["skills"]] == ["skill:mobile"]


def test_attach_never_raises(monkeypatch):
    monkeypatch.setattr(skills_block, "build_skills_block", lambda *_a, **_k: 1 / 0)
    contract: dict = {}
    assert attach_skills(contract, None) is contract  # degraded, not raised


def test_a_missing_catalogue_is_not_reported_as_read(monkeypatch, tmp_path):
    """`available: true` must mean "we looked", not "nothing raised".

    `_load_catalogue` degrades a MISSING directory to an empty list rather than
    raising, so before the review on #760 an absent catalogue was
    indistinguishable from a catalogue with no matching skill — the exact
    distinction this block's docstring promises to keep.
    """
    from plan.registry import loader

    monkeypatch.setattr(loader, "_CATALOGUE_DIR", tmp_path / "not-there")

    block = build_skills_block(_compliance(data_classes=["profile"]))

    assert block["available"] is False
    assert block["skills"] == []
    assert block["matched_on"] == ["privacy"]  # what we would have looked for
