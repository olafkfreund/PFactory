"""Tests for the per-language mutation dispatch (#41)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents import mutation_dispatch as md  # noqa: E402

# ── normalize_language ─────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    (None, "python"),
    ("python", "python"), ("Python", "python"), ("py", "python"),
    ("typescript", "typescript"), ("TS", "typescript"),
    ("javascript", "typescript"), ("js", "typescript"),
    ("java", "java"),  # unknown passes through (lowercased)
])
def test_normalize_language(raw, expected):
    assert md.normalize_language(raw) == expected


# ── is_mutation_supported ──────────────────────────────────────────────

@pytest.mark.parametrize("lang", [None, "python", "py", "typescript", "ts", "js"])
def test_supported(lang):
    assert md.is_mutation_supported(lang) is True


@pytest.mark.parametrize("lang", ["java", "go", "csharp", "ruby"])
def test_unsupported(lang):
    assert md.is_mutation_supported(lang) is False


def test_mutant_extension():
    assert md.mutant_extension("python") == "py"
    assert md.mutant_extension(None) == "py"
    assert md.mutant_extension("typescript") == "ts"
    assert md.mutant_extension("ts") == "ts"


# ── run_language_mutation routing ──────────────────────────────────────

def test_routes_python_to_py_probe(monkeypatch, tmp_path):
    calls = {}

    def fake_py(test_file, project_dir, runner_fn, *, write_mutant_to):
        calls["backend"] = "python"
        calls["mutant"] = write_mutant_to
        return "PY_REPORT"

    import agents.mutate_probe as mp
    monkeypatch.setattr(mp, "run_mutate_probe", fake_py)

    out = md.run_language_mutation(
        "python", tmp_path / "t.py", tmp_path, runner_fn=object(),
        mutant_path=tmp_path / "m.py",
    )
    assert out == "PY_REPORT"
    assert calls["backend"] == "python"
    assert calls["mutant"] == tmp_path / "m.py"


def test_routes_typescript_to_ts_probe(monkeypatch, tmp_path):
    calls = {}

    def fake_ts(test_file, project_dir, *, runner_fn=None, **kw):
        calls["backend"] = "typescript"
        return "TS_REPORT"

    import agents.lang_typescript.mutate_probe as tsp
    monkeypatch.setattr(tsp, "run_ts_mutate_probe", fake_ts)

    out = md.run_language_mutation(
        "typescript", tmp_path / "t.ts", tmp_path, runner_fn=object(),
        mutant_path=tmp_path / "m.ts",
    )
    assert out == "TS_REPORT"
    assert calls["backend"] == "typescript"


def test_js_alias_routes_to_ts_probe(monkeypatch, tmp_path):
    seen = {}
    import agents.lang_typescript.mutate_probe as tsp
    monkeypatch.setattr(tsp, "run_ts_mutate_probe",
                        lambda *a, **k: seen.setdefault("hit", True))
    md.run_language_mutation("js", tmp_path / "t.test.js", tmp_path,
                             runner_fn=None, mutant_path=tmp_path / "m.ts")
    assert seen.get("hit") is True


def test_unsupported_language_returns_none(tmp_path):
    out = md.run_language_mutation(
        "java", tmp_path / "T.java", tmp_path, runner_fn=None,
        mutant_path=tmp_path / "m.java",
    )
    assert out is None


def test_default_none_language_routes_python(monkeypatch, tmp_path):
    import agents.mutate_probe as mp
    monkeypatch.setattr(mp, "run_mutate_probe", lambda *a, **k: "PY")
    out = md.run_language_mutation(
        None, tmp_path / "t.py", tmp_path, runner_fn=None,
        mutant_path=tmp_path / "m.py",
    )
    assert out == "PY"
