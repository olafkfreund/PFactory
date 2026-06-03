"""Tests for BYO-LLM / air-gapped egress classification (#38)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from byo_llm import (  # noqa: E402
    EgressClass,
    classify,
    egress_report,
    host_is_local,
    keeps_data_local,
    resolve_base_url,
)

# ── host_is_local ──────────────────────────────────────────────────────

@pytest.mark.parametrize("host", [
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
    "192.168.1.10", "10.0.0.5", "172.16.4.4", "169.254.1.1",
    "ollama.local", "vllm.internal", "box.lan",
])
def test_local_hosts(host):
    assert host_is_local(host) is True


@pytest.mark.parametrize("host", [
    "api.openai.com", "openrouter.ai", "8.8.8.8", "example.com",
    "1.1.1.1", None, "",
])
def test_non_local_hosts(host):
    assert host_is_local(host) is False


# ── classify: Ollama (local by default) ────────────────────────────────

def test_ollama_default_is_local():
    assert classify("ollama:qwen3:14b") is EgressClass.LOCAL
    assert keeps_data_local("ollama:qwen3:14b") is True


def test_ollama_remote_is_self_hosted():
    assert classify("ollama:qwen3", "http://gpu-box.example.com:11434") is (
        EgressClass.SELF_HOSTED
    )
    assert keeps_data_local("ollama:qwen3", "http://gpu-box.example.com:11434") is False


# ── classify: openai-compatible (depends on host) ──────────────────────

def test_openai_compatible_localhost_is_local():
    # vLLM / LM Studio / LocalAI on localhost
    assert classify("openai-compatible:qwen2.5", "http://localhost:8000/v1") is (
        EgressClass.LOCAL
    )


def test_openai_compatible_private_ip_is_local():
    assert classify("openai-compatible:llama", "http://192.168.1.50:1234") is (
        EgressClass.LOCAL
    )


def test_openai_compatible_managed_host_is_cloud():
    assert classify("openai-compatible:gpt-4o-mini", "https://openrouter.ai/api/v1") is (
        EgressClass.MANAGED_CLOUD
    )


def test_openai_compatible_own_vps_is_self_hosted():
    assert classify("openai-compatible:llama", "https://llm.mycorp.example/v1") is (
        EgressClass.SELF_HOSTED
    )


# ── classify: managed providers ────────────────────────────────────────

def test_claude_default_is_managed():
    assert classify("claude-sonnet-4-5-20250929") is EgressClass.MANAGED_CLOUD
    assert keeps_data_local("claude-sonnet-4-5-20250929") is False


def test_claude_local_proxy_is_local():
    # ANTHROPIC_BASE_URL repointed at a local proxy (e.g. LiteLLM)
    assert classify("claude-sonnet-4-5", "http://localhost:4000") is EgressClass.LOCAL


def test_gemini_is_managed():
    assert classify("gemini-2.0-flash") is EgressClass.MANAGED_CLOUD


# ── resolve_base_url (env-aware) ───────────────────────────────────────

def test_resolve_ollama_default(monkeypatch):
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    assert resolve_base_url("ollama:qwen3") == "http://localhost:11434"


def test_resolve_ollama_env_override(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://10.0.0.9:11434")
    assert resolve_base_url("ollama:qwen3") == "http://10.0.0.9:11434"
    assert classify("ollama:qwen3") is EgressClass.LOCAL  # 10/8 is private


def test_resolve_openai_compatible_env(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "http://localhost:8000/v1")
    assert resolve_base_url("openai-compatible:x") == "http://localhost:8000/v1"
    assert classify("openai-compatible:x") is EgressClass.LOCAL


# ── egress_report surface ──────────────────────────────────────────────

def test_egress_report_local(monkeypatch):
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    rep = egress_report("ollama:qwen3:14b")
    assert rep["provider"] == "ollama"
    assert rep["egress"] == "local"
    assert rep["keeps_data_local"] is True
    assert rep["host"] == "localhost"
    assert "no data egress" in rep["badge"]


def test_egress_report_managed():
    rep = egress_report("claude-sonnet-4-5-20250929")
    assert rep["egress"] == "managed_cloud"
    assert rep["keeps_data_local"] is False
