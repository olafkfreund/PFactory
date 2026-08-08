"""
Email OAuth and account management routes.

Handles:
- OAuth flow for connecting Outlook (Microsoft) email accounts
- Listing and disconnecting email accounts
- Sending test emails
- Checking OAuth credential configuration status
"""

import logging
import os
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import delete, select

from ..config import get_settings
from ..database import EmailAccount
from ..database.engine import async_session_factory
from .._get_email_oauth_credentials import get_email_oauth_credentials, get_google_oauth_credentials

logger = logging.getLogger(__name__)


def _get_oauth_redirect_uri(request: Request, provider: str = "outlook") -> str:
    """Build the OAuth redirect URI for the given provider.

    Uses EMAIL_OAUTH_REDIRECT_URI env var if set (for outlook only), otherwise derives from request.
    The env var is useful when the app is behind a reverse proxy or accessed via
    a LAN IP, since Azure/Google require HTTPS for non-localhost redirect URIs.
    """
    if provider == "outlook":
        override = os.environ.get("EMAIL_OAUTH_REDIRECT_URI")
        if override:
            return override.rstrip("/")
    return str(request.base_url).rstrip("/") + f"/api/email/auth/{provider}/callback"


router = APIRouter(prefix="/api/email", tags=["Email"])

# In-memory OAuth state store: state_token -> {user_id, provider, created_at}
# Entries expire after 10 minutes
_oauth_states: dict[str, dict] = {}
_STATE_TTL_SECONDS = 600


def _cleanup_expired_states() -> None:
    """Remove expired OAuth state entries."""
    now = time.time()
    expired = [k for k, v in _oauth_states.items() if now - v["created_at"] > _STATE_TTL_SECONDS]
    for k in expired:
        del _oauth_states[k]


def _get_user_id(request: Request) -> str:
    """Extract user_id from authenticated request.

    When auth is disabled (APP_DISABLE_AUTH=true), returns a default user ID.
    """
    user = getattr(request.state, "user", None)
    if user and isinstance(user, dict) and user.get("id"):
        return user["id"]
    # When auth is disabled, use a default user
    settings = get_settings()
    if settings.DISABLE_AUTH:
        return "default"
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
    )


# --------------------------------------------------------------------------
# Account Management
# --------------------------------------------------------------------------


@router.get("/accounts")
async def list_email_accounts(request: Request):
    """List the user's connected email accounts (tokens not exposed)."""
    user_id = _get_user_id(request)

    async with async_session_factory() as session:
        result = await session.execute(select(EmailAccount).where(EmailAccount.user_id == user_id))
        accounts = result.scalars().all()

    return [
        {
            "id": a.id,
            "provider": a.provider,
            "email_address": a.email_address,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in accounts
    ]


@router.delete("/accounts/{account_id}")
async def disconnect_email_account(account_id: str, request: Request):
    """Disconnect (delete) an email account."""
    user_id = _get_user_id(request)

    async with async_session_factory() as session:
        result = await session.execute(
            select(EmailAccount).where(
                EmailAccount.id == account_id,
                EmailAccount.user_id == user_id,
            )
        )
        account = result.scalar_one_or_none()
        if not account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Email account not found",
            )
        await session.execute(delete(EmailAccount).where(EmailAccount.id == account_id))
        await session.commit()

    return {"success": True, "message": "Email account disconnected"}


# --------------------------------------------------------------------------
# OAuth Credential Status
# --------------------------------------------------------------------------


@router.get("/credentials-status")
async def get_credentials_status():
    """Check if OAuth credentials are configured for each provider."""
    ms_creds = get_email_oauth_credentials()
    google_creds = get_google_oauth_credentials()
    return {
        "microsoft": ms_creds is not None,
        "google": google_creds is not None,
    }


# --------------------------------------------------------------------------
# Test Email
# --------------------------------------------------------------------------


@router.post("/test/{account_id}")
async def send_test_email(account_id: str, request: Request):
    """Send a test email to verify the connection works."""
    user_id = _get_user_id(request)

    async with async_session_factory() as session:
        result = await session.execute(
            select(EmailAccount).where(
                EmailAccount.id == account_id,
                EmailAccount.user_id == user_id,
            )
        )
        account = result.scalar_one_or_none()
        if not account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Email account not found",
            )

    from ..services.email_service import email_service

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    subject = "PFactory - Test Email"
    body_html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 500px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #0ea5e9;">PFactory Email Notifications</h2>
        <p>This is a test email to confirm your email notifications are working correctly.</p>
        <p style="color: #6b7280; font-size: 14px;">Sent at {now}</p>
    </div>
    """

    sent = await email_service.send_notification_email(
        user_id=user_id,
        subject=subject,
        body_html=body_html,
        body_text=f"PFactory Test Email - Sent at {now}",
    )

    if sent:
        return {"success": True, "message": "Test email sent"}
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send test email. Check server logs for details.",
        )


# --------------------------------------------------------------------------
# Outlook OAuth Flow
# --------------------------------------------------------------------------

OUTLOOK_SCOPES = "https://graph.microsoft.com/Mail.Send User.Read offline_access"


@router.get("/auth/outlook/start")
async def start_outlook_oauth(request: Request):
    """Generate Microsoft OAuth authorization URL."""
    user_id = _get_user_id(request)

    creds = get_email_oauth_credentials()
    if not creds:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Microsoft OAuth credentials not configured. Set them in Integrations settings.",
        )

    client_id, _client_secret = creds

    # Generate state token
    _cleanup_expired_states()
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {
        "user_id": user_id,
        "provider": "outlook",
        "created_at": time.time(),
    }

    callback_url = _get_oauth_redirect_uri(request)

    auth_url = (
        "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        f"?client_id={client_id}"
        f"&response_type=code"
        f"&redirect_uri={callback_url}"
        f"&response_mode=query"
        f"&scope={OUTLOOK_SCOPES}"
        f"&state={state}"
        f"&prompt=consent"
    )

    return {"authUrl": auth_url}


@router.get("/auth/outlook/callback", response_class=HTMLResponse)
async def outlook_oauth_callback(
    request: Request,
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    error_description: str = Query(default=""),
):
    """Handle OAuth callback from Microsoft.

    This endpoint is a redirect target from Microsoft, so it cannot use
    bearer token auth. It's in PUBLIC_PREFIXES in auth.py.
    Returns HTML that posts a message to the opener window and closes itself.
    """
    # Handle error from Microsoft
    if error:
        return _oauth_result_html(
            success=False,
            message=error_description or error,
        )

    if not code or not state:
        return _oauth_result_html(
            success=False,
            message="Missing authorization code or state parameter",
        )

    # Validate state
    _cleanup_expired_states()
    state_data = _oauth_states.pop(state, None)
    if not state_data:
        return _oauth_result_html(
            success=False,
            message="Invalid or expired OAuth state. Please try again.",
        )

    user_id = state_data["user_id"]

    # Get credentials
    creds = get_email_oauth_credentials()
    if not creds:
        return _oauth_result_html(
            success=False,
            message="OAuth credentials not configured on server",
        )

    client_id, client_secret = creds
    callback_url = _get_oauth_redirect_uri(request)

    # Exchange code for tokens
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            token_response = await client.post(
                "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": callback_url,
                    "grant_type": "authorization_code",
                    "scope": OUTLOOK_SCOPES,
                },
            )

        if token_response.status_code != 200:
            logger.error(
                "Outlook token exchange failed: %d %s",
                token_response.status_code,
                token_response.text[:500],
            )
            return _oauth_result_html(
                success=False,
                message="Failed to exchange authorization code for tokens",
            )

        token_data = token_response.json()
    except Exception as e:
        logger.error("Outlook token exchange error: %s", e, exc_info=True)
        return _oauth_result_html(
            success=False,
            message="Network error during token exchange",
        )

    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in", 3600)
    token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    # Fetch user email from Microsoft Graph
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            me_response = await client.get(
                "https://graph.microsoft.com/v1.0/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if me_response.status_code != 200:
            return _oauth_result_html(
                success=False,
                message="Failed to fetch user profile from Microsoft",
            )

        me_data = me_response.json()
        email_address = me_data.get("mail") or me_data.get("userPrincipalName", "")
    except Exception as e:
        logger.error("Failed to fetch MS Graph /me: %s", e, exc_info=True)
        return _oauth_result_html(
            success=False,
            message="Failed to fetch user profile",
        )

    if not email_address:
        return _oauth_result_html(
            success=False,
            message="Could not determine email address from Microsoft account",
        )

    # Upsert EmailAccount
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(EmailAccount).where(
                    EmailAccount.user_id == user_id,
                    EmailAccount.provider == "outlook",
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.email_address = email_address
                existing.access_token = access_token
                existing.refresh_token = refresh_token
                existing.token_expiry = token_expiry
                existing.scopes = OUTLOOK_SCOPES
            else:
                account = EmailAccount(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    provider="outlook",
                    email_address=email_address,
                    access_token=access_token,
                    refresh_token=refresh_token,
                    token_expiry=token_expiry,
                    scopes=OUTLOOK_SCOPES,
                )
                session.add(account)

            await session.commit()
    except Exception as e:
        logger.error("Failed to save email account: %s", e, exc_info=True)
        return _oauth_result_html(
            success=False,
            message="Failed to save email account to database",
        )

    logger.info("Outlook account connected for user %s: %s", user_id, email_address)

    return _oauth_result_html(
        success=True,
        message=f"Connected: {email_address}",
        email=email_address,
        provider="outlook",
    )


# --------------------------------------------------------------------------
# Gmail OAuth Flow
# --------------------------------------------------------------------------

GMAIL_SCOPES = "https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/userinfo.email openid"


@router.get("/auth/gmail/start")
async def start_gmail_oauth(request: Request):
    """Generate Google OAuth authorization URL."""
    user_id = _get_user_id(request)

    creds = get_google_oauth_credentials()
    if not creds:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google OAuth credentials not configured. Set them in Integrations settings.",
        )

    client_id, _client_secret = creds

    # Generate state token
    _cleanup_expired_states()
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {
        "user_id": user_id,
        "provider": "gmail",
        "created_at": time.time(),
    }

    callback_url = _get_oauth_redirect_uri(request, provider="gmail")

    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={client_id}"
        f"&response_type=code"
        f"&redirect_uri={callback_url}"
        f"&scope={GMAIL_SCOPES}"
        f"&state={state}"
        f"&access_type=offline"
        f"&prompt=consent"
    )

    return {"authUrl": auth_url}


@router.get("/auth/gmail/callback", response_class=HTMLResponse)
async def gmail_oauth_callback(
    request: Request,
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    error_description: str = Query(default=""),
):
    """Handle OAuth callback from Google.

    This endpoint is a redirect target from Google, so it cannot use
    bearer token auth. It's in PUBLIC_PREFIXES in auth.py.
    Returns HTML that posts a message to the opener window and closes itself.
    """
    # Handle error from Google
    if error:
        return _oauth_result_html(
            success=False,
            message=error_description or error,
            provider="gmail",
        )

    if not code or not state:
        return _oauth_result_html(
            success=False,
            message="Missing authorization code or state parameter",
            provider="gmail",
        )

    # Validate state
    _cleanup_expired_states()
    state_data = _oauth_states.pop(state, None)
    if not state_data:
        return _oauth_result_html(
            success=False,
            message="Invalid or expired OAuth state. Please try again.",
            provider="gmail",
        )

    user_id = state_data["user_id"]

    # Get credentials
    creds = get_google_oauth_credentials()
    if not creds:
        return _oauth_result_html(
            success=False,
            message="OAuth credentials not configured on server",
            provider="gmail",
        )

    client_id, client_secret = creds
    callback_url = _get_oauth_redirect_uri(request, provider="gmail")

    # Exchange code for tokens
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            token_response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": callback_url,
                    "grant_type": "authorization_code",
                },
            )

        if token_response.status_code != 200:
            logger.error(
                "Gmail token exchange failed: %d %s",
                token_response.status_code,
                token_response.text[:500],
            )
            return _oauth_result_html(
                success=False,
                message="Failed to exchange authorization code for tokens",
                provider="gmail",
            )

        token_data = token_response.json()
    except Exception as e:
        logger.error("Gmail token exchange error: %s", e, exc_info=True)
        return _oauth_result_html(
            success=False,
            message="Network error during token exchange",
            provider="gmail",
        )

    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in", 3600)
    token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    # Fetch user email from Google userinfo
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            userinfo_response = await client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if userinfo_response.status_code != 200:
            return _oauth_result_html(
                success=False,
                message="Failed to fetch user profile from Google",
                provider="gmail",
            )

        userinfo = userinfo_response.json()
        email_address = userinfo.get("email", "")
    except Exception as e:
        logger.error("Failed to fetch Google userinfo: %s", e, exc_info=True)
        return _oauth_result_html(
            success=False,
            message="Failed to fetch user profile",
            provider="gmail",
        )

    if not email_address:
        return _oauth_result_html(
            success=False,
            message="Could not determine email address from Google account",
            provider="gmail",
        )

    # Upsert EmailAccount
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(EmailAccount).where(
                    EmailAccount.user_id == user_id,
                    EmailAccount.provider == "gmail",
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.email_address = email_address
                existing.access_token = access_token
                existing.refresh_token = refresh_token
                existing.token_expiry = token_expiry
                existing.scopes = GMAIL_SCOPES
            else:
                account = EmailAccount(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    provider="gmail",
                    email_address=email_address,
                    access_token=access_token,
                    refresh_token=refresh_token,
                    token_expiry=token_expiry,
                    scopes=GMAIL_SCOPES,
                )
                session.add(account)

            await session.commit()
    except Exception as e:
        logger.error("Failed to save Gmail account: %s", e, exc_info=True)
        return _oauth_result_html(
            success=False,
            message="Failed to save email account to database",
            provider="gmail",
        )

    logger.info("Gmail account connected for user %s: %s", user_id, email_address)

    return _oauth_result_html(
        success=True,
        message=f"Connected: {email_address}",
        email=email_address,
        provider="gmail",
    )


def _oauth_result_html(
    success: bool, message: str, email: str = "", provider: str = "outlook"
) -> HTMLResponse:
    """Return HTML that communicates the OAuth result to the opener window and closes itself."""
    status_text = "success" if success else "error"
    html = f"""<!DOCTYPE html>
<html>
<head><title>PFactory - Email Connection</title></head>
<body>
<p>{message}</p>
<script>
  if (window.opener) {{
    window.opener.postMessage({{
      type: 'email-oauth-callback',
      status: '{status_text}',
      message: {_js_string(message)},
      email: {_js_string(email)},
      provider: {_js_string(provider)}
    }}, '*');
  }}
  setTimeout(function() {{ window.close(); }}, 2000);
</script>
</body>
</html>"""
    return HTMLResponse(content=html)


def _js_string(s: str) -> str:
    """Escape a string for safe embedding in JavaScript."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"
