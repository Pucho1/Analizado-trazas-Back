from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from app.domain.models import Summary, TimeRange, Trace
from app.domain.ports import TraceRepository


def utc_now() -> datetime:
    """Current moment as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


class TraceService:
    """Use cases for listing traces and summarising them over a time range.

    Reads from the repository, which the synchronisation keeps up to date.
    """

    def __init__(
        self, repository: TraceRepository, clock: Callable[[], datetime] = utc_now
    ) -> None:
        """Use `repository` to read traces and `clock` to know when "now" is."""
        self._repository = repository
        self._clock = clock

    async def list_traces(self, time_range: TimeRange) -> list[Trace]:
        """Return the traces in the range, most recent first."""
        traces = await self._fetch(time_range)
        return sorted(traces, key=lambda trace: trace.timestamp, reverse=True)

    async def get_summary(self, time_range: TimeRange) -> Summary:
        """Return the aggregated metrics of the traces in the range."""
        return Summary.from_traces(await self._fetch(time_range))

    async def _fetch(self, time_range: TimeRange) -> Sequence[Trace]:
        """Read the stored traces of the window that ends now."""
        end = self._clock()
        start = end - time_range.duration
        return await self._repository.list_traces(start, end)
