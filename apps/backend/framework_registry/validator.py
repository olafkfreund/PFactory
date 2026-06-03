"""Descriptor validator — Task 1 (#17).

Converts a raw ``dict`` (the ``yaml.safe_load`` output for a
``descriptor.yaml`` file) into a validated :class:`FrameworkDescriptor`.

``validate_descriptor(data)`` raises :class:`FrameworkDescriptorError` with
a ``field`` attribute and a clear ``reason`` for every schema violation it
detects — one error at a time (first error wins; no partial validation).

Example::

    import yaml
    from framework_registry.validator import validate_descriptor

    with open("frameworks/pytest/descriptor.yaml") as fh:
        data = yaml.safe_load(fh)

    descriptor = validate_descriptor(data)
"""

from __future__ import annotations

import fnmatch
import re
from typing import Any

from packaging.specifiers import (  # type: ignore[import-untyped]
    InvalidSpecifier,
    SpecifierSet,
)
from test_plan.enums import Lane, _parse_lane_str

from .descriptor import FrameworkDescriptor, RuntimeSpec

# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

_VALID_COVERAGE_STRATEGIES = frozenset({"lcov", "cobertura", "skip"})

_REQUIRED_FIELDS = (
    "name",
    "language",
    "lanes",
    "version_range",
    "runtime",
    "manifest_signals",
    "test_path_conventions",
    "coverage_strategy",
    "context_block",
)

# Optional fields get empty defaults if absent.
_OPTIONAL_FIELDS_DEFAULTS: dict[str, Any] = {
    "templates": [],
    "evaluator_hooks": [],
}


class FrameworkDescriptorError(ValueError):
    """Raised when a descriptor dict fails validation.

    Attributes:
        field: The YAML field (or ``"<root>"`` for top-level issues) that
            triggered the error.
        reason: Human-readable explanation of what went wrong.
    """

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"descriptor field {field!r}: {reason}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_str(data: dict, key: str) -> str:
    val = data.get(key)
    if val is None:
        raise FrameworkDescriptorError(key, "required field is missing")
    if not isinstance(val, str):
        raise FrameworkDescriptorError(key, f"expected str, got {type(val).__name__}")
    stripped = val.strip()
    if not stripped:
        raise FrameworkDescriptorError(key, "must not be empty or whitespace-only")
    return stripped


def _require_list_of_str(data: dict, key: str) -> list[str]:
    val = data.get(key)
    if val is None:
        raise FrameworkDescriptorError(key, "required field is missing")
    if not isinstance(val, list):
        raise FrameworkDescriptorError(key, f"expected list, got {type(val).__name__}")
    for i, item in enumerate(val):
        if not isinstance(item, str):
            raise FrameworkDescriptorError(
                key, f"item at index {i} is {type(item).__name__}, expected str"
            )
    return val


def _optional_list_of_str(data: dict, key: str) -> list[str]:
    val = data.get(key, _OPTIONAL_FIELDS_DEFAULTS.get(key, []))
    if not isinstance(val, list):
        raise FrameworkDescriptorError(key, f"expected list, got {type(val).__name__}")
    for i, item in enumerate(val):
        if not isinstance(item, str):
            raise FrameworkDescriptorError(
                key, f"item at index {i} is {type(item).__name__}, expected str"
            )
    return val


def _parse_version_range(version_range: str) -> SpecifierSet:
    """Parse a PEP 440 specifier string; raise FrameworkDescriptorError on failure."""
    try:
        return SpecifierSet(version_range)
    except InvalidSpecifier as exc:
        raise FrameworkDescriptorError(
            "version_range",
            f"invalid PEP 440 specifier: {version_range!r} — {exc}",
        ) from exc


def _compile_globs(patterns: list[str]) -> None:
    """Pre-compile each glob through fnmatch.translate to catch malformed patterns.

    Raises :class:`FrameworkDescriptorError` if any pattern is invalid.
    """
    for pattern in patterns:
        try:
            re.compile(fnmatch.translate(pattern))
        except re.error as exc:
            raise FrameworkDescriptorError(
                "test_path_conventions",
                f"glob pattern {pattern!r} is malformed: {exc}",
            ) from exc


def _parse_lanes(raw_lanes: list[str]) -> tuple[Lane, ...]:
    """Convert a list of lane strings to a tuple of Lane enum members.

    Raises :class:`FrameworkDescriptorError` for unknown lane names.
    Note: v0.1 alias strings emit a DeprecationWarning via _parse_lane_str.
    """
    parsed: list[Lane] = []
    for raw in raw_lanes:
        if not isinstance(raw, str):
            raise FrameworkDescriptorError(
                "lanes",
                f"expected list of strings, got {type(raw).__name__} item",
            )
        try:
            parsed.append(_parse_lane_str(raw))
        except ValueError:
            valid = ", ".join(f"{m.value!r}" for m in Lane)
            raise FrameworkDescriptorError(
                "lanes",
                f"unknown lane {raw!r}; valid lanes are: {valid}",
            )
    if not parsed:
        raise FrameworkDescriptorError("lanes", "must contain at least one lane")
    return tuple(parsed)


def _parse_runtime(raw: Any) -> RuntimeSpec:
    """Validate + build a RuntimeSpec from the ``runtime:`` dict.

    Raises :class:`FrameworkDescriptorError` for any missing or malformed field.
    """
    if not isinstance(raw, dict):
        raise FrameworkDescriptorError(
            "runtime", f"expected mapping, got {type(raw).__name__}"
        )
    image_val = raw.get("image")
    if not image_val:
        raise FrameworkDescriptorError(
            "runtime.image", "required field is missing or empty"
        )
    if not isinstance(image_val, str):
        raise FrameworkDescriptorError(
            "runtime.image", f"expected str, got {type(image_val).__name__}"
        )

    entry_val = raw.get("entrypoint")
    if entry_val is None:
        # If entrypoint is absent derive a sensible default; validators that
        # need strictness can require it explicitly.  For now we accept absence
        # with a minimal default so the 3 shipped descriptors don't all need
        # this key.
        entry_val = []
    if not isinstance(entry_val, list):
        raise FrameworkDescriptorError(
            "runtime.entrypoint", f"expected list, got {type(entry_val).__name__}"
        )
    for i, part in enumerate(entry_val):
        if not isinstance(part, str):
            raise FrameworkDescriptorError(
                "runtime.entrypoint",
                f"item at index {i} is {type(part).__name__}, expected str",
            )
    return RuntimeSpec(image=image_val, entrypoint=tuple(entry_val))


def _parse_coverage_strategy(raw: Any) -> str:
    """Validate the coverage_strategy field."""
    if not isinstance(raw, str):
        raise FrameworkDescriptorError(
            "coverage_strategy",
            f"expected str, got {type(raw).__name__}",
        )
    if raw not in _VALID_COVERAGE_STRATEGIES:
        raise FrameworkDescriptorError(
            "coverage_strategy",
            f"{raw!r} is not a valid coverage strategy; "
            f"must be one of {sorted(_VALID_COVERAGE_STRATEGIES)}",
        )
    return raw


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_descriptor(data: dict) -> FrameworkDescriptor:
    """Validate ``data`` (a ``yaml.safe_load`` result) and return a
    :class:`FrameworkDescriptor`.

    Raises:
        FrameworkDescriptorError: on the first validation failure encountered.
            The ``field`` attribute names the offending YAML key.
    """
    if not isinstance(data, dict):
        raise FrameworkDescriptorError(
            "<root>", f"descriptor must be a YAML mapping, got {type(data).__name__}"
        )

    # Check all required fields are present before doing deeper validation
    for field_name in _REQUIRED_FIELDS:
        if field_name not in data:
            raise FrameworkDescriptorError(field_name, "required field is missing")

    # Validate + convert each field
    name = _require_str(data, "name")
    language = _require_str(data, "language")

    raw_lanes = _require_list_of_str(data, "lanes")
    lanes = _parse_lanes(raw_lanes)

    version_range = _require_str(data, "version_range")
    _parse_version_range(version_range)  # raises on malformed specifier

    runtime = _parse_runtime(data.get("runtime"))

    manifest_signals = _require_list_of_str(data, "manifest_signals")

    test_path_conventions = _require_list_of_str(data, "test_path_conventions")
    _compile_globs(test_path_conventions)  # raises on malformed glob

    templates = _optional_list_of_str(data, "templates")
    evaluator_hooks = _optional_list_of_str(data, "evaluator_hooks")

    coverage_strategy = _parse_coverage_strategy(data["coverage_strategy"])  # type: ignore[arg-type]

    context_block_raw = data.get("context_block")
    if context_block_raw is None:
        raise FrameworkDescriptorError("context_block", "required field is missing")
    if not isinstance(context_block_raw, str):
        raise FrameworkDescriptorError(
            "context_block",
            f"expected str, got {type(context_block_raw).__name__}",
        )
    # context_block is allowed to be empty (though unusual), so we don't
    # require non-empty here — the descriptor's __post_init__ won't check it.

    return FrameworkDescriptor(
        name=name,
        language=language,
        lanes=lanes,
        version_range=version_range,
        runtime=runtime,
        manifest_signals=tuple(manifest_signals),
        test_path_conventions=tuple(test_path_conventions),
        templates=tuple(templates),
        coverage_strategy=coverage_strategy,  # type: ignore[arg-type]
        context_block=context_block_raw,
        evaluator_hooks=tuple(evaluator_hooks),
    )
