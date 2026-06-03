"""Per-language mutation-signal dispatch (#41).

The mutate-and-check signal is the strongest of the five — a test whose
single mutated assertion still passes (SURVIVED) isn't constraining the
behaviour it claims to. Two language backends already exist:

  - Python   → ``agents.mutate_probe.run_mutate_probe``  (AST assertion mutation)
  - TypeScript → ``agents.lang_typescript.mutate_probe.run_ts_mutate_probe``
                 (regex assertion mutation + Stryker)

…but the Evaluator only ever called the Python one, so mutation scoring
silently degraded to "not computed" for TypeScript subtasks. This module
is the single dispatch seam: given a subtask's ``language``, route to the
right probe. Adding Java (PIT) / .NET later is one more branch.

Both probes return an object exposing ``.verdict`` (a ``*MutationVerdict``
enum whose ``KILLED`` / ``SURVIVED`` values the Evaluator prompt + Triager
read uniformly), so callers don't need to know which backend ran.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Language aliases → canonical key.
_ALIASES = {
    "python": "python", "py": "python",
    "typescript": "typescript", "ts": "typescript",
    "javascript": "typescript", "js": "typescript",  # JS tests run via the Jest/TS probe
}

# Languages with a wired mutation backend. Java (PIT) / C# (Stryker.NET)
# are registry-known but not yet implemented — see lang_registry.py.
SUPPORTED_LANGUAGES = frozenset({"python", "typescript"})

# File extension for the written mutant, per language.
_MUTANT_EXT = {"python": "py", "typescript": "ts"}


def normalize_language(language: str | None) -> str:
    """Canonicalise a language string (default ``python``)."""
    return _ALIASES.get((language or "python").strip().lower(),
                        (language or "python").strip().lower())


def is_mutation_supported(language: str | None) -> bool:
    """True if a mutation backend is wired for *language*."""
    return normalize_language(language) in SUPPORTED_LANGUAGES


def mutant_extension(language: str | None) -> str:
    """Mutant-file extension for *language* (``py`` / ``ts``)."""
    return _MUTANT_EXT.get(normalize_language(language), "py")


def run_language_mutation(
    language: str | None,
    test_file: Path,
    project_dir: Path,
    runner_fn: Any,
    *,
    mutant_path: Path,
) -> Any | None:
    """Run the mutation probe for *language*; return its report (or ``None``).

    ``None`` means "no mutation signal" — either the language has no wired
    backend, or the backend declined. The returned report always exposes
    ``.verdict`` regardless of backend.

    Args:
        language: subtask language (``python`` / ``typescript`` / aliases).
        test_file: the generated test to probe.
        project_dir: project root.
        runner_fn: the sandbox runner seam the probe uses to execute the
            mutant. Must match the chosen backend's expected shape.
        mutant_path: where the Python probe writes its mutant (TS uses its
            own temp dir).
    """
    lang = normalize_language(language)
    if lang == "python":
        from agents.mutate_probe import run_mutate_probe

        return run_mutate_probe(
            test_file, project_dir, runner_fn, write_mutant_to=mutant_path
        )
    if lang == "typescript":
        from agents.lang_typescript.mutate_probe import run_ts_mutate_probe

        return run_ts_mutate_probe(test_file, project_dir, runner_fn=runner_fn)
    return None
