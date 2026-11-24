"""Map an approved intent/spec/plan triple to the governed plan models."""

from __future__ import annotations

import re
from collections.abc import Mapping

from plan.decompose.models import ChildIssue, EpicPlan
from plan.models import Criterion, NormalizedPlan, make_plan_id


class ArtifactIngestError(ValueError):
    """The supplied triple cannot produce an executable plan."""


def _body(text: str) -> str:
    return re.sub(r"\A---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.DOTALL)


def _section(text: str, name: str) -> str:
    unfenced = []
    fenced = False
    for line in text.splitlines(keepends=True):
        if re.match(r"^\s*(```|~~~)", line):
            fenced = not fenced
        if fenced:
            unfenced.append(" " * len(line.rstrip("\n")) + ("\n" if line.endswith("\n") else ""))
        else:
            unfenced.append(line)
    headings = "".join(unfenced)
    match = re.search(rf"(?im)^## {re.escape(name)}\s*$", headings)
    if match is None:
        return ""
    end = re.search(r"(?m)^## ", headings[match.end() :])
    return text[match.end() : match.end() + end.start() if end else None].strip()


def _bullets(section: str) -> list[str]:
    items: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if re.match(r"^[-*+]\s+", stripped) and not line[:1].isspace():
            items.append(re.sub(r"^[-*+]\s+", "", stripped))
        elif items and stripped and line[:1].isspace():
            items[-1] += " " + re.sub(r"^[-*+]\s+", "", stripped)
    return items


def ingest_artifacts(
    artifacts: Mapping[str, str], *, seq: int = 1
) -> tuple[NormalizedPlan, EpicPlan]:
    """Return one child per numbered step, retaining all source text for scanning."""
    try:
        intent, spec, plan_text = (_body(artifacts[stage]) for stage in ("intent", "spec", "plan"))
    except KeyError as exc:
        raise ArtifactIngestError(f"missing {exc.args[0]} artifact") from exc
    title_match = re.search(r"(?m)^#\s+(?:Intent:\s*)?(.+?)\s*$", intent)
    if title_match is None:
        raise ArtifactIngestError("intent needs a title")
    title = title_match.group(1)
    criteria = _bullets(_section(spec, "Verification"))
    steps_text = _section(plan_text, "Steps")
    steps: list[str] = []
    for line in steps_text.splitlines():
        if re.match(r"^\d+[.)]\s+", line):
            steps.append(line)
        elif steps:
            steps[-1] += "\n" + line
    if not steps:
        raise ArtifactIngestError("plan needs numbered steps")
    plan = NormalizedPlan(
        plan_id=make_plan_id(seq, title),
        title=title,
        description="\n\n".join(
            part
            for part in (_section(intent, "Problem"), _section(intent, "Proposed outcome"))
            if part
        ),
        source_format="factory-artifacts",
        source_channel="mcp",
        content_trust="untrusted_user_content",
        target_kind="software",
        raw_text="\n\n".join(artifacts[stage] for stage in ("intent", "spec", "plan")),
        criteria=[Criterion(id=f"AC{i}", text=value) for i, value in enumerate(criteria, 1)],
    ).with_hash()
    children: list[ChildIssue] = []
    for i, step in enumerate(steps, 1):
        heading = step.splitlines()[0]
        kind = (
            "testing"
            if re.search(r"\btests?\b", heading, re.I)
            else "cicd"
            if re.search(r"\bCI\b", heading)
            else "task"
        )
        body = step.strip()
        if i == len(steps):
            body += "\n\n## Tests\n" + _section(plan_text, "Tests")
            body += "\n\n## Rollback\n" + _section(plan_text, "Rollback")
        children.append(
            ChildIssue(
                key=f"C{i}",
                title=re.sub(r"\*\*(.*?)\*\*", r"\1", re.sub(r"^\d+[.)]\s+", "", heading)).strip()[
                    :120
                ],
                body=body,
                kind=kind,
                depends_on=[f"C{i - 1}"] if i > 1 else [],
                acceptance_criteria=criteria if i == len(steps) else [],
            )
        )
    return plan, EpicPlan(
        plan_id=plan.plan_id,
        epic_title=title,
        epic_body=plan_text,
        children=children,
        summary=f"{len(children)} approved plan steps",
    )
