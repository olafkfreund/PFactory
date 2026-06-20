"""Unit tests for the RFC-0012 house-standards adapter (Backstage layer mocked).

Run: PYTHONPATH=apps/backend apps/backend/.venv/bin/pytest \
        apps/backend/plan/emit/test_house_standards.py
"""

from __future__ import annotations

from typing import Any

from plan.emit.house_standards import attach_house_standards


class _FakeBackstage:
    """In-memory Backstage source; raises if ``boom`` set (failure path)."""

    def __init__(self, entities: list[dict[str, Any]], *, boom: bool = False) -> None:
        self._entities = entities
        self._boom = boom

    def entities(self) -> list[dict[str, Any]]:
        if self._boom:
            raise RuntimeError("catalog down")
        return self._entities


def _component(
    repo: str,
    *,
    techdocs: str | None = None,
    tags=None,
    lifecycle="production",
    spec_type="service",
):
    annotations = {"github.com/project-slug": repo}
    if techdocs:
        annotations["backstage.io/techdocs-ref"] = techdocs
    return {
        "kind": "Component",
        "metadata": {
            "name": "svc",
            "namespace": "default",
            "annotations": annotations,
            "tags": tags or [],
        },
        "spec": {"type": spec_type, "lifecycle": lifecycle},
    }


def _template(name: str, tags: list[str]):
    return {
        "kind": "Template",
        "metadata": {"name": name, "namespace": "default", "tags": tags},
        "spec": {"type": "service"},
    }


def _contract(repo: str | None = "olafkfreund/AIFactory", conventions=None) -> dict[str, Any]:
    c: dict[str, Any] = {
        "contract_version": "2",
        "feature": "x",
        "workflow_type": "feature",
        "phases": [],
    }
    if repo:
        c["provenance"] = {"source": "pfactory", "repo": repo}
    if conventions is not None:
        c["baseline"] = {"available": True, "conventions": conventions}
    return c


# --------------------------------------------------------------------------- #
# baseline source (RFC-0010 conventions, always offline)
# --------------------------------------------------------------------------- #


def test_baseline_conventions_surfaced_with_hash():
    c = _contract(repo=None, conventions={"linter": "ruff", "test_layout": "tests/"})
    attach_house_standards(c, backstage=None)
    hs = c["epic_context"]["house_standards"]
    assert hs["available"] is True
    baseline = [s for s in hs["sources"] if s["source"] == "baseline"]
    assert len(baseline) == 1
    assert baseline[0]["conventions"] == {"linter": "ruff", "test_layout": "tests/"}
    assert baseline[0]["content_hash"].startswith("sha256:")


def test_baseline_hash_is_stable_and_content_bound():
    c1 = _contract(repo=None, conventions={"linter": "ruff"})
    c2 = _contract(repo=None, conventions={"linter": "ruff"})
    c3 = _contract(repo=None, conventions={"linter": "black"})
    attach_house_standards(c1, backstage=None)
    attach_house_standards(c2, backstage=None)
    attach_house_standards(c3, backstage=None)

    def h(c):
        return c["epic_context"]["house_standards"]["sources"][0]["content_hash"]

    assert h(c1) == h(c2)  # same content -> same hash
    assert h(c1) != h(c3)  # different content -> different hash


def test_no_conventions_no_backstage_marks_unavailable():
    c = _contract(repo=None)  # no baseline, no client
    attach_house_standards(c, backstage=None)
    hs = c["epic_context"]["house_standards"]
    assert hs["available"] is False
    assert hs["sources"] == []
    assert hs["error"]  # explains why


# --------------------------------------------------------------------------- #
# backstage source (catalog mocked)
# --------------------------------------------------------------------------- #


def test_backstage_component_matched_by_project_slug():
    entities = [
        _component(
            "olafkfreund/AIFactory",
            techdocs="url:https://github.com/olafkfreund/AIFactory/tree/dev",
            tags=["rust"],
        ),
        _component("someone/else"),
    ]
    c = _contract(repo="olafkfreund/AIFactory")
    attach_house_standards(c, backstage=_FakeBackstage(entities))
    sources = c["epic_context"]["house_standards"]["sources"]
    comp = [s for s in sources if s.get("kind") == "component"]
    assert len(comp) == 1
    assert comp[0]["entity_ref"] == "component:default/svc"
    assert comp[0]["techdocs_refs"] == ["url:https://github.com/olafkfreund/AIFactory/tree/dev"]
    assert comp[0]["lifecycle"] == "production"
    assert comp[0]["content_hash"].startswith("sha256:")


def test_backstage_match_is_case_insensitive():
    entities = [_component("OlafKFreund/AIFactory")]
    c = _contract(repo="olafkfreund/aifactory")
    attach_house_standards(c, backstage=_FakeBackstage(entities))
    assert any(
        s.get("kind") == "component" for s in c["epic_context"]["house_standards"]["sources"]
    )


def test_golden_path_template_matched_by_tag_intersection():
    entities = [
        _component("olafkfreund/AIFactory", tags=["rust", "service"]),
        _template("rust-service", ["rust", "service", "axum", "nix"]),
        _template("typescript-service", ["typescript", "node"]),
    ]
    c = _contract(repo="olafkfreund/AIFactory")
    attach_house_standards(c, backstage=_FakeBackstage(entities))
    templates = [
        s for s in c["epic_context"]["house_standards"]["sources"] if s.get("kind") == "template"
    ]
    assert len(templates) == 1
    assert templates[0]["entity_ref"] == "template:default/rust-service"


def test_no_matching_component_records_error_but_keeps_baseline():
    entities = [_component("someone/else")]
    c = _contract(repo="olafkfreund/AIFactory", conventions={"linter": "ruff"})
    attach_house_standards(c, backstage=_FakeBackstage(entities))
    hs = c["epic_context"]["house_standards"]
    assert hs["available"] is True  # baseline still present
    assert [s["source"] for s in hs["sources"]] == ["baseline"]
    # baseline present => no top-level error (error only when nothing retrieved)
    assert "error" not in hs


# --------------------------------------------------------------------------- #
# best-effort: never raises, degrades cleanly
# --------------------------------------------------------------------------- #


def test_backstage_failure_degrades_to_baseline_never_raises():
    c = _contract(repo="olafkfreund/AIFactory", conventions={"linter": "ruff"})
    attach_house_standards(c, backstage=_FakeBackstage([], boom=True))
    hs = c["epic_context"]["house_standards"]
    assert hs["available"] is True
    assert [s["source"] for s in hs["sources"]] == ["baseline"]


def test_backstage_failure_with_no_baseline_is_unavailable():
    c = _contract(repo="olafkfreund/AIFactory")
    attach_house_standards(c, backstage=_FakeBackstage([], boom=True))
    hs = c["epic_context"]["house_standards"]
    assert hs["available"] is False
    assert "backstage unavailable" in hs["error"]


def test_malformed_contract_never_raises():
    # Pathological inputs must not throw (a sanitizer that throws leaks/blocks).
    for bad in [{}, {"baseline": "nope"}, {"provenance": 7}]:
        attach_house_standards(bad, backstage=None)  # no exception
        assert isinstance(bad.get("epic_context", {}), dict)


def test_preserves_existing_epic_context_keys():
    c = _contract(repo=None, conventions={"linter": "ruff"})
    c["epic_context"] = {"constraints": [{"adapter": "aws"}], "knowledge_links": []}
    attach_house_standards(c, backstage=None)
    assert c["epic_context"]["constraints"] == [{"adapter": "aws"}]
    assert "house_standards" in c["epic_context"]
