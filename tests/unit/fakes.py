from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from app.domain.models import Trace

DEFAULT_TIMESTAMP = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def make_trace(
    *,
    id: str = "trace-1",
    timestamp: datetime = DEFAULT_TIMESTAMP,
    name: str = "agent",
    latency: float | None = 1.0,
    cost: Decimal = Decimal("0.01"),
    input_tokens: int = 10,
    output_tokens: int = 5,
    total_tokens: int | None = None,
) -> Trace:
    return Trace(
        id=id,
        timestamp=timestamp,
        name=name,
        latency=latency,
        cost=cost,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens if total_tokens is None else total_tokens,
    )


class InMemoryTraceSource:
    """TraceSource backed by a fixed list; records the windows it was asked for."""

    def __init__(self, traces: Sequence[Trace] = ()) -> None:
        self._traces = list(traces)
        self.calls: list[tuple[datetime, datetime]] = []

    async def list_traces(self, start: datetime, end: datetime) -> Sequence[Trace]:
        self.calls.append((start, end))
        return [trace for trace in self._traces if start <= trace.timestamp <= end]


class FailingTraceSource:
    """TraceSource that always raises the given error."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def list_traces(self, start: datetime, end: datetime) -> Sequence[Trace]:
        raise self._error
