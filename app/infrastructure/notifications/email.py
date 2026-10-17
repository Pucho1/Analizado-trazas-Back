"""Email channel through SMTP (aiosmtplib).

Port 465 uses implicit TLS; any other port upgrades the connection with STARTTLS.
Sending happens in three steps so failures are told apart: connecting (unreachable),
logging in (credentials) and sending (recipient refused). Gmail closes the connection
instead of answering when the credentials are wrong, so a disconnection while logging
in also means bad credentials. Errors carry only a `NotificationFailure` reason.
"""

from collections.abc import Callable
from contextlib import suppress
from email.message import EmailMessage
from typing import Any, Protocol

import aiosmtplib

from app.core.constants import NOTIFICATION_TIMEOUT_SECONDS
from app.core.exceptions import NotificationError
from app.domain.notifications import NotificationFailure, OutgoingMessage

_IMPLICIT_TLS_PORT = 465
_HTML_FALLBACK = "Este informe se ve en un cliente de correo con HTML."


class SmtpClient(Protocol):
    """The part of `aiosmtplib.SMTP` the notifier uses."""

    async def connect(self) -> Any:
        """Open the connection (with TLS or STARTTLS)."""
        ...

    async def login(self, username: str, password: str) -> Any:
        """Authenticate."""
        ...

    async def send_message(self, message: EmailMessage) -> Any:
        """Send one message."""
        ...

    async def quit(self) -> Any:
        """Close the session."""
        ...


SmtpClientFactory = Callable[..., SmtpClient]


class SmtpEmailNotifier:
    """Sends each message as one email to a fixed recipient."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        sender: str,
        recipient: str,
        client_factory: SmtpClientFactory = aiosmtplib.SMTP,
    ) -> None:
        """Connect to `host`:`port` as `username`; `client_factory` is replaceable in tests."""
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender
        self._recipient = recipient
        self._client_factory = client_factory

    async def send(self, message: OutgoingMessage) -> None:
        """Send `message` (HTML with a plain-text fallback, or plain text)."""
        implicit_tls = self._port == _IMPLICIT_TLS_PORT
        smtp = self._client_factory(
            hostname=self._host,
            port=self._port,
            use_tls=implicit_tls,
            start_tls=not implicit_tls,
            timeout=NOTIFICATION_TIMEOUT_SECONDS,
        )
        await self._connect(smtp)
        try:
            await self._login(smtp)
            await self._send(smtp, self._email(message))
        finally:
            await _quit_quietly(smtp)

    def _email(self, message: OutgoingMessage) -> EmailMessage:
        """The email for `message`."""
        email = EmailMessage()
        email["Subject"] = message.subject
        email["From"] = self._sender
        email["To"] = self._recipient
        if message.content_type.startswith("text/html"):
            email.set_content(_HTML_FALLBACK)
            email.add_alternative(message.body, subtype="html")
        else:
            email.set_content(message.body)
        return email

    @staticmethod
    async def _connect(smtp: SmtpClient) -> None:
        """Open the connection; any failure means the server is unreachable."""
        try:
            await smtp.connect()
        except (aiosmtplib.SMTPException, OSError) as error:
            raise NotificationError(
                NotificationFailure.UNAVAILABLE, "The SMTP server could not be reached"
            ) from error

    async def _login(self, smtp: SmtpClient) -> None:
        """Authenticate; a rejection or a disconnection here means bad credentials."""
        try:
            await smtp.login(self._username, self._password)
        except (aiosmtplib.SMTPAuthenticationError, aiosmtplib.SMTPServerDisconnected) as error:
            raise NotificationError(
                NotificationFailure.AUTH_FAILED, "The SMTP server rejected the credentials"
            ) from error
        except (aiosmtplib.SMTPException, OSError) as error:
            raise NotificationError(
                NotificationFailure.UNAVAILABLE, "The SMTP server failed while logging in"
            ) from error

    @staticmethod
    async def _send(smtp: SmtpClient, email: EmailMessage) -> None:
        """Send the email; refused sender or recipient means the message was rejected."""
        try:
            await smtp.send_message(email)
        except (aiosmtplib.SMTPRecipientsRefused, aiosmtplib.SMTPSenderRefused) as error:
            raise NotificationError(
                NotificationFailure.REJECTED, "The SMTP server refused the sender or recipient"
            ) from error
        except (aiosmtplib.SMTPException, OSError) as error:
            raise NotificationError(
                NotificationFailure.UNAVAILABLE, "The SMTP server failed while sending"
            ) from error


async def _quit_quietly(smtp: SmtpClient) -> None:
    """Close the session, ignoring errors (the connection may already be gone)."""
    with suppress(aiosmtplib.SMTPException, OSError):
        await smtp.quit()
