"""Attach the per-project constitution to the Task Contract (RFC-0015 §3.1).

RFC-0015 borrows spec-kit's ``constitution`` — a single, version-controlled,
human-authored governing-principles artifact (testing policy, architecture
constraints, quality bars, security posture) — but, unlike spec-kit, does not
leave it as a prompt-reference. It *feeds the existing controls*:

- the PFactory planner injects it into the decomposition prompt (the same
  pattern RFC-0012 uses for house standards) and into the readiness checks, and
- the downstream ``standards_conformance`` gate treats clauses tagged
  ``enforceable: true`` as **HARD** checks (closing spec-kit's soft-enforcement
  gap).

PFactory's contribution is to *produce* the ``epic_context.constitution`` block:
``{ source, principles[{id,text,enforceable}], enforceable_ids[], available }``.
Source of truth is the project's ``.factory/constitution.md`` (mirrored to
Backstage per RFC-0012; the Backstage path lands when that connector grows a
constitution lookup). It is read during reconnaissance from the read-only
checkout and stashed on the plan, so emit never needs the repo again.

Like :mod:`plan.emit.house_standards`, this module is **best-effort and never
raises**: a missing/unreadable constitution degrades to ``available: false``
(the gate then scores it ``not_applicable`` — never a false pass), and an absent
block means today's behaviour. Retrieval **degrades, never fakes**.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# The conventional location of the per-project constitution in the target repo.
CONSTITUTION_PATH = ".factory/constitution.md"

# A heading line that opens a principle, e.g. "## P1: Test-first" or "### Testing".
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
# A bold-led principle line, e.g. "- **P2 (enforceable):** No plaintext secrets".
_BOLD_RE = re.compile(r"^\s*(?:[-*+]\s+)?\*\*(.+?)\*\*\s*[:.\-]?\s*(.*)$")
# An explicit id token inside a heading/label, e.g. "P1", "P12".
_ID_RE = re.compile(r"\b([A-Z]{1,4}-?\d{1,3})\b")
# The enforceable marker, tolerant of phrasing: "[enforceable]", "(enforceable)",
# "enforceable: true", "(hard)". A "non-enforceable"/"advisory" marker wins as
# explicitly advisory.
_ENFORCEABLE_RE = re.compile(r"(?i)\b(?:enforceable\s*(?::\s*true)?|hard(?:\s+check)?)\b")
_ADVISORY_RE = re.compile(r"(?i)\b(?:non[\s-]?enforceable|advisory|enforceable\s*:\s*false|soft)\b")

_MAX_PRINCIPLES = 50
_MAX_TEXT = 2000


def _enforceable(label: str) -> bool:
    """Decide a principle's enforceable flag from its label/heading text.

    An explicit advisory marker (``advisory`` / ``non-enforceable`` /
    ``enforceable: false``) always wins; otherwise an ``enforceable`` / ``hard``
    marker turns the clause into a hard check. Absent markers ⇒ advisory.
    """
    if _ADVISORY_RE.search(label):
        return False
    return bool(_ENFORCEABLE_RE.search(label))


def _principle_id(label: str, ordinal: int) -> str:
    """Pull a stable id from the label, else synthesize ``P{ordinal}``."""
    m = _ID_RE.search(label)
    if m:
        return m.group(1)
    return f"P{ordinal}"


def _clean_text(label: str, body: str) -> str:
    """Build the principle statement: prefer the body, fall back to the label.

    Strips the enforceable/advisory markers and any leading id token so the
    rendered text reads as the principle itself, not its metadata.
    """
    text = body.strip() or label.strip()
    text = _ENFORCEABLE_RE.sub("", text)
    text = _ADVISORY_RE.sub("", text)
    # Drop a leading "P1:" / "P1 -" id prefix and tidy stray punctuation/brackets.
    text = re.sub(r"^\s*[A-Z]{1,4}-?\d{1,3}\s*[:.\-]?\s*", "", text)
    text = text.replace("()", "").replace("[]", "").strip(" :-—()[]")
    return text[:_MAX_TEXT].strip()


def parse_constitution(text: str) -> list[dict[str, Any]]:
    """Parse ``constitution.md`` into ``[{id, text, enforceable}]`` principles.

    Two principle shapes are recognised (a doc may mix them):

    * **headings** — ``## P1: <statement>`` (the statement may continue on the
      following non-empty, non-heading line), and
    * **bold-led bullets/lines** — ``- **P2 (enforceable):** <statement>``.

    The ``enforceable`` flag is read from an ``enforceable``/``hard`` marker in
    the heading/label (an ``advisory``/``non-enforceable`` marker forces
    advisory). Returns ``[]`` when nothing parses — the caller then records the
    block as unavailable rather than empty-but-present.
    """
    principles: list[dict[str, Any]] = []
    lines = text.splitlines()
    ordinal = 0

    i = 0
    while i < len(lines):
        line = lines[i]

        bold = _BOLD_RE.match(line)
        heading = _HEADING_RE.match(line)

        label: str | None = None
        body = ""
        if bold:
            label, body = bold.group(1), bold.group(2)
        elif heading:
            label = heading.group(2)
            # A heading's statement may be the next non-empty, non-heading line.
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and not _HEADING_RE.match(lines[j]) and not _BOLD_RE.match(lines[j]):
                body = lines[j].strip()

        if label is not None:
            # A bare top-level title (e.g. "# Constitution") with no id and no
            # marker and no body is a document title, not a principle — skip it.
            looks_like_title = (
                heading is not None
                and not _ID_RE.search(label)
                and not _ENFORCEABLE_RE.search(label)
                and not body
            )
            if not looks_like_title:
                ordinal += 1
                statement = _clean_text(label, body)
                if statement:
                    principles.append(
                        {
                            "id": _principle_id(label, ordinal),
                            "text": statement,
                            "enforceable": _enforceable(f"{label} {body}"),
                        }
                    )
            if len(principles) >= _MAX_PRINCIPLES:
                break
        i += 1

    # De-duplicate ids deterministically (a doc may reuse "P1"): keep the first,
    # suffix later collisions so enforceable_ids stays unambiguous.
    seen: dict[str, int] = {}
    for p in principles:
        pid = p["id"]
        if pid in seen:
            seen[pid] += 1
            p["id"] = f"{pid}.{seen[pid]}"
        else:
            seen[pid] = 0
    return principles


def build_constitution_block(text: str | None, *, source: str) -> dict[str, Any]:
    """Build the ``epic_context.constitution`` block from raw markdown.

    ``text`` is the constitution file contents (or ``None`` when it could not be
    read). Returns the additive block; ``available`` is ``False`` (and no
    principles) when ``text`` is missing/empty/unparseable, so the gate scores it
    ``not_applicable`` — never a false pass.
    """
    if not text or not text.strip():
        return {"available": False, "source": source, "principles": [], "enforceable_ids": []}

    principles = parse_constitution(text)
    if not principles:
        return {"available": False, "source": source, "principles": [], "enforceable_ids": []}

    enforceable_ids = [p["id"] for p in principles if p.get("enforceable")]
    return {
        "available": True,
        "source": source,
        "principles": principles,
        "enforceable_ids": enforceable_ids,
    }


def load_constitution(root: str | Path, *, source: str | None = None) -> dict[str, Any]:
    """Read ``<root>/.factory/constitution.md`` and build the contract block.

    ``root`` is a checkout directory (the read-only reconnaissance clone, or any
    repo root). Best-effort: a missing file or read error yields
    ``available: false``. ``source`` overrides the recorded provenance string.
    """
    path = Path(root) / CONSTITUTION_PATH
    src = source or CONSTITUTION_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {"available": False, "source": src, "principles": [], "enforceable_ids": []}
    return build_constitution_block(text, source=src)


def read_constitution_md(repo: str | None, base_ref: str | None = None) -> str | None:
    """Read ``.factory/constitution.md`` from a read-only checkout of ``repo``.

    Uses the same safe, no-execution reconnaissance clone the RepoMap build uses
    (:func:`plan.recon.clone.clone_for_recon`). Best-effort: no repo, an
    unreachable repo, or a missing file all yield ``None`` (greenfield / no
    constitution). **Never raises** — a failure here must not break a plan run.
    """
    if not repo:
        return None
    try:
        # Lazy import: keep the recon clone machinery out of the module import graph.
        from plan.recon.clone import clone_for_recon  # noqa: PLC0415

        with clone_for_recon(repo, base_ref) as c:
            if not c.ok or c.path is None:
                return None
            path = Path(c.path) / CONSTITUTION_PATH
            if not path.is_file():
                return None
            return path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001 — reconnaissance read is best-effort, never fatal
        return None


def attach_constitution(contract: dict[str, Any], plan: Any = None) -> dict[str, Any]:
    """Attach the RFC-0015 constitution block to ``epic_context`` in place.

    Reads the constitution markdown captured on the plan during reconnaissance
    (``plan.constitution_md``). Absent ⇒ an ``available: false`` block is still
    recorded so downstream consumers can tell "we looked and found nothing" from
    "this contract predates the feature". Returns the contract for composability.
    **Never raises.**
    """
    try:
        text = getattr(plan, "constitution_md", None) if plan is not None else None
        block = build_constitution_block(text, source=CONSTITUTION_PATH)

        epic_context = contract.get("epic_context")
        if not isinstance(epic_context, dict):
            epic_context = {}
            contract["epic_context"] = epic_context
        epic_context["constitution"] = block
        return contract
    except Exception:  # noqa: BLE001 — best-effort: a constitution lookup must never break emit
        return contract


def render_constitution_prompt(constitution: dict[str, Any] | None) -> str:
    """Render the constitution as a prompt fragment for the planner.

    Mirrors the RFC-0012 house-standards injection: a short, explicit block the
    decomposer must honour, with enforceable clauses called out as HARD. Returns
    ``""`` when no usable constitution exists (so the prompt is unchanged —
    today's behaviour).
    """
    if not isinstance(constitution, dict) or not constitution.get("available"):
        return ""
    principles = constitution.get("principles") or []
    if not principles:
        return ""

    lines = [
        "",
        "Project constitution (governing principles — honour every clause; clauses",
        "marked [HARD] are gated and a violation blocks the plan):",
    ]
    for p in principles:
        if not isinstance(p, dict):
            continue
        mark = "[HARD]" if p.get("enforceable") else "[advisory]"
        pid = str(p.get("id", "")).strip()
        text = str(p.get("text", "")).strip()
        if text:
            lines.append(f"- {pid} {mark}: {text}")
    return "\n".join(lines)
