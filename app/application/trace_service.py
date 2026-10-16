from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from app.domain.models import Summary, TimeRange, Trace
from app.domain.ports import TraceSource


def utc_now() -> datetime:
    """Current moment as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


class TraceService:
    """Use cases for listing traces and summarising them over a time range."""

    def __init__(self, source: TraceSource, clock: Callable[[], datetime] = utc_now) -> None:
        """Use `source` to read traces and `clock` to know when "now" is (injectable in tests)."""
        self._source = source
        self._clock = clock

    async def list_traces(self, time_range: TimeRange) -> list[Trace]:
        """Return the traces in the range, most recent first."""
        traces = await self._fetch(time_range)
        return sorted(traces, key=lambda trace: trace.timestamp, reverse=True)

    async def get_summary(self, time_range: TimeRange) -> Summary:
        """Return the aggregated metrics of the traces in the range."""
        return Summary.from_traces(await self._fetch(time_range))

    async def _fetch(self, time_range: TimeRange) -> Sequence[Trace]:
        """Read from the source the traces of the window that ends now."""
        end = self._clock()
        start = end - time_range.duration
        return await self._source.list_traces(start, end)
