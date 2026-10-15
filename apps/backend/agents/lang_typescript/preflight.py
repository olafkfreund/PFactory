"""TypeScript pre-flight check — Task 9 (#25) commit 1.

Before Gen-Functional commits an LLM-generated TypeScript test file, this
module verifies that every import resolves and that the file has no
TypeScript compilation errors.  Catches the same class of hallucination as
the Python sibling (``preflight_static.py``): ~39% of LLM-generated test
failures are caused by unresolved imports.

The check runs ``tsc --noEmit <test_file>`` inside the runner Docker image
(``tfactory-runner-jest:latest`` by default).  ``tsc`` exits non-zero if
there are any errors; the output is parsed to classify findings:

  TS2307  — "Cannot find module '…'"           → unresolved_imports
  TS2304  — "Cannot find name '…'"             → unresolved_imports
  TS1005  — "… expected"  (syntax/parse error)  → other_errors
  everything else                              → other_errors

The report shape mirrors ``preflight_static.PreflightResult`` so the
Evaluator can dispatch by language without per-language branches.

Public API::

    report = run_ts_preflight(test_file, project_dir)
    if not report.ok:
        # report.unresolved_imports — tuple of bare specifiers like './foo'
        # report.other_errors       — tuple of raw error strings
        # report.raw_output         — full tsc stderr for debugging

Implementation note on runner_fn injection:

    The default ``runner_fn=None`` path constructs a ``DockerRunner`` from
    ``tools.runners.docker_runner`` and runs ``tsc --noEmit`` in the
    container.  Tests inject a mock callable with signature:

        runner_fn(cmd: list[str], cwd: str) -> _RunResultLike

    where ``_RunResultLike`` is a duck-type with ``.returncode``,
    ``.stdout``, and ``.stderr`` attributes.
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─── Error-code regex patterns ──────────────────────────────────────────────

# tsc error format:
#   path/to/file.ts(line,col): error TSxxxx: message text
_TSC_LINE_RE = re.compile(
    r"^(?P<file>[^(]+)\((?P<line>\d+),(?P<col>\d+)\):\s+error\s+(?P<code>TS\d+):\s+(?P<msg>.+)$"
)

# TS2307: Cannot find module './foo' or its corresponding type declarations.
# TS2304: Cannot find name 'Foo'.
_UNRESOLVED_IMPORT_CODES = frozenset({"TS2307", "TS2304"})

# Module specifier extraction from TS2307 messages.
# E.g. "Cannot find module './does-not-exist' or its corresponding type declarations."
_MODULE_SPECIFIER_RE = re.compile(r"Cannot find module '([^']+)'")

# TS name extraction from TS2304 messages.
# E.g. "Cannot find name 'Foo'."
_NAME_SPECIFIER_RE = re.compile(r"Cannot find name '([^']+)'")


# ─── Result type ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TSPreflightReport:
    """Outcome of one TypeScript pre-flight check.

    Attributes:
        test_file: The TypeScript test file that was checked.
        ok: True if there were no compilation errors (exit code 0).
        unresolved_imports: Tuple of module / name specifiers that tsc
            reported as unresolvable (TS2307 / TS2304 codes).  E.g.
            ``("./does-not-exist", "@org/missing-pkg")``.
        other_errors: Tuple of raw error strings for all other tsc codes
            (syntax errors, type errors, etc.).
        raw_output: Full tsc stdout + stderr, useful for debugging.
    """

    test_file: Path
    ok: bool
    unresolved_imports: tuple[str, ...]
    other_errors: tuple[str, ...]
    raw_output: str

    def summary(self) -> str:
        """Human-readable one-line summary."""
        if self.ok:
            return "OK (tsc --noEmit passed)"
        parts = []
        if self.unresolved_imports:
            parts.append(f"{len(self.unresolved_imports)} unresolved import(s)")
        if self.other_errors:
            parts.append(f"{len(self.other_errors)} other error(s)")
        return "; ".join(parts) or "tsc reported errors (no details parsed)"


# ─── Output parser ──────────────────────────────────────────────────────────


def _parse_tsc_output(raw: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Parse ``tsc --noEmit`` output into (unresolved_imports, other_errors).

    Args:
        raw: The combined stdout + stderr from the tsc invocation.

    Returns:
        A pair ``(unresolved_imports, other_errors)`` where
        ``unresolved_imports`` is a deduplicated tuple of module/name
        specifiers and ``other_errors`` is a tuple of raw error lines.
    """
    unresolved: list[str] = []
    other: list[str] = []

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue

        m = _TSC_LINE_RE.match(line)
        if m is None:
            # Not a structured tsc error line; include in other_errors if it
            # looks like an error (starts with "error" or "Error").
            if line.lower().startswith("error"):
                other.append(line)
            continue

        code = m.group("code")
        msg = m.group("msg")

        if code == "TS2307":
            specifier_m = _MODULE_SPECIFIER_RE.search(msg)
            specifier = specifier_m.group(1) if specifier_m else msg
            if specifier not in unresolved:
                unresolved.append(specifier)
        elif code == "TS2304":
            name_m = _NAME_SPECIFIER_RE.search(msg)
            specifier = name_m.group(1) if name_m else msg
            if specifier not in unresolved:
                unresolved.append(specifier)
        else:
            # All other TS error codes → other_errors.
            other.append(f"{code}: {msg}")

    return tuple(unresolved), tuple(other)


# ─── Duck-type for runner result ─────────────────────────────────────────────


class _RunResultLike:
    """Minimal duck-type expected from runner_fn return values.

    DockerRunResult and the test mock both implement this interface.
    """

    returncode: int
    stdout: str
    stderr: str


# ─── Default runner implementation ──────────────────────────────────────────


def _default_runner(
    cmd: list[str],
    cwd: str,
    *,
    image: str,
    timeout: int = 120,
) -> Any:
    """Fall-back runner that calls the real DockerRunner.

    Imported lazily to avoid the import at module level in test environments
    where Docker is not available.

    Args:
        cmd: Command to run inside the container.
        cwd: Working directory inside the container (mounted project dir).
        image: Docker image to use.
        timeout: Container run timeout in seconds.

    Returns:
        A DockerRunResult with .returncode, .stdout, .stderr.
    """
    from tools.runners.docker_runner import DockerRunner  # noqa: PLC0415

    runner = DockerRunner(image=image, timeout=timeout)
    return runner.run(cmd, cwd=cwd)


# ─── Public entry point ──────────────────────────────────────────────────────


def run_ts_preflight(
    test_file: Path,
    project_dir: Path,
    *,
    runner_fn: Callable[..., Any] | None = None,
    runner_image: str = "tfactory-runner-jest:latest",
    timeout: int = 120,
) -> TSPreflightReport:
    """Run ``tsc --noEmit <test_file>`` inside the runner image.

    Returns an :class:`TSPreflightReport` describing whether the file
    compiles cleanly.  ``ok=True`` means no unresolved imports and no
    syntax errors.

    Args:
        test_file: Absolute path to the TypeScript test file to check.
        project_dir: Project root (mounted into the container at the same
            path so that relative imports resolve correctly).
        runner_fn: Injection point for tests.  Must accept
            ``(cmd: list[str], cwd: str, *, image: str, timeout: int)``
            and return an object with ``.returncode``, ``.stdout``,
            ``.stderr``.  Defaults to a real DockerRunner invocation.
        runner_image: Docker image that contains ``tsc``.  Defaults to
            ``tfactory-runner-jest:latest`` (Task 7 build).
        timeout: Container run timeout in seconds.

    Returns:
        :class:`TSPreflightReport`
    """
    # Build the tsc command.
    # --noEmit: type-check only, don't write JS output.
    # --skipLibCheck: skip type-checking of .d.ts files to keep the check fast.
    # --strict: enable all strict checks so we catch implicit any + null issues.
    # --esModuleInterop: allow default imports from CommonJS modules.
    cmd = [
        "tsc",
        "--noEmit",
        "--skipLibCheck",
        "--strict",
        "--esModuleInterop",
        str(test_file),
    ]
    cwd = str(project_dir)

    effective_runner = runner_fn or _default_runner

    try:
        result = effective_runner(
            cmd,
            cwd,
            image=runner_image,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return TSPreflightReport(
            test_file=test_file,
            ok=False,
            unresolved_imports=(),
            other_errors=("tsc invocation timed out",),
            raw_output="",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("tsc runner raised: %s", exc)
        return TSPreflightReport(
            test_file=test_file,
            ok=False,
            unresolved_imports=(),
            other_errors=(f"runner error: {exc}",),
            raw_output="",
        )

    raw_output = (result.stdout or "") + (result.stderr or "")

    if result.returncode == 0:
        return TSPreflightReport(
            test_file=test_file,
            ok=True,
            unresolved_imports=(),
            other_errors=(),
            raw_output=raw_output,
        )

    unresolved, other = _parse_tsc_output(raw_output)
    return TSPreflightReport(
        test_file=test_file,
        ok=False,
        unresolved_imports=unresolved,
        other_errors=other,
        raw_output=raw_output,
    )
