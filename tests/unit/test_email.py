"""SMTP email adapter with a fake SMTP client (no network)."""

from email.message import EmailMessage
from typing import Any

import aiosmtplib
import pytest

from app.core.exceptions import NotificationError
from app.domain.notifications import OutgoingMessage
from app.infrastructure.notifications.email import SmtpEmailNotifier

pytestmark = pytest.mark.anyio

PASSWORD = "abcd efgh ijkl mnop"
HTML = OutgoingMessage("[A vigilar] Observa", "<p>Hola</p>", "text/html; charset=utf-8")


class FakeSmtp:
    """Records the session; raises `error` at the step named `fail_at`."""

    def __init__(self, fail_at: str | None = None, error: Exception | None = None) -> None:
        """Succeed unless told where and how to fail."""
        self.fail_at = fail_at
        self.error = error
        self.options: dict[str, Any] = {}
        self.steps: list[str] = []
        self.sent: list[EmailMessage] = []
        self.credentials: tuple[str, str] | None = None

    def __call__(self, **options: Any) -> FakeSmtp:
        """Act as the client factory."""
        self.options = options
        return self

    def _step(self, name: str) -> None:
        """Record a step, failing there if asked to."""
        self.steps.append(name)
        if self.fail_at == name and self.error is not None:
            raise self.error

    async def connect(self) -> None:
        """Pretend to connect."""
        self._step("connect")

    async def login(self, username: str, password: str) -> None:
        """Pretend to authenticate."""
        self.credentials = (username, password)
        self._step("login")

    async def send_message(self, message: EmailMessage) -> None:
        """Pretend to send."""
        self._step("send")
        self.sent.append(message)

    async def quit(self) -> None:
        """Pretend to close; may fail too."""
        self._step("quit")


def notifier(smtp: FakeSmtp, port: int = 587) -> SmtpEmailNotifier:
    """Notifier for a Gmail-like account."""
    return SmtpEmailNotifier(
        host="smtp.gmail.com",
        port=port,
        username="yo@gmail.com",
        password=PASSWORD,
        sender="Observa <yo@gmail.com>",
        recipient="yo@gmail.com",
        client_factory=smtp,
    )


async def test_sends_html_with_a_plain_fallback_over_starttls() -> None:
    smtp = FakeSmtp()

    await notifier(smtp).send(HTML)

    assert smtp.steps == ["connect", "login", "send", "quit"]
    assert smtp.credentials == ("yo@gmail.com", PASSWORD)
    assert smtp.options["hostname"] == "smtp.gmail.com"
    assert (smtp.options["port"], smtp.options["use_tls"], smtp.options["start_tls"]) == (
        587,
        False,
        True,
    )
    (message,) = smtp.sent
    assert (message["Subject"], message["From"], message["To"]) == (
        "[A vigilar] Observa",
        "Observa <yo@gmail.com>",
        "yo@gmail.com",
    )
    html = message.get_body(("html",))
    assert html is not None
    assert "<p>Hola</p>" in html.get_content()


async def test_port_465_uses_implicit_tls() -> None:
    smtp = FakeSmtp()

    await notifier(smtp, port=465).send(HTML)

    assert (smtp.options["use_tls"], smtp.options["start_tls"]) == (True, False)


async def test_plain_text_message() -> None:
    smtp = FakeSmtp()

    await notifier(smtp).send(OutgoingMessage("s", "hola", "text/plain"))

    (message,) = smtp.sent
    assert not message.is_multipart()
    assert message.get_content().strip() == "hola"


@pytest.mark.parametrize(
    ("step", "error", "reason"),
    [
        ("connect", aiosmtplib.SMTPConnectError("refused"), "unavailable"),
        ("connect", OSError("network down"), "unavailable"),
        ("login", aiosmtplib.SMTPAuthenticationError(535, f"bad {PASSWORD}"), "auth_failed"),
        # What Gmail really does with wrong credentials: it hangs up.
        ("login", aiosmtplib.SMTPServerDisconnected("Unexpected EOF received"), "auth_failed"),
        ("login", aiosmtplib.SMTPResponseException(454, "try later"), "unavailable"),
        ("send", aiosmtplib.SMTPRecipientsRefused([]), "rejected"),
        ("send", aiosmtplib.SMTPSenderRefused(550, "no", "yo@gmail.com"), "rejected"),
        ("send", aiosmtplib.SMTPDataError(554, "spam"), "unavailable"),
    ],
)
async def test_each_step_fails_with_its_reason(step: str, error: Exception, reason: str) -> None:
    email = notifier(FakeSmtp(step, error))

    with pytest.raises(NotificationError) as raised:
        await email.send(HTML)

    assert raised.value.reason == reason
    assert PASSWORD not in str(raised.value)


async def test_the_session_is_closed_after_a_failure_and_quit_errors_are_ignored() -> None:
    after_login = FakeSmtp("login", aiosmtplib.SMTPAuthenticationError(535, "no"))
    email = notifier(after_login)
    with pytest.raises(NotificationError):
        await email.send(HTML)
    assert after_login.steps == ["connect", "login", "quit"]

    broken_quit = FakeSmtp("quit", aiosmtplib.SMTPServerDisconnected("gone"))
    await notifier(broken_quit).send(HTML)
    assert broken_quit.sent
