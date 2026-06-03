"""Intake channels — turn any inbound plan into a :class:`NormalizedPlan` (issue #4).

A plan can arrive through several doors:

  * **CLI** — a file path on disk (``ingest_path``); PDF / DOCX / text all work
    because :func:`plan.ingest.document_loaders.ingest_document` flattens binary
    documents first.
  * **MCP / API text** — raw acceptance-criteria text (``ingest_text``), e.g. a
    markdown AC list pasted into an MCP tool call.
  * **Portal upload** — in-memory upload bytes + a filename (``ingest_bytes``),
    flattened with :func:`plan.ingest.document_loaders.extract_text` so the
    upload never has to touch disk.
  * **GitHub** — an issue or discussion body (``ingest_github_body``).

Every channel funnels through the private :func:`_to_plan` helper so id +
content-hash assignment, format detection, and parsing stay DRY. Each entry
point records the ``source_channel`` and the flattened ``raw_text`` on the
resulting :class:`NormalizedPlan`.

This layer is intentionally thin: it composes the existing
:mod:`spec_sources`, :mod:`plan.ingest.document_loaders`, and
:class:`plan.models.NormalizedPlan` contracts and adds no new parsing. Parse
failures propagate as-is — :class:`~spec_sources.SpecSourceError` for empty /
unparseable text, :class:`~plan.ingest.document_loaders.DocumentLoadError` for
unreadable / unsupported documents.
"""

from __future__ import annotations

from pathlib import Path

import spec_sources
from plan.ingest.document_loaders import extract_text, load_document_text
from plan.models import NormalizedPlan, SourceChannel
from spec_sources import SpecFormat

__all__ = [
    "ingest_path",
    "ingest_text",
    "ingest_bytes",
    "ingest_github_body",
]


def _to_plan(
    text: str,
    *,
    filename: str | None,
    source_channel: SourceChannel,
    seq: int = 1,
    plan_id: str | None = None,
    fmt: SpecFormat | None = None,
    title: str | None = None,
) -> NormalizedPlan:
    """Normalise flattened ``text`` into a :class:`NormalizedPlan`.

    The single seam every channel shares: run :func:`spec_sources.ingest`
    (format detection + AC parsing) then :meth:`NormalizedPlan.from_spec`
    (id + content hash), recording ``source_channel`` and ``raw_text``.
    """
    spec = spec_sources.ingest(text, fmt=fmt, filename=filename, title=title)
    return NormalizedPlan.from_spec(
        spec,
        seq=seq,
        plan_id=plan_id,
        source_channel=source_channel,
        raw_text=text,
    )


def ingest_path(
    path: str | Path,
    *,
    source_channel: SourceChannel = "cli",
    seq: int = 1,
    plan_id: str | None = None,
    fmt: SpecFormat | None = None,
    title: str | None = None,
) -> NormalizedPlan:
    """Ingest a plan document from disk (PDF / DOCX / text) into a plan.

    Flattens the document with :func:`load_document_text`, then normalises it.
    The flattened text is preserved on the plan as ``raw_text``.
    """
    text = load_document_text(path)
    return _to_plan(
        text,
        filename=Path(path).name,
        source_channel=source_channel,
        seq=seq,
        plan_id=plan_id,
        fmt=fmt,
        title=title,
    )


def ingest_text(
    text: str,
    *,
    source_channel: SourceChannel,
    seq: int = 1,
    plan_id: str | None = None,
    fmt: SpecFormat | None = None,
    title: str | None = None,
    filename: str | None = None,
) -> NormalizedPlan:
    """Ingest raw acceptance-criteria text (markdown / Gherkin / EARS).

    ``filename`` is optional and only used to aid format auto-detection.
    """
    return _to_plan(
        text,
        filename=filename,
        source_channel=source_channel,
        seq=seq,
        plan_id=plan_id,
        fmt=fmt,
        title=title,
    )


def ingest_bytes(
    data: bytes,
    *,
    filename: str,
    source_channel: SourceChannel = "portal",
    seq: int = 1,
    plan_id: str | None = None,
    title: str | None = None,
) -> NormalizedPlan:
    """Ingest an in-memory upload (portal file upload) into a plan.

    Flattens ``data`` by ``filename`` extension via :func:`extract_text`
    (PDF / DOCX / text), so the upload never has to touch disk.
    """
    text = extract_text(data, filename=filename)
    return _to_plan(
        text,
        filename=filename,
        source_channel=source_channel,
        seq=seq,
        plan_id=plan_id,
        title=title,
    )


def ingest_github_body(
    body: str,
    *,
    title: str | None = None,
    discussion: bool = False,
    seq: int = 1,
    plan_id: str | None = None,
) -> NormalizedPlan:
    """Ingest a GitHub issue or discussion body as a plan.

    Sets ``source_channel`` to ``github_discussion`` when ``discussion`` is
    true, else ``github_issue``.
    """
    channel: SourceChannel = "github_discussion" if discussion else "github_issue"
    return _to_plan(
        body,
        filename=None,
        source_channel=channel,
        seq=seq,
        plan_id=plan_id,
        title=title,
    )
