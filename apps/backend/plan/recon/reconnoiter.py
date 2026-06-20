"""Build a :class:`RepoMap` from a static read-only checkout (RFC-0010, Phase 2).

This is the planner's reconnaissance entry point. It clones the target repo
(read-only, no execution — see :mod:`plan.recon.clone`), runs the existing
``project/*`` detectors plus the IaC probe over the working tree, and returns a
:class:`RepoMap`. It **never raises**: any failure yields an unavailable
RepoMap and the pipeline continues in greenfield mode.

The detectors (``StackDetector``, ``StructureAnalyzer``, ``FrameworkDetector``)
are pure static file parsers already vendored in this repo; reconnaissance wires
them into planning for the first time (they were previously only used by the
security-profile builder).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from plan.recon.clone import clone_for_recon
from plan.recon.iac_probe import iac_tools, probe_iac
from plan.recon.models import RepoMap
from project.framework_detector import FrameworkDetector
from project.stack_detector import StackDetector
from project.structure_analyzer import StructureAnalyzer

logger = logging.getLogger(__name__)

# Version-pin files: filename -> (tool key, how to read the version).
_VERSION_FILES: dict[str, str] = {
    ".python-version": "python",
    ".nvmrc": "node",
    ".ruby-version": "ruby",
    ".terraform-version": "terraform",
    ".tool-versions": "",  # asdf: multi-tool, parsed specially
}


def _read(fp: Path) -> str:
    try:
        return fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _detect_versions(root: Path) -> dict[str, str]:
    """Best-effort pinned tool/runtime versions from common pin files."""
    versions: dict[str, str] = {}
    for fname, tool in _VERSION_FILES.items():
        fp = root / fname
        if not fp.is_file():
            continue
        text = _read(fp).strip()
        if fname == ".tool-versions":
            for line in text.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    versions[parts[0]] = parts[1]
        elif text and tool:
            versions[tool] = text.splitlines()[0].strip()
    # rust-toolchain.toml: channel = "1.90"
    rt = root / "rust-toolchain.toml"
    if rt.is_file():
        m = re.search(r'channel\s*=\s*"([^"]+)"', _read(rt))
        if m:
            versions["rust"] = m.group(1)
    # go.mod: `go 1.25`
    gm = root / "go.mod"
    if gm.is_file():
        m = re.search(r"^go\s+(\S+)", _read(gm), re.MULTILINE)
        if m:
            versions["go"] = m.group(1)
    # pyproject requires-python
    pp = root / "pyproject.toml"
    if pp.is_file():
        m = re.search(r'requires-python\s*=\s*"([^"]+)"', _read(pp))
        if m:
            versions.setdefault("python", m.group(1))
    return versions


def _existing_test_command(scripts) -> str | None:
    """Pick the most plausible test/check command from discovered scripts."""
    for name in scripts.npm_scripts:
        if name in ("test", "test:unit", "check"):
            return f"npm run {name}"
    for target in scripts.make_targets:
        if target in ("test", "check"):
            return f"make {target}"
    for name in scripts.poetry_scripts:
        if "test" in name:
            return f"poetry run {name}"
    return None


def _top_level_layout(root: Path) -> dict[str, list[str]]:
    """A shallow directory/file map (top level only — cheap, orienting)."""
    dirs: list[str] = []
    files: list[str] = []
    try:
        for entry in sorted(root.iterdir()):
            if entry.name == ".git":
                continue
            (dirs if entry.is_dir() else files).append(entry.name)
    except OSError:
        pass
    return {"dirs": dirs, "files": files}


def _conventions(stack) -> dict:
    """Convention signals that should constrain generation (linters/formatters)."""
    conv: dict = {}
    if stack.code_quality_tools:
        conv["code_quality_tools"] = list(stack.code_quality_tools)
    if stack.version_managers:
        conv["version_managers"] = list(stack.version_managers)
    return conv


def build_repo_map(root: Path, *, repo: str, base_ref: str | None, commit: str | None) -> RepoMap:
    """Build a RepoMap from an already-checked-out tree (no cloning here)."""
    stack = StackDetector(root).detect_all()
    scripts, _script_cmds, _custom = StructureAnalyzer(root).analyze()
    frameworks = FrameworkDetector(root).detect_all()
    inventory = probe_iac(root)
    iac = iac_tools(inventory)
    # An IaC-only repo (e.g. pure Terraform) has no code "language" by the stack
    # detector, but a change to it still has a grounded language — the IaC tool.
    # Fold it in so environment.language / change classification aren't blank.
    languages = list(stack.languages)
    if not languages and iac:
        languages = list(iac)
    return RepoMap(
        available=True,
        repo=repo,
        base_ref=base_ref,
        commit=commit,
        languages=languages,
        versions=_detect_versions(root),
        package_managers=list(stack.package_managers),
        frameworks=sorted(set(frameworks) | set(stack.frameworks)),
        databases=list(stack.databases),
        cloud_providers=list(stack.cloud_providers),
        iac=iac,
        iac_resources=inventory,
        layout=_top_level_layout(root),
        existing_test_command=_existing_test_command(scripts),
        conventions=_conventions(stack),
    )


def reconnoiter(repo: str | None, base_ref: str | None = None) -> RepoMap:
    """Clone ``repo`` read-only and return a static :class:`RepoMap`.

    Never raises. No target repo, or an unreadable one, yields
    ``RepoMap(available=False, ...)`` → greenfield planning.
    """
    if not repo:
        return RepoMap(available=False, error="no target repo supplied")
    try:
        with clone_for_recon(repo, base_ref) as c:
            if not c.ok or c.path is None:
                return RepoMap(available=False, repo=repo, base_ref=base_ref, error=c.error)
            return build_repo_map(c.path, repo=repo, base_ref=base_ref, commit=c.commit)
    except Exception as exc:
        logger.warning("reconnaissance failed for %s: %s", repo, exc)
        return RepoMap(available=False, repo=repo, base_ref=base_ref, error=str(exc))
