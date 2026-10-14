"""The run signature written beside each plan page (#700).

The ask: a plans folder in the repo where every plan carries a machine-readable
record of the run that produced it, so a later run can tell whether the document
on disk is still current.

`content_hash` is the join key, so the property that matters is that an
unchanged plan re-renders to an IDENTICAL signature — otherwise every re-run
looks like a change and the file is useless for detecting staleness.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from plan.emit.docs.emit_docs import _app_version  # noqa: E402
from plan.emit.docs.render import _controls_engaged, render_plan_docs  # noqa: E402
from plan.emit.docs.targets.repo import RepoDocsTarget  # noqa: E402
from plan.service import PlanService  # noqa: E402

_PLAN = """# Refund API
Add a REST API endpoint to the payments microservice.
## Acceptance Criteria
- User can request a refund through the API
- The endpoint requires a valid JWT and rejects unauthenticated callers
"""

_CONTROL_IDS = (
    "audit-logging",
    "secrets-management",
    "supply-chain",
    "vuln-mgmt",
    "change-mgmt-sod",
    "runtime-isolation",
    "data-governance",
    "agentic-ai-governance",
)
_CATALOG = {"version": 1, "controls": [{"id": item} for item in _CONTROL_IDS]}


@pytest.fixture
def service():
    return PlanService(persist=False)


@pytest.fixture
def processed(service):
    session = service.ingest_text(_PLAN, title="Refund API")
    return service.process(session.session_id)


def _rows(tmp_path):
    """registry.json stores plans as a dict keyed by correlation_key."""
    registry = json.loads((tmp_path / "registry.json").read_text())
    return list(registry["plans"].values())


def test_app_version_resolves_to_the_real_release_version():
    """A relative path that stops resolving degrades to "unknown" SILENTLY.

    The signature would still be written, still look fine, and identify no
    build at all — so assert the value, not merely that a string came back.
    """
    version = _app_version()

    package_json = json.loads((Path(__file__).parent.parent / "package.json").read_text())
    assert version == package_json["version"], (
        f"_app_version() returned {version!r}; the path to apps/backend/__init__.py "
        "has probably moved"
    )


def test_signature_identifies_the_run_and_the_text(processed):
    sig = render_plan_docs(processed).run_signature

    assert sig["session_id"] == processed.session_id
    assert sig["plan_id"] == processed.plan.plan_id
    assert sig["content_hash"] == processed.plan.content_hash
    assert sig["generated_by"] == "pfactory"
    assert sig["signature_schema"] == 2
    # A version we could not determine must say so, never a plausible number.
    assert sig["pfactory_version"] == "unknown"  # default when not injected


def test_signature_carries_the_verdict(processed):
    sig = render_plan_docs(processed).run_signature

    assert sig["status"] == "processed"
    assert sig["gates_passed"] == processed.review.gates_passed
    assert sig["aggregate_score"] == processed.review.aggregate_score
    assert set(sig["lens_scores"]) == {lens.lens for lens in processed.review.lenses}
    assert sig["children"] == len(processed.epic.children)


def test_governance_fields_are_honest_when_absent(processed):
    sig = render_plan_docs(processed).run_signature

    assert {
        key: sig[key]
        for key in (
            "risk_class",
            "risk_class_basis",
            "autonomy_tier",
            "autonomy_verdict",
            "data_classification",
            "data_classification_basis",
            "approved_by",
            "approved_at",
            "controls",
            "controls_basis",
            "control_catalog_version",
            "change_id",
            "originator",
            "agent_identity",
            "model_route",
        )
    } == {
        "risk_class": "unknown",
        "risk_class_basis": "absent",
        "autonomy_tier": "unknown",
        "autonomy_verdict": None,
        "data_classification": "unknown",
        "data_classification_basis": "absent",
        "approved_by": None,
        "approved_at": None,
        "controls": None,
        "controls_basis": None,
        "control_catalog_version": None,
        "change_id": None,
        "originator": None,
        "agent_identity": None,
        "model_route": None,
    }
    assert "low" not in (sig["risk_class"], sig["autonomy_tier"])
    assert "" not in (sig["approved_by"], sig["approved_at"])


def test_governance_fields_copy_existing_evidence(processed):
    processed.epic.deployment = {"risk_class": "high"}
    processed.plan.autonomy_tier = "hard"
    processed.contract_result = {
        "contract": {
            "execution": {"routing": {"autonomy": {"verdict": "approval"}}},
            "compliance": {"data_classes": ["user-contact"]},
        }
    }
    processed.review.human_approval.approved = True
    processed.review.human_approval.valid = True
    processed.review.human_approval.plan_hash = processed.plan.compute_hash()
    processed.review.human_approval.approved_by = "reviewer"
    processed.review.human_approval.approved_at = "2026-10-14T12:00:00Z"

    sig = render_plan_docs(processed, control_catalog=_CATALOG).run_signature

    assert sig["risk_class"] == "high"
    assert sig["risk_class_basis"] == "deployment-block"
    assert sig["autonomy_tier"] == "hard"
    assert sig["autonomy_verdict"] == "approval"
    assert sig["data_classification"] == "Regulated"
    assert sig["data_classification_basis"] == "compliance-data-classes"
    assert sig["approved_by"] == "reviewer"
    assert sig["approved_at"] == "2026-10-14T12:00:00Z"
    assert sig["controls"] == ["audit-logging", "change-mgmt-sod", "data-governance"]
    assert sig["controls_basis"] == "gates-engaged"
    assert sig["control_catalog_version"] == 1


def test_unapproved_and_nonpersonal_values_do_not_claim_evidence(processed):
    processed.contract_result = {
        "contract": {
            "compliance": {"data_classes": ["store-distribution"]},
            "execution": {"routing": {"autonomy": {"verdict": None}}},
        }
    }
    processed.review.human_approval.approved_by = "reviewer"
    processed.review.human_approval.approved_at = "2026-10-14T12:00:00Z"
    sig = render_plan_docs(processed, control_catalog=_CATALOG).run_signature
    assert sig["data_classification"] == "unknown"
    assert sig["data_classification_basis"] == "absent"
    assert sig["autonomy_verdict"] is None
    assert sig["approved_by"] is None and sig["approved_at"] is None
    processed.review.human_approval.approved = True
    processed.review.human_approval.approved_by = ""
    processed.review.human_approval.approved_at = ""
    sig = render_plan_docs(processed, control_catalog=_CATALOG).run_signature
    assert sig["approved_by"] is None and sig["approved_at"] is None


def test_a_stale_approval_is_not_stamped(processed):
    """Approved, then the plan text changed. Nothing in the service resets
    `valid` on an edit, so the hash is the guard: nobody is named."""
    processed.review.human_approval.approved = True
    processed.review.human_approval.valid = True
    processed.review.human_approval.plan_hash = processed.plan.compute_hash()
    processed.review.human_approval.approved_by = "reviewer"
    processed.review.human_approval.approved_at = "2026-10-14T12:00:00Z"
    live = render_plan_docs(processed, control_catalog=_CATALOG).run_signature
    assert live["approved_by"] == "reviewer"

    processed.plan.title = processed.plan.title + " (edited after approval)"
    assert processed.review.human_approval.valid  # still True: the trap
    sig = render_plan_docs(processed, control_catalog=_CATALOG).run_signature
    assert sig["approved_by"] is None and sig["approved_at"] is None
    assert "change-mgmt-sod" not in sig["controls"]


def test_missing_epic_and_open_contract_are_safe(processed):
    processed.epic = None
    processed.contract_result = {"contract": {"compliance": {"data_classes": [{"bad": "value"}]}}}
    sig = render_plan_docs(processed, control_catalog=_CATALOG).run_signature
    assert sig["risk_class"] == "unknown"
    assert sig["data_classification"] == "unknown"
    assert sig["controls"] == ["audit-logging"]


@pytest.mark.parametrize(
    ("deployment", "expected"),
    [
        ({"required_scans": ["secret-scan"]}, ["secrets-management"]),
        ({"required_scans": ["dependency-audit"]}, ["supply-chain", "vuln-mgmt"]),
        ({"required_scans": ["sast"]}, ["vuln-mgmt"]),
        ({"required_scans": ["iac-scan"]}, ["vuln-mgmt"]),
        ({"system_gates": ["human-approval"]}, ["change-mgmt-sod"]),
        ({"production_classification": "production"}, ["change-mgmt-sod", "runtime-isolation"]),
    ],
)
def test_deployment_controls(processed, deployment, expected):
    processed.epic.deployment = deployment
    assert _controls_engaged(processed, _CATALOG) == sorted(["audit-logging", *expected])


def test_other_control_rules_and_unknown_catalog(processed):
    assert _controls_engaged(processed, _CATALOG) == ["audit-logging"]
    processed.review.human_approval.approved = True
    processed.review.human_approval.valid = True
    processed.review.human_approval.plan_hash = processed.plan.compute_hash()
    processed.contract_result = {"contract": {"compliance": {"data_classes": ["account"]}}}
    processed.injection_scan = {"verdict": "pass"}
    expected = sorted(
        ["audit-logging", "change-mgmt-sod", "data-governance", "agentic-ai-governance"]
    )
    assert _controls_engaged(processed, _CATALOG) == expected
    processed.injection_scan = {"verdict": "flagged"}
    assert _controls_engaged(processed, _CATALOG) == expected
    processed.injection_scan = {"verdict": "skipped"}
    assert _controls_engaged(processed, _CATALOG) == sorted(
        ["audit-logging", "change-mgmt-sod", "data-governance"]
    )
    assert _controls_engaged(processed, None) is None
    assert _controls_engaged(processed, {"version": 1, "controls": []}) is None


def test_render_is_independent_of_environment_and_working_directory(
    processed, tmp_path, monkeypatch
):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    (first_dir / "control-catalog.json").write_text(json.dumps(_CATALOG))
    monkeypatch.chdir(first_dir)
    first = render_plan_docs(processed, control_catalog=_CATALOG).run_signature
    monkeypatch.setattr(os, "environ", {})
    monkeypatch.chdir(second_dir)
    second = render_plan_docs(processed, control_catalog=_CATALOG).run_signature
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_an_unchanged_plan_re_renders_an_identical_signature(processed):
    """The staleness check depends on this: same text in, same signature out."""
    first = render_plan_docs(processed).run_signature
    second = render_plan_docs(processed).run_signature

    assert first == second, "a re-render must not look like a change"


def test_an_edit_and_re_run_changes_the_content_hash(service, processed):
    """...and a real edit must be visible, or the file cannot detect staleness.

    Through the actual revise loop (#692), not by poking the model: the stored
    `content_hash` is refreshed by `with_hash()` inside process(), so an edit is
    only reflected once the plan has been re-processed — which is exactly when a
    new page and signature would be written.
    """
    before = render_plan_docs(processed).run_signature["content_hash"]

    service.update_plan(processed.session_id, description="Lawful basis: contract.")
    reprocessed = service.process(processed.session_id)

    assert render_plan_docs(reprocessed).run_signature["content_hash"] != before


def test_target_writes_the_signature_beside_the_page(tmp_path, processed):
    bundle = render_plan_docs(processed)

    result = RepoDocsTarget(tmp_path, updated_at="2026-09-03T21:00:00Z").publish(bundle)

    assert result.status == "written"
    sig_file = tmp_path / f"{bundle.slug}.run.json"
    assert sig_file.exists(), "the run signature must land next to the page"
    assert (tmp_path / f"{bundle.slug}.md").exists()

    on_disk = json.loads(sig_file.read_text())
    assert on_disk == bundle.run_signature
    # No temp file left behind by the atomic write.
    assert not list(tmp_path.glob("*.tmp"))


def test_registry_row_points_at_the_signature_file(tmp_path, processed):
    bundle = render_plan_docs(processed)
    RepoDocsTarget(tmp_path, updated_at="2026-09-03T21:00:00Z").publish(bundle)

    row = next(r for r in _rows(tmp_path) if r["plan_id"] == bundle.plan_id)

    assert row["signature_file"] == f"{bundle.slug}.run.json"
    assert row["content_hash"] == processed.plan.content_hash
    assert row["status"] == "processed"


def test_publishing_twice_is_idempotent(tmp_path, processed):
    """A re-run must update in place, not accumulate duplicates."""
    bundle = render_plan_docs(processed)
    target = RepoDocsTarget(tmp_path, updated_at="2026-09-03T21:00:00Z")
    target.publish(bundle)
    target.publish(bundle)

    assert len([r for r in _rows(tmp_path) if r["plan_id"] == bundle.plan_id]) == 1


def test_the_git_write_target_commits_the_signature_too(processed):
    """A registry row naming a file the target never wrote is a dangling pointer.

    `registry_entry` carries `signature_file` for every target, and Backstage's
    git-write path is the "plans in the repo" lane — so if it committed the
    registry without the signature, the dangling pointer would be the NORMAL
    case. Found in review on PR #700.
    """
    from plan.emit.docs.targets.backstage import BackstageTarget

    written: dict[str, str] = {}

    class _Writer:
        def get_file(self, path: str) -> str | None:
            return written.get(path)

        def put_file(self, path: str, content: str, message: str) -> dict:
            written[path] = content
            return {"path": path}

    bundle = render_plan_docs(processed, pfactory_version="0.6.17")
    target = BackstageTarget(
        base_url="http://backstage.example",
        repo="olafkfreund/PFactory",
        docs_subdir="techdocs/plans",
        git_write=True,
        writer=_Writer(),
    )

    result = target.publish(bundle)

    assert result.status != "error", result.detail
    sig_path = f"techdocs/plans/{bundle.slug}.run.json"
    assert sig_path in written, f"signature not committed; wrote {sorted(written)}"
    assert json.loads(written[sig_path]) == bundle.run_signature

    # And the committed registry's pointer resolves to something committed.
    registry = json.loads(written["techdocs/plans/registry.json"])
    row = next(r for r in registry["plans"].values() if r["plan_id"] == bundle.plan_id)
    assert f"techdocs/plans/{row['signature_file']}" in written
