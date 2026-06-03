"""MCP intake channel — the ``mcp__pfactory__plan_ingest`` tool (issue #4).

Exposes a single plain callable, :func:`plan_ingest`, that turns either inline
acceptance-criteria ``text`` or a ``path`` to a plan document into a
:class:`~plan.models.NormalizedPlan` and returns its ``model_dump()`` dict —
the JSON-serialisable shape an MCP client expects.

Registration
------------
This module deliberately does **not** import or touch the MCP server. To wire
it up, register :func:`plan_ingest` as the ``mcp__pfactory__plan_ingest`` tool
in ``apps/backend/mcp_server/pfactory_server.py`` (e.g. add a
``@mcp.tool()``-decorated thin wrapper that calls :func:`plan_ingest`). Keeping
the callable framework-agnostic here means it stays unit-testable without an
MCP runtime.
"""

from __future__ import annotations

from plan.ingest.channels import ingest_path, ingest_text
from plan.models import SourceChannel


def plan_ingest(
    *,
    text: str | None = None,
    path: str | None = None,
    filename: str | None = None,
    channel: SourceChannel = "mcp",
    title: str | None = None,
) -> dict:
    """Ingest a plan from inline ``text`` or a ``path`` and return its dict form.

    Exactly one of ``text`` / ``path`` must be supplied. ``filename`` is only
    used (with ``text``) to aid source-format auto-detection. Returns
    :meth:`NormalizedPlan.model_dump`.

    Raises:
        ValueError: if neither or both of ``text`` and ``path`` are given.
    """
    if (text is None) == (path is None):
        raise ValueError("provide exactly one of 'text' or 'path'.")

    if path is not None:
        plan = ingest_path(path, source_channel=channel, title=title)
    else:
        plan = ingest_text(
            text,  # type: ignore[arg-type]  # narrowed: path is None here
            source_channel=channel,
            title=title,
            filename=filename,
        )
    return plan.model_dump()
