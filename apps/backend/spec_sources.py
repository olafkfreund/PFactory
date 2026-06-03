"""Generic acceptance-criteria ingestion — decouple from AIFactory (#40).

PFactory's Planner reads ``context/aifactory_spec.md`` — a markdown spec
whose acceptance criteria it turns into a lane-tagged test plan. Until now
that file could only come from an AIFactory ``spec.md`` snapshot, which
caps adoption to AIFactory users (epic #33, decision DEC-001: PFactory is
a standalone product with AIFactory as the wedge).

This module ingests **any** acceptance-criteria source — plain markdown,
Gherkin ``.feature`` files, or EARS-notation requirements — and normalises
it into the same canonical spec markdown the pipeline already consumes. A
team with no AIFactory can point PFactory at a `.feature` or a markdown AC
list and get a triaged test report.

Pipeline seam: :func:`write_spec_markdown` drops a normalised spec at
``context/aifactory_spec.md`` so the existing Planner path is unchanged.

Pure + dependency-light (regex + string handling only).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class SpecFormat(str, Enum):
    """A supported acceptance-criteria source format."""

    MARKDOWN = "markdown"
    GHERKIN = "gherkin"
    EARS = "ears"


class SpecSourceError(ValueError):
    """Raised when a source can't be parsed into any acceptance criteria."""


@dataclass(frozen=True)
class AcceptanceCriterion:
    """One acceptance criterion, with a stable ``AC#N`` id."""

    id: str
    text: str


@dataclass(frozen=True)
class NormalizedSpec:
    """A source-neutral spec the Planner can consume."""

    title: str
    description: str
    criteria: tuple[AcceptanceCriterion, ...]
    source_format: SpecFormat

    def to_markdown(self) -> str:
        """Render the canonical ``aifactory_spec.md`` shape.

        The Planner extracts ``AC#N`` markers from this section (see
        ``prompts/planner.md``), so the heading + ``AC#N:`` prefixes are
        the contract.
        """
        lines = [f"# {self.title}", ""]
        if self.description:
            lines += [self.description.strip(), ""]
        lines += ["## Acceptance Criteria", ""]
        for ac in self.criteria:
            lines.append(f"- **{ac.id}:** {ac.text}")
        lines.append("")
        lines.append(f"> Ingested from a {self.source_format.value} source by PFactory.")
        return "\n".join(lines) + "\n"


# ── format detection ───────────────────────────────────────────────────

_GHERKIN_FEATURE = re.compile(r"^\s*Feature:", re.MULTILINE)
_GHERKIN_SCENARIO = re.compile(r"^\s*Scenario(?: Outline)?:", re.MULTILINE)
# EARS templates: "<...> shall <...>", "When <x>, the <y> shall <z>", etc.
_EARS_SHALL = re.compile(r"\bshall\b", re.IGNORECASE)


def detect_format(text: str, *, filename: str | None = None) -> SpecFormat:
    """Best-effort format detection from filename + content."""
    if filename:
        low = filename.lower()
        if low.endswith(".feature"):
            return SpecFormat.GHERKIN
    if _GHERKIN_FEATURE.search(text) and _GHERKIN_SCENARIO.search(text):
        return SpecFormat.GHERKIN
    # EARS if a clear majority of non-empty, non-heading lines use "shall".
    content_lines = [
        ln for ln in text.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    if content_lines:
        shall = sum(1 for ln in content_lines if _EARS_SHALL.search(ln))
        if shall >= 2 and shall / len(content_lines) >= 0.5:
            return SpecFormat.EARS
    return SpecFormat.MARKDOWN


# ── helpers ────────────────────────────────────────────────────────────

def _number(criteria: list[str]) -> list[AcceptanceCriterion]:
    """Assign stable ``AC#N`` ids to non-empty criterion strings."""
    return [AcceptanceCriterion(id=f"AC#{i}", text=t.strip())
            for i, t in enumerate((c for c in criteria if c.strip()), start=1)]


def _first_h1(text: str, default: str) -> str:
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return default


_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*\S)\s*$")
_HEADING = re.compile(r"^\s*#{1,6}\s+(.*\S)\s*$")
_AC_INLINE = re.compile(r"\bAC\s*#?\s*\d+\s*[:.\-]\s*(.*\S)", re.IGNORECASE)
_AC_HEADING_WORDS = ("acceptance criteria", "acceptance", "requirements")


# ── markdown ───────────────────────────────────────────────────────────

def parse_markdown(text: str, *, title: str | None = None) -> NormalizedSpec:
    """Parse acceptance criteria from a markdown doc.

    Strategy (first that yields ≥1 criterion wins):
      1. Bullets/numbered items under an "Acceptance Criteria" /
         "Acceptance" / "Requirements" heading.
      2. Any ``AC#N: ...`` inline lines anywhere in the doc.
    """
    lines = text.splitlines()
    title = title or _first_h1(text, "Untitled spec")

    # 1) collect bullets under an acceptance heading
    criteria: list[str] = []
    in_section = False
    for ln in lines:
        h = _HEADING.match(ln)
        if h:
            in_section = any(w in h.group(1).lower() for w in _AC_HEADING_WORDS)
            continue
        if in_section:
            b = _BULLET.match(ln)
            if b:
                criteria.append(b.group(1))

    # 2) fall back to inline AC#N markers
    if not criteria:
        for ln in lines:
            m = _AC_INLINE.search(ln)
            if m:
                criteria.append(m.group(1))

    acs = _number(criteria)
    if not acs:
        raise SpecSourceError(
            "no acceptance criteria found — add an '## Acceptance Criteria' "
            "section with bullets, or 'AC#N: ...' lines."
        )
    return NormalizedSpec(
        title=title, description="", criteria=tuple(acs),
        source_format=SpecFormat.MARKDOWN,
    )


# ── gherkin ────────────────────────────────────────────────────────────

def parse_gherkin(text: str, *, title: str | None = None) -> NormalizedSpec:
    """Parse a Gherkin ``.feature`` — one acceptance criterion per Scenario.

    Each criterion is ``<Scenario name> — <Given/When/Then steps joined>``.
    """
    feature_title = title
    description_parts: list[str] = []
    criteria: list[str] = []
    cur_name: str | None = None
    cur_steps: list[str] = []
    _STEP = re.compile(r"^\s*(Given|When|Then|And|But)\b\s*(.*)$", re.IGNORECASE)

    def _flush() -> None:
        nonlocal cur_name, cur_steps
        if cur_name is not None:
            steps = "; ".join(s for s in cur_steps if s)
            criteria.append(f"{cur_name} — {steps}" if steps else cur_name)
        cur_name, cur_steps = None, []

    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("Feature:"):
            feature_title = feature_title or s[len("Feature:"):].strip()
            continue
        if s.startswith(("Scenario:", "Scenario Outline:")):
            _flush()
            cur_name = s.split(":", 1)[1].strip()
            continue
        step = _STEP.match(s)
        if step and cur_name is not None:
            cur_steps.append(f"{step.group(1).lower()} {step.group(2).strip()}".strip())
        elif cur_name is None and s and not s.startswith(("#", "@", "Background:")):
            description_parts.append(s)
    _flush()

    acs = _number(criteria)
    if not acs:
        raise SpecSourceError("no Gherkin Scenario blocks found.")
    return NormalizedSpec(
        title=feature_title or "Untitled feature",
        description=" ".join(description_parts).strip(),
        criteria=tuple(acs),
        source_format=SpecFormat.GHERKIN,
    )


# ── EARS ───────────────────────────────────────────────────────────────

# EARS line shapes (Mavin et al.): ubiquitous / event / state / option /
# unwanted-behaviour — all contain "shall". We also accept a leading bullet.
_EARS_LINE = re.compile(
    r"^\s*(?:[-*+]\s+|\d+[.)]\s+)?((?:When|While|Where|If)\b.*\bshall\b.*"
    r"|The\b.*\bshall\b.*|.*\bshall\b.*)$",
    re.IGNORECASE,
)


def parse_ears(text: str, *, title: str | None = None) -> NormalizedSpec:
    """Parse EARS-notation requirements — each ``shall`` line is a criterion."""
    title = title or _first_h1(text, "Untitled requirements")
    criteria: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        m = _EARS_LINE.match(s)
        if m:
            criteria.append(m.group(1).strip())

    acs = _number(criteria)
    if not acs:
        raise SpecSourceError(
            "no EARS requirements found — expected lines containing 'shall'."
        )
    return NormalizedSpec(
        title=title, description="", criteria=tuple(acs),
        source_format=SpecFormat.EARS,
    )


# ── unified entry points ───────────────────────────────────────────────

_PARSERS = {
    SpecFormat.MARKDOWN: parse_markdown,
    SpecFormat.GHERKIN: parse_gherkin,
    SpecFormat.EARS: parse_ears,
}


def ingest(
    text: str,
    *,
    fmt: SpecFormat | None = None,
    filename: str | None = None,
    title: str | None = None,
) -> NormalizedSpec:
    """Normalise any AC source into a :class:`NormalizedSpec`.

    ``fmt`` forces a format; otherwise it is auto-detected.
    """
    if not text or not text.strip():
        raise SpecSourceError("empty source.")
    fmt = fmt or detect_format(text, filename=filename)
    return _PARSERS[fmt](text, title=title)


def ingest_file(path: Path, *, fmt: SpecFormat | None = None,
                title: str | None = None) -> NormalizedSpec:
    """Ingest from a file, using its name for format detection."""
    p = Path(path)
    return ingest(p.read_text(), fmt=fmt, filename=p.name, title=title)


def write_spec_markdown(spec: NormalizedSpec, context_dir: Path) -> Path:
    """Write ``spec.to_markdown()`` to ``<context_dir>/aifactory_spec.md``.

    This is the pipeline seam: after writing, the Planner consumes the file
    exactly as it would an AIFactory snapshot. Returns the written path.
    """
    context_dir = Path(context_dir)
    context_dir.mkdir(parents=True, exist_ok=True)
    dst = context_dir / "aifactory_spec.md"
    dst.write_text(spec.to_markdown())
    return dst


if __name__ == "__main__":  # pragma: no cover - operator ingestion CLI
    # Ingest a non-AIFactory acceptance-criteria file into a spec's context.
    #   python spec_sources.py login.feature --context <spec_dir>/context
    #   python spec_sources.py reqs.md --format ears        # print only
    import argparse as _argparse
    import sys as _sys

    _ap = _argparse.ArgumentParser(
        description="Normalise a markdown / Gherkin / EARS acceptance-criteria "
        "source into PFactory's canonical spec markdown.",
    )
    _ap.add_argument("source", help="path to the AC source file")
    _ap.add_argument("--format", choices=[f.value for f in SpecFormat],
                     help="force a source format (default: auto-detect)")
    _ap.add_argument("--title", help="override the spec title")
    _ap.add_argument("--context", help="write aifactory_spec.md into this context dir")
    _args = _ap.parse_args()

    try:
        _spec = ingest_file(
            Path(_args.source),
            fmt=SpecFormat(_args.format) if _args.format else None,
            title=_args.title,
        )
    except (SpecSourceError, OSError) as _exc:
        print(f"error: {_exc}", file=_sys.stderr)
        _sys.exit(2)

    print(f"# {_spec.title}  ({_spec.source_format.value}, "
          f"{len(_spec.criteria)} acceptance criteria)", file=_sys.stderr)
    if _args.context:
        _dst = write_spec_markdown(_spec, Path(_args.context))
        print(f"wrote {_dst}", file=_sys.stderr)
    else:
        print(_spec.to_markdown())
