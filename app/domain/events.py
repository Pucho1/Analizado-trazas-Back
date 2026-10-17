"""Business events: steps of the agent that mean something to the business.

Each profile maps step names to business labels (many names may share a label, e.g.
two spellings of the same event). Mapped steps count as events whatever their level;
unmapped steps at WARNING or ERROR level count as technical warnings or errors; any
other step is plumbing and is ignored. The mapping starts empty: the user decides.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.domain.models import ObservationLevel


@dataclass(frozen=True, slots=True)
class EventMapping:
    """Steps named `observation_name` are the business event `label`."""

    observation_name: str
    label: str

    def __post_init__(self) -> None:
        """Reject blank names and labels."""
        if not self.observation_name.strip() or not self.label.strip():
            raise ValueError("An event mapping needs a step name and a label")


@dataclass(frozen=True, slots=True)
class StepCount:
    """How often steps with one name and level ran, and in which queries."""

    name: str
    level: ObservationLevel
    occurrences: int
    trace_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class EventCount:
    """How often something happened and in how many distinct queries."""

    label: str
    occurrences: int
    queries: int


@dataclass(frozen=True, slots=True)
class EventSummary:
    """Business events (most frequent first) and technical problems of a period."""

    events: tuple[EventCount, ...]
    technical_errors: EventCount
    technical_warnings: EventCount

    @property
    def empty(self) -> bool:
        """Whether there is nothing to report."""
        return (
            not self.events
            and self.technical_errors.occurrences == 0
            and self.technical_warnings.occurrences == 0
        )


TECHNICAL_ERRORS_LABEL = "Errores técnicos"
TECHNICAL_WARNINGS_LABEL = "Avisos técnicos"


def summarize_events(steps: Iterable[StepCount], mappings: Sequence[EventMapping]) -> EventSummary:
    """Turn step counts into business events and technical problems.

    Queries are counted once per label even when several mapped names (or the same
    name at several levels) occur in the same query.
    """
    label_of = {mapping.observation_name: mapping.label for mapping in mappings}
    occurrences: dict[str, int] = defaultdict(int)
    queries: dict[str, set[str]] = defaultdict(set)
    errors, warnings = _Accumulator(), _Accumulator()
    for step in steps:
        label = label_of.get(step.name)
        if label is not None:
            occurrences[label] += step.occurrences
            queries[label] |= step.trace_ids
        elif step.level is ObservationLevel.ERROR:
            errors.add(step)
        elif step.level is ObservationLevel.WARNING:
            warnings.add(step)
    events = sorted(
        (EventCount(label, occurrences[label], len(queries[label])) for label in occurrences),
        key=lambda event: (-event.occurrences, event.label),
    )
    return EventSummary(
        events=tuple(events),
        technical_errors=errors.count(TECHNICAL_ERRORS_LABEL),
        technical_warnings=warnings.count(TECHNICAL_WARNINGS_LABEL),
    )


class _Accumulator:
    """Running occurrences and distinct queries of one kind of step."""

    def __init__(self) -> None:
        """Start empty."""
        self.occurrences = 0
        self.trace_ids: set[str] = set()

    def add(self, step: StepCount) -> None:
        """Add a step count."""
        self.occurrences += step.occurrences
        self.trace_ids |= step.trace_ids

    def count(self, label: str) -> EventCount:
        """The accumulated count under `label`."""
        return EventCount(label, self.occurrences, len(self.trace_ids))
