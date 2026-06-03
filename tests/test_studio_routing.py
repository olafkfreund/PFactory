#!/usr/bin/env python3
"""
Unit tests for studio: model prefix routing and Google AI Studio native
OpenAI-compatible integration.
"""

import os
import sys
from pathlib import Path

import pytest

# Make apps/backend importable
_BACKEND_DIR = Path(__file__).resolve().parent.parent / "apps" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


def test_infer_provider_from_studio_model():
    """Verify studio: prefixed models route to 'openai-compatible' canonical provider."""
    from phase_config import infer_provider_from_model

    assert infer_provider_from_model("studio:gemini-2.5-flash") == "openai-compatible"
    assert infer_provider_from_model("studio:gemini-2.5-pro") == "openai-compatible"
    assert infer_provider_from_model("studio:custom-model-name") == "openai-compatible"


def test_strip_provider_prefix_studio():
    """Verify strip_provider_prefix strips the 'studio:' prefix correctly."""
    from phase_config import strip_provider_prefix

    assert strip_provider_prefix("studio:gemini-2.5-flash") == "gemini-2.5-flash"
    assert strip_provider_prefix("studio:gemini-2.5-pro") == "gemini-2.5-pro"
    assert strip_provider_prefix("studio:") == ""


def test_get_provider_extra_kwargs_studio(monkeypatch):
    """Verify get_provider_extra_kwargs returns correct parameters for studio: models."""
    from phase_config import get_provider_extra_kwargs

    # Mock environment variables
    monkeypatch.setenv("GOOGLE_API_KEY", "mock-google-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    kwargs = get_provider_extra_kwargs("openai-compatible", "studio:gemini-2.5-flash")

    assert kwargs["base_url"] == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert kwargs["api_key"] == "mock-google-key"
    assert kwargs["model"] == "gemini-2.5-flash"

    # Verify fallback to GEMINI_API_KEY if GOOGLE_API_KEY is not set
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "mock-gemini-key")

    kwargs_fallback = get_provider_extra_kwargs("openai-compatible", "studio:gemini-2.5-pro")
    assert kwargs_fallback["api_key"] == "mock-gemini-key"
    assert kwargs_fallback["model"] == "gemini-2.5-pro"


def test_provider_factory_alias_resolution():
    """Verify 'studio' alias resolves correctly in factory._resolve_canonical."""
    from providers.factory import _resolve_canonical

    assert _resolve_canonical("studio") == "openai-compatible"
    assert _resolve_canonical("STUDIO") == "openai-compatible"
