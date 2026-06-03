"""
Email sending service via OAuth-connected accounts.

Sends notification emails through Microsoft Graph API (Outlook) or Gmail API.
Handles token refresh transparently when tokens are near expiry.

Usage::

    from ..services.email_service import email_service

    await email_service.send_notification_email(
        user_id="user-uuid",
        subject="Task completed: 001-feature",
        body_html="<p>Your task completed successfully.</p>",
        body_text="Your task completed successfully.",
    )
"""

import base64
import logging
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx
from sqlalchemy import select, update

from ..database import EmailAccount
from ..database.engine import async_session_factory

logger = logging.getLogger(__name__)


class EmailService:
    """Send emails via OAuth-connected email accounts (Outlook/Gmail)."""

    # Refresh token if it expires within this many seconds
    REFRESH_THRESHOLD_SECONDS = 300  # 5 minutes

    async def send_notification_email(
        self,
        user_id: str,
        subject: str,
        body_html: str,
        body_text: str = "",
    ) -> bool:
        """Send a notification email to the user's connected email account.

        Tries each connected account for the user until one succeeds.
        Returns True if the email was sent, False otherwise.
        """
        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    select(EmailAccount).where(EmailAccount.user_id == user_id)
                )
                accounts = result.scalars().all()
        except Exception:
            logger.warning(
                "Failed to load email accounts for user %s", user_id, exc_info=True
            )
            return False

        if not accounts:
            logger.debug("No email accounts configured for user %s", user_id)
            return False

        for account in accounts:
            try:
                if account.provider == "outlook":
                    sent = await self._send_via_outlook(account, subject, body_html)
                elif account.provider == "gmail":
                    sent = await self._send_via_gmail(account, subject, body_html)
                else:
                    logger.debug("Unsupported email provider: %s", account.provider)
                    continue

                if sent:
                    logger.info(
                        "Email sent to %s via %s: %s",
                        account.email_address,
                        account.provider,
                        subject,
                    )
                    return True
            except Exception:
                logger.warning(
                    "Failed to send email via %s (%s)",
                    account.provider,
                    account.email_address,
                    exc_info=True,
                )

        return False

    async def _send_via_outlook(
        self, account: EmailAccount, subject: str, body_html: str
    ) -> bool:
        """Send email via Microsoft Graph API (POST /me/sendMail)."""
        access_token = await self._refresh_token_if_needed(account)
        if not access_token:
            return False

        payload = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "HTML",
                    "content": body_html,
                },
                "toRecipients": [
                    {
                        "emailAddress": {
                            "address": account.email_address,
                        }
                    }
                ],
            },
            "saveToSentItems": "false",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://graph.microsoft.com/v1.0/me/sendMail",
                json=payload,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )

        if response.status_code == 202:
            return True

        logger.warning(
            "MS Graph sendMail failed: status=%d body=%s",
            response.status_code,
            response.text[:500],
        )
        return False

    async def _refresh_token_if_needed(self, account: EmailAccount) -> str | None:
        """Return a valid access token, refreshing if near expiry."""
        now = datetime.now(timezone.utc)

        # Check if token needs refresh
        if account.token_expiry is not None:
            expiry = account.token_expiry
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            seconds_until_expiry = (expiry - now).total_seconds()
            if seconds_until_expiry > self.REFRESH_THRESHOLD_SECONDS:
                return account.access_token

        # Need to refresh
        if not account.refresh_token:
            logger.warning(
                "Token expired and no refresh token for %s (%s)",
                account.provider,
                account.email_address,
            )
            return None

        if account.provider == "outlook":
            return await self._refresh_outlook_token(account)
        elif account.provider == "gmail":
            return await self._refresh_gmail_token(account)

        return None

    async def _refresh_outlook_token(self, account: EmailAccount) -> str | None:
        """Refresh an Outlook OAuth token via Microsoft identity platform."""
        from .._get_email_oauth_credentials import get_email_oauth_credentials

        creds = get_email_oauth_credentials()
        if not creds:
            logger.warning("No Microsoft OAuth credentials configured for token refresh")
            return None

        client_id, client_secret = creds

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": account.refresh_token,
                    "grant_type": "refresh_token",
                    "scope": "https://graph.microsoft.com/Mail.Send User.Read offline_access",
                },
            )

        if response.status_code != 200:
            logger.warning(
                "Outlook token refresh failed: status=%d body=%s",
                response.status_code,
                response.text[:500],
            )
            return None

        token_data = response.json()
        new_access_token = token_data["access_token"]
        new_refresh_token = token_data.get("refresh_token", account.refresh_token)
        expires_in = token_data.get("expires_in", 3600)

        new_expiry = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=expires_in)

        # Update DB
        try:
            async with async_session_factory() as session:
                await session.execute(
                    update(EmailAccount)
                    .where(EmailAccount.id == account.id)
                    .values(
                        access_token=new_access_token,
                        refresh_token=new_refresh_token,
                        token_expiry=new_expiry,
                    )
                )
                await session.commit()
        except Exception:
            logger.warning(
                "Failed to persist refreshed token for %s", account.email_address,
                exc_info=True,
            )

        logger.info("Refreshed Outlook token for %s", account.email_address)
        return new_access_token

    async def _send_via_gmail(
        self, account: EmailAccount, subject: str, body_html: str
    ) -> bool:
        """Send email via Gmail API (POST /gmail/v1/users/me/messages/send)."""
        access_token = await self._refresh_token_if_needed(account)
        if not access_token:
            return False

        # Build RFC 2822 message
        msg = MIMEMultipart("alternative")
        msg["From"] = account.email_address
        msg["To"] = account.email_address
        msg["Subject"] = subject
        msg.attach(MIMEText(body_html, "html"))

        # Base64url-encode the raw message
        raw_message = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                json={"raw": raw_message},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )

        if response.status_code == 200:
            return True

        logger.warning(
            "Gmail sendMessage failed: status=%d body=%s",
            response.status_code,
            response.text[:500],
        )
        return False

    async def _refresh_gmail_token(self, account: EmailAccount) -> str | None:
        """Refresh a Gmail OAuth token via Google OAuth2."""
        from .._get_email_oauth_credentials import get_google_oauth_credentials

        creds = get_google_oauth_credentials()
        if not creds:
            logger.warning("No Google OAuth credentials configured for token refresh")
            return None

        client_id, client_secret = creds

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": account.refresh_token,
                    "grant_type": "refresh_token",
                },
            )

        if response.status_code != 200:
            logger.warning(
                "Gmail token refresh failed: status=%d body=%s",
                response.status_code,
                response.text[:500],
            )
            return None

        token_data = response.json()
        new_access_token = token_data["access_token"]
        # Google may not return a new refresh token on refresh
        new_refresh_token = token_data.get("refresh_token", account.refresh_token)
        expires_in = token_data.get("expires_in", 3600)

        new_expiry = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=expires_in)

        # Update DB
        try:
            async with async_session_factory() as session:
                await session.execute(
                    update(EmailAccount)
                    .where(EmailAccount.id == account.id)
                    .values(
                        access_token=new_access_token,
                        refresh_token=new_refresh_token,
                        token_expiry=new_expiry,
                    )
                )
                await session.commit()
        except Exception:
            logger.warning(
                "Failed to persist refreshed token for %s", account.email_address,
                exc_info=True,
            )

        logger.info("Refreshed Gmail token for %s", account.email_address)
        return new_access_token


# Module-level singleton
email_service = EmailService()
