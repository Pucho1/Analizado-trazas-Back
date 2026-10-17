from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from statistics import fmean


class TimeRange(StrEnum):
    """Relative time window, ending now, over which traces are analysed."""

    LAST_24_HOURS = "24h"
    LAST_7_DAYS = "7d"
    LAST_30_DAYS = "30d"

    @property
    def duration(self) -> timedelta:
        """Length of the window."""
        return _TIME_RANGE_DURATIONS[self]


_TIME_RANGE_DURATIONS: dict[TimeRange, timedelta] = {
    TimeRange.LAST_24_HOURS: timedelta(hours=24),
    TimeRange.LAST_7_DAYS: timedelta(days=7),
    TimeRange.LAST_30_DAYS: timedelta(days=30),
}


class ObservationLevel(StrEnum):
    """Severity that the source assigns to an observation."""

    DEBUG = "DEBUG"
    DEFAULT = "DEFAULT"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class Observation:
    """One step inside a trace (a span, an LLM call, an event...).

    Only metadata is kept, never its input or output. `type` is free text because
    sources keep adding kinds of steps (SPAN, GENERATION, TOOL, ...).
    """

    id: str
    name: str | None
    type: str
    level: ObservationLevel
    model: str | None
    start_time: datetime

    def __post_init__(self) -> None:
        """Reject naive start times."""
        if self.start_time.utcoffset() is None:
            raise ValueError("Observation start_time must be timezone-aware")


@dataclass(frozen=True, slots=True)
class Trace:
    """A single execution of an agent or tool.

    `latency` is in seconds and is None when the source does not report it
    (e.g. a trace still in progress). `cost` is in USD. `tags` are sorted and
    `observations` are ordered by start time.
    """

    id: str
    timestamp: datetime
    name: str
    environment: str
    latency: float | None
    cost: Decimal
    input_tokens: int
    output_tokens: int
    total_tokens: int
    tags: tuple[str, ...] = ()
    observations: tuple[Observation, ...] = ()

    def __post_init__(self) -> None:
        """Reject naive timestamps and negative latency, cost or token counts."""
        if self.timestamp.utcoffset() is None:
            raise ValueError("Trace timestamp must be timezone-aware")
        if self.latency is not None and self.latency < 0:
            raise ValueError("Trace latency must not be negative")
        if self.cost < 0:
            raise ValueError("Trace cost must not be negative")
        if min(self.input_tokens, self.output_tokens, self.total_tokens) < 0:
            raise ValueError("Trace token counts must not be negative")


@dataclass(frozen=True, slots=True)
class TraceKey:
    """The attributes of a stored trace that profiles classify on, plus its profile."""

    id: str
    environment: str
    name: str
    tags: tuple[str, ...]
    profile_id: int | None = None


@dataclass(frozen=True, slots=True)
class ProfileRule:
    """Which traces belong to a profile.

    A trace matches when its environment is `environment`, its name is `trace_name`
    (any name if None) and it carries every tag in `tags` (it may carry more). Tags
    are stored sorted and without repetitions.
    """

    environment: str
    trace_name: str | None = None
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Reject blank values and normalise the tags."""
        if not self.environment.strip():
            raise ValueError("A profile rule needs an environment")
        if self.trace_name is not None and not self.trace_name.strip():
            raise ValueError("A profile rule's trace name must not be blank")
        if any(not tag.strip() for tag in self.tags):
            raise ValueError("A profile rule's tags must not be blank")
        object.__setattr__(self, "tags", tuple(sorted(set(self.tags))))

    @property
    def specificity(self) -> int:
        """How many criteria beyond the environment the rule sets (name + each tag)."""
        return (self.trace_name is not None) + len(self.tags)

    def matches(self, trace: TraceKey) -> bool:
        """Whether `trace` belongs to a profile with this rule."""
        return (
            trace.environment == self.environment
            and (self.trace_name is None or trace.name == self.trace_name)
            and set(self.tags) <= set(trace.tags)
        )


class ProfileStatus(StrEnum):
    """Whether a profile is being monitored."""

    ACTIVE = "active"
    # No longer monitored; keeps its history and keeps receiving its new traces.
    ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class Profile:
    """A group of traces that stands for one tool or agent, with a business name."""

    id: int
    name: str
    rule: ProfileRule
    status: ProfileStatus
    created_at: datetime
    archived_at: datetime | None = None

    def __post_init__(self) -> None:
        """Require a name and an archive date exactly when archived."""
        if not self.name.strip():
            raise ValueError("A profile needs a name")
        if (self.status is ProfileStatus.ARCHIVED) != (self.archived_at is not None):
            raise ValueError("A profile has an archive date if and only if it is archived")


def classify(traces: Iterable[TraceKey], profiles: Sequence[Profile]) -> dict[str, int | None]:
    """Profile id for each trace id; None means "unclassified".

    When several profiles match, the most specific rule wins; on a tie, the oldest
    profile. Archived profiles still classify their traces.
    """
    by_priority = sorted(
        profiles, key=lambda profile: (-profile.rule.specificity, profile.created_at, profile.id)
    )
    return {
        trace.id: next((profile.id for profile in by_priority if profile.rule.matches(trace)), None)
        for trace in traces
    }


@dataclass(frozen=True, slots=True)
class DiscoveredGroup:
    """Unclassified traces sharing environment, name and tags: a candidate profile."""

    environment: str
    trace_name: str
    tags: tuple[str, ...]
    trace_count: int
    first_seen: datetime
    last_seen: datetime
    total_cost: Decimal

    @property
    def rule(self) -> ProfileRule:
        """The rule that would capture exactly this group."""
        return ProfileRule(environment=self.environment, trace_name=self.trace_name, tags=self.tags)


class SyncTrigger(StrEnum):
    """What started a synchronisation."""

    # Automatic: one of the daily scans, or the first sync at startup.
    SCHEDULED = "scheduled"
    # Requested by the user ("sync now").
    MANUAL = "manual"


class SyncStatus(StrEnum):
    """Outcome of a synchronisation."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class SyncFailureReason(StrEnum):
    """Why a synchronisation failed; stored instead of internal error details."""

    TRACE_SOURCE_AUTH_FAILED = "trace_source_auth_failed"
    TRACE_SOURCE_UNAVAILABLE = "trace_source_unavailable"
    DATABASE_UNAVAILABLE = "database_unavailable"


@dataclass(frozen=True, slots=True)
class SyncRun:
    """One synchronisation of traces from a source into the repository.

    It read the traces that started within [window_start, window_end]. A failed run
    has a `failure_reason`; a successful one does not.
    """

    source: str
    trigger: SyncTrigger
    status: SyncStatus
    started_at: datetime
    finished_at: datetime
    window_start: datetime
    window_end: datetime
    trace_count: int
    failure_reason: SyncFailureReason | None = None

    def __post_init__(self) -> None:
        """Keep status and failure reason consistent and the window well formed."""
        if (self.status is SyncStatus.FAILED) != (self.failure_reason is not None):
            raise ValueError("A sync run has a failure reason if and only if it failed")
        if self.window_start > self.window_end:
            raise ValueError("Sync window must not end before it starts")
        if self.trace_count < 0:
            raise ValueError("Sync trace count must not be negative")


@dataclass(frozen=True, slots=True)
class Summary:
    """Aggregated metrics over a set of traces.

    `average_latency` only considers traces that report a latency and is None
    when none of them do.
    """

    trace_count: int
    total_cost: Decimal
    total_tokens: int
    average_latency: float | None

    @classmethod
    def from_traces(cls, traces: Sequence[Trace]) -> Summary:
        """Aggregate the given traces; an empty sequence yields zeros and no latency."""
        latencies = [trace.latency for trace in traces if trace.latency is not None]
        return cls(
            trace_count=len(traces),
            total_cost=sum((trace.cost for trace in traces), start=Decimal(0)),
            total_tokens=sum(trace.total_tokens for trace in traces),
            average_latency=fmean(latencies) if latencies else None,
        )
