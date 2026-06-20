"""Living-template drift watcher (issue #27).

Templates carry an embedded :class:`~plan.templates.models.Policy` (required
tags, allowed regions, IAM + security baselines). Cloud providers and security
best practices move on — a new compliant region opens, a new CIS baseline lands —
and a template that was golden last quarter quietly falls *behind*. This module
is the **drift watcher**: it compares a template's policy against a current
best-practice snapshot and, when the template is behind, **proposes the update
via a pull request** — never a silent in-place edit on the main branch.

Design principles (mirroring ``tools/git_writer`` + ``tools/pr_comment``):
  - **Dry-run first.** :func:`propose_update` defaults to ``dry_run=True``,
    returning the *proposed* PR (title, body, branch, content) WITHOUT touching
    git. Live mode drives an injected ``git`` runner.
  - **Injected seams.** The best-practice ``source`` and the ``git`` runner are
    both injected objects — the seam to cloud / best-practice MCP servers and to
    the host's git/PR tooling. Defensive when absent.
  - **Conservative.** By default drift only proposes *adding* newly-recommended
    items. Items present in the template but missing from the snapshot are
    surfaced as ``changed`` notes (not removed) unless ``allow_removals=True``.
  - **PR, never silent edit.** The template on main is never mutated in place;
    the update always lands on a fresh branch behind a PR.
"""

from __future__ import annotations

from typing import Any

from plan.templates.models import Template
from pydantic import BaseModel, Field

# Policy fields this watcher tracks. All are list-valued on the Policy model, so
# drift reduces to set difference per field.
_POLICY_LIST_FIELDS = (
    "required_tags",
    "allowed_regions",
    "required_iam",
    "security_baselines",
)


class TemplateDriftReport(BaseModel):
    """Outcome of comparing a template's policy to a best-practice snapshot.

    ``added`` / ``removed`` / ``changed`` are keyed by policy field, e.g.
    ``{"allowed_regions": ["europe-west9"]}``:
      - ``added``: items in the snapshot but not in the template (newly
        recommended — what we propose adding).
      - ``removed``: items in the template but not in the snapshot, only when
        removals are allowed; otherwise these surface under ``changed``.
      - ``changed``: advisory notes (e.g. items the snapshot no longer lists)
        that are NOT applied by default.
    """

    template: str
    drifted: bool = False
    added: dict[str, list[str]] = Field(default_factory=dict)
    removed: dict[str, list[str]] = Field(default_factory=dict)
    changed: dict[str, list[str]] = Field(default_factory=dict)
    summary: str = ""
    proposed_policy: dict[str, Any] | None = None


def _policy_dict(template: Template) -> dict[str, list[str]]:
    """Project the template's policy to a ``{field: [items]}`` dict."""
    policy = template.policy
    return {field: list(getattr(policy, field) or []) for field in _POLICY_LIST_FIELDS}


def detect_drift(
    template: Template,
    current: dict[str, Any],
    *,
    allow_removals: bool = False,
) -> TemplateDriftReport:
    """Compare ``template``'s policy to a ``current`` best-practice snapshot.

    Args:
        template: The template whose embedded policy is under watch.
        current: Best-practice snapshot, same shape as Policy fields, e.g.
            ``{"allowed_regions": [...], "required_tags": [...],
            "security_baselines": [...]}``. Missing fields are treated as
            "no recommendation" for that field (never a removal signal).
        allow_removals: When True, items present in the template but absent
            from the snapshot are proposed for removal. Default False:
            removals become advisory ``changed`` notes only.

    Returns:
        A :class:`TemplateDriftReport`. ``drifted`` is True iff any field
        differs; ``proposed_policy`` is the merged updated policy dict.
    """
    template_policy = _policy_dict(template)
    proposed = {field: list(items) for field, items in template_policy.items()}

    added: dict[str, list[str]] = {}
    removed: dict[str, list[str]] = {}
    changed: dict[str, list[str]] = {}

    for field in _POLICY_LIST_FIELDS:
        if field not in current:
            # No recommendation for this field — leave the template untouched.
            continue
        have = template_policy[field]
        have_set = set(have)
        want = list(current.get(field) or [])
        want_set = set(want)

        # Newly recommended items (in snapshot, not in template) → propose adding.
        new_items = [item for item in want if item not in have_set]
        if new_items:
            added[field] = new_items
            # Append while preserving order + uniqueness.
            for item in new_items:
                if item not in proposed[field]:
                    proposed[field].append(item)

        # Items the template has that the snapshot no longer lists.
        stale_items = [item for item in have if item not in want_set]
        if stale_items:
            if allow_removals:
                removed[field] = stale_items
                proposed[field] = [item for item in proposed[field] if item in want_set]
            else:
                changed[field] = stale_items

    drifted = bool(added or removed or changed)
    summary = _summarise(template.metadata.name, added, removed, changed)
    return TemplateDriftReport(
        template=template.metadata.name,
        drifted=drifted,
        added=added,
        removed=removed,
        changed=changed,
        summary=summary,
        proposed_policy=proposed if drifted else None,
    )


def _summarise(
    name: str,
    added: dict[str, list[str]],
    removed: dict[str, list[str]],
    changed: dict[str, list[str]],
) -> str:
    """Build a one-line human summary of the policy diff."""
    if not (added or removed or changed):
        return f"template '{name}' is up to date"
    parts: list[str] = []
    for label, mapping in (("add", added), ("remove", removed), ("note", changed)):
        for field, items in mapping.items():
            parts.append(f"{label} {field}: {', '.join(items)}")
    return f"template '{name}' drift — " + "; ".join(parts)


def fetch_current_best_practices(template: Template, *, source: Any = None) -> dict[str, Any]:
    """Fetch the current best-practice snapshot for ``template``.

    The ``source`` is the injected seam to cloud / best-practice MCP servers —
    any object exposing ``snapshot(template_name) -> dict``. When ``source`` is
    None (or lacks ``snapshot``), returns ``{}`` so :func:`detect_drift` reports
    no drift. Lazy + defensive: a misbehaving source yields ``{}`` rather than
    raising.

    Args:
        template: The template to fetch best practices for.
        source: Injected snapshot provider, or None.

    Returns:
        A best-practice snapshot dict (possibly empty).
    """
    if source is None:
        return {}
    snapshot = getattr(source, "snapshot", None)
    if not callable(snapshot):
        return {}
    result = snapshot(template.metadata.name)
    if not isinstance(result, dict):
        return {}
    return result


def _render_policy_yaml(name: str, proposed_policy: dict[str, Any]) -> str:
    """Render the updated ``template.yaml`` policy block as YAML.

    Only the embedded ``policy:`` block is rendered — the surrounding template
    is left to the PR reviewer / loader to splice. Kept deterministic for tests.
    """
    import yaml

    body = {"metadata": {"name": name}, "policy": proposed_policy}
    return yaml.safe_dump(body, sort_keys=True, default_flow_style=False)


def _build_pr_body(report: TemplateDriftReport) -> str:
    """Compose the PR body: a markdown summary of the policy diff."""
    lines = [
        f"## Template drift: `{report.template}`",
        "",
        report.summary,
        "",
    ]
    if report.added:
        lines.append("### Newly recommended (added)")
        for field, items in report.added.items():
            lines.append(f"- **{field}**: {', '.join(items)}")
        lines.append("")
    if report.removed:
        lines.append("### Removed")
        for field, items in report.removed.items():
            lines.append(f"- **{field}**: {', '.join(items)}")
        lines.append("")
    if report.changed:
        lines.append("### Notes (not applied)")
        for field, items in report.changed.items():
            lines.append(
                f"- **{field}**: {', '.join(items)} "
                "(in template but absent from current best practices)"
            )
        lines.append("")
    lines.append(
        "_Proposed automatically by the PFactory living-template drift watcher. "
        "Review before merging — this PR never edits the template in place._"
    )
    return "\n".join(lines)


def propose_update(
    report: TemplateDriftReport,
    *,
    repo: str,
    template_path: str,
    git: Any = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Propose the template update as a pull request.

    When ``report.drifted`` is False, returns ``{"opened": False, "reason":
    "no drift"}``. Otherwise:

      - **dry_run (default):** returns the *proposed* PR — ``title``, ``body``
        (summarising the policy diff), ``branch`` name and the new template
        ``content`` — WITHOUT calling git.
      - **live (dry_run=False):** drives the injected ``git`` runner (an object
        with ``create_branch(name)``, ``write_file(path, content)``,
        ``commit(msg)`` and ``open_pr(title, body, base, head) -> url``) to
        branch → write → commit → open PR, and returns
        ``{"opened": True, "pr_url": ...}``.

    The template on the main branch is **never** edited in place — the update
    always lands behind a PR.

    Args:
        report: The drift report to act on.
        repo: Repository slug / identifier (informational; for traceability).
        template_path: Path to the template file the PR should update.
        git: Injected git/PR runner (live mode only).
        dry_run: When True (default), return the proposal without side effects.

    Returns:
        A dict describing the (proposed or opened) PR.
    """
    if not report.drifted:
        return {"opened": False, "reason": "no drift"}

    proposed_policy = report.proposed_policy or {}
    branch = f"template-drift/{report.template}"
    title = f"chore(template): update '{report.template}' policy to current best practices"
    body = _build_pr_body(report)
    content = _render_policy_yaml(report.template, proposed_policy)

    if dry_run:
        return {
            "opened": False,
            "dry_run": True,
            "repo": repo,
            "template_path": template_path,
            "title": title,
            "body": body,
            "branch": branch,
            "content": content,
            "proposed_policy": proposed_policy,
        }

    if git is None:
        return {
            "opened": False,
            "reason": "no git runner provided for live run",
            "title": title,
            "body": body,
            "branch": branch,
            "content": content,
        }

    # Live: branch → write → commit → open PR. Order matters.
    git.create_branch(branch)
    git.write_file(template_path, content)
    git.commit(f"chore(template): update {report.template} policy ({report.summary})")
    pr_url = git.open_pr(title, body, "main", branch)
    return {
        "opened": True,
        "pr_url": pr_url,
        "repo": repo,
        "template_path": template_path,
        "branch": branch,
        "title": title,
    }


def watch(
    template: Template,
    *,
    source: Any,
    repo: str,
    template_path: str,
    git: Any = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """One-shot drift check + PR proposal for ``template``.

    Wires the pipeline end-to-end:
    :func:`fetch_current_best_practices` → :func:`detect_drift` →
    :func:`propose_update`. Designed to be driven by an external
    scheduler/loop (one template per call).

    Args:
        template: The template under watch.
        source: Injected best-practice snapshot provider (or None → no drift).
        repo: Repository slug / identifier.
        template_path: Path to the template file the PR should update.
        git: Injected git/PR runner (live mode only).
        dry_run: When True (default), propose without side effects.

    Returns:
        The :func:`propose_update` result dict, with the drift ``report``
        attached under the ``"report"`` key for observability.
    """
    current = fetch_current_best_practices(template, source=source)
    report = detect_drift(template, current)
    result = propose_update(
        report, repo=repo, template_path=template_path, git=git, dry_run=dry_run
    )
    result["report"] = report.model_dump()
    return result
