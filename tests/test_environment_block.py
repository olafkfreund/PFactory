"""RFC-0005 (Phase 0): attach_environment derives the env manifest from tfactory."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from plan.emit.environment_block import (  # noqa: E402
    attach_environment,
    derive_environment,
)

_SCHEMA = (
    _BACKEND / "plan" / "emit" / "contracts" / "task-contract.schema.json"
)


def _validate(contract: dict) -> list[str]:
    from jsonschema import Draft202012Validator

    schema = json.loads(_SCHEMA.read_text())
    return [e.message for e in Draft202012Validator(schema).iter_errors(contract)]


def test_no_tfactory_is_noop():
    c = {"feature": "x"}
    assert attach_environment(c) is c
    assert "environment" not in c


def test_disabled_is_noop():
    c = {"tfactory": {"lanes": ["unit"], "frameworks": {"unit": "pytest"}}}
    attach_environment(c, enabled=False)
    assert "environment" not in c


def test_python_unit_only():
    env = derive_environment(
        {"tfactory": {"lanes": ["unit"], "frameworks": {"unit": "pytest"}}}
    )
    assert env["language"] == "python"
    assert env["verify_commands"] == ["pytest -q"]
    assert env["system_packages"] == []
    assert env["network"] == "none"  # hermetic
    assert env["provisioning"] == {"method": "nix", "ref": "flake.nix", "generated": True}
    assert env["proof"]["verify"] == ["python --version"]


def test_browser_lane_adds_chromium_and_playwright():
    env = derive_environment(
        {"tfactory": {
            "lanes": ["unit", "api", "browser"],
            "frameworks": {"unit": "pytest", "api": "pytest", "browser": "playwright"},
        }}
    )
    assert env["system_packages"] == ["chromium"]
    assert "playwright test" in env["verify_commands"]
    assert env["network"] == "restricted"  # must reach the running app
    assert "playwright --version" in env["proof"]["verify"]


def test_node_unit():
    env = derive_environment(
        {"tfactory": {"lanes": ["unit"], "frameworks": {"unit": "jest"}}}
    )
    assert env["language"] == "typescript"
    assert env["verify_commands"] == ["npm test"]
    assert env["proof"]["verify"] == ["node --version"]


def test_attached_block_is_schema_valid():
    contract = {
        "contract_version": "2",
        "feature": "demo",
        "workflow_type": "feature",
        "phases": [{"phase": 1, "name": "p", "subtasks": [{"id": "t1", "description": "d"}]}],
        "tfactory": {
            "lanes": ["unit", "browser"],
            "frameworks": {"unit": "pytest", "browser": "playwright"},
        },
    }
    attach_environment(contract)
    assert "environment" in contract
    assert _validate(contract) == [], _validate(contract)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok: {name}")
    print("environment_block tests: passed")
