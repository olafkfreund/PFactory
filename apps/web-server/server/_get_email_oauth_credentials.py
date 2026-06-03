"""
Helper to retrieve OAuth credentials from app settings or env vars.

Shared between email routes and email service to avoid circular imports.
"""

import json
import os
from pathlib import Path


def get_email_oauth_credentials() -> tuple[str, str] | None:
    """Return (client_id, client_secret) for Microsoft OAuth, or None if not configured.

    Checks app settings file first, then falls back to environment variables.
    """
    client_id = os.environ.get("APP_EMAIL_MICROSOFT_CLIENT_ID")
    client_secret = os.environ.get("APP_EMAIL_MICROSOFT_CLIENT_SECRET")

    # Try loading from settings file
    try:
        from .config import get_settings

        settings = get_settings()
        settings_file = Path(settings.PROJECTS_DATA_DIR) / "settings.json"
        if settings_file.exists():
            data = json.loads(settings_file.read_text())
            file_client_id = data.get("emailMicrosoftClientId")
            file_client_secret = data.get("emailMicrosoftClientSecret")
            if file_client_id and file_client_secret:
                client_id = file_client_id
                client_secret = file_client_secret
    except Exception:
        pass

    if client_id and client_secret:
        return (client_id, client_secret)

    return None


def get_google_oauth_credentials() -> tuple[str, str] | None:
    """Return (client_id, client_secret) for Google OAuth, or None if not configured.

    Checks app settings file first, then falls back to environment variables.
    """
    client_id = os.environ.get("APP_EMAIL_GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("APP_EMAIL_GOOGLE_CLIENT_SECRET")

    # Try loading from settings file
    try:
        from .config import get_settings

        settings = get_settings()
        settings_file = Path(settings.PROJECTS_DATA_DIR) / "settings.json"
        if settings_file.exists():
            data = json.loads(settings_file.read_text())
            file_client_id = data.get("emailGoogleClientId")
            file_client_secret = data.get("emailGoogleClientSecret")
            if file_client_id and file_client_secret:
                client_id = file_client_id
                client_secret = file_client_secret
    except Exception:
        pass

    if client_id and client_secret:
        return (client_id, client_secret)

    return None
