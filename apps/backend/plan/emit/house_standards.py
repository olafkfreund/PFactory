"""Attach the team's house standards to the Task Contract (RFC-0012).

RFC-0012 grounds the fleet in the organization's house standards
(best-practices, code-style, testing guides, golden-path templates) so that
PFactory planning / AIFactory coding / TFactory testing follow them, and the
fail-closed ``standards_conformance`` gate can later prove they were applied.

:func:`attach_house_standards` records a standards manifest into
``contract['epic_context']['house_standards']`` from two sources:

1. **baseline** — the RFC-0010 repo ``conventions`` (linters/formatters/test
   layout) the reconnaissance already detected and placed on
   ``contract['baseline']['conventions']``, but which nothing previously
   surfaced for the downstream agents. Always offline-available.
2. **backstage** — a best-effort lookup against the live Backstage catalog,
   keyed off ``provenance.repo`` (``owner/name`` = the
   ``github.com/project-slug`` annotation on a Component entity). Records the
   entity ref + its TechDocs refs + lifecycle/type, plus any golden-path
   Scaffolder ``Template`` entities whose tags intersect the component's stack.

Every recorded source carries a stable ``content_hash`` so a standard cannot be
silently swapped after the human approves the plan, and any deviation waiver can
bind to the exact hash.

Mirrors :mod:`plan.emit.handoff_sanitize`: this module is best-effort and
**never raises** — the Backstage layer is optional and a missing/unreachable
catalog degrades to a ``baseline``-only manifest (or ``available: false``),
because a standards lookup that throws would block every plan. The
``standards_conformance`` gate, by contrast, fails closed; the asymmetry is by
design (see RFC-0012 section 2).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

# ── caps (keep the contract small; refs not bodies) ─────────────────────────
_MAX_TECHDOCS_REFS = 5
_MAX_TEMPLATES = 5
_MAX_TAGS = 25

_PROJECT_SLUG_ANNOTATION = "github.com/project-slug"
_TECHDOCS_ANNOTATION = "backstage.io/techdocs-ref"


class BackstageEntities(Protocol):
    """Minimal shape the adapter needs from a Backstage source.

    ``entities()`` returns the raw catalog entity dicts (Backstage's
    ``{kind, metadata, spec}`` shape). Any object exposing this is accepted, so
    tests inject a tiny fake and production can wrap the existing
    :class:`plan.enrich.knowledge.backstage.BackstageConnector`.
    """

    def entities(self) -> list[dict[str, Any]]: ...


def _content_hash(obj: Any) -> str:
    """Stable ``sha256:...`` digest of a JSON-serializable source-of-truth."""
    try:
        payload = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        payload = repr(obj)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _baseline_source(contract: dict[str, Any]) -> dict[str, Any] | None:
    """Build the ``baseline`` standards source from RFC-0010 conventions.

    Reads ``contract['baseline']['conventions']`` (populated by reconnaissance).
    Returns ``None`` when no conventions were detected (nothing to enforce).
    """
    baseline = contract.get("baseline")
    if not isinstance(baseline, dict):
        return None
    conventions = baseline.get("conventions")
    if not isinstance(conventions, dict) or not conventions:
        return None
    return {
        "source": "baseline",
        "kind": "conventions",
        "conventions": conventions,
        "content_hash": _content_hash(conventions),
    }


def _entity_meta(entity: dict[str, Any]) -> dict[str, Any]:
    meta = entity.get("metadata")
    return meta if isinstance(meta, dict) else {}


def _entity_spec(entity: dict[str, Any]) -> dict[str, Any]:
    spec = entity.get("spec")
    return spec if isinstance(spec, dict) else {}


def _annotation(entity: dict[str, Any], key: str) -> str | None:
    ann = _entity_meta(entity).get("annotations")
    if isinstance(ann, dict):
        value = ann.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _entity_ref(entity: dict[str, Any]) -> str:
    meta = _entity_meta(entity)
    kind = str(entity.get("kind", "Component")).lower()
    ns = str(meta.get("namespace", "default"))
    name = str(meta.get("name", "") or "entity")
    return f"{kind}:{ns}/{name}"


def _tags(entity: dict[str, Any]) -> list[str]:
    tags = _entity_meta(entity).get("tags")
    if isinstance(tags, list):
        return [str(t) for t in tags if isinstance(t, str)][:_MAX_TAGS]
    return []


def _component_for_repo(
    entities: list[dict[str, Any]], repo: str
) -> dict[str, Any] | None:
    """Find the Component whose ``github.com/project-slug`` matches ``repo``.

    Match is case-insensitive on ``owner/name``.
    """
    want = repo.strip().lower()
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        if str(entity.get("kind", "")).lower() != "component":
            continue
        slug = _annotation(entity, _PROJECT_SLUG_ANNOTATION)
        if slug and slug.strip().lower() == want:
            return entity
    return None


def _component_source(entity: dict[str, Any]) -> dict[str, Any]:
    """Build the ``backstage`` component standards source."""
    spec = _entity_spec(entity)
    techdocs = _annotation(entity, _TECHDOCS_ANNOTATION)
    techdocs_refs = [techdocs][:_MAX_TECHDOCS_REFS] if techdocs else []
    ref = _entity_ref(entity)
    source: dict[str, Any] = {
        "source": "backstage",
        "kind": "component",
        "entity_ref": ref,
    }
    if techdocs_refs:
        source["techdocs_refs"] = techdocs_refs
    lifecycle = spec.get("lifecycle")
    if isinstance(lifecycle, str) and lifecycle:
        source["lifecycle"] = lifecycle
    spec_type = spec.get("type")
    if isinstance(spec_type, str) and spec_type:
        source["spec_type"] = spec_type
    tags = _tags(entity)
    if tags:
        source["tags"] = tags
    # Hash binds to the stable identity + the standards it points at.
    source["content_hash"] = _content_hash(
        {
            "entity_ref": ref,
            "techdocs_refs": techdocs_refs,
            "lifecycle": source.get("lifecycle"),
            "spec_type": source.get("spec_type"),
        }
    )
    return source


def _golden_path_sources(
    entities: list[dict[str, Any]], component_tags: list[str]
) -> list[dict[str, Any]]:
    """Recorded golden-path ``Template`` entities matching the component stack.

    A template matches when its tags intersect the component's tags (e.g. a
    Rust component picks up the ``rust-service`` template). Without component
    tags there is no basis to pick a blessed stack, so none are recorded.
    """
    if not component_tags:
        return []
    want = {t.lower() for t in component_tags}
    out: list[dict[str, Any]] = []
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        if str(entity.get("kind", "")).lower() != "template":
            continue
        tags = _tags(entity)
        if not want.intersection({t.lower() for t in tags}):
            continue
        ref = _entity_ref(entity)
        out.append(
            {
                "source": "backstage",
                "kind": "template",
                "entity_ref": ref,
                "tags": tags,
                "content_hash": _content_hash({"entity_ref": ref, "tags": tags}),
            }
        )
        if len(out) >= _MAX_TEMPLATES:
            break
    return out


def _backstage_sources(
    backstage: BackstageEntities | None, repo: str | None
) -> tuple[list[dict[str, Any]], str | None]:
    """Best-effort Backstage sources for ``repo``. Returns ``(sources, error)``.

    Never raises. ``error`` is a short string when the lookup was attempted but
    unavailable (no client / no entities / no matching component / failure).
    """
    if backstage is None:
        return [], "no backstage client configured"
    if not repo:
        return [], "no provenance.repo to key the lookup on"
    try:
        entities = backstage.entities()
    except Exception as exc:  # best-effort: a failed catalog never blocks a plan
        return [], f"backstage unavailable: {exc}"[:200]
    if not isinstance(entities, list) or not entities:
        return [], "backstage catalog returned no entities"

    component = _component_for_repo(entities, repo)
    if component is None:
        return [], f"no catalog component for {repo}"

    sources = [_component_source(component)]
    sources.extend(_golden_path_sources(entities, _tags(component)))
    return sources, None


def attach_house_standards(
    contract: dict[str, Any],
    plan: Any = None,
    *,
    backstage: BackstageEntities | None = None,
) -> dict[str, Any]:
    """Attach the RFC-0012 house-standards manifest to ``epic_context`` in place.

    Records ``contract['epic_context']['house_standards']`` from the RFC-0010
    ``baseline.conventions`` already on the contract plus a best-effort Backstage
    lookup keyed off ``provenance.repo``. ``backstage`` is an optional injected
    client exposing ``entities()`` (tests inject a fake; absent => baseline-only,
    or ``available: false`` when even the baseline is empty). Returns the
    contract for composability. **Never raises.**
    """
    try:
        sources: list[dict[str, Any]] = []

        baseline = _baseline_source(contract)
        if baseline is not None:
            sources.append(baseline)

        provenance = contract.get("provenance")
        repo = provenance.get("repo") if isinstance(provenance, dict) else None
        bs_sources, bs_error = _backstage_sources(backstage, repo)
        sources.extend(bs_sources)

        manifest: dict[str, Any] = {
            "available": bool(sources),
            "sources": sources,
        }
        # Surface why nothing came from Backstage only when we got nothing at
        # all (a baseline-only manifest is still 'available').
        if not sources and bs_error:
            manifest["error"] = bs_error

        epic_context = contract.get("epic_context")
        if not isinstance(epic_context, dict):
            epic_context = {}
            contract["epic_context"] = epic_context
        epic_context["house_standards"] = manifest

        return contract
    except Exception:
        return contract
