"""Cloud findings → GitHub issues (#133/#152).

Turn a cloud assessment's findings into a GitHub **epic + child issues** so
AIFactory (or any tool) can pick them up and fix them. Each child issue carries
*What's wrong* (the risk) + *How to fix* (Prowler's remediation) + the affected
resources + references + a severity label.

The issue specs are also a **downloadable artifact** (``findings/cloud_issues.json``):
feed it straight to an AIFactory task, or register them on GitHub.

Registration is **outward-facing** — it creates real GitHub issues. Per the
"no automatic pushes" policy it is **dry-run by default**; callers opt in with
``create=True`` (CLI: ``--create``).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Callable

from .assessment import CloudFinding, parse_ocsf
from .remediation import _group_fails

__all__ = [
    "IssueSpec",
    "build_issue_specs",
    "issue_specs_to_dict",
    "register_issues",
]


@dataclass
class IssueSpec:
    title: str
    body: str
    labels: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"title": self.title, "body": self.body, "labels": list(self.labels)}


def _child_body(it: dict, provider: str, account: str) -> str:
    resources = ", ".join(it["resources"]) if it["resources"] else "—"
    more = "" if it["count"] <= len(it["resources"]) else f" (+{it['count'] - len(it['resources'])} more)"
    refs = "\n".join(f"- {r}" for r in it["references"][:5]) or "- (none)"
    return (
        f"**Provider:** {provider} · **Account:** {account} · "
        f"**Severity:** {it['severity']} · **Affected:** {it['count']}\n\n"
        f"## What's wrong\n{it['risk'] or '(no risk detail)'}\n\n"
        f"## How to fix\n{it['remediation'] or '(see references)'}\n\n"
        f"**Affected resources:** {resources}{more}\n\n"
        f"## References\n{refs}\n\n"
        f"---\n_Auto-registered by PFactory from a cloud assessment "
        f"(check `{it['check_id']}`). Safe for AIFactory / automated remediation._"
    )


def build_issue_specs(
    findings: list[CloudFinding],
    *,
    provider: str,
    account: str,
    fail_on_severity: str = "high",
) -> tuple[IssueSpec, list[IssueSpec]]:
    """Build an epic + one child issue per failing check (deduped, worst-first)."""
    items = list(_group_fails(findings).values())
    children: list[IssueSpec] = []
    for it in items:
        sev = it["severity"]
        children.append(
            IssueSpec(
                title=f"[cloud][{sev}] {it['title']}",
                body=_child_body(it, provider, account),
                labels=["cloud", "remediation", f"severity:{sev}"],
            )
        )
    checklist = "\n".join(f"- [ ] {c.title}" for c in children) or "- (none)"
    epic = IssueSpec(
        title=f"[cloud] Remediation: {provider.upper()} {account} — {len(children)} issue type(s)",
        body=(
            f"Auto-registered by PFactory from a read-only cloud assessment of "
            f"**{provider}** account `{account}` (gate: fail_on_severity={fail_on_severity}).\n\n"
            f"{len(children)} issue type(s) to remediate, worst-first:\n\n{checklist}\n\n"
            "Each child issue has *what's wrong* + *how to fix* — pick them up with "
            "AIFactory or any remediation tool."
        ),
        labels=["cloud", "epic", "remediation"],
    )
    return epic, children


def issue_specs_to_dict(epic: IssueSpec, children: list[IssueSpec]) -> dict:
    """Serialise the epic + children (the downloadable ``cloud_issues.json``)."""
    return {"epic": epic.to_dict(), "children": [c.to_dict() for c in children]}


# ── registration (outward-facing; dry-run by default) ────────────────────────


def _gh(argv: list[str]) -> tuple[int, str]:
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    return proc.returncode, (proc.stdout or "").strip()


def _create_issue(run: Callable, repo: str, spec: IssueSpec) -> str:
    argv = ["gh", "issue", "create", "--repo", repo, "--title", spec.title, "--body", spec.body]
    for label in spec.labels:
        argv += ["--label", label]
    _rc, out = run(argv)
    return out.strip()


def register_issues(
    epic: IssueSpec,
    children: list[IssueSpec],
    repo: str,
    *,
    create: bool = False,
    gh_runner: Callable | None = None,
) -> dict:
    """Register the epic + children on GitHub. Dry-run unless ``create=True``.

    Dry-run returns the plan (what *would* be created) without any ``gh`` call.
    """
    if not create:
        return {
            "dry_run": True,
            "repo": repo,
            "epic": epic.title,
            "children": [c.title for c in children],
            "count": len(children),
        }
    run = gh_runner or _gh
    epic_url = _create_issue(run, repo, epic)
    epic_num = epic_url.rstrip("/").split("/")[-1] if epic_url else "?"
    created: list[str] = []
    for c in children:
        linked = IssueSpec(c.title, c.body + f"\n\nPart of epic #{epic_num}.", c.labels)
        created.append(_create_issue(run, repo, linked))
    return {"dry_run": False, "repo": repo, "epic": epic_url, "children": created, "count": len(created)}


# ── CLI ──────────────────────────────────────────────────────────────────────


def _main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Register cloud findings as GitHub issues.")
    p.add_argument("source", help="Prowler OCSF JSON, or a cloud_issues.json specs file")
    p.add_argument("--repo", required=True, help="owner/repo")
    p.add_argument("--provider", default="aws")
    p.add_argument("--account", default="?")
    p.add_argument("--create", action="store_true", help="actually create issues (default: dry-run)")
    args = p.parse_args(argv)

    data = json.loads(open(args.source, encoding="utf-8").read())
    if isinstance(data, dict) and "children" in data:  # a cloud_issues.json specs file
        epic = IssueSpec(**data["epic"])
        children = [IssueSpec(**c) for c in data["children"]]
    else:  # raw OCSF
        findings = parse_ocsf(data)
        epic, children = build_issue_specs(findings, provider=args.provider, account=args.account)

    result = register_issues(epic, children, args.repo, create=args.create)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
