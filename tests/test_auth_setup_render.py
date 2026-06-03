"""Tests for Playwright storageState auth rendering (#107 task 5).

Backend-pure: renders the bundled templates as strings; no Playwright/runner.
"""

from __future__ import annotations

import re
from pathlib import Path

from agents.evidence.layout import render_auth_setup, render_playwright_config
from templates_pkg.engine import load_templates_for_framework

# ── render_playwright_config: auth gating ────────────────────────────────────


def test_config_no_auth_has_no_storage_state(tmp_path: Path) -> None:
    rendered = render_playwright_config(tmp_path / "ev", "http://localhost:3000")
    assert "storageState" not in rendered
    assert 'name: "setup"' not in rendered
    assert 'dependencies: ["setup"]' not in rendered
    assert "@@" not in rendered  # no placeholder leakage


def test_config_with_auth_adds_setup_project_and_storage_state(tmp_path: Path) -> None:
    rendered = render_playwright_config(
        tmp_path / "ev", "http://localhost:3000", requires_auth=True
    )
    assert 'storageState: "state.json"' in rendered
    assert 'name: "setup"' in rendered
    assert "auth\\.setup\\.ts" in rendered
    assert 'dependencies: ["setup"]' in rendered
    assert "@@" not in rendered


def test_config_custom_storage_state_path(tmp_path: Path) -> None:
    rendered = render_playwright_config(
        tmp_path / "ev",
        "http://localhost",
        requires_auth=True,
        storage_state_path="auth/admin.json",
    )
    assert 'storageState: "auth/admin.json"' in rendered


# ── render_auth_setup ────────────────────────────────────────────────────────


def test_render_auth_setup_substitutes_all_fields() -> None:
    rendered = render_auth_setup(
        login_url="https://app.example.com/login",
        username_selector="#user",
        password_selector="#pass",
        submit_selector="button[type=submit]",
        success_url_pattern="dashboard",
        username_env="SN_USERNAME",
        secret_env="SN_PASSWORD",
        storage_state_path="state.json",
    )
    assert "https://app.example.com/login" in rendered
    assert "#user" in rendered and "#pass" in rendered
    assert "button[type=submit]" in rendered
    assert "dashboard" in rendered
    # creds read from env vars, never inlined
    assert 'process.env["SN_USERNAME"]' in rendered
    assert 'process.env["SN_PASSWORD"]' in rendered
    assert "storageState({ path: STORAGE_STATE })" in rendered
    assert "@@" not in rendered  # no placeholder leakage


# ── login-flow template now reads injected env vars ──────────────────────────


def test_login_flow_template_reads_injected_env() -> None:
    tmpls = load_templates_for_framework(
        "playwright", include_harvested=False, include_library=False
    )
    login = tmpls["login-flow.spec.ts.tmpl"]
    # requires_auth flips on — the Executor injects creds for this template
    assert login.metadata.requires_auth is True
    # vars match placeholders and include the env-name vars
    body_ph = set(re.findall(r"\$\{(\w+)}", login.body))
    declared = set(login.metadata.vars)
    assert body_ph == declared
    assert {"username_env", "secret_env"} <= declared
    # no hard-coded credentials remain
    assert "test-password-123" not in login.body
    rendered = login.instantiate(
        target_base_url="http://x",
        test_name="t",
        login_path="/login",
        username_selector="#u",
        password_selector="#p",
        submit_selector="#s",
        success_url_pattern="home",
        username_env="U",
        secret_env="S",
    )
    assert "process.env['U']" in rendered
    assert "process.env['S']" in rendered
