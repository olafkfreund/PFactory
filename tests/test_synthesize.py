"""Tests for the synthesize stage — Testing Strategy + CI/CD (issues #13/#14)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from plan.decompose.models import ChildIssue, EpicPlan  # noqa: E402
from plan.models import Criterion, NormalizedPlan  # noqa: E402
from plan.recon.delta import compute_footprints  # noqa: E402
from plan.recon.models import RepoMap  # noqa: E402
from plan.synthesize.cicd_generator import generate_cicd  # noqa: E402
from plan.synthesize.run import synthesize  # noqa: E402
from plan.synthesize.testing_strategy import (  # noqa: E402
    generate_testing_strategy,
)


def _plan(title="Add API endpoint", desc="Add a REST API endpoint to the service.",
          kind="software", criteria=None):
    return NormalizedPlan(
        plan_id="001-add-api-endpoint",
        title=title,
        description=desc,
        source_format="markdown",
        target_kind=kind,
        criteria=[
            Criterion(id=f"AC#{i}", text=t)
            for i, t in enumerate(criteria or [], 1)
        ],
    )


def _software_plan():
    return _plan(criteria=[
        "The endpoint returns 200 for a valid request.",
        "The user can submit the form and see a confirmation.",
    ])


def _generic_plan():
    return _plan(
        title="Hiring campaign",
        desc="Plan a hiring campaign for the marketing team.",
        kind="non-software",
        criteria=["A shortlist of candidates is produced."],
    )


def test_software_plan_generates_both_artifacts():
    plan = _software_plan()

    cicd = generate_cicd(plan)
    testing = generate_testing_strategy(plan)

    assert cicd is not None
    assert testing is not None

    # Documents are non-empty markdown.
    assert cicd.document.strip()
    assert testing.document.strip()
    assert cicd.document.lstrip().startswith("#")
    assert testing.document.lstrip().startswith("#")

    # Kinds and filenames are correct.
    assert cicd.kind == "cicd"
    assert cicd.child.kind == "cicd"
    assert cicd.filename == "docs/plans/001-add-api-endpoint-cicd-pipeline.md"

    assert testing.kind == "testing"
    assert testing.child.kind == "testing"
    assert testing.filename == "docs/plans/001-add-api-endpoint-testing-strategy.md"

    # Child keys + key labels.
    assert cicd.child.key == "CICD"
    assert "area:cicd" in cicd.child.labels
    assert testing.child.key == "TEST"
    assert "handover:tfactory" in testing.child.labels


def test_testing_doc_maps_each_acceptance_criterion():
    plan = _software_plan()
    testing = generate_testing_strategy(plan)
    assert testing is not None

    # Every AC id appears in the AC → approach mapping table.
    for c in plan.criteria:
        assert c.id in testing.document
    assert "Test approach" in testing.document


def test_cicd_includes_container_and_terraform_stages_on_signals():
    plan = _plan(
        desc="Provision a Kubernetes cluster with Terraform and deploy via Helm.",
        criteria=["The service is reachable in the cluster."],
    )
    cicd = generate_cicd(plan)
    assert cicd is not None
    doc = cicd.document.lower()
    assert "containerise" in doc
    assert "cluster" in doc
    assert "terraform" in doc


def test_non_software_plan_synthesizes_nothing():
    plan = _generic_plan()
    assert generate_cicd(plan) is None
    assert generate_testing_strategy(plan) is None


def test_synthesize_appends_two_children_with_sane_graph():
    plan = _software_plan()
    epic = EpicPlan(
        plan_id=plan.plan_id,
        epic_title=plan.title,
        children=[ChildIssue(key="C1", title="Implement endpoint")],
    )

    artifacts = synthesize(plan, epic)

    assert len(artifacts) == 2
    assert len(epic.children) == 3  # C1 + TEST + CICD
    keys = {c.key for c in epic.children}
    assert {"C1", "TEST", "CICD"} <= keys
    assert epic.validate_dependencies() == []


def test_synthesize_is_idempotent_on_keys():
    plan = _software_plan()
    epic = EpicPlan(plan_id=plan.plan_id, epic_title=plan.title)

    synthesize(plan, epic)
    synthesize(plan, epic)

    # Running twice does not duplicate the synthesized children.
    assert len(epic.children) == 2
    assert epic.validate_dependencies() == []


# ── AIFactory#1113: the CI/CD child must target the pipeline, not a doc ──────


def _cicd_footprint(plan, repo_map):
    """The contract footprint the delta pass derives for the CI/CD child.

    Goes through the real machinery (synthesize -> compute_footprints) because
    that chain IS the defect: the child's text is the only source of a file
    target, so whatever the body names is what the coder is told to touch.
    """
    plan.repo_map = repo_map
    epic = EpicPlan(
        plan_id=plan.plan_id,
        epic_title=plan.title,
        children=[ChildIssue(key="C1", title="Implement endpoint")],
    )
    synthesize(plan, epic)
    return compute_footprints(plan, epic).get("CICD", {})


def test_cicd_child_names_the_pipeline_file_not_a_design_doc():
    # Greenfield: no RepoMap at all, so the default pipeline path is used.
    cicd = generate_cicd(_software_plan())
    assert cicd is not None

    assert ".github/workflows/ci.yml" in cicd.child.body
    # The dangling docs/plans/... reference is the path the coder used to create.
    assert "docs/plans/" not in cicd.child.body


def test_cicd_footprint_modifies_the_discovered_pipeline():
    repo_map = RepoMap(
        available=True,
        ci_system="github-actions",
        ci_pipeline_paths=[".github/workflows/ci.yml"],
        layout={"files": ["pyproject.toml"], "dirs": ["src"]},
    )
    fp = _cicd_footprint(_software_plan(), repo_map)

    assert ".github/workflows/ci.yml" in fp["files_to_modify"]
    # Regression: the only file it used to create was the design document.
    assert not [f for f in fp["files_to_create"] if f.endswith(".md")]


def test_cicd_footprint_creates_the_default_pipeline_when_repo_has_none():
    repo_map = RepoMap(available=True, ci_system="gitlab-ci", layout={"files": ["go.mod"]})
    fp = _cicd_footprint(_software_plan(), repo_map)

    assert ".gitlab-ci.yml" in fp["files_to_create"] + fp["files_to_modify"]


# ── PFactory#461: the testing child must target test files, not a doc ────────


def _testing(plan, repo_map):
    """The testing child and the footprint the delta pass derives for it.

    Same machinery as ``_cicd_footprint`` and for the same reason: the child's
    text is the only source of a file target, so the footprint — not the prose —
    is what the coder is handed.
    """
    plan.repo_map = repo_map
    epic = EpicPlan(
        plan_id=plan.plan_id,
        epic_title=plan.title,
        children=[ChildIssue(key="C1", title="Implement endpoint")],
    )
    synthesize(plan, epic)
    child = next(c for c in epic.children if c.key == "TEST")
    return child, compute_footprints(plan, epic).get("TEST", {})


def test_testing_child_names_test_files_not_a_design_doc():
    # Greenfield: no RepoMap, so the language comes from the spec text.
    plan = _plan(desc="Add a REST API endpoint to the Python service, tested with pytest.")
    testing = generate_testing_strategy(plan)
    assert testing is not None

    # A directory alone would mine nothing: _FILE_TOKEN needs an extension.
    assert "tests/test_add_api_endpoint_unit.py" in testing.child.body
    # The dangling docs/plans/... reference is the path the coder used to create.
    assert "docs/plans/" not in testing.child.body


def test_testing_footprint_creates_real_test_files():
    repo_map = RepoMap(
        available=True,
        languages=["python"],
        layout={"files": ["pyproject.toml"], "dirs": ["src"]},
    )
    _child, fp = _testing(_software_plan(), repo_map)

    assert fp["files_to_create"] == [
        "tests/test_add_api_endpoint_e2e.py",
        "tests/test_add_api_endpoint_integration.py",
        "tests/test_add_api_endpoint_unit.py",
    ]
    # Regression: the only file it used to create was the design document.
    assert not [f for f in fp["files_to_create"] if f.endswith(".md")]


def test_testing_child_uses_the_repos_own_test_dir_and_command():
    # An already-tested repo: new tests land in the tree it already has (test/,
    # not tests/) and run under the command reconnaissance already found.
    repo_map = RepoMap(
        available=True,
        languages=["typescript"],
        layout={"files": ["package.json"], "dirs": ["src", "test"]},
        existing_test_command="npm run test",
    )
    child, fp = _testing(_software_plan(), repo_map)

    assert fp["files_to_create"] == [
        "test/add_api_endpoint_e2e.test.ts",
        "test/add_api_endpoint_integration.test.ts",
        "test/add_api_endpoint_unit.test.ts",
    ]
    assert "`npm run test`" in child.body


def test_testing_child_names_no_files_for_an_unmapped_language():
    # A language with no entry in the test-layout table gets no path at all,
    # rather than a plausible Python one (#585). Haskell, because C# — the
    # original example — is mapped now that the miner can see `.cs` (#475).
    repo_map = RepoMap(available=True, languages=["haskell"], layout={"dirs": ["src"]})
    child, fp = _testing(_software_plan(), repo_map)

    assert fp == {}
    assert "docs/plans/" not in child.body


# ── PFactory#462: the CI/CD child must be scoped to the delta ────────────────


_WIRED_WORKFLOW = """\
name: CI
on: [push, pull_request]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: ruff check .
      - run: pytest -q
      - run: docker build -t app .
      - run: ./deploy.sh
"""


def _repo_with_pipeline(tmp_path, body=_WIRED_WORKFLOW):
    """A RepoMap built the way reconnaissance builds one, from a real file."""
    from plan.recon.ci_probe import pipeline_stages, probe_ci

    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(body)
    ci = probe_ci(tmp_path)
    return RepoMap(
        available=True,
        languages=["python"],
        ci_system=ci["system"],
        ci_pipeline_paths=ci["paths"],
        ci_stages=pipeline_stages(tmp_path, ci["paths"]),
        layout={"files": ["pyproject.toml"], "dirs": ["src"]},
    )


def test_pipeline_stages_reads_tools_not_stage_labels(tmp_path):
    """`ruff check .` IS a lint stage even though the file never says "lint".

    Matching on stage labels would report a gap in every pipeline that names its
    jobs after tools, which is most of them.
    """
    repo_map = _repo_with_pipeline(tmp_path)
    assert set(repo_map.ci_stages) >= {"lint", "test", "build", "deploy"}
    assert "security scan" not in repo_map.ci_stages


def test_cicd_child_asks_only_for_the_stages_the_pipeline_lacks(tmp_path):
    """The defect: a repo with a working ci.yml got the whole pipeline restated.

    aifactory-demo has had lint+test+build wired throughout, and every feature
    still bought a full CI/CD child. Now it asks for the security scans and
    nothing else.
    """
    plan = _software_plan()
    plan.repo_map = _repo_with_pipeline(tmp_path)
    cicd = generate_cicd(plan)
    assert cicd is not None

    criteria = " ".join(cicd.child.acceptance_criteria).lower()
    assert "security scan" in criteria
    # The stages the repo already runs must not be asked for again.
    assert "coverage report" not in criteria       # tied to the test stage
    assert "manual approval" not in criteria       # tied to the deploy stage
    assert len(cicd.child.acceptance_criteria) == 1


def test_cicd_child_body_names_what_is_already_wired(tmp_path):
    """The coder needs to be told to EXTEND the pipeline, not rewrite it."""
    plan = _software_plan()
    plan.repo_map = _repo_with_pipeline(tmp_path)
    cicd = generate_cicd(plan)
    assert cicd is not None

    assert "do NOT re-specify" in cicd.child.body
    for wired in ("lint", "test", "build"):
        assert wired in cicd.child.body
    assert "already wired" in cicd.document


def test_no_cicd_child_when_the_pipeline_already_runs_everything(tmp_path):
    """Nothing missing means no child at all — not a child with no asks."""
    complete = _WIRED_WORKFLOW + "      - run: trivy fs .\n"
    plan = _software_plan()
    plan.repo_map = _repo_with_pipeline(tmp_path, complete)

    assert generate_cicd(plan) is None


def test_cicd_child_is_unchanged_when_recon_found_no_pipeline():
    """Greenfield, and every older RepoMap, must behave exactly as before.

    The scoping is driven by positive evidence only: no evidence means no
    narrowing, so a plan that genuinely needs a whole pipeline still gets one.
    This is the direction that would fail silently — an over-eager filter drops
    real work and nothing reports it.
    """
    cicd = generate_cicd(_software_plan())
    assert cicd is not None

    criteria = " ".join(cicd.child.acceptance_criteria).lower()
    for stage in ("lint", "test", "build", "security scan"):
        assert stage in criteria
    assert "coverage report" in criteria
    assert "manual approval" in criteria
    assert "already wired" not in cicd.document
# ── PFactory#475: the five languages the miner could not see ─────────────────


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("csharp", [
            "tests/AddApiEndpointE2ETests.cs",
            "tests/AddApiEndpointIntegrationTests.cs",
            "tests/AddApiEndpointUnitTests.cs",
        ]),
        ("kotlin", [
            "src/test/kotlin/AddApiEndpointE2ETest.kt",
            "src/test/kotlin/AddApiEndpointIntegrationTest.kt",
            "src/test/kotlin/AddApiEndpointUnitTest.kt",
        ]),
        ("php", [
            "tests/AddApiEndpointE2ETest.php",
            "tests/AddApiEndpointIntegrationTest.php",
            "tests/AddApiEndpointUnitTest.php",
        ]),
        ("swift", [
            "Tests/AddApiEndpointE2ETests.swift",
            "Tests/AddApiEndpointIntegrationTests.swift",
            "Tests/AddApiEndpointUnitTests.swift",
        ]),
        ("cpp", [
            "tests/add_api_endpoint_e2e_test.cpp",
            "tests/add_api_endpoint_integration_test.cpp",
            "tests/add_api_endpoint_unit_test.cpp",
        ]),
    ],
)
def test_testing_footprint_reaches_the_five_added_languages(language, expected):
    """A repo in these languages used to get an EMPTY footprint (#475).

    Two lists had to agree for a path to reach the coder: the test-layout table
    has to name a file, and `delta._CODE_EXTS` has to recognise its extension.
    These five were in neither, so the child arrived at AIFactory with no file
    target at all — this asserts the whole chain, not just the extension list.
    """
    repo_map = RepoMap(available=True, languages=[language], layout={"dirs": ["src"]})
    _child, fp = _testing(_software_plan(), repo_map)

    assert fp.get("files_to_create") == expected


def test_code_exts_covers_every_detectable_language():
    """The extension list and the language signal table must not drift apart.

    #475 was exactly this drift: `_LANGUAGE_SIGNALS` could name twelve
    languages and `_CODE_EXTS` could mine seven, so PFactory detected a repo's
    language and then planned as if its files did not exist. A language added to
    the signal table without an extension here is silently unplannable.
    """
    from plan.recon.delta import _CODE_EXTS
    from plan.recon.language_reconcile import _LANGUAGE_SIGNALS

    exts_for = {
        "rust": ".rs", "go": ".go", "typescript": ".ts", "javascript": ".js",
        "python": ".py", "java": ".java", "csharp": ".cs", "ruby": ".rb",
        "php": ".php", "kotlin": ".kt", "swift": ".swift", "cpp": ".cpp",
    }
    for language, _needles in _LANGUAGE_SIGNALS:
        ext = exts_for.get(language)
        assert ext is not None, f"{language} detectable but this test has no extension for it"
        assert ext in _CODE_EXTS, (
            f"{language} is detectable by _LANGUAGE_SIGNALS but {ext} is not in "
            f"_CODE_EXTS, so every file token in that repo's children is discarded"
        )
