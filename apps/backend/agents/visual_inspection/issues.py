"""Visual Inspection → GitHub issues (#170 / P2 #172).

Turn a run's failed steps into a registerable epic + one child issue per problem,
mirroring ``agents/cloud/issues.py``: each child carries what broke, the error,
the screenshot reference, and a recommendation, so a human can pick it up or
hand it to a coding agent. **Dry-run by default** (no GitHub call unless
``create=True``), consistent with the no-automatic-pushes policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .model import RunMeta

__all__ = ["IssueSpec", "build_issue_specs", "issue_specs_to_dict", "register_issues"]


@dataclass(frozen=True)
class IssueSpec:
    title: str
    body: str
    labels: list[str]

    def to_dict(self) -> dict:
        return {"title": self.title, "body": self.body, "labels": list(self.labels)}


def _child_body(step, target: dict) -> str:
    shot = step.screenshot or "(no screenshot)"
    return "\n".join(
        [
            "## What's wrong",
            f"Visual inspection step {step.n} (\"{step.label}\") failed on "
            f"`{target.get('name', '?')}`.",
            "",
            f"> {step.error or 'the step assertion failed'}",
            "",
            "## Evidence",
            f"- Screenshot: `{shot}`",
            "- Replay: `recording/trace.zip` (`npx playwright show-trace`)",
            "",
            "## How to fix",
            "Reproduce from the trace, correct the UI/flow (or the assertion if the "
            "expectation is wrong), and confirm the step passes on a re-run.",
        ]
    )


def build_issue_specs(
    meta: RunMeta, *, repo: str | None = None
) -> tuple[IssueSpec, list[IssueSpec]]:
    """An (epic, children) pair from a run's failures (worst-first by step order)."""
    d = meta.to_dict()
    target = d["target"]
    fails = [s for s in meta.steps if s.state == "fail"]
    name = target.get("name", "target")

    epic = IssueSpec(
        title=f"Visual inspection: {name} — {len(fails)} problem(s) [{d['verdict']}]",
        body="\n".join(
            [
                f"Automated visual inspection of `{name}` "
                f"({target.get('platform', 'web')}) found "
                f"**{len(fails)}** failing step(s) of {d['counts']['steps']}.",
                "",
                f"- Run: `{d['id']}` · {d['created_at']}",
                f"- Verdict: **{d['verdict']}**",
                "- Report + recording: see the `automated-test/<run>/` folder.",
                "",
                "Child issues track each problem.",
            ]
        ),
        labels=["visual-inspection", "epic"],
    )
    children = [
        IssueSpec(
            title=f"[{name}] step {s.n}: {s.label}",
            body=_child_body(s, target),
            labels=["visual-inspection", "bug"],
        )
        for s in fails
    ]
    return epic, children


def issue_specs_to_dict(epic: IssueSpec, children: list[IssueSpec]) -> dict:
    return {"epic": epic.to_dict(), "children": [c.to_dict() for c in children]}


def register_issues(
    epic: IssueSpec,
    children: list[IssueSpec],
    repo: str,
    *,
    create: bool = False,
    gh_runner: Callable | None = None,
) -> dict:
    """Register the epic + children on GitHub. **Dry-run unless ``create=True``.**

    ``gh_runner(argv) -> (returncode, stdout)`` is injected in tests; the real
    runner shells ``gh``. The epic is created first; each child references it.
    """
    if not create:
        return {"dry_run": True, "count": len(children), "epic": None, "children": []}

    run = gh_runner or _default_gh
    rc, out = run(["gh", "issue", "create", "--repo", repo, "--title", epic.title,
                   "--body", epic.body, "--label", ",".join(epic.labels)])
    epic_url = (out or "").strip()
    epic_num = epic_url.rstrip("/").rsplit("/", 1)[-1] if epic_url else ""
    created: list[str] = []
    for c in children:
        body = c.body + (f"\n\nPart of epic #{epic_num}." if epic_num else "")
        _, curl = run(["gh", "issue", "create", "--repo", repo, "--title", c.title,
                       "--body", body, "--label", ",".join(c.labels)])
        created.append((curl or "").strip())
    return {"dry_run": False, "count": len(children), "epic": epic_url, "children": created}


def _default_gh(argv: list[str]):  # pragma: no cover - real subprocess
    import subprocess

    p = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout
