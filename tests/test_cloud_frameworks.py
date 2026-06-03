"""Tests for the cloud framework descriptors + check library (#133 E / #138).

Guards that ``frameworks/cloud-discover`` + ``frameworks/cloud-prowler`` load
into the registry with the expected shape, and that the high-signal check
catalogue under ``cloud-prowler/library/`` is well-formed.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from framework_registry import load_registry
from test_plan.enums import Lane

_LIBRARY = (
    Path(__file__).resolve().parents[1] / "frameworks" / "cloud-prowler" / "library"
)


def test_cloud_descriptors_load_into_registry() -> None:
    reg = load_registry()
    assert "cloud-discover" in reg and "cloud-prowler" in reg


def test_cloud_descriptors_are_integration_lane_skip_coverage() -> None:
    reg = load_registry()
    for name in ("cloud-discover", "cloud-prowler"):
        d = reg[name]
        assert d.lanes == (Lane.INTEGRATION,)
        assert d.coverage_strategy == "skip"  # cloud emits no per-test coverage
        assert d.runtime.image == "pfactory-runner-cloud:latest"
        assert d.manifest_signals == (".pfactory.yml:targets",)


def test_cloud_prowler_references_ocsf_verdict_mapping() -> None:
    # #138: the Evaluator OCSF→verdict mapping is referenced from the descriptor.
    d = load_registry()["cloud-prowler"]
    assert "agents.cloud.assessment.assess" in d.evaluator_hooks


def test_check_library_catalogue_is_well_formed() -> None:
    files = sorted(_LIBRARY.glob("*.yaml"))
    ids = {yaml.safe_load(f.read_text())["id"] for f in files}
    # the canonical high-signal checks #138 calls out
    assert {"s3-public", "iam-overprivileged", "nsg-open-ports"} <= ids
    for f in files:
        c = yaml.safe_load(f.read_text())
        assert c["severity"] in {"critical", "high", "medium", "low"}
        assert set(c["providers"]) <= {"aws", "gcp", "azure"} and c["providers"]
        assert c["prowler_checks"]  # at least one provider's prowler check ids
        # the catalogue carries the what/why/how a remediation report needs
        assert c["whats_wrong"] and c["why_it_matters"] and c["how_to_fix"]
