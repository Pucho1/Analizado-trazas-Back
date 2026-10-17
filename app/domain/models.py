from collections.abc import Sequence
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
