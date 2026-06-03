#!/usr/bin/env python3
"""
Tests for OpenAI-compatible endpoints and settings.

Verifies:
  1. Module-level logger is defined in settings.py
  2. GET /settings/openai-compat/models returns error JSON (not an exception)
     when the remote server is unavailable
  3. POST /settings/openai-compat/test returns {success: false, error: ...}
     when the remote server is not reachable
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Make server package importable
# ---------------------------------------------------------------------------
_WEB_SERVER = Path(__file__).resolve().parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))


# ---------------------------------------------------------------------------
# Minimal FastAPI application containing only the settings router
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def _app() -> FastAPI:
    """Minimal FastAPI app with only the settings router mounted."""
    from server.routes.settings import router as settings_router

    app = FastAPI(title="Settings Test App")
    app.include_router(settings_router, prefix="/settings")
    return app


@pytest.fixture
def client(_app: FastAPI):
    """TestClient wrapping the minimal settings app."""
    with TestClient(_app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# 1. Module-level logger is defined
# ---------------------------------------------------------------------------


class TestModuleLevelLogger:
    """Verify that the settings module defines a module-level logger."""

    def test_settings_module_has_logger_attribute(self):
        """Import the settings module and assert it has a 'logger' attribute."""
        import server.routes.settings as settings_module

        assert hasattr(settings_module, "logger"), (
            "server.routes.settings is missing a module-level 'logger' variable. "
            "This would cause NameError when Ollama/OpenAI-compat routes log messages."
        )

    def test_logger_is_logging_logger(self):
        """The module-level logger should be a standard logging.Logger instance."""
        import logging

        import server.routes.settings as settings_module

        assert isinstance(settings_module.logger, logging.Logger), (
            "settings_module.logger must be a logging.Logger, "
            f"got {type(settings_module.logger)!r} instead."
        )


# ---------------------------------------------------------------------------
# 2. GET /settings/openai-compat/models — server unavailable
# ---------------------------------------------------------------------------


class TestListOpenAICompatModels:
    """Tests for GET /settings/openai-compat/models."""

    def test_returns_error_json_when_server_unreachable(self, client):
        """
        When the OpenAI-compatible server cannot be reached, the endpoint must
        return a JSON body with {success: false, error: <message>} rather than
        raising an unhandled exception that would produce a 500 response with
        no structured body.
        """
        import httpx

        with patch(
            "httpx.AsyncClient.get",
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            resp = client.get(
                "/settings/openai-compat/models",
                params={"baseUrl": "http://127.0.0.1:19999"},
            )

        # Must always return 200 with a structured error body — never a bare 500
        assert resp.status_code == 200, (
            f"Expected 200 (structured error), got {resp.status_code}"
        )
        data = resp.json()
        assert data.get("success") is False, (
            f"Expected 'success': false in error response, got: {data}"
        )
        assert "error" in data, f"Expected 'error' key in response body, got: {data}"
        assert isinstance(data["error"], str), (
            f"Expected 'error' to be a string, got {type(data['error'])!r}"
        )

    def test_returns_models_list_shape_on_success(self, client):
        """
        When the server returns a valid /v1/models response, the endpoint
        must return {models: [{name: str}]} (no embedding models included).
        """
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"id": "mistral-7b-instruct"},
                {"id": "llama3"},
                {"id": "text-embedding-ada-002"},  # should be filtered out
                {"id": "bge-m3"},                  # should be filtered out
            ]
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = client.get(
                "/settings/openai-compat/models",
                params={"baseUrl": "http://localhost:1234"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data, f"Expected 'models' key in response, got: {data}"
        names = [m["name"] for m in data["models"]]
        assert "mistral-7b-instruct" in names
        assert "llama3" in names
        # Embedding models should be filtered out
        assert "text-embedding-ada-002" not in names
        assert "bge-m3" not in names

    def test_timeout_returns_error_json(self, client):
        """ConnectTimeout must also result in structured error JSON, not a 500."""
        import httpx

        with patch(
            "httpx.AsyncClient.get",
            side_effect=httpx.ConnectTimeout("Timed out"),
        ):
            resp = client.get(
                "/settings/openai-compat/models",
                params={"baseUrl": "http://10.255.255.1:8080"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is False
        assert "error" in data


# ---------------------------------------------------------------------------
# 3. POST /settings/openai-compat/test — server not reachable
# ---------------------------------------------------------------------------


class TestOpenAICompatConnectionTest:
    """Tests for POST /settings/openai-compat/test."""

    def test_returns_failure_when_server_not_reachable(self, client):
        """
        When the connection attempt fails (server down / wrong port),
        the endpoint must return {success: false, error: <message>}.
        """
        import httpx

        with patch(
            "httpx.AsyncClient.get",
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            resp = client.post(
                "/settings/openai-compat/test",
                json={"baseUrl": "http://127.0.0.1:19999"},
            )

        assert resp.status_code == 200, (
            f"Expected 200 with failure body, got {resp.status_code}"
        )
        data = resp.json()
        assert data.get("success") is False, (
            f"Expected 'success': false when server unreachable, got: {data}"
        )
        assert "error" in data, f"Expected 'error' key in response body, got: {data}"

    def test_returns_success_with_model_count_when_reachable(self, client):
        """
        When the server responds with valid data, the endpoint must return
        {success: true, modelCount: N, message: str}.
        """
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"id": "mistral-7b"},
                {"id": "llama3"},
                {"id": "embed-model"},  # filtered out
            ]
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = client.post(
                "/settings/openai-compat/test",
                json={"baseUrl": "http://localhost:1234"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True
        assert "modelCount" in data
        assert data["modelCount"] == 2  # embed-model is filtered out
        assert "message" in data

    def test_timeout_returns_failure_json(self, client):
        """Timeout must produce {success: false, error: ...} not an exception."""
        import httpx

        with patch(
            "httpx.AsyncClient.get",
            side_effect=httpx.TimeoutException("Read timed out"),
        ):
            resp = client.post(
                "/settings/openai-compat/test",
                json={"baseUrl": "http://10.255.255.1:8080"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is False
        assert "error" in data

    def test_requires_base_url_field(self, client):
        """Missing baseUrl must result in a 422 validation error."""
        resp = client.post(
            "/settings/openai-compat/test",
            json={},
        )
        assert resp.status_code == 422

    def test_optional_api_key_accepted(self, client):
        """apiKey is optional; request with apiKey must be valid (not 422)."""
        import httpx

        with patch(
            "httpx.AsyncClient.get",
            side_effect=httpx.ConnectError("refused"),
        ):
            resp = client.post(
                "/settings/openai-compat/test",
                json={"baseUrl": "http://localhost:1234", "apiKey": "sk-test"},
            )

        # Should be 200 (structured error), not 422 (validation failure)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is False


