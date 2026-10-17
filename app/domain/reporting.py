"""Report delivery settings: which sections each channel gets, in which order."""

from dataclasses import dataclass
from enum import StrEnum


class Channel(StrEnum):
    """Where a report is delivered."""

    EMAIL = "email"
    TELEGRAM = "telegram"


@dataclass(frozen=True, slots=True)
class ChannelSettings:
    """Whether a channel receives the daily report and with which sections."""

    channel: Channel
    enabled: bool
    sections: tuple[str, ...]

    def __post_init__(self) -> None:
        """Reject repeated sections."""
        if len(set(self.sections)) != len(self.sections):
            raise ValueError("A channel must not repeat a section")
