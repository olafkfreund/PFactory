"""Template discovery, selection, and policy enforcement (issue #26).

This module turns on-disk Backstage ``template.yaml`` files (each carrying an
embedded PFactory ``policy`` block) into :class:`~plan.templates.models.Template`
objects, picks the best-matching template for a given plan, assembles the loose
policy-check ``context`` from a :class:`~plan.models.NormalizedPlan`, and runs
:meth:`Template.check` to produce review
:class:`~plan.review.models.Finding` objects.

The whole module is intentionally permissive: missing files, malformed plans,
and absent enrichment all degrade gracefully (empty result / ``None``) rather
than raising, so template enforcement can be wired into review without becoming
a new failure surface.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from plan.review.models import Finding
from plan.templates.models import Policy, Template

if TYPE_CHECKING:
    from plan.models import NormalizedPlan

# Default discovery root = this package's directory.
_PACKAGE_DIR = Path(__file__).resolve().parent

# Cheap keyword index used by :func:`select_template` to map free-text plan
# signals onto a template's tags/metadata.
_WORD_RE = re.compile(r"[a-z0-9]+")

# A small region vocabulary so :func:`build_context` can pull a region out of
# free text without an exhaustive cloud-region table.
_REGION_RE = re.compile(
    r"\b("
    r"(?:europe|us|asia|australia|northamerica|southamerica)"
    r"-[a-z]+\d+"
    r")\b",
    re.IGNORECASE,
)

# IAM role references look like ``roles/<something>`` in GCP plans. The body
# may end on a sentence boundary, so a trailing dot is not part of the role.
_IAM_RE = re.compile(r"\broles/[a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+)*")

# Generic words that carry no matching signal — excluded from keyword overlap
# so prose like "a project" doesn't accidentally match an unrelated plan.
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "the",
        "to",
        "of",
        "in",
        "on",
        "for",
        "with",
        "or",
        "it",
        "is",
        "as",
        "at",
        "by",
        "be",
        "this",
        "that",
        "new",
        "set",
        "up",
        "create",
        "add",
        "build",
        "make",
        "use",
    }
)


def load_template(path: str | Path) -> Template:
    """Read a single ``template.yaml`` into a :class:`Template`.

    The top-level ``policy`` key holds the PFactory policy extension; every
    other key is the standard Backstage Template document. A missing or empty
    ``policy`` block yields an empty (non-blocking) :class:`Policy`.
    """
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    policy_data = data.pop("policy", None) or {}
    template = Template(**data)
    template.policy = Policy(**policy_data)
    return template


def load_templates(root: str | Path | None = None) -> dict[str, Template]:
    """Discover every ``*/template.yaml`` under ``root``, keyed by ``metadata.name``.

    ``root`` defaults to this package's directory. Templates that fail to parse
    are skipped silently so one bad file can't break discovery of the rest.
    """
    base = Path(root) if root is not None else _PACKAGE_DIR
    templates: dict[str, Template] = {}
    for template_file in sorted(base.glob("*/template.yaml")):
        try:
            template = load_template(template_file)
        except Exception:
            continue
        templates[template.metadata.name] = template
    return templates


def _template_keywords(template: Template) -> set[str]:
    """Collect the lowercase keyword set a template can be matched on."""
    meta = template.metadata
    words: set[str] = set()
    words.update(t.lower() for t in meta.tags)
    for field in (meta.name, meta.title, meta.description):
        words.update(_WORD_RE.findall(field.lower()))
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def _plan_keywords(plan: NormalizedPlan) -> set[str]:
    """Collect the lowercase keyword set extracted from a plan."""
    words: set[str] = set()
    if plan.plan_type:
        words.update(_WORD_RE.findall(plan.plan_type.lower()))
    for field in (plan.title or "", plan.description or "", plan.raw_text or ""):
        words.update(_WORD_RE.findall(field.lower()))
    for criterion in plan.criteria:
        words.update(_WORD_RE.findall(criterion.text.lower()))
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def select_template(
    plan: NormalizedPlan,
    templates: dict[str, Template] | None = None,
) -> Template | None:
    """Pick the template whose keywords best overlap the plan, or ``None``.

    Uses a simple keyword-overlap heuristic between a template's
    tags/name/title/description and the plan's ``plan_type`` + free text. The
    template with the most shared keywords wins; ties break alphabetically by
    name for determinism. Returns ``None`` when nothing overlaps.
    """
    catalog = templates if templates is not None else load_templates()
    if not catalog:
        return None

    plan_words = _plan_keywords(plan)
    if not plan_words:
        return None

    best: Template | None = None
    best_score = 0
    for name in sorted(catalog):
        template = catalog[name]
        overlap = len(_template_keywords(template) & plan_words)
        if overlap > best_score:
            best_score = overlap
            best = template
    return best if best_score > 0 else None


def _collect_tags(plan: NormalizedPlan) -> list[str]:
    """Gather tags from the plan body and enrichment findings, defensively."""
    tags: list[str] = []

    def _extend(value: object) -> None:
        if isinstance(value, str):
            tags.append(value)
        elif isinstance(value, (list, tuple, set)):
            tags.extend(str(v) for v in value if isinstance(v, str))

    for finding in (*plan.enrichment.infra, *plan.enrichment.knowledge):
        if isinstance(finding, dict):
            _extend(finding.get("tags"))
            _extend(finding.get("tag"))

    # ``key=value`` / ``key: value`` tag mentions in free text (e.g. acceptance
    # criteria that spell out the required labels).
    text = " ".join(part for part in (plan.title, plan.description, plan.raw_text) if part)
    for criterion in plan.criteria:
        text += " " + criterion.text
    for match in re.finditer(r"\b([a-z][a-z0-9-]*)\s*[:=]", text.lower()):
        tags.append(match.group(1))

    # De-duplicate while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for tag in tags:
        if tag and tag not in seen:
            seen.add(tag)
            ordered.append(tag)
    return ordered


def build_context(plan: NormalizedPlan) -> dict:
    """Assemble the loose policy-check ``context`` from a plan.

    Produces ``{"tags", "region", "iam", "text"}`` where each value is derived
    defensively from the plan body, its acceptance criteria, and any enrichment
    findings. Missing signals simply yield empty values rather than errors.
    """
    text_parts = [part for part in (plan.title, plan.description, plan.raw_text) if part]
    text_parts.extend(c.text for c in plan.criteria)
    text = "\n".join(text_parts)

    region_match = _REGION_RE.search(text)
    region = region_match.group(1).lower() if region_match else None

    iam = sorted({m.group(0) for m in _IAM_RE.finditer(text)})

    return {
        "tags": _collect_tags(plan),
        "region": region,
        "iam": iam,
        "text": text,
    }


def enforce(
    plan: NormalizedPlan,
    *,
    templates: dict[str, Template] | None = None,
) -> list[Finding]:
    """Select a template for ``plan`` and enforce its policy.

    Returns the :class:`Finding` list from :meth:`Template.check` against
    :func:`build_context`, or ``[]`` when no template matches the plan.
    """
    template = select_template(plan, templates)
    if template is None:
        return []
    return template.check(build_context(plan))
