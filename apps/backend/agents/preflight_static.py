"""Pre-flight static check for Gen-Functional — Task 6 (#7) commit 2.

Before Gen-Functional commits an LLM-generated test file, this module
verifies that every import + every from-import attribute actually
resolves against the target project's Python environment. Catches the
single biggest failure mode in LLM-generated tests: hallucinated
imports + nonexistent methods (cited in the design plan's research as
~39% of Python test failures).

The check is a two-step pass:

  1. AST extraction — walk the generated source; collect every Import
     / ImportFrom node with line numbers. Aliased + multi-name imports
     expand into one entry per actual name.

  2. Subprocess introspection — for each extracted import, spawn
     `python -c "..."` with the target project's directory on
     PYTHONPATH and try to import the module (and, for from-imports,
     getattr the name). A non-zero exit is a hallucination.

Failures don't raise — they collect into a PreflightResult that the
caller (Gen-Functional in commit 5) uses to decide between (a) write
the test file, (b) write context/replan_request.json for the Planner.

What this DOES NOT check yet (covered in commit 3 via the flake-lint
sibling, or deferred):

  - Method calls like `auth.bcrypt_hash(...)` where `auth` is a local
    variable — needs symbol tracking, not just import tables.
  - Type-only imports (`if TYPE_CHECKING: import ...`) — treated as
    regular imports; the subprocess check will report them as failed
    if they're conditional-only modules.
  - Imports inside try/except blocks meant to be soft — same caveat.

For MVP, "every textual import statement must resolve" is the bar.
LLM-emitted hallucinations almost always show up at this layer.
"""

from __future__ import annotations

import ast
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# ─── Result + per-import dataclasses ─────────────────────────────────────


@dataclass
class PreflightImport:
    """One import statement (or one name in a from-import) we'll verify.

    For ``import a.b.c``: module='a.b.c', name=None
    For ``from a.b import c``: module='a.b', name='c'
    For ``from a.b import c, d as e``: two entries, names 'c' and 'd'
        (alias 'e' tracked separately; we verify the ORIGINAL name)
    For ``from a.b import *``: module='a.b', name='*' — module-only check
    For ``from . import x``: module='.x', name=None, is_relative=True
        — skipped at check time with a "couldn't verify" note
    """

    module: str
    name: str | None = None
    alias: str | None = None
    lineno: int = 0
    is_relative: bool = False

    # Filled in by check_import; defaults represent "not yet checked".
    failed: bool = False
    reason: str | None = None
    skipped: bool = False  # relative imports + similar non-checkable cases

    def describe(self) -> str:
        if self.name is None:
            return f"import {self.module}"
        if self.name == "*":
            return f"from {self.module} import *"
        a = f" as {self.alias}" if self.alias else ""
        return f"from {self.module} import {self.name}{a}"


@dataclass
class PreflightResult:
    """Outcome of one pre-flight check over a generated test source."""

    ok: bool
    imports_checked: list[PreflightImport] = field(default_factory=list)
    syntax_error: str | None = None  # set when the test source itself doesn't parse

    @property
    def failures(self) -> list[PreflightImport]:
        return [i for i in self.imports_checked if i.failed]

    @property
    def skipped(self) -> list[PreflightImport]:
        return [i for i in self.imports_checked if i.skipped]

    def summary(self) -> str:
        if self.syntax_error:
            return f"syntax error: {self.syntax_error}"
        if self.ok:
            return f"OK ({len(self.imports_checked)} imports checked)"
        bits = [f"{len(self.failures)} failed"]
        if self.skipped:
            bits.append(f"{len(self.skipped)} skipped")
        return ", ".join(bits)


# ─── AST extraction ──────────────────────────────────────────────────────


def extract_imports(source: str) -> tuple[list[PreflightImport], str | None]:
    """Walk the AST and collect every import statement.

    Returns ``(imports, syntax_error)``. On a parse failure, imports is
    empty and syntax_error has the exception text.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [], f"{exc.__class__.__name__}: {exc.msg} (line {exc.lineno})"

    out: list[PreflightImport] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            # `import a, b.c, d as e` → three entries
            for alias in node.names:
                out.append(PreflightImport(
                    module=alias.name,
                    name=None,
                    alias=alias.asname,
                    lineno=node.lineno,
                ))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            is_relative = node.level > 0
            # Reconstruct relative dotting for human-readable description
            if is_relative:
                module = "." * node.level + module
            for alias in node.names:
                out.append(PreflightImport(
                    module=module,
                    name=alias.name,  # may be '*'
                    alias=alias.asname,
                    lineno=node.lineno,
                    is_relative=is_relative,
                ))

    return out, None


# ─── Subprocess introspection ───────────────────────────────────────────


# NOTE on the script templates: we use plain str.format() substitution
# of repr'd values, then plain string concatenation (NOT f-strings) for
# error reporting. Nested f-strings + injected repr'd strings collide on
# quote characters and produce SyntaxError at the subprocess level.
_INTROSPECT_SCRIPT_IMPORT = """\
import importlib, sys
_mod = {module!r}
try:
    importlib.import_module(_mod)
except Exception as e:
    sys.stderr.write(type(e).__name__ + ': ' + str(e))
    sys.exit(1)
"""

_INTROSPECT_SCRIPT_FROM = """\
import importlib, sys
_mod = {module!r}
_name = {name!r}
try:
    m = importlib.import_module(_mod)
except Exception as e:
    sys.stderr.write('import ' + _mod + ': ' + type(e).__name__ + ': ' + str(e))
    sys.exit(1)
if not hasattr(m, _name):
    sys.stderr.write(_mod + ' has no attribute ' + _name)
    sys.exit(2)
"""


def check_import(
    imp: PreflightImport,
    *,
    project_dir: Path | None = None,
    python_exe: str | None = None,
    timeout_sec: float = 15.0,
) -> PreflightImport:
    """Mutate ``imp`` with check results. Returns the same object."""
    if imp.is_relative:
        imp.skipped = True
        imp.reason = "relative import — can't verify without package context"
        return imp

    python_exe = python_exe or sys.executable

    if imp.name in (None, "*"):
        script = _INTROSPECT_SCRIPT_IMPORT.format(module=imp.module)
    else:
        script = _INTROSPECT_SCRIPT_FROM.format(module=imp.module, name=imp.name)

    env = os.environ.copy()
    if project_dir is not None:
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{project_dir}{os.pathsep}{existing}" if existing else str(project_dir)
        )

    try:
        proc = subprocess.run(
            [python_exe, "-c", script],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        imp.failed = True
        imp.reason = f"introspection timed out after {timeout_sec}s"
        return imp
    except FileNotFoundError as exc:
        imp.failed = True
        imp.reason = f"python exe not found: {exc}"
        return imp

    if proc.returncode != 0:
        imp.failed = True
        imp.reason = (proc.stderr or proc.stdout or "exit non-zero").strip()[:300]

    return imp


# ─── Top-level entry point ──────────────────────────────────────────────


def preflight_check(
    test_source: str,
    *,
    project_dir: Path | None = None,
    python_exe: str | None = None,
    timeout_per_import_sec: float = 15.0,
    skip_stdlib_check: bool = False,
) -> PreflightResult:
    """Run the full pre-flight check on one generated test source.

    Args:
        test_source: The Python source the LLM produced.
        project_dir: Root of the target project (added to PYTHONPATH).
            Pass None to check against PFactory's own environment only
            (mostly useful in unit tests where stdlib resolution
            suffices).
        python_exe: Override the Python interpreter. Defaults to
            ``sys.executable``. For the eventual per-project venv use
            case, callers pass the target project's venv python.
        timeout_per_import_sec: Per-import subprocess cap.
        skip_stdlib_check: If True, common stdlib modules
            (json/pathlib/etc.) are skipped without subprocess. Faster
            but slightly less rigorous.

    Returns:
        PreflightResult with one entry per import + summary stats.
    """
    imports, syntax_err = extract_imports(test_source)
    if syntax_err is not None:
        return PreflightResult(ok=False, imports_checked=[], syntax_error=syntax_err)

    stdlib_names = set(sys.stdlib_module_names) if skip_stdlib_check else set()

    for imp in imports:
        if skip_stdlib_check and imp.module.split(".")[0] in stdlib_names:
            imp.skipped = True
            imp.reason = "stdlib — skipped"
            continue
        check_import(
            imp,
            project_dir=project_dir,
            python_exe=python_exe,
            timeout_sec=timeout_per_import_sec,
        )

    ok = all(not i.failed for i in imports)
    return PreflightResult(ok=ok, imports_checked=imports)
