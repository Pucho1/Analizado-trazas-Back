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
        return _TIME_RANGE_DURATIONS[self]


_TIME_RANGE_DURATIONS: dict[TimeRange, timedelta] = {
    TimeRange.LAST_24_HOURS: timedelta(hours=24),
    TimeRange.LAST_7_DAYS: timedelta(days=7),
    TimeRange.LAST_30_DAYS: timedelta(days=30),
}


@dataclass(frozen=True, slots=True)
class Trace:
    """A single execution of an agent or tool.

    `latency` is in seconds and is None when the source does not report it
    (e.g. a trace still in progress). `cost` is in USD.
    """

    id: str
    timestamp: datetime
    name: str
    latency: float | None
    cost: Decimal
    input_tokens: int
    output_tokens: int
    total_tokens: int

    def __post_init__(self) -> None:
        if self.timestamp.utcoffset() is None:
            raise ValueError("Trace timestamp must be timezone-aware")
        if self.latency is not None and self.latency < 0:
            raise ValueError("Trace latency must not be negative")
        if self.cost < 0:
            raise ValueError("Trace cost must not be negative")
        if min(self.input_tokens, self.output_tokens, self.total_tokens) < 0:
            raise ValueError("Trace token counts must not be negative")


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
        latencies = [trace.latency for trace in traces if trace.latency is not None]
        return cls(
            trace_count=len(traces),
            total_cost=sum((trace.cost for trace in traces), start=Decimal(0)),
            total_tokens=sum(trace.total_tokens for trace in traces),
            average_latency=fmean(latencies) if latencies else None,
        )
