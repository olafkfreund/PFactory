"""RFC-0005 (Phase 0): attach_environment derives the env manifest from tfactory."""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from plan.emit.environment_block import (  # noqa: E402
    _EXACT_VERSION,
    _NIX_CACHED_PYTHON,
    attach_environment,
    contract_toolchain,
    derive_environment,
)

_SCHEMA = _BACKEND / "plan" / "emit" / "contracts" / "task-contract.schema.json"


def _validate(contract: dict) -> list[str]:
    from jsonschema import Draft202012Validator  # noqa: PLC0415

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
    env = derive_environment({"tfactory": {"lanes": ["unit"], "frameworks": {"unit": "pytest"}}})
    assert env["language"] == "python"
    assert env["verify_commands"] == ["pytest -q"]
    assert env["system_packages"] == []
    assert env["network"] == "none"  # hermetic
    assert env["provisioning"] == {"method": "nix", "ref": "flake.nix", "generated": True}
    assert env["proof"]["verify"] == ["python --version"]


def test_browser_lane_adds_chromium_and_playwright():
    env = derive_environment(
        {
            "tfactory": {
                "lanes": ["unit", "api", "browser"],
                "frameworks": {"unit": "pytest", "api": "pytest", "browser": "playwright"},
            }
        }
    )
    assert env["system_packages"] == ["chromium"]
    assert "playwright test" in env["verify_commands"]
    assert env["network"] == "restricted"  # must reach the running app
    assert "playwright --version" in env["proof"]["verify"]


def test_node_unit():
    env = derive_environment({"tfactory": {"lanes": ["unit"], "frameworks": {"unit": "jest"}}})
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


_PYTHON_CASES = [
    (">=3.11", "3.14", None),
    (">= 3.11, < 3.13", "3.12", None),
    (">=3.11,!=3.14.*", "3.13", None),
    ("~=3.12", "3.14", None),
    ("==3.12.*", "3.12", None),
    ("==3.12.0", None, "unsatisfiable"),
    (">=3.12,!=3.14.1", "3.13", None),
    (">=3.10,<3.12", "3.10", None),
    (">=3.10,!=3.10.*,<3.12", "3.11", None),
    ("<3.12", "3.8", None),
    (">3.13,<3.14", None, "unsatisfiable"),
    *(
        (value, None, "unparseable")
        for value in ("stable", "system", ">=3.11rc1", ">=", ">=3.11,", "٣.١٢", "9" * 5000)
    ),
    ("3.12", "3.12", None),
    (" 3.12\n", "3.12", None),
    (3, None, "non_string"),
]


def test_python_toolchain_cases():
    for raw, expected, reason in _PYTHON_CASES:
        with patch("plan.emit.environment_block.logger.warning") as warning:
            assert contract_toolchain({"python": raw}) == (
                {"python": expected} if expected else {}
            ), repr(raw)[:80]
            if reason:
                warning.assert_called_once_with("Omitting toolchain %r: %s", "python", reason)
            else:
                warning.assert_not_called()


def test_other_toolchains_and_logging():
    versions = {
        "go": "1.25",
        "node": " 22 ",
        "rust": "stable",
        "java": "21",
    }
    assert contract_toolchain(versions) == {
        "go": "1.25",
        "node": "22",
        "rust": "stable",
        "java": "21",
    }
    with patch("plan.emit.environment_block.logger.warning") as warning:
        assert (
            contract_toolchain(
                {
                    "go": "1.20",
                    "python": "secret-value",
                    "node": 3,
                }
            )
            == {}
        )
        assert [call.args[2] for call in warning.call_args_list] == [
            "go_too_old",
            "unparseable",
            "non_string",
        ]
        assert [call.args[1] for call in warning.call_args_list] == [
            "go",
            "python",
            "node",
        ]
        assert "secret-value" not in str(warning.call_args_list)
    with patch("plan.emit.environment_block.logger.warning") as warning:
        assert contract_toolchain({"go": "1.25rc1"}) == {}
        warning.assert_called_once_with("Omitting toolchain %r: %s", "go", "unparseable")


def test_cached_python_versions():
    assert _NIX_CACHED_PYTHON == ("3.14", "3.13", "3.12")
    assert all(_EXACT_VERSION.fullmatch(version) for version in _NIX_CACHED_PYTHON)


def test_toolchain_in_both_emission_paths(_pytest_only=None):
    for tfactory in (
        {"lanes": ["unit"], "language": "python"},
        {"lanes": ["unit"], "frameworks": {"unit": "pytest"}},
    ):
        versions = {"python": ">=3.11", "go": "1.25"}
        contract = {"tfactory": tfactory, "baseline": {"versions": versions}}
        assert derive_environment(contract)["toolchain"] == {
            "python": "3.14",
            "go": "1.25",
        }
        assert contract["baseline"]["versions"] == versions

        dropped = {"python": "stable", "go": "1.20"}
        contract = {"tfactory": tfactory, "baseline": {"versions": dropped}}
        assert "toolchain" not in derive_environment(contract)
        assert contract["baseline"]["versions"] == dropped


def test_resolved_toolchain_is_schema_valid(_pytest_only=None):
    contract = {
        "contract_version": "2",
        "feature": "demo",
        "workflow_type": "feature",
        "phases": [{"phase": 1, "name": "p", "subtasks": [{"id": "t1", "description": "d"}]}],
        "tfactory": {"lanes": ["unit"], "language": "python"},
        "baseline": {"versions": {"python": ">=3.11"}},
    }
    attach_environment(contract)
    assert contract["environment"]["toolchain"] == {"python": "3.14"}
    assert _validate(contract) == [], _validate(contract)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn) and not inspect.signature(fn).parameters:
            fn()
            print(f"ok: {name}")  # noqa: T201
    print("environment_block tests: passed")  # noqa: T201
