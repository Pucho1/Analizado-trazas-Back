"""Report delivery settings: which sections each channel gets, in which order."""

from dataclasses import dataclass
from enum import StrEnum


class Channel(StrEnum):
    """Where a report is delivered."""

    EMAIL = "email"
    TELEGRAM = "telegram"


@dataclass(frozen=True, slots=True)
class ChannelSettings:
    """What a channel receives.

    `enabled` turns the daily report on (with `sections` in order); `alerts_enabled`
    the alerts after each scan; `scan_summary_enabled` a short note after every scan
    (off by default: silence means everything went well).
    """

    channel: Channel
    enabled: bool
    sections: tuple[str, ...]
    alerts_enabled: bool = True
    scan_summary_enabled: bool = False

    def __post_init__(self) -> None:
        """Reject repeated sections."""
        if len(set(self.sections)) != len(self.sections):
            raise ValueError("A channel must not repeat a section")
