"""Notifications: messages sent through a channel and the record of each delivery."""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from app.domain.reporting import Channel


class DeliveryKind(StrEnum):
    """What a delivery carried."""

    DAILY_REPORT = "daily_report"
    ALERTS = "alerts"
    SCAN_SUMMARY = "scan_summary"
    TEST = "test"


class DeliveryStatus(StrEnum):
    """Whether a delivery reached the channel."""

    SENT = "sent"
    FAILED = "failed"


class NotificationFailure(StrEnum):
    """Why a delivery failed; stored instead of the provider's message."""

    # The channel's variables are missing from `.env`.
    NOT_CONFIGURED = "not_configured"
    # The provider rejected the credentials (SMTP password, bot token).
    AUTH_FAILED = "auth_failed"
    # The provider refused the message (unknown recipient or chat, bot blocked...).
    REJECTED = "rejected"
    # The provider could not be reached or failed.
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class OutgoingMessage:
    """What a notifier sends: a presenter's output."""

    subject: str
    body: str
    content_type: str


@dataclass(frozen=True, slots=True)
class Delivery:
    """One attempt to send something through a channel.

    `day` is the day a daily report is about; `error_code` is set only on failures.
    """

    channel: Channel
    kind: DeliveryKind
    status: DeliveryStatus
    created_at: datetime
    day: date | None = None
    error_code: NotificationFailure | None = None
    id: int | None = None

    def __post_init__(self) -> None:
        """Require an error code exactly on failures, and a day on daily reports."""
        if (self.status is DeliveryStatus.FAILED) != (self.error_code is not None):
            raise ValueError("A delivery has an error code if and only if it failed")
        if (self.kind is DeliveryKind.DAILY_REPORT) != (self.day is not None):
            raise ValueError("A delivery has a day if and only if it is a daily report")


@dataclass(frozen=True, slots=True)
class TelegramChat:
    """A chat that wrote to the bot: where it can send messages."""

    id: str
    name: str
    kind: str
