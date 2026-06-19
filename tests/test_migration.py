"""Tests for RFC-0010 Phase 5: language-migration planning (PFactory)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.decompose.migration_planner import (  # noqa: E402
    build_equivalence_block,
    build_golden_corpus_manifest,
    build_module_map,
)
from plan.detect.migration_classifier import classify_migration  # noqa: E402
from plan.detect.source_inspector import build_behavioral_contract  # noqa: E402
from plan.emit import task_contract as tc  # noqa: E402
from plan.emit.contract_emit import assemble_contract  # noqa: E402
from plan.emit.migration_block import attach_migration  # noqa: E402
from plan.models import Criterion, NormalizedPlan  # noqa: E402
from plan.recon import RepoMap  # noqa: E402


def _plan(desc="", crits=(), **kw):
    return NormalizedPlan(
        plan_id="001-x",
        title="Port payments",
        description=desc,
        source_format="markdown",
        criteria=[Criterion(id=f"AC#{i}", text=t) for i, t in enumerate(crits, 1)],
        **kw,
    )


_PY_REPO = RepoMap(available=True, repo="o/pay", commit="abc", languages=["python"])


# ── directional migration classifier ────────────────────────────────────


def test_classify_directional_from_to():
    sig = classify_migration(
        _plan(desc="rewrite the service from python to rust"), _PY_REPO
    )
    assert sig.is_migration
    assert sig.source_language == "python" and sig.target_language == "rust"


def test_classify_rewrite_in_target_uses_repo_as_source():
    sig = classify_migration(
        _plan(desc="reimplement this in rust with cargo"), _PY_REPO
    )
    assert (
        sig.is_migration
        and sig.source_language == "python"
        and sig.target_language == "rust"
    )


def test_non_directional_is_not_migration():
    sig = classify_migration(
        _plan(desc="add a rust-style retry to the python service"), _PY_REPO
    )
    assert sig.is_migration is False


def test_classifier_robust_to_distractors_and_punctuation():
    # Realistic ingested text: a distractor "Rust" mention in the criteria, an
    # "AC#1" id (must not match the C# signal), and a trailing period on the
    # target — the directional clause must still resolve python -> rust.
    plan = _plan(
        desc="Rewrite the payments module from Python to Rust.",
        crits=("The Rust refund behaves identically",),
    )
    plan = plan.model_copy(update={"raw_text": "rewrite from Python to Rust."})
    sig = classify_migration(plan, _PY_REPO)
    assert sig.is_migration
    assert sig.source_language == "python" and sig.target_language == "rust"


def test_same_language_is_not_migration():
    sig = classify_migration(
        _plan(desc="rewrite the python module in python"), _PY_REPO
    )
    assert sig.is_migration is False


# ── source inspector (AST, no execution) ────────────────────────────────


def test_behavioral_contract_extraction(tmp_path: Path):
    (tmp_path / "pay").mkdir()
    (tmp_path / "pay" / "__init__.py").write_text("")
    (tmp_path / "pay" / "refund.py").write_text(
        "def refund(amount, reason):\n    return amount\n\n"
        "def _private():\n    return 1\n\nclass Ledger:\n    pass\n"
    )
    (tmp_path / "pay" / "api.py").write_text(
        "from pay.refund import refund\n\ndef handle(req):\n    return refund(req, 'x')\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_refund.py").write_text(
        "def test_x():\n    assert True\n"
    )

    c = build_behavioral_contract(tmp_path)
    names = {(s.name, s.kind) for s in c.public_api}
    assert ("refund", "function") in names
    assert ("Ledger", "class") in names
    assert ("_private", "function") not in names  # underscore excluded
    assert any(s.signature == "refund(amount, reason)" for s in c.public_api)
    assert "tests/test_refund.py" in c.test_files
    assert "pay.api" in c.module_graph  # imports pay.refund


# ── migration planner ───────────────────────────────────────────────────


def test_extracts_input_vectors_from_tests(tmp_path: Path):
    (tmp_path / "pay").mkdir()
    (tmp_path / "pay" / "__init__.py").write_text("")
    (tmp_path / "pay" / "refund.py").write_text(
        "def refund(amount, reason):\n"
        '    if amount <= 0:\n        raise ValueError("bad")\n'
        "    return amount\n\ndef fee(amount):\n    return amount * 0.03\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_pay.py").write_text(
        "import pytest\nfrom pay.refund import refund, fee\n"
        'def test_ok(): assert refund(100, "x") == 100\n'
        'def test_neg():\n    with pytest.raises(ValueError): refund(-5, "y")\n'
        "def test_fee(): assert fee(7.5) == 0.225\n"
    )
    c = build_behavioral_contract(tmp_path)
    vecs = {(v["function"], tuple(v["args"])) for v in c.input_vectors}
    assert ("refund", (100, "x")) in vecs
    assert ("refund", (-5, "y")) in vecs  # extracted from inside pytest.raises
    assert ("fee", (7.5,)) in vecs
    # manifest + equivalence block carry the concrete vectors
    eq = build_equivalence_block(c, "rust")
    assert eq["manifest"]["input_vectors"]
    assert {iv["function"] for iv in eq["manifest"]["input_vectors"]} == {
        "refund",
        "fee",
    }


def test_module_map_excludes_tests_and_init(tmp_path: Path):
    (tmp_path / "pay").mkdir()
    (tmp_path / "pay" / "__init__.py").write_text("")
    (tmp_path / "pay" / "refund.py").write_text("def refund(a):\n    return a\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_refund.py").write_text(
        "def test_x():\n    assert True\n"
    )
    c = build_behavioral_contract(tmp_path)
    # test functions are not public surface to port/capture
    assert "test_x" not in {s.name for s in c.public_api}
    mm = build_module_map(c, "rust", crate="port")
    assert mm == {
        "pay/refund.py": "rust/port/src/pay/refund.rs"
    }  # no __init__, no tests


def test_module_map_and_corpus(tmp_path: Path):
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    (tmp_path / "b.py").write_text("from a import f\n\ndef g():\n    return f()\n")
    c = build_behavioral_contract(tmp_path)
    mm = build_module_map(c, "rust", crate="pay")
    assert mm["a.py"] == "rust/pay/src/a.rs"
    # leaf-first: a before b
    assert list(mm).index("a.py") < list(mm).index("b.py")
    corpus = build_golden_corpus_manifest(c)
    assert corpus["capture"] == "run-legacy-in-sandbox"
    assert {f["name"] for f in corpus["functions"]} == {"f", "g"}
    eq = build_equivalence_block(c, "rust")
    assert eq["parity_threshold"] == 1.0 and eq["differential_lanes"] == ["equivalence"]


# ── emit: migration block ───────────────────────────────────────────────


def _epic():
    from plan.decompose.models import ChildIssue, EpicPlan

    return EpicPlan(
        plan_id="001-x",
        epic_title="Port payments",
        children=[ChildIssue(key="C1", title="port refund", kind="feature")],
    )


def test_attach_migration_sets_languages_and_equivalence():
    plan = _plan(
        change_mode="migration",
        source_language="python",
        target_language="rust",
        migration={
            "source_language": "python",
            "target_language": "rust",
            "equivalence": {
                "golden_corpus_ref": "findings/golden_corpus.json",
                "module_map": {},
            },
        },
    )
    contract = {"tfactory": {"lanes": ["unit"]}, "environment": {"language": "python"}}
    attach_migration(contract, plan)
    assert contract["environment"]["source_language"] == "python"
    assert contract["environment"]["target_language"] == "rust"
    assert contract["environment"]["language"] == "rust"
    assert "equivalence" in contract["tfactory"]["lanes"]
    assert contract["tfactory"]["equivalence"]["golden_corpus_ref"]


def test_migration_contract_validates():
    plan = _plan(
        crits=("Behaves like the Python refund",),
        change_mode="migration",
        source_language="python",
        target_language="rust",
        repo_map=_PY_REPO,
        migration={
            "source_language": "python",
            "target_language": "rust",
            "equivalence": {
                "golden_corpus_ref": "findings/golden_corpus.json",
                "parity_threshold": 1.0,
                "differential_lanes": ["equivalence"],
                "module_map": {"pay/refund.py": "rust/pay/src/pay/refund.rs"},
            },
        },
    )
    c = assemble_contract(plan, _epic(), repo="o/pay")
    assert c["workflow_type"] == "migration"
    assert c["change_mode"] == "migration"
    assert c["environment"]["target_language"] == "rust"
    assert "equivalence" in c["tfactory"]["lanes"]
    assert tc.validate_contract(c) == []  # schema-valid


# ── service wiring (no network) ─────────────────────────────────────────


def test_process_detects_migration(monkeypatch):
    from plan import service as service_mod
    from plan.detect.source_inspector import BehavioralContract, PublicSymbol

    monkeypatch.setattr(
        service_mod, "reconnoiter", lambda repo, base_ref=None: _PY_REPO
    )
    contract = BehavioralContract(
        language="python",
        modules=["pay/refund.py"],
        public_api=[
            PublicSymbol("pay/refund.py", "refund", "function", "refund(amount)")
        ],
        test_files=["tests/test_refund.py"],
    )
    monkeypatch.setattr(service_mod, "inspect_source", lambda *a, **k: contract)

    svc = service_mod.PlanService(persist=False)
    text = "# Port payments\n\nRewrite the payments module from Python to Rust.\n\n## Acceptance Criteria\n- AC#1: behaves identically\n"
    s = svc.ingest_text(
        text, title="Port", channel="cli", repo="o/pay", base_ref="main"
    )
    out = svc.process(s.session_id)
    assert out.plan.change_mode == "migration"
    assert out.plan.source_language == "python" and out.plan.target_language == "rust"
    assert out.plan.migration and "equivalence" in out.plan.migration
