"""Factory Task Contract v2 — schema adoption + validation (epic #65, child 1).

RFC-0002 defines a canonical, signed cross-factory task contract: a superset of
AIFactory's ``implementation_plan.json`` that adds ``execution`` and ``tfactory``
profiles so AIFactory can skip its own planner. PFactory *produces* the contract;
this module is the gate that **every emitted contract is validated against the
vendored schema** before it crosses the boundary.

Validation is dependency-light by design (matching the rules engine ethos — it
must always run): if ``jsonschema`` is importable we use the full draft-2020-12
validator; otherwise we fall back to a focused structural validator covering the
contract's required fields, enums, and the phase/subtask shape. Either way
:func:`validate_contract` returns a list of human-readable error strings (empty
means valid) and :func:`assert_valid` raises on the first failure.

Schema: ``contracts/task-contract.schema.json`` (vendored from
github.com/olafkfreund/Factory · apis/task-contract.schema.json).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_SCHEMA_PATH = Path(__file__).parent / "contracts" / "task-contract.schema.json"

# Enum constraints mirrored from the schema for the structural fallback. Kept in
# sync with contracts/task-contract.schema.json (the schema is the source of
# truth when jsonschema is available).
_CONTRACT_VERSIONS = {"1", "2"}
_WORKFLOW_TYPES = {
    "feature",
    "refactor",
    "investigation",
    "migration",
    "simple",
    "development",
    "enhancement",
}
_PHASE_TYPES = {"setup", "implementation", "investigation", "integration", "cleanup"}
_SUBTASK_STATUS = {"pending", "in_progress", "completed", "blocked", "failed"}
_VERIFICATION_TYPES = {
    "command",
    "api",
    "browser",
    "component",
    "manual",
    "none",
    "e2e",
}
_COMPLEXITY = {"simple", "standard", "complex"}
_PROVIDERS = {
    "claude",
    "codex",
    "antigravity",
    "ollama",
    "copilot",
    "opencode",
    "openai-compatible",
    "gemini",
}
_REVIEW_TIERS = {"auto", "async", "blocking"}
_TFACTORY_LANES = {
    "unit",
    "api",
    "browser",
    "integration",
    "security",
    "mutation",
    "equivalence",
}
_CHANGE_MODES = {"greenfield", "modify", "migration"}  # RFC-0010
_APPROVAL_REQUIRED = (
    "approved_by",
    "approval_timestamp",
    "plan_contract_version",
    "signature",
)


class ContractValidationError(ValueError):
    """Raised by :func:`assert_valid` when a contract fails validation."""


@lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    """Load and cache the vendored RFC-0002 Task Contract schema."""
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def schema_version() -> str:
    """The contract schema's title (for provenance/debugging)."""
    return str(load_schema().get("title", "Task Contract"))


def validate_contract(contract: Any) -> list[str]:
    """Validate ``contract`` against the RFC-0002 schema.

    Returns a list of error strings (empty == valid). Uses ``jsonschema`` for a
    full draft-2020-12 check when available, else a focused structural fallback.
    """
    try:
        import jsonschema  # type: ignore
    except Exception:
        return _structural_validate(contract)

    validator_cls = jsonschema.validators.validator_for(load_schema())
    validator = validator_cls(load_schema())
    errors = sorted(validator.iter_errors(contract), key=lambda e: list(e.path))
    return [_format_jsonschema_error(e) for e in errors]


def assert_valid(contract: Any) -> None:
    """Raise :class:`ContractValidationError` if ``contract`` is invalid."""
    errors = validate_contract(contract)
    if errors:
        joined = "; ".join(errors)
        raise ContractValidationError(f"invalid Task Contract: {joined}")


def is_valid(contract: Any) -> bool:
    """Convenience boolean wrapper over :func:`validate_contract`."""
    return not validate_contract(contract)


# ── jsonschema error formatting ────────────────────────────────────────────


def _format_jsonschema_error(err: Any) -> str:
    loc = "/".join(str(p) for p in err.path) or "<root>"
    return f"{loc}: {err.message}"


# ── structural fallback (dependency-free) ──────────────────────────────────


def _structural_validate(contract: Any) -> list[str]:
    """A focused, dependency-free validation of the contract's key constraints.

    Not a complete JSON-Schema implementation — it enforces the required fields,
    enums, and phase/subtask shape that matter for a safe handoff. When
    ``jsonschema`` is installed the full validator supersedes this.
    """
    errors: list[str] = []

    def _enum(value: Any, allowed: set[str], loc: str) -> None:
        if value is not None and value not in allowed:
            errors.append(f"{loc}: {value!r} is not one of {sorted(allowed)}")

    if not isinstance(contract, dict):
        return ["<root>: contract must be an object"]

    # Required top-level fields (schema.required).
    for key in ("contract_version", "feature", "workflow_type", "phases"):
        if key not in contract:
            errors.append(f"<root>: '{key}' is a required property")

    _enum(contract.get("contract_version"), _CONTRACT_VERSIONS, "contract_version")
    _enum(contract.get("workflow_type"), _WORKFLOW_TYPES, "workflow_type")
    _enum(contract.get("change_mode"), _CHANGE_MODES, "change_mode")  # RFC-0010

    # phases: non-empty array of well-formed phase objects.
    phases = contract.get("phases")
    if "phases" in contract:
        if not isinstance(phases, list) or not phases:
            errors.append("phases: must be a non-empty array")
        else:
            for i, phase in enumerate(phases):
                errors.extend(_validate_phase(phase, f"phases/{i}", _enum))

    # approval (optional, but when present its sub-fields are required).
    approval = contract.get("approval")
    if approval is not None:
        if not isinstance(approval, dict):
            errors.append("approval: must be an object")
        else:
            for key in _APPROVAL_REQUIRED:
                if key not in approval:
                    errors.append(f"approval: '{key}' is a required property")

    # execution / tfactory enums (optional blocks).
    execution = contract.get("execution")
    if isinstance(execution, dict):
        _enum(execution.get("complexity"), _COMPLEXITY, "execution/complexity")
        _enum(execution.get("provider"), _PROVIDERS, "execution/provider")
        _enum(execution.get("review_tier"), _REVIEW_TIERS, "execution/review_tier")

    tfactory = contract.get("tfactory")
    if isinstance(tfactory, dict):
        lanes = tfactory.get("lanes")
        if isinstance(lanes, list):
            for lane in lanes:
                _enum(lane, _TFACTORY_LANES, "tfactory/lanes")

    return errors


def _validate_phase(phase: Any, loc: str, _enum: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(phase, dict):
        return [f"{loc}: phase must be an object"]
    for key in ("phase", "name", "subtasks"):
        if key not in phase:
            errors.append(f"{loc}: '{key}' is a required property")
    _enum(phase.get("type"), _PHASE_TYPES, f"{loc}/type")

    subtasks = phase.get("subtasks")
    if "subtasks" in phase:
        if not isinstance(subtasks, list) or not subtasks:
            errors.append(f"{loc}/subtasks: must be a non-empty array")
        else:
            for j, sub in enumerate(subtasks):
                errors.extend(_validate_subtask(sub, f"{loc}/subtasks/{j}", _enum))
    return errors


def _validate_subtask(sub: Any, loc: str, _enum: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(sub, dict):
        return [f"{loc}: subtask must be an object"]
    for key in ("id", "description"):
        if key not in sub:
            errors.append(f"{loc}: '{key}' is a required property")
    _enum(sub.get("status"), _SUBTASK_STATUS, f"{loc}/status")
    verification = sub.get("verification")
    if isinstance(verification, dict):
        _enum(verification.get("type"), _VERIFICATION_TYPES, f"{loc}/verification/type")
    return errors
