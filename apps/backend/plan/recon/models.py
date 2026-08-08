"""The :class:`RepoMap` — what reconnaissance statically observed (RFC-0010).

This is the planner's view of the target repository's existing code. It is built
by the reconnaissance stage (Phase 2) and threaded through the pipeline so
decomposition and emit are *delta-aware* instead of guessing from spec text.

Kept deliberately self-contained (no import of :mod:`plan.models`) to avoid a
circular import — ``NormalizedPlan`` carries an optional ``repo_map`` of this
type. The field set mirrors the contract's ``$defs.baseline`` block
(``apis/task-contract.schema.json``) so it serialises straight into the emitted
contract's ``baseline`` (Phase 4).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RepoMap(BaseModel):
    """A static snapshot of the target repo the plan is grounded on.

    ``available=False`` (the default) means reconnaissance could not read the
    repo (private / unreachable / empty / no target repo). Reconnaissance
    **degrades, never raises**: an unavailable RepoMap simply leaves the plan in
    greenfield mode with an honest ``error`` recorded — the run continues.
    """

    # ── provenance of the snapshot ──────────────────────────────────────
    available: bool = False
    error: str | None = None
    repo: str | None = None  # owner/name reconnoitred
    base_ref: str | None = None  # branch/tag reconnoitred
    commit: str | None = None  # resolved SHA the snapshot was built from

    # ── what the static scan found ──────────────────────────────────────
    languages: list[str] = Field(default_factory=list)  # most-prevalent first
    versions: dict[str, str] = Field(default_factory=dict)  # tool/runtime -> pin
    package_managers: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    databases: list[str] = Field(default_factory=list)
    cloud_providers: list[str] = Field(default_factory=list)
    iac: list[str] = Field(default_factory=list)  # terraform/helm/kubernetes/...
    iac_resources: dict[str, Any] = Field(default_factory=dict)
    # ── delivery surface (RFC-0013): how this repo ships ────────────────
    # CI provider detected, e.g. "github-actions" | "gitlab-ci" |
    # "azure-pipelines" | "none". None => recon never ran a CI probe (older
    # snapshot / non-software plan) and the deployment dimension is unknown.
    ci_system: str | None = None
    ci_pipeline_paths: list[str] = Field(default_factory=list)
    # Stages the discovered pipeline already shows evidence of running, e.g.
    # ["lint", "test", "build"]. Empty => no pipeline, or none recognised. This
    # is what lets the synthesized CI/CD child ask for the DELTA rather than
    # restating a pipeline the repo has had working for months (#462).
    ci_stages: list[str] = Field(default_factory=list)
    # Deploy mechanism, e.g. "argocd" | "helm" | "terraform" | "kubectl" |
    # "unknown" | "none". None => recon never ran a deploy probe.
    deploy_system: str | None = None
    # Discovered deploy manifests keyed by kind (argocd_applications, helm_charts,
    # terraform_files, dockerfiles, k8s_manifests), each a list of repo paths.
    deploy_manifests: dict[str, Any] = Field(default_factory=dict)
    # Environments the change can reach, inferred from manifest paths, e.g.
    # ["dev", "staging", "production"]. Empty => unknown (never fabricated).
    deploy_environments: list[str] = Field(default_factory=list)
    layout: dict[str, Any] = Field(default_factory=dict)
    existing_test_command: str | None = None
    entrypoints: list[str] = Field(default_factory=list)
    conventions: dict[str, Any] = Field(default_factory=dict)
    dependency_graph: dict[str, Any] = Field(default_factory=dict)

    # ── impact of the planned change (filled by the delta pass, Phase 4) ─
    blast_radius: dict[str, Any] = Field(default_factory=dict)

    def to_baseline_block(self) -> dict[str, Any]:
        """Render the contract ``baseline`` block (RFC-0010 / ``$defs.baseline``).

        Emits only populated fields so an unavailable or sparse RepoMap stays
        compact in the contract. Used by emit (Phase 4).
        """
        block: dict[str, Any] = {"available": self.available}
        if self.error:
            block["error"] = self.error
        for key in (
            "repo",
            "base_ref",
            "commit",
            "languages",
            "versions",
            "package_managers",
            "frameworks",
            "databases",
            "cloud_providers",
            "iac",
            "iac_resources",
            "ci_system",
            "ci_pipeline_paths",
            "deploy_system",
            "deploy_manifests",
            "deploy_environments",
            "layout",
            "existing_test_command",
            "entrypoints",
            "conventions",
            "dependency_graph",
            "blast_radius",
        ):
            value = getattr(self, key)
            if value:
                block[key] = value
        return block
