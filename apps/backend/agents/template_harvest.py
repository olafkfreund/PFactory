"""Template harvesting — promote high-confidence accepted tests into a reusable
template library that grows over time.

When the Triager finishes, any *accepted* test that clears a confidence bar
(stable across re-runs · mutation killed (or N/A for the lane) · high semantic
relevance) is promoted into a `.tmpl` template — so a test written once becomes
a reusable pattern the next run / project can start from via
``/pfactory-from-template``.

Two libraries (chosen in the demo design):
  * project-local — ``<project_dir>/.pfactory/templates/<framework>/`` (committed
    with the repo; the default).
  * global        — ``~/.pfactory/templates/<framework>/`` (shared across every
    project; opt-in via ``also_global``).

Each library carries a ``templates-index.json`` so harvested templates are
discoverable and fingerprint-deduped (the same pattern is never harvested twice).
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# Mutation values that DON'T block promotion (killed = good; the rest mean the
# lane simply doesn't run mutation).
_MUTATION_OK = frozenset({"killed", "no_mutation", "skip", "skipped", None})


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "template").lower()).strip("-")
    return s or "template"


def _ext_for(language: str | None, framework: str | None) -> str:
    lang = (language or "").lower()
    if lang == "python":
        return "py"
    if framework in ("playwright",):
        return "spec.ts"
    return "test.ts" if lang == "typescript" else "txt"


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    """Read a field from a dict-or-dataclass candidate/verdict."""
    for n in names:
        if isinstance(obj, dict) and n in obj:
            return obj[n]
        if hasattr(obj, n):
            return getattr(obj, n)
    return default


def _signals(verdict: Any) -> dict:
    sig = _get(verdict, "signals_summary", "signals", default={}) or {}
    return sig if isinstance(sig, dict) else {}


def _passes_bar(candidate: Any) -> bool:
    """High-confidence accept: stable · mutation-ok · semantic high."""
    if _get(candidate, "verdict_label", default="") != "accept":
        return False
    verdict = _get(candidate, "verdict", default={}) or {}
    sig = _signals(verdict)
    stability = sig.get("stability")
    mutation = sig.get("mutation")
    semantic = _get(verdict, "semantic_relevance", default=sig.get("semantic_relevance"))
    return (
        stability in ("stable", None) and mutation in _MUTATION_OK and (semantic in ("high", None))
    )


def _parametrise_python(body: str) -> tuple[str, list[str]]:
    """Best-effort: turn the imported module path into ``${module_path}`` so the
    pattern is reusable against a different module. Everything else stays
    verbatim (safe). Returns (body, vars)."""
    try:
        tree = ast.parse(body)
    except SyntaxError:
        return body, []
    mod = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module
            not in (
                "__future__",
                "pytest",
                "httpx",
            )
        ):
            mod = node.module
            break
    if not mod:
        return body, []
    new = re.sub(rf"\bfrom\s+{re.escape(mod)}\s+import\b", "from ${module_path} import", body)
    return new, ["module_path"]


def _fingerprint(body: str) -> str:
    norm = re.sub(r"\s+", " ", body).strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def _load_index(lib_root: Path) -> dict:
    idx = lib_root / "templates-index.json"
    if idx.exists():
        try:
            return json.loads(idx.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"version": 1, "templates": []}


def _write_index(lib_root: Path, index: dict) -> None:
    lib_root.mkdir(parents=True, exist_ok=True)
    (lib_root / "templates-index.json").write_text(json.dumps(index, indent=2, sort_keys=True))


def _detect_from_ext(rel: str) -> tuple[str, str]:
    """(framework, language) inferred from the test file name as a last resort."""
    r = rel.lower()
    if r.endswith(".spec.ts") or r.endswith(".spec.tsx"):
        return "playwright", "typescript"
    if r.endswith(".test.ts") or r.endswith(".test.tsx"):
        return "jest", "typescript"
    if r.endswith(".py"):
        return "pytest", "python"
    return "pytest", "python"


def _harvest_one(
    test_path: Path,
    candidate: Any,
    lib_root: Path,
    meta: dict,
) -> Path | None:
    """Write one accepted test into ``lib_root`` as a `.tmpl`, dedup by
    fingerprint. ``meta`` carries the resolved framework/language/lane/covers/
    rationale (from the test_plan subtask, with verdict + extension fallbacks).
    ``lib_root`` is the per-scope ``.pfactory/templates`` dir."""
    body = test_path.read_text(encoding="utf-8")
    fp = _fingerprint(body)
    index = _load_index(lib_root)
    if any(t.get("fingerprint") == fp for t in index["templates"]):
        return None  # already harvested this pattern

    framework = meta["framework"]
    language = meta["language"]
    lane = meta["lane"]
    test_id = meta["test_id"]
    covers = meta["covers_acs"]
    rationale = meta["rationale"]
    description = (rationale.splitlines()[0] if rationale else f"Harvested from {test_id}")[:160]

    if (language or "").lower() == "python":
        tmpl_body, tvars = _parametrise_python(body)
    else:
        tmpl_body, tvars = body, []

    front = {
        "description": description,
        "framework": framework,
        "lane": lane,
        "language": language,
        "covers_acs": covers,
        "harvested_from": test_id,
        "harvested_at": _now_iso(),
        "fingerprint": fp,
        "vars": tvars,
    }
    fm = "\n".join(f"{k}: {json.dumps(v)}" for k, v in front.items())
    content = f"---\n{fm}\n---\n{tmpl_body}"

    fw_dir = lib_root / framework
    fw_dir.mkdir(parents=True, exist_ok=True)
    ext = _ext_for(language, framework)
    out = fw_dir / f"{_slug(test_id)}.{ext}.tmpl"
    out.write_text(content, encoding="utf-8")

    index["templates"].append(
        {
            k: front[k]
            for k in (
                "description",
                "framework",
                "lane",
                "harvested_from",
                "fingerprint",
                "harvested_at",
            )
        }
        | {"file": str(out.relative_to(lib_root))}
    )
    _write_index(lib_root, index)
    return out


def harvest_accepted_tests(
    spec_dir: Path,
    project_dir: Path,
    candidates: Iterable[Any],
    *,
    also_global: bool = False,
) -> list[Path]:
    """Promote each high-confidence accepted candidate into the reusable
    template library (project-local, and optionally the global library).

    Returns the list of written template paths (deduped patterns are skipped).
    Never raises — harvesting is a best-effort, non-fatal side-effect.
    """
    written: list[Path] = []
    roots = [Path(project_dir) / ".pfactory" / "templates"]
    if also_global:
        roots.append(Path(os.path.expanduser("~/.pfactory")) / "templates")

    # Map test_file -> subtask so we can read the AUTHORITATIVE framework /
    # language / lane (the verdict dict doesn't carry them per-test).
    plan_by_file: dict[str, dict] = {}
    try:
        plan = json.loads((Path(spec_dir) / "test_plan.json").read_text())
        for ph in plan.get("phases", []):
            for st in ph.get("subtasks", []):
                for f in st.get("files_to_create") or []:
                    plan_by_file[f] = st
    except (OSError, json.JSONDecodeError):
        pass

    for candidate in candidates:
        if not _passes_bar(candidate):
            continue
        rel = _get(candidate, "test_file", default=None)
        if not rel:
            continue
        test_path = Path(spec_dir) / rel
        if not test_path.exists():
            test_path = Path(project_dir) / rel
        if not test_path.exists():
            continue

        verdict = _get(candidate, "verdict", default={}) or {}
        st = plan_by_file.get(rel, {})
        ext_fw, ext_lang = _detect_from_ext(rel)
        rationale = st.get("rationale") or _get(verdict, "rationale", default="") or ""
        meta = {
            "framework": st.get("framework") or _get(verdict, "framework") or ext_fw,
            "language": st.get("language") or _get(verdict, "language") or ext_lang,
            "lane": st.get("lane") or _get(verdict, "lane") or "unit",
            "test_id": _get(candidate, "test_id", default=st.get("id") or "harvested"),
            "covers_acs": list(
                st.get("covers_acs") or _get(verdict, "covers_acs", default=[]) or []
            ),
            "rationale": rationale,
        }
        for lib_root in roots:
            try:
                out = _harvest_one(test_path, candidate, lib_root, meta)
                if out is not None:
                    written.append(out)
                    _log.info("harvested template %s", out)
            except Exception as exc:  # noqa: BLE001 — non-fatal
                _log.warning("template harvest failed for %s: %s", rel, exc)
    return written
