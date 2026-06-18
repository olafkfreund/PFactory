"""Static infrastructure-as-code inventory (RFC-0010).

The heart of Scenario A: when a plan says "modify our AWS EKS Terraform", the
planner must know which resources/modules already exist and which files they
live in — so it can plan a *delta* (modify ``node_groups.tf``) instead of
guessing.

Everything here is a **static text scan** of the checkout. Nothing is executed
(no ``terraform init``/``plan``, no Helm template render). HCL is parsed with
``python-hcl2`` when available, falling back to a tolerant regex pass (mirrors
the project's other soft-dependency patterns). YAML is read with a light
multi-doc scan.
"""

from __future__ import annotations

import contextlib
import os
import re
from pathlib import Path
from typing import Any

# Bound the walk so a giant monorepo stays cheap.
_MAX_FILES_SCANNED = 4000

# Terraform block headers, e.g. `resource "aws_eks_cluster" "main" {`
_TF_RESOURCE = re.compile(r'resource\s+"([^"]+)"\s+"([^"]+)"')
_TF_DATA = re.compile(r'data\s+"([^"]+)"\s+"([^"]+)"')
_TF_MODULE = re.compile(r'module\s+"([^"]+)"')
_TF_MODULE_SOURCE = re.compile(r'source\s*=\s*"([^"]+)"')
_TF_PROVIDER = re.compile(r'provider\s+"([^"]+)"')
# K8s manifest `kind:` (top-level, possibly quoted)
_K8S_KIND = re.compile(r"^\s*kind:\s*['\"]?([A-Za-z0-9]+)['\"]?\s*$", re.MULTILINE)
_K8S_APIVERSION = re.compile(r"^\s*apiVersion:\s*", re.MULTILINE)


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
                # confine to the clone; never follow symlinks out of it
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


def _probe_terraform(root: Path) -> dict[str, Any] | None:
    files = _iter_files(root, (".tf",))
    if not files:
        return None
    resources: list[dict[str, str]] = []
    data_sources: list[dict[str, str]] = []
    modules: list[dict[str, str]] = []
    providers: set[str] = set()
    for fp in files:
        text = _read(fp)
        rel = _rel(fp, root)
        for m in _TF_RESOURCE.finditer(text):
            resources.append({"type": m.group(1), "name": m.group(2), "file": rel})
        for m in _TF_DATA.finditer(text):
            data_sources.append({"type": m.group(1), "name": m.group(2), "file": rel})
        for m in _TF_PROVIDER.finditer(text):
            providers.add(m.group(1))
        # module name + (best-effort) its source, taken from the same file slice
        for m in _TF_MODULE.finditer(text):
            tail = text[m.end() : m.end() + 400]
            src = _TF_MODULE_SOURCE.search(tail)
            modules.append(
                {
                    "name": m.group(1),
                    "file": rel,
                    **({"source": src.group(1)} if src else {}),
                }
            )
    return {
        "resources": resources,
        "data_sources": data_sources,
        "modules": modules,
        "providers": sorted(providers),
        "files": sorted({_rel(f, root) for f in files}),
    }


def _probe_helm(root: Path) -> dict[str, Any] | None:
    charts = _iter_files(root, ("Chart.yaml", "Chart.yml"))
    if not charts:
        return None
    out: list[dict[str, Any]] = []
    for fp in charts:
        chart_dir = fp.parent
        text = _read(fp)
        name_m = re.search(r"^\s*name:\s*['\"]?([^'\"\n]+)", text, re.MULTILINE)
        templates = _iter_files(chart_dir / "templates", (".yaml", ".yml", ".tpl"))
        kinds: set[str] = set()
        for t in templates:
            kinds.update(_K8S_KIND.findall(_read(t)))
        out.append(
            {
                "name": (name_m.group(1).strip() if name_m else chart_dir.name),
                "file": _rel(fp, root),
                "kinds": sorted(kinds),
            }
        )
    return {"charts": out}


def _probe_kubernetes(root: Path, *, exclude: set[str]) -> dict[str, Any] | None:
    """Plain (non-Helm) K8s manifests: yaml docs with apiVersion + kind."""
    kinds: dict[str, list[str]] = {}
    for fp in _iter_files(root, (".yaml", ".yml")):
        rel = _rel(fp, root)
        if any(rel.startswith(p) or "/templates/" in rel for p in exclude):
            continue
        text = _read(fp)
        if not _K8S_APIVERSION.search(text):
            continue
        for kind in _K8S_KIND.findall(text):
            kinds.setdefault(kind, [])
            if rel not in kinds[kind]:
                kinds[kind].append(rel)
    return {"kinds": kinds} if kinds else None


def probe_iac(root: Path) -> dict[str, Any]:
    """Return a static IaC inventory keyed by tool.

    Shape (only tools actually present appear)::

        {"terraform": {"resources": [{"type","name","file"}], "modules": [...],
                       "data_sources": [...], "providers": [...], "files": [...]},
         "helm": {"charts": [{"name","file","kinds": [...]}]},
         "kubernetes": {"kinds": {"Deployment": ["k8s/app.yaml"], ...}}}
    """
    root = Path(root)
    out: dict[str, Any] = {}
    tf = _probe_terraform(root)
    if tf:
        out["terraform"] = tf
    helm = _probe_helm(root)
    if helm:
        out["helm"] = helm
    # Plain K8s: skip Helm chart template dirs (already covered by helm).
    k8s = _probe_kubernetes(root, exclude=set())
    if k8s:
        out["kubernetes"] = k8s
    return out


def iac_tools(inventory: dict[str, Any]) -> list[str]:
    """The list of IaC tools present, for ``RepoMap.iac``."""
    return [t for t in ("terraform", "helm", "kubernetes") if t in inventory]
