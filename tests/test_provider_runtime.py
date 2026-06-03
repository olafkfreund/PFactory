"""Tests for the provider runtime version manager (#121).

Backend-pure: shutil.which / subprocess / urllib / Path.home are all mocked so
no real CLI, network, or host state is touched.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import provider_runtime as pr
import pytest

# ── registry + parsing ───────────────────────────────────────────────────────


def test_registry_has_expected_providers() -> None:
    names = {rt.name for rt in pr.runtimes()}
    assert {"claude", "codex", "copilot", "gemini", "ollama"} <= names


def test_get_runtime_unknown_raises() -> None:
    with pytest.raises(KeyError):
        pr.get_runtime("nope")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("codex/1.2.3", "1.2.3"),
        ("v2.0.0\n", "2.0.0"),
        ("gemini-cli 0.10.4-beta.1", "0.10.4-beta.1"),
        ("no version here", None),
        (None, None),
    ],
)
def test_parse_version(text, expected) -> None:
    assert pr._parse_version(text) == expected


# ── detection ────────────────────────────────────────────────────────────────


def test_detect_pip_uses_importlib_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("importlib.metadata.version", lambda pkg: "0.1.20")
    assert pr.detect_installed(pr.get_runtime("claude")) == "0.1.20"


def test_detect_binary_runs_version_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pr.shutil, "which", lambda b: f"/usr/bin/{b}" if b == "codex" else None)
    monkeypatch.setattr(
        pr.subprocess, "run",
        lambda *a, **k: SimpleNamespace(stdout="codex 1.5.0\n", stderr="", returncode=0),
    )
    assert pr.detect_installed(pr.get_runtime("codex")) == "1.5.0"


def test_detect_returns_none_when_binary_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pr.shutil, "which", lambda b: None)
    assert pr.detect_installed(pr.get_runtime("codex")) is None


def test_detect_pip_falls_back_to_backend_venv(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate being imported from a venv that lacks the SDK: importlib fails,
    # so detection must shell out to the backend venv interpreter (#121).
    def _boom(pkg):
        raise ModuleNotFoundError(pkg)

    monkeypatch.setattr("importlib.metadata.version", _boom)
    monkeypatch.setattr(pr, "_backend_python", lambda: "/be/.venv/bin/python")
    monkeypatch.setattr(
        pr.subprocess, "run",
        lambda *a, **k: SimpleNamespace(stdout="0.2.87\n", stderr="", returncode=0),
    )
    assert pr.detect_installed(pr.get_runtime("claude")) == "0.2.87"


def test_detect_pip_none_when_no_backend_venv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "importlib.metadata.version", lambda pkg: (_ for _ in ()).throw(Exception())
    )
    monkeypatch.setattr(pr, "_backend_python", lambda: None)
    assert pr.detect_installed(pr.get_runtime("claude")) is None


# ── binary resolution via well-known paths (antigravity) ─────────────────────


def test_find_binary_probes_extra_paths(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pr.shutil, "which", lambda b: None)  # not on PATH
    binp = tmp_path / "antigravity"
    binp.write_text("#!/bin/sh\n")
    rt = pr.ProviderRuntime(
        "g", "npm", ("gemini",), ("--version",), package="x", extra_paths=(str(binp),)
    )
    assert pr._find_binary(rt) == str(binp)


def test_gemini_registry_has_antigravity_extra_paths() -> None:
    paths = pr.get_runtime("gemini").extra_paths
    assert paths and any("antigravity-cli" in p for p in paths)


def test_npm_package_version_reads_package_json(tmp_path) -> None:
    pkg = tmp_path / "node_modules" / "@google" / "gemini-cli"
    (pkg / "bin").mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps({"version": "0.10.4"}))
    binp = pkg / "bin" / "gemini"
    binp.write_text("#!/usr/bin/env node\n")
    assert pr._npm_package_version(str(binp)) == "0.10.4"


def test_detect_npm_prefers_package_json_over_cli(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = tmp_path / "lib" / "codex"
    (pkg / "bin").mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps({"version": "1.7.2"}))
    binp = pkg / "bin" / "codex"
    binp.write_text("#!/usr/bin/env node\n")
    monkeypatch.setattr(pr.shutil, "which", lambda b: str(binp) if b == "codex" else None)

    def _should_not_run(*a, **k):  # version must come from package.json, not the CLI
        raise AssertionError("CLI --version should not be spawned when package.json wins")

    monkeypatch.setattr(pr.subprocess, "run", _should_not_run)
    assert pr.detect_installed(pr.get_runtime("codex")) == "1.7.2"


# ── latest_version ───────────────────────────────────────────────────────────


def test_latest_npm_via_npm_view(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pr.shutil, "which", lambda b: "/usr/bin/npm" if b == "npm" else None)
    monkeypatch.setattr(
        pr.subprocess, "run",
        lambda *a, **k: SimpleNamespace(stdout="1.9.0\n", stderr="", returncode=0),
    )
    assert pr.latest_version(pr.get_runtime("codex")) == "1.9.0"


def test_latest_pip_via_pypi(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        def __enter__(self):
            return SimpleNamespace(
                read=lambda: json.dumps({"info": {"version": "0.2.0"}}).encode()
            )

        def __exit__(self, *a):
            return False

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    assert pr.latest_version(pr.get_runtime("claude")) == "0.2.0"


def test_latest_is_none_for_gh_kind() -> None:
    assert pr.latest_version(pr.get_runtime("copilot")) is None


# ── pins ─────────────────────────────────────────────────────────────────────


def test_pin_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert pr.pinned_version("codex") is None
    pr.set_pin("codex", "1.4.0")
    assert pr.pinned_version("codex") == "1.4.0"
    # persisted to ~/.pfactory/provider-runtimes.json
    stored = json.loads((tmp_path / ".pfactory" / "provider-runtimes.json").read_text())
    assert stored["codex"] == "1.4.0"
    pr.set_pin("codex", None)  # clear
    assert pr.pinned_version("codex") is None


# ── status ───────────────────────────────────────────────────────────────────


def test_status_flags_update_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pr, "detect_installed", lambda rt: "1.0.0")
    monkeypatch.setattr(pr, "latest_version", lambda rt: "1.2.0")
    monkeypatch.setattr(pr, "pinned_version", lambda name: None)
    st = pr.get_status("codex")
    assert st.installed and st.installed_version == "1.0.0"
    assert st.latest_version == "1.2.0"
    assert st.update_available is True


def test_status_no_update_when_current(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pr, "detect_installed", lambda rt: "1.2.0")
    monkeypatch.setattr(pr, "latest_version", lambda rt: "1.2.0")
    monkeypatch.setattr(pr, "pinned_version", lambda name: "1.2.0")
    st = pr.get_status("codex")
    assert st.update_available is False
    assert st.pinned_version == "1.2.0"


def test_status_unmanaged_has_no_latest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pr, "detect_installed", lambda rt: "0.5.0")
    monkeypatch.setattr(pr, "pinned_version", lambda name: None)
    st = pr.get_status("ollama")
    assert st.managed is False
    assert st.latest_version is None
    assert st.update_available is False


# ── install_argv (pure) ──────────────────────────────────────────────────────


def test_install_argv_npm_latest_and_pinned() -> None:
    rt = pr.get_runtime("codex")
    assert pr.install_argv(rt) == ["npm", "install", "-g", "@openai/codex@latest"]
    assert pr.install_argv(rt, "1.4.0") == ["npm", "install", "-g", "@openai/codex@1.4.0"]


def test_install_argv_pip() -> None:
    rt = pr.get_runtime("claude")
    latest = pr.install_argv(rt)
    assert latest[1:] == ["-m", "pip", "install", "--upgrade", "claude-agent-sdk"]
    pinned = pr.install_argv(rt, "0.1.20")
    assert pinned[-1] == "claude-agent-sdk==0.1.20"


def test_install_argv_gh_is_upgrade() -> None:
    assert pr.install_argv(pr.get_runtime("copilot")) == ["copilot", "upgrade"]


def test_install_argv_unmanaged_raises() -> None:
    with pytest.raises(ValueError):
        pr.install_argv(pr.get_runtime("ollama"))


# ── semver compare ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "a,b,older",
    [("1.0.0", "1.2.0", True), ("1.2.0", "1.2.0", False), ("2.0.0", "1.9.9", False),
     ("0.1.16", "0.1.20", True)],
)
def test_semver_lt(a, b, older) -> None:
    assert pr._semver_lt(a, b) is older


# ── run_install (live runner; mocked subprocess) ─────────────────────────────


def test_run_install_executes_and_redetects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pr.subprocess, "run",
        lambda *a, **k: SimpleNamespace(stdout="added 1 package\n", stderr="", returncode=0),
    )
    monkeypatch.setattr(pr, "detect_installed", lambda rt: "1.9.0")
    res = pr.run_install("codex")
    assert res.returncode == 0
    assert res.installed_version == "1.9.0"
    assert res.command == ["npm", "install", "-g", "@openai/codex@latest"]


def test_run_install_unmanaged_raises() -> None:
    with pytest.raises(ValueError):
        pr.run_install("ollama")


def test_run_install_unknown_raises() -> None:
    with pytest.raises(KeyError):
        pr.run_install("nope")
