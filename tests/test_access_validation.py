"""RFC-0007 (#84): validate_access turns discovery into a readiness check."""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from plan.access_discovery import validate_access  # noqa: E402

_PRESENT = lambda _name: True  # noqa: E731
_ABSENT = lambda _name: False  # noqa: E731


def test_machine_native_with_present_env_is_ready():
    reqs = [
        {
            "resource": "api",
            "auth_class": "A-machine-native",
            "bootstrap": "none",
            "credential_ref": "env:T",
        }
    ]
    v = validate_access(reqs, env_present=_PRESENT)
    assert v == {"ready": True, "issues": []}


def test_missing_env_credential_flagged():
    reqs = [
        {
            "resource": "api",
            "auth_class": "A-machine-native",
            "bootstrap": "none",
            "credential_ref": "env:STAGING_TOKEN",
        }
    ]
    v = validate_access(reqs, env_present=_ABSENT)
    assert v["ready"] is False
    assert v["issues"][0]["kind"] == "missing_credential"
    assert "STAGING_TOKEN" in v["issues"][0]["detail"]


def test_class_D_is_un_automatable():
    reqs = [
        {
            "resource": "web",
            "auth_class": "D-un-automatable",
            "bootstrap": "human",
            "mvp_note": "push approval",
        }
    ]
    v = validate_access(reqs, env_present=_PRESENT)
    assert v["ready"] is False
    assert v["issues"][0]["kind"] == "un_automatable"
    assert v["issues"][0]["detail"] == "push approval"


def test_human_bootstrap_needed_when_not_curated():
    reqs = [
        {
            "resource": "web",
            "auth_class": "B-bootstrap-once",
            "bootstrap": "human",
            "credential_ref": "store:tc_1",
        }
    ]
    v = validate_access(reqs, env_present=_PRESENT)
    assert v["issues"][0]["kind"] == "needs_bootstrap"


def test_curated_requirement_raises_no_issue():
    reqs = [
        {
            "resource": "web",
            "auth_class": "D-un-automatable",
            "bootstrap": "human",
            "curated": True,
        }
    ]
    assert validate_access(reqs, env_present=_PRESENT) == {"ready": True, "issues": []}


def test_empty_or_none_is_ready():
    assert validate_access(None) == {"ready": True, "issues": []}
    assert validate_access([]) == {"ready": True, "issues": []}


def test_store_ref_not_probed_here():
    # store:/vault: existence is the curation gate's job, not flagged as missing.
    reqs = [
        {
            "resource": "api",
            "auth_class": "A-machine-native",
            "bootstrap": "none",
            "credential_ref": "store:tc_9",
        }
    ]
    assert validate_access(reqs, env_present=_ABSENT) == {"ready": True, "issues": []}
