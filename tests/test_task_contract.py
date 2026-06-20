"""Tests for RFC-0002 Task Contract schema adoption + validation (epic #65)."""

from __future__ import annotations

import copy

import pytest
from plan.emit.task_contract import (
    ContractValidationError,
    assert_valid,
    is_valid,
    load_schema,
    validate_contract,
)


def _valid_contract() -> dict:
    return {
        "contract_version": "2",
        "feature": "Add widget service",
        "workflow_type": "feature",
        "phases": [
            {
                "phase": 1,
                "name": "Scaffold",
                "type": "setup",
                "subtasks": [
                    {"id": "C1", "description": "Create the module", "status": "pending"},
                ],
            }
        ],
        "execution": {"complexity": "standard", "provider": "claude", "review_tier": "async"},
        "tfactory": {"lanes": ["unit", "api"]},
    }


def test_schema_loads() -> None:
    schema = load_schema()
    assert schema["title"] == "Factory Task Contract v2"
    assert "phases" in schema["required"]


def test_valid_contract_passes() -> None:
    assert validate_contract(_valid_contract()) == []
    assert is_valid(_valid_contract())
    assert_valid(_valid_contract())  # does not raise


def test_missing_required_top_level_field() -> None:
    c = _valid_contract()
    del c["feature"]
    errors = validate_contract(c)
    assert errors
    assert any("feature" in e for e in errors)


def test_bad_contract_version_enum() -> None:
    c = _valid_contract()
    c["contract_version"] = "9"
    assert any("contract_version" in e for e in validate_contract(c))


def test_bad_workflow_type_enum() -> None:
    c = _valid_contract()
    c["workflow_type"] = "nonsense"
    assert any("workflow_type" in e for e in validate_contract(c))


def test_empty_phases_rejected() -> None:
    c = _valid_contract()
    c["phases"] = []
    assert validate_contract(c)


def test_phase_missing_subtasks() -> None:
    c = _valid_contract()
    del c["phases"][0]["subtasks"]
    assert any("subtasks" in e for e in validate_contract(c))


def test_subtask_missing_id() -> None:
    c = _valid_contract()
    del c["phases"][0]["subtasks"][0]["id"]
    assert any("id" in e for e in validate_contract(c))


def test_bad_subtask_status_enum() -> None:
    c = _valid_contract()
    c["phases"][0]["subtasks"][0]["status"] = "halfway"
    assert any("status" in e for e in validate_contract(c))


def test_approval_requires_signature() -> None:
    c = _valid_contract()
    c["approval"] = {"approved_by": "pfactory"}  # missing signature etc.
    errors = validate_contract(c)
    assert any("signature" in e for e in errors)


def test_execution_and_tfactory_enums() -> None:
    c = _valid_contract()
    c["execution"]["provider"] = "skynet"
    c["tfactory"]["lanes"] = ["unit", "telepathy"]
    errors = validate_contract(c)
    assert any("provider" in e for e in errors)
    assert any("lanes" in e for e in errors)


def test_assert_valid_raises() -> None:
    bad = copy.deepcopy(_valid_contract())
    del bad["phases"]
    with pytest.raises(ContractValidationError):
        assert_valid(bad)


def test_autonomy_tier_valid() -> None:
    # RFC-0011: an additive execution.autonomy_tier validates against the schema.
    for tier in ("low", "medium", "hard"):
        c = _valid_contract()
        c["execution"]["autonomy_tier"] = tier
        assert validate_contract(c) == []


def test_autonomy_tier_bad_value_rejected() -> None:
    c = _valid_contract()
    c["execution"]["autonomy_tier"] = "extreme"
    errors = validate_contract(c)
    assert any("autonomy_tier" in e for e in errors)


def test_autonomy_tier_in_schema_enum() -> None:
    schema = load_schema()
    enum = schema["properties"]["execution"]["properties"]["autonomy_tier"]["enum"]
    assert enum == ["low", "medium", "hard"]
