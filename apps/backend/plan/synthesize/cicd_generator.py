"""CI/CD pipeline synthesizer (issue #13).

Deterministically renders a markdown CI/CD pipeline spec for a
:class:`~plan.models.NormalizedPlan` and a dedicated ``cicd`` child issue, so
AIFactory implements the pipeline as tracked work. No LLM — the document is built
from string templates tailored by signals found in the plan's enrichment and
text (Kubernetes/OpenShift, Helm, Terraform).
"""

from __future__ import annotations

from plan.decompose.models import ChildIssue
from plan.models import NormalizedPlan
from plan.plan_types import PlanTypeDescriptor, select_for
from plan.synthesize.models import SynthesizedArtifact

# Signal keywords scanned across enrichment infra findings + plan text.
_CONTAINER_KEYWORDS = ("kubernetes", "openshift", "helm", "k8s")
_TERRAFORM_KEYWORDS = ("terraform",)

# The pipeline file a CI system uses when reconnaissance found none.
_DEFAULT_PIPELINE_PATH: dict[str, str] = {
    "github-actions": ".github/workflows/ci.yml",
    "gitlab-ci": ".gitlab-ci.yml",
    "azure-pipelines": "azure-pipelines.yml",
}
_FALLBACK_PIPELINE_PATH = ".github/workflows/ci.yml"
# ponytail: three providers because that is what plan.recon.ci_probe detects.
# Add a default here only when the probe learns to report a fourth.


def pipeline_paths(plan: NormalizedPlan) -> list[str]:
    """The CI file(s) the ``cicd`` child must edit — discovered, else the default.

    This is the whole fix for AIFactory#1113. The child's text is the ONLY place
    a file target can come from: :func:`plan.recon.delta.compute_footprints`
    mines file-like tokens out of ``title + body + acceptance_criteria`` and that
    becomes the contract subtask's ``files_to_modify`` / ``files_to_create``,
    which AIFactory renders to the coder as "Files to Modify". The old body named
    only ``docs/plans/<id>-cicd-pipeline.md`` — a document PFactory never writes
    into the target repo — so the coder was handed one file to create, a
    markdown file, and created it. Its acceptance criteria ("stages run on every
    push and PR") were unsatisfiable by construction. Naming the real pipeline
    file makes the same machinery point at the workflow instead.
    """
    repo_map = plan.repo_map
    if repo_map is not None and repo_map.available and repo_map.ci_pipeline_paths:
        return list(repo_map.ci_pipeline_paths)[:3]
    system = (repo_map.ci_system if repo_map is not None else None) or ""
    return [_DEFAULT_PIPELINE_PATH.get(system, _FALLBACK_PIPELINE_PATH)]


def _plan_text(plan: NormalizedPlan) -> str:
    """Lowercased title + description + criteria + raw text for signal scanning."""
    parts = [plan.title, plan.description, *(c.text for c in plan.criteria)]
    if plan.raw_text:
        parts.append(plan.raw_text)
    return "\n".join(p for p in parts if p).lower()


def _infra_text(plan: NormalizedPlan) -> str:
    """Flatten the enrichment infra findings into a single lowercased blob."""
    chunks: list[str] = []
    for finding in plan.enrichment.infra:
        for value in finding.values():
            chunks.append(str(value))
    return "\n".join(chunks).lower()


def _has_signal(haystacks: tuple[str, ...], keywords: tuple[str, ...]) -> bool:
    """True if any keyword appears in any haystack."""
    return any(kw in hay for hay in haystacks for kw in keywords)


def generate_cicd(
    plan: NormalizedPlan,
    *,
    descriptor: PlanTypeDescriptor | None = None,
) -> SynthesizedArtifact | None:
    """Synthesize a CI/CD pipeline spec + child issue for ``plan``.

    Returns ``None`` when the plan's type does not enable the
    ``synthesize_cicd`` stage (e.g. non-software / generic deliverables).
    """
    descriptor = descriptor or select_for(plan)
    if not descriptor.stages.synthesize_cicd:
        return None

    text = _plan_text(plan)
    infra = _infra_text(plan)
    wants_container = _has_signal((text, infra), _CONTAINER_KEYWORDS)
    wants_terraform = _has_signal((text, infra), _TERRAFORM_KEYWORDS)

    document = _render_document(plan, descriptor, wants_container, wants_terraform)
    child = _build_child(plan, descriptor, wants_container, wants_terraform)

    return SynthesizedArtifact(
        kind="cicd",
        title=f"CI/CD pipeline for {plan.title}",
        document=document,
        child=child,
        filename=f"docs/plans/{plan.plan_id}-cicd-pipeline.md",
    )


def _render_document(
    plan: NormalizedPlan,
    descriptor: PlanTypeDescriptor,
    wants_container: bool,
    wants_terraform: bool,
) -> str:
    """Build the markdown pipeline spec, tailored by detected signals."""
    lines: list[str] = [
        f"# CI/CD Pipeline — {plan.title}",
        "",
        f"> Plan: `{plan.plan_id}` · Plan type: `{descriptor.name}`",
        "",
        "## Overview",
        "",
        "Continuous integration and delivery pipeline for this plan. The pipeline "
        "runs on every push and pull request; deploy stages are gated on a green "
        "build of all preceding stages.",
        "",
        "## Stages",
        "",
    ]

    # Base stages, always present.
    stages: list[tuple[str, str]] = [
        ("lint", "Static analysis and formatting checks; fail fast on style or type errors."),
        (
            "test",
            "Run the full automated test suite; require all lanes green and "
            "publish the coverage report.",
        ),
        ("build", "Compile / bundle the application and produce versioned build artifacts."),
    ]

    if wants_container:
        stages.append(
            (
                "containerise",
                "Build and tag a container image from the build artifacts, then push "
                "it to the registry.",
            )
        )

    stages.append(
        (
            "security scan",
            "Scan dependencies and (if built) the container image for known "
            "vulnerabilities; fail on findings above the agreed threshold.",
        )
    )

    if wants_terraform:
        stages.append(
            (
                "infra plan/apply (gated)",
                "Run `terraform plan` on every change for review, and `terraform "
                "apply` only after manual approval on the protected branch.",
            )
        )

    if wants_container:
        stages.append(
            (
                "deploy to cluster",
                "Roll out the new image to the Kubernetes/OpenShift cluster (Helm "
                "release or manifests), then verify readiness and health probes.",
            )
        )
    else:
        stages.append(
            (
                "deploy",
                "Promote the build artifacts to the target environment after manual "
                "approval; verify the deployment is healthy.",
            )
        )

    for i, (name, desc) in enumerate(stages, 1):
        lines.append(f"{i}. **{name}** — {desc}")
    lines.append("")

    lines += [
        "## Gating",
        "",
        "- Each stage depends on a green result from the previous stage.",
        "- Deploy stages require manual approval and run only on the default branch.",
    ]
    if wants_terraform:
        lines.append("- `terraform apply` is a protected, manually-approved step.")
    lines.append("")

    lines += [
        "## Acceptance Criteria Coverage",
        "",
        "The pipeline must keep these plan acceptance criteria green:",
        "",
    ]
    if plan.criteria:
        for c in plan.criteria:
            lines.append(f"- `{c.id}` — {c.text}")
    else:
        lines.append("- _No acceptance criteria defined on the plan._")
    lines.append("")

    return "\n".join(lines)


def _build_child(
    plan: NormalizedPlan,
    descriptor: PlanTypeDescriptor,
    wants_container: bool,
    wants_terraform: bool,
) -> ChildIssue:
    """Build the ``cicd`` child issue that tracks implementing the pipeline."""
    acceptance: list[str] = [
        "Lint, test, build, and security-scan stages run on every push and PR.",
        "The test stage runs the full suite and publishes a coverage report.",
        "Deploy stages are gated on a green build and require manual approval.",
    ]
    if wants_container:
        acceptance.append("A container image is built, scanned, and deployed to the cluster.")
    if wants_terraform:
        acceptance.append("Infra changes run `terraform plan` on PRs and a gated `apply` on merge.")

    stages = (
        "lint -> test -> build -> security scan -> deploy"
        + (" (containerised, deployed to cluster)" if wants_container else "")
        + (", with gated Terraform plan/apply" if wants_terraform else "")
    )
    paths = pipeline_paths(plan)
    body = (
        "Add these pipeline stages to this repo's CI configuration: "
        f"{stages}.\n\n"
        "File(s) to change:\n"
        + "".join(f"- `{p}`\n" for p in paths)
        + "\nThe acceptance criteria are about stages that RUN in CI, so this "
        "subtask is done only when the file(s) above change. Writing a design "
        "document satisfies none of them (AIFactory#1113)."
    )

    return ChildIssue(
        key="CICD",
        title=f"Set up CI/CD for {plan.title}",
        body=body,
        kind="cicd",
        labels=["pfactory", "area:cicd", f"plan-type:{descriptor.name}"],
        complexity="standard",
        acceptance_criteria=acceptance,
    )
