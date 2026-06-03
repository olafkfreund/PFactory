"""Agent output envelope — one tolerant JSON extractor for all agents (#96).

Chat-tuned models and agentic CLIs rarely return a bare JSON document.
They wrap it in a ```json fence, prepend "Here's the plan:", append a
closing remark, or — on a dropped or rate-limited turn — return nothing
at all. Historically every PFactory agent that reads a JSON artefact out
of a model response re-implemented its own salvage path: the brace-match
fallback in ``analysis.insight_extractor`` and the ``_loads_tolerant``
helper in ``agents.evaluator`` are two slightly different solutions to the
same problem. This module is the single canonical extractor those call
sites can share.

Pure compute, stdlib-only. ``extract_json`` never returns ``None`` — it
returns ``(doc, salvaged)`` on success and raises ``OutputEnvelopeError``
(a ``ValueError``) when no JSON value can be recovered. Callers pick their
own failure policy (return ``None``, write a ``*_failed`` status patch, …),
and use ``salvaged`` to decide whether to rewrite a clean artefact file.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["OutputEnvelopeError", "extract_json", "strip_code_fence"]


class OutputEnvelopeError(ValueError):
    """Raised when no JSON value can be recovered from agent output."""


def strip_code_fence(text: str) -> str:
    """Drop a leading ```/```json fence line and a trailing ``` fence.

    Returns the inner body (stripped). Text without an opening fence is
    returned stripped but otherwise untouched.
    """
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned
    # Drop the opening fence line (```json / ```)...
    cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
    # ...then everything from the trailing fence onward.
    fence = cleaned.rfind("```")
    if fence != -1:
        cleaned = cleaned[:fence]
    return cleaned.strip()


def extract_json(text: str | None, *, strict: bool = False) -> tuple[Any, bool]:
    """Recover a JSON document from raw agent/LLM output.

    Returns ``(doc, salvaged)`` where ``salvaged`` is ``True`` when a lenient
    path (fence-strip / first-value scan / brace-match) was needed — callers
    can use that to rewrite a clean artefact file.

    Strategy, in order (steps 2-4 skipped when ``strict``):
      1. Direct ``json.loads`` of the stripped text → ``(doc, False)``.
      2. Strip a markdown code fence and parse the inner body.
      3. ``raw_decode`` from the first ``{`` or ``[`` — tolerates trailing
         prose after a complete JSON value.
      4. Brace-match the outermost ``{ … }`` as a last resort.

    Args:
        text: Raw model response (may be ``None``).
        strict: When ``True``, only step 1 is attempted.

    Raises:
        OutputEnvelopeError: empty/whitespace input, or no JSON recoverable.
    """
    if text is None or not text.strip():
        raise OutputEnvelopeError("empty agent output")

    raw = text.strip()

    # 1. Fast path: the whole response is already valid JSON.
    try:
        return json.loads(raw), False
    except json.JSONDecodeError as exc:
        if strict:
            raise OutputEnvelopeError(f"not valid JSON (strict mode): {exc}") from None

    # 2. Strip a markdown fence and retry a clean parse.
    cleaned = strip_code_fence(raw)
    if cleaned and cleaned != raw:
        try:
            return json.loads(cleaned), True
        except json.JSONDecodeError:
            pass
    if not cleaned:
        cleaned = raw

    # 3. raw_decode from the first JSON value — tolerates a trailing remark.
    starts = [i for i in (cleaned.find("{"), cleaned.find("[")) if i != -1]
    if starts:
        try:
            doc, _end = json.JSONDecoder().raw_decode(cleaned[min(starts) :])
            return doc, True
        except json.JSONDecodeError:
            pass

    # 4. Last resort: the outermost { … } span.
    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first != -1 and last > first:
        try:
            return json.loads(cleaned[first : last + 1]), True
        except json.JSONDecodeError:
            pass

    raise OutputEnvelopeError("no JSON value found in agent output")
