"""Custom exceptions for the .pfactory.yml parser/validator.

Reconstructed for Task 2 (#18) merge into v0.2: the subagent's branch was
authored against a corrupted worktree that didn't include this module on
disk, even though every other module in the package imports from it.
The shape here matches every constructor call in ``parser.py`` —
``PFactoryYmlError(path, message, errors=[])`` — and the attribute-access
expectations of ``tests/test_pfactory_yml.py`` (path-in-``str(exc_info.value)``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class PFactoryYmlError(Exception):
    """Raised when ``.pfactory.yml`` parsing or validation fails.

    Parameters
    ----------
    path:
        Filesystem path to the ``.pfactory.yml`` file the error refers to.
        Included in the formatted message so operators can find the file.
    message:
        Human-readable description of what went wrong.
    errors:
        Optional list of structured Pydantic ``ValidationError`` items
        (each typically a dict with ``loc``, ``msg``, ``type``). Empty for
        non-Pydantic errors (YAML syntax, type-at-root mismatch).
    """

    def __init__(
        self,
        path: Path,
        message: str,
        *,
        errors: list[Any] | None = None,
    ) -> None:
        self.path = path
        self.message = message
        self.errors: list[Any] = list(errors) if errors else []
        formatted = f"{path}: {message}"
        if self.errors:
            formatted += f" ({len(self.errors)} validation error{'s' if len(self.errors) != 1 else ''})"
        super().__init__(formatted)

    def __repr__(self) -> str:
        return (
            f"PFactoryYmlError(path={self.path!r}, "
            f"message={self.message!r}, errors={self.errors!r})"
        )
