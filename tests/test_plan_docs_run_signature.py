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
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from plan.emit.docs.emit_docs import _app_version  # noqa: E402
from plan.emit.docs.render import render_plan_docs  # noqa: E402
from plan.emit.docs.targets.repo import RepoDocsTarget  # noqa: E402
from plan.service import PlanService  # noqa: E402

_PLAN = """# Refund API
Add a REST API endpoint to the payments microservice.
## Acceptance Criteria
- User can request a refund through the API
- The endpoint requires a valid JWT and rejects unauthenticated callers
"""


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
    assert sig["signature_schema"] == 1
    # A version we could not determine must say so, never a plausible number.
    assert sig["pfactory_version"] == "unknown"  # default when not injected


def test_signature_carries_the_verdict(processed):
    sig = render_plan_docs(processed).run_signature

    assert sig["status"] == "processed"
    assert sig["gates_passed"] == processed.review.gates_passed
    assert sig["aggregate_score"] == processed.review.aggregate_score
    assert set(sig["lens_scores"]) == {lens.lens for lens in processed.review.lenses}
    assert sig["children"] == len(processed.epic.children)


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
