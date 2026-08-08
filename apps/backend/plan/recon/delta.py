"""Map acceptance criteria / child issues to real files (RFC-0010, Phase 4).

Turns a :class:`~plan.recon.models.RepoMap` plus the decomposed epic into the
*delta* the plan will make: which existing files each unit of work touches
(``files_to_modify``), which look genuinely new (``files_to_create``), exemplar
neighbours to imitate (``patterns_from``), and the acceptance-criterion → code
map. All best-effort and static — a miss just leaves a field empty.

Heuristics (deliberately conservative to avoid noise):

* A known repo path is *modified* when its basename appears in the unit's text,
  or when an IaC resource's type/name keywords appear (e.g. "node group" →
  ``aws_eks_node_group`` → ``node_groups.tf``).
* A file-like token with a code extension that is **not** a known path is
  *created*.
* ``patterns_from`` offers up to two existing files sharing a modified file's
  extension, as style exemplars.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan
    from plan.models import NormalizedPlan
    from plan.recon.models import RepoMap

# Extensions a file token must carry to be mined into a child's footprint.
#
# SCOPE. This list covers every language `plan.recon.language_reconcile.
# _LANGUAGE_SIGNALS` can detect. It used to cover seven of them, so a repo in
# C#, Kotlin, PHP, Swift or C/C++ had every file token in every child's text
# discarded — the child reached AIFactory with an EMPTY footprint and the coder
# got no file target at all. PFactory could name those languages and then plan
# as if their files did not exist (#475). Tying the two lists together is what
# stops that reopening: a language added to the signal table without an
# extension here is silently unplannable again.
#
# WHY `.md`, `.yaml`, `.json` ARE STILL HERE. The list does two jobs — "is this
# a code file" and "is this a file at all" — and #461's design-doc defect came
# in through `.md`. Removing it is not the fix: plans legitimately name docs to
# create or modify, and the #461 defect was a child pointing at a design doc as
# its TEST target, which was fixed at its source by naming concrete test files.
# Narrowing here would break real footprints to re-fix a fixed bug.
_CODE_EXTS = (
    ".py",
    ".tf",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".rb",
    # C#, Kotlin, PHP, Swift, C/C++ — the five the signal table knew and this
    # list did not (#475).
    ".cs",
    ".kt",
    ".kts",
    ".php",
    ".swift",
    ".c",
    ".h",
    ".cc",
    ".hh",
    ".cpp",
    ".hpp",
    ".cxx",
    ".yaml",
    ".yml",
    ".json",
    ".sh",
    ".sql",
    ".tpl",
    ".md",
)
# NOTE: the {1,5} bound means a 6-character extension is never even tokenised,
# so `.csproj` and `.gradle` paths cannot be mined however this list grows.
# Left as-is: widening it also admits more false positives from prose, and no
# child currently needs to name one.
_FILE_TOKEN = re.compile(r"[\w./-]+\.[A-Za-z]{1,5}")


def known_paths(repo_map: RepoMap) -> set[str]:
    """All file paths reconnaissance saw (IaC inventory + CI + top-level layout).

    ``ci_pipeline_paths`` matters because the layout scan is top-level only, so
    ``.github/workflows/ci.yml`` was invisible here: a child naming it was told
    to *create* a file that already existed. The CI probe already found it
    (RFC-0013), so use what it found.
    """
    paths: set[str] = set()
    paths.update(repo_map.ci_pipeline_paths or [])
    iac = repo_map.iac_resources or {}
    tf = iac.get("terraform") or {}
    paths.update(tf.get("files", []) or [])
    for r in tf.get("resources", []) or []:
        if r.get("file"):
            paths.add(r["file"])
    for c in (iac.get("helm") or {}).get("charts", []) or []:
        if c.get("file"):
            paths.add(c["file"])
    for files in ((iac.get("kubernetes") or {}).get("kinds") or {}).values():
        paths.update(files)
    paths.update((repo_map.layout or {}).get("files", []) or [])
    return {p for p in paths if p}


def _iac_resource_files(repo_map: RepoMap, text: str) -> set[str]:
    """Files of IaC resources whose type/name keywords appear in ``text``.

    Each resource contributes the significant words of its type (sans common
    provider prefixes) and its name. A resource matches when at least two such
    words appear in the text — enough to catch "modify the EKS node group"
    (``eks`` + ``node`` + ``group``) without a single generic word over-matching.
    """
    out: set[str] = set()
    tf = (repo_map.iac_resources or {}).get("terraform") or {}
    low = f" {text.lower()} "
    for r in tf.get("resources", []) or []:
        if not r.get("file"):
            continue
        rtype = r.get("type", "")
        for prefix in ("aws_", "google_", "azurerm_"):
            rtype = rtype.removeprefix(prefix)
        words = {w for w in (rtype + "_" + r.get("name", "")).split("_") if len(w) > 2}
        hits = sum(1 for w in words if w in low)
        if hits >= 2:
            out.add(r["file"])
    return out


def _referenced(text: str, paths: set[str], repo_map: RepoMap) -> tuple[list[str], list[str]]:
    """Return (modified, created) file paths referenced by ``text``."""
    low = text.lower()
    by_base: dict[str, str] = {}
    for p in paths:
        by_base.setdefault(p.split("/")[-1].lower(), p)

    modify: set[str] = set()
    for base, full in by_base.items():
        if base in low:
            modify.add(full)
    modify |= _iac_resource_files(repo_map, text)

    create: set[str] = set()
    for tok in _FILE_TOKEN.findall(text):
        if not tok.lower().endswith(_CODE_EXTS):
            continue
        if tok in paths or tok.split("/")[-1].lower() in by_base:
            modify.add(by_base.get(tok.split("/")[-1].lower(), tok))
        else:
            create.add(tok)
    return sorted(modify), sorted(create - set(modify))


def _patterns_from(modify: list[str], paths: set[str]) -> list[str]:
    """Up to two existing files sharing a modified file's extension (exemplars)."""
    if not modify:
        return []
    exts = {("." + m.rsplit(".", 1)[-1]) for m in modify if "." in m}
    out = [p for p in sorted(paths) if p not in modify and any(p.endswith(e) for e in exts)]
    return out[:2]


def compute_footprints(plan: NormalizedPlan, epic: EpicPlan) -> dict[str, dict[str, list[str]]]:
    """Per-child footprints keyed by child.key. Empty when no RepoMap."""
    rm = plan.repo_map
    if rm is None or not rm.available:
        return {}
    paths = known_paths(rm)
    out: dict[str, dict[str, list[str]]] = {}
    for child in epic.children:
        text = " ".join([child.title, child.body or "", *child.acceptance_criteria])
        modify, create = _referenced(text, paths, rm)
        if modify or create:
            out[child.key] = {
                "files_to_modify": modify,
                "files_to_create": create,
                "patterns_from": _patterns_from(modify, paths),
            }
    return out


def build_ac_to_code_map(plan: NormalizedPlan, epic: EpicPlan) -> dict[str, list[str]]:
    """AC id → existing files it most plausibly covers (pre-seeded at plan time)."""
    rm = plan.repo_map
    base = {c.id: [] for c in plan.criteria}
    if rm is None or not rm.available:
        return base
    paths = known_paths(rm)
    for crit in plan.criteria:
        modify, _create = _referenced(crit.text, paths, rm)
        base[crit.id] = modify
    return base


def blast_radius(
    footprints: dict[str, dict[str, list[str]]], repo_map: RepoMap | None
) -> dict[str, Any]:
    """Union of touched files + a destructive-IaC advisory list (best-effort)."""
    touched = sorted({f for fp in footprints.values() for f in fp.get("files_to_modify", [])})
    destructive: list[str] = []
    if repo_map is not None:
        tf = (repo_map.iac_resources or {}).get("terraform") or {}
        cluster_files = {
            r["file"] for r in tf.get("resources", []) or [] if "cluster" in r.get("type", "")
        }
        # Modifying a cluster-defining file is the classic force-replace risk.
        destructive = sorted(cluster_files & set(touched))
    out: dict[str, Any] = {"files": touched}
    if destructive:
        out["destructive_iac"] = destructive
    return out
