"""Channel-neutral report content.

Sections produce these blocks (already in plain business Spanish); presenters lay them
out for each channel. Blocks carry no HTML, Markdown or emoji.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class Tone(StrEnum):
    """How a piece of content should feel: it answers "is it worrying?"."""

    NEUTRAL = "neutral"
    OK = "ok"
    ATTENTION = "attention"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        """Order of seriousness."""
        return [Tone.NEUTRAL, Tone.OK, Tone.ATTENTION, Tone.CRITICAL].index(self)


@dataclass(frozen=True, slots=True)
class Headline:
    """The key sentence about something (e.g. one profile's day)."""

    text: str
    tone: Tone = Tone.NEUTRAL


@dataclass(frozen=True, slots=True)
class Subheading:
    """Groups the blocks that follow (e.g. one profile within a section)."""

    text: str


@dataclass(frozen=True, slots=True)
class Fact:
    """A labelled value, with optional context (comparison, normal, what to do)."""

    label: str
    value: str
    detail: str | None = None
    tone: Tone = Tone.NEUTRAL


@dataclass(frozen=True, slots=True)
class Table:
    """Rows of values under column headers; the last row may be a total."""

    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    has_total_row: bool = False

    def __post_init__(self) -> None:
        """Require every row to have one value per column."""
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("Every table row needs one value per column")


@dataclass(frozen=True, slots=True)
class Note:
    """A sentence of context or advice."""

    text: str
    tone: Tone = Tone.NEUTRAL


Block = Headline | Subheading | Fact | Table | Note


@dataclass(frozen=True, slots=True)
class Section:
    """One part of a report."""

    key: str
    title: str
    blocks: tuple[Block, ...]

    @property
    def tone(self) -> Tone:
        """The most serious tone among the section's blocks."""
        tones = [block.tone for block in self.blocks if not isinstance(block, Subheading | Table)]
        return max(tones, key=lambda tone: tone.rank, default=Tone.NEUTRAL)


@dataclass(frozen=True, slots=True)
class Report:
    """A report about one day, as an ordered list of sections."""

    title: str
    day: date
    sections: tuple[Section, ...]

    @property
    def tone(self) -> Tone:
        """The most serious tone of the whole report."""
        return max((s.tone for s in self.sections), key=lambda t: t.rank, default=Tone.NEUTRAL)


@dataclass(frozen=True, slots=True)
class RenderedReport:
    """A report laid out for one channel, ready to be sent."""

    subject: str
    content_type: str
    body: str
