"""Static delivery-surface probe (RFC-0013) — CI system + deploy manifests.

The deployment analogue of :mod:`plan.recon.iac_probe`: a pure, read-only text
scan of the checkout that answers "how does this repo ship?" — which CI provider
runs it and which deploy mechanism it uses. Nothing is executed (no
``act``/``gitlab-runner``, no ``helm``/``terraform``); we only read files.

It feeds the RFC-0013 ``deployment`` contract block: the CI fields populate
``ci_system`` / ``ci_exists`` / ``ci_pipeline_paths`` / ``needs_pipeline`` and
the deploy fields seed ``deploy_system`` / ``deploy_target_environments``.

The CI-parsing approach is ported from AIFactory's ``analysis/ci_discovery.py``
(GitHub Actions / GitLab / Azure detection), re-implemented here as pure
functions so PFactory does NOT import across repos. Detection is by file
presence + a tolerant ``kind:`` regex (mirrors ``iac_probe``); no manifest is
deeply parsed and nothing is executed.
"""

from __future__ import annotations

import contextlib
import os
import re
from pathlib import Path
from typing import Any

# Bound the walk so a giant monorepo stays cheap (mirrors iac_probe).
_MAX_FILES_SCANNED = 6000

# Deploy-system signals, checked against discovered manifests.
_ARGOCD_KINDS = {"Application", "ApplicationSet", "AppProject"}
_ENV_TOKENS: tuple[tuple[str, str], ...] = (
    ("production", "production"),
    ("prod", "production"),
    ("staging", "staging"),
    ("stage", "staging"),
    ("preprod", "preprod"),
    ("pre-prod", "preprod"),
    ("uat", "preprod"),
    ("dev", "dev"),
    ("development", "dev"),
    ("test", "dev"),
    ("qa", "dev"),
)

_K8S_KIND = re.compile(r"^\s*kind:\s*['\"]?([A-Za-z0-9]+)['\"]?\s*$", re.MULTILINE)


def _iter_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    """Static, symlink-safe walk for files with the given suffixes."""
    out: list[Path] = []
    root = root.resolve()
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        if ".git" in dirnames:
            dirnames.remove(".git")
        for name in filenames:
            count += 1
            if count > _MAX_FILES_SCANNED:
                return out
            if name.endswith(suffixes):
                fp = Path(dirpath) / name
                if fp.is_symlink():
                    continue
                with contextlib.suppress(OSError):
                    if root in fp.resolve().parents or fp.resolve() == root:
                        out.append(fp)
    return out


def _read(fp: Path) -> str:
    try:
        return fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _rel(fp: Path, root: Path) -> str:
    try:
        return str(fp.resolve().relative_to(root.resolve()))
    except ValueError:
        return fp.name


# ── CI provider detection ──────────────────────────────────────────────────


def probe_ci(root: Path) -> dict[str, Any]:
    """Detect the CI provider and its pipeline files.

    Returns ``{"system": <provider>, "paths": [...]}`` where ``system`` is one of
    ``github-actions`` / ``gitlab-ci`` / ``azure-pipelines`` / ``none``. Only the
    first provider found (in that precedence order) is reported; ``paths`` lists
    the discovered pipeline files relative to ``root``.
    """
    root = Path(root)

    gha_dir = root / ".github" / "workflows"
    if gha_dir.is_dir():
        paths = sorted(
            _rel(p, root) for p in (*gha_dir.glob("*.yml"), *gha_dir.glob("*.yaml")) if p.is_file()
        )
        if paths:
            return {"system": "github-actions", "paths": paths}

    gitlab = root / ".gitlab-ci.yml"
    if gitlab.is_file():
        return {"system": "gitlab-ci", "paths": [_rel(gitlab, root)]}

    azure_paths = sorted(
        _rel(p, root)
        for name in ("azure-pipelines.yml", "azure-pipelines.yaml")
        if (p := root / name).is_file()
    )
    if azure_paths:
        return {"system": "azure-pipelines", "paths": azure_paths}

    return {"system": "none", "paths": []}


# ── deploy-manifest discovery ───────────────────────────────────────────────


def _find_argocd_apps(root: Path) -> list[str]:
    """Argo CD CRs: YAML docs whose ``kind`` is an Argo CD application kind."""
    found: list[str] = []
    for fp in _iter_files(root, (".yaml", ".yml")):
        text = _read(fp)
        if "argoproj.io" not in text and "argocd" not in text.lower():
            continue
        if any(k in _ARGOCD_KINDS for k in _K8S_KIND.findall(text)):
            rel = _rel(fp, root)
            if rel not in found:
                found.append(rel)
    return sorted(found)


def _find_dockerfiles(root: Path) -> list[str]:
    out: list[str] = []
    for fp in _iter_files(root, ("Dockerfile",)):
        out.append(_rel(fp, root))
    for fp in _iter_files(root, (".dockerfile",)):
        out.append(_rel(fp, root))
    return sorted(set(out))


def probe_deploy(root: Path, iac_inventory: dict[str, Any] | None = None) -> dict[str, Any]:
    """Discover deploy manifests and resolve the deploy mechanism.

    Reuses the existing :func:`plan.recon.iac_probe.probe_iac` inventory (passed
    in as ``iac_inventory`` to avoid a second walk) for Helm charts, Terraform
    files and plain K8s manifests, and adds Argo CD CRs + Dockerfiles. The deploy
    *system* is resolved by precedence: argocd > helm > terraform > kubectl(k8s).

    Returns ``{"system": <mechanism>, "manifests": {kind: [paths]}}`` where
    ``system`` is ``argocd`` / ``helm`` / ``terraform`` / ``kubectl`` /
    ``unknown`` / ``none``. ``unknown`` => deploy indicators (Dockerfile only)
    present but no resolvable mechanism; ``none`` => nothing deploy-shaped found.
    """
    root = Path(root)
    iac = iac_inventory or {}
    manifests: dict[str, list[str]] = {}

    argo = _find_argocd_apps(root)
    if argo:
        manifests["argocd_applications"] = argo

    helm = iac.get("helm") or {}
    helm_charts = sorted(c["file"] for c in helm.get("charts", []) if c.get("file"))
    if helm_charts:
        manifests["helm_charts"] = helm_charts

    tf = iac.get("terraform") or {}
    tf_files = sorted(tf.get("files", []) or [])
    if tf_files:
        manifests["terraform_files"] = tf_files

    k8s = iac.get("kubernetes") or {}
    k8s_paths = sorted({p for paths in (k8s.get("kinds") or {}).values() for p in paths})
    if k8s_paths:
        manifests["k8s_manifests"] = k8s_paths

    dockerfiles = _find_dockerfiles(root)
    if dockerfiles:
        manifests["dockerfiles"] = dockerfiles

    if argo:
        system = "argocd"
    elif helm_charts:
        system = "helm"
    elif tf_files:
        system = "terraform"
    elif k8s_paths:
        system = "kubectl"
    elif dockerfiles:
        # Image present but no orchestration manifest => mechanism unresolved.
        system = "unknown"
    else:
        system = "none"

    return {"system": system, "manifests": manifests}


def infer_environments(deploy: dict[str, Any]) -> list[str]:
    """Best-effort environment list from manifest paths (dev/staging/preprod/prod).

    Pure heuristic over discovered manifest paths: if a path contains an env
    token (``prod``/``staging``/…) we record that environment. Returns the
    normalized, de-duplicated set in a stable severity order. Empty when no
    environment is discernible — the caller treats that as ``unknown``
    (degrade, never fabricate a production target).
    """
    found: set[str] = set()
    paths = [p for paths in deploy.get("manifests", {}).values() for p in paths]
    for path in paths:
        low = path.lower()
        for token, env in _ENV_TOKENS:
            if re.search(rf"(^|[/_.-]){re.escape(token)}([/_.-]|$)", low):
                found.add(env)
    order = ["dev", "staging", "preprod", "production"]
    return [e for e in order if e in found]
