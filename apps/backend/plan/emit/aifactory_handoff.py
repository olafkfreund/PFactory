"""Emit stage — hand a child issue off to AIFactory (issue #19).

The second half of the Emit stage: once a child issue exists on GitHub, this
module shapes it into the request AIFactory expects and (optionally) triggers
AIFactory to start work on it.

Three injectable, dry-run-friendly surfaces:

* :func:`build_requirements` — pure: a :class:`~plan.decompose.models.ChildIssue`
  → the ``requirements.json`` dict AIFactory consumes.
* :func:`write_requirements` — persist that dict under
  ``.aifactory/specs/<id>/requirements.json``.
* :func:`trigger_api` — dry-run returns the request it *would* POST; live POSTs
  to AIFactory's ``/api/tasks/create-and-run`` via an injected HTTP client.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # pragma: no cover - typing only
    from plan.decompose.models import ChildIssue
    from plan.models import NormalizedPlan


class HttpClient(Protocol):
    """Minimal HTTP client contract used by :func:`trigger_api`.

    Mirrors the ``httpx``/``requests`` ``post`` shape; tests inject a fake that
    records the call and returns a stub response.
    """

    def post(self, url: str, *, params: dict[str, Any], json: Any) -> Any: ...


def handover_labels() -> list[str]:
    """Labels marking an issue as handed off to AIFactory."""
    return ["pfactory", "handoff:aifactory"]


def _description(child: ChildIssue) -> str:
    """Compose the AIFactory description: child body + acceptance criteria."""
    parts = [child.body.rstrip()] if child.body.strip() else []
    if child.acceptance_criteria:
        parts.append("### Acceptance criteria")
        parts.extend(f"- {ac}" for ac in child.acceptance_criteria)
    return "\n\n".join(p for p in parts if p).strip()


def build_requirements(
    child: ChildIssue,
    *,
    plan: NormalizedPlan,
    complexity: str | None = None,
    github_issue_number: int | None = None,
    model: str | None = None,
    thinking_level: str | None = None,
    require_review_before_coding: bool = True,
    phase_models: dict[str, Any] | None = None,
    phase_thinking: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Shape a child issue into AIFactory's ``requirements.json`` dict.

    The result is exactly what AIFactory expects::

        {"title": ..., "description": ...,
         "metadata": {"complexity", "githubIssueNumber", "model",
                      "thinkingLevel", "requireReviewBeforeCoding",
                      "phaseModels", "phaseThinking"}}

    ``None`` metadata values are omitted (``requireReviewBeforeCoding`` is a
    plain bool and always present). ``complexity`` defaults to the child's own
    :attr:`~plan.decompose.models.ChildIssue.complexity`.
    """
    metadata: dict[str, Any] = {
        "complexity": complexity if complexity is not None else child.complexity,
        "githubIssueNumber": github_issue_number,
        "model": model,
        "thinkingLevel": thinking_level,
        "requireReviewBeforeCoding": require_review_before_coding,
        "phaseModels": phase_models,
        "phaseThinking": phase_thinking,
    }
    metadata = {k: v for k, v in metadata.items() if v is not None}
    return {
        "title": child.title,
        "description": _description(child),
        "metadata": metadata,
    }


def write_requirements(
    child: ChildIssue,
    target_root: str | Path,
    *,
    plan: NormalizedPlan,
    **kw: Any,
) -> Path:
    """Write the child's ``requirements.json`` under ``target_root``.

    Path: ``<target_root>/.aifactory/specs/<spec_id>/requirements.json`` where
    ``spec_id`` is the plan id (or a child-derived ``<plan_id>-<key>`` slug when
    the plan has no id). Extra keyword args flow through to
    :func:`build_requirements`. Returns the written path.
    """
    payload = build_requirements(child, plan=plan, **kw)
    plan_id = getattr(plan, "plan_id", "") or ""
    spec_id = plan_id or f"plan-{child.key}"
    out_dir = Path(target_root) / ".aifactory" / "specs" / spec_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "requirements.json"
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    return out_path


def trigger_api(
    payload: dict[str, Any],
    *,
    base_url: str,
    project_id: str,
    http: HttpClient | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Trigger AIFactory to create-and-run a task from a requirements payload.

    Dry-run (default) returns the request that *would* be sent. Live mode POSTs
    to ``{base_url}/api/tasks/create-and-run`` with ``project_id``/``title``/
    ``description`` as query params and the full payload as the JSON body, via
    the injected ``http`` client.
    """
    url = f"{base_url.rstrip('/')}/api/tasks/create-and-run"
    params = {
        "project_id": project_id,
        "title": payload.get("title", ""),
        "description": payload.get("description", ""),
    }
    request = {"url": url, "params": params, "json": payload}

    if dry_run:
        return {"dry_run": True, "request": request}

    if http is None:
        raise ValueError("live trigger_api requires an injected `http` client")

    response = http.post(url, params=params, json=payload)
    return {"dry_run": False, "request": request, "response": response}
