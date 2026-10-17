"""Synchronisation of traces from a source (Langfuse) into the repository (PostgreSQL)."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.application.trace_service import utc_now
from app.core.exceptions import (
    RepositoryError,
    SyncAlreadyRunningError,
    TraceSourceAuthenticationError,
    TraceSourceError,
)
from app.domain.models import SyncFailureReason, SyncRun, SyncStatus, SyncTrigger
from app.domain.ports import SyncRunRepository, TraceRepository, TraceSource

# Runs after every successful sync (e.g. recompute totals, check alert thresholds).
AfterSyncHook = Callable[[SyncRun], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class SyncState:
    """Whether a sync is running now, the last run and the last successful one."""

    running: bool
    last_run: SyncRun | None
    last_successful_run: SyncRun | None


class SyncService:
    """Copies the traces of one source into the repository, one window at a time.

    Each run reads from where the last successful one ended (the marker) minus
    `overlap`, up to now; the first run reads `initial_lookback` back. Saving is
    idempotent, so overlapping windows never duplicate traces. A failed run is
    recorded but does not move the marker, so the next run covers the gap. Only one
    run happens at a time.
    """

    def __init__(
        self,
        *,
        source_name: str,
        source: TraceSource,
        traces: TraceRepository,
        runs: SyncRunRepository,
        overlap: timedelta,
        initial_lookback: timedelta,
        clock: Callable[[], datetime] = utc_now,
        after_sync: Sequence[AfterSyncHook] = (),
    ) -> None:
        """Sync `source` (recorded as `source_name`) into `traces`, logging into `runs`."""
        self._source_name = source_name
        self._source = source
        self._traces = traces
        self._runs = runs
        self._overlap = overlap
        self._initial_lookback = initial_lookback
        self._clock = clock
        self._after_sync = tuple(after_sync)
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        """Whether a sync is in progress right now."""
        return self._lock.locked()

    async def sync(self, trigger: SyncTrigger) -> SyncRun:
        """Run one sync and return it.

        Raises `SyncAlreadyRunningError` if another one is in progress. If reading or
        saving fails, the failed run is recorded and the original error is re-raised.
        """
        if self._lock.locked():
            raise SyncAlreadyRunningError("A synchronisation is already running")
        async with self._lock:
            return await self._run(trigger)

    async def sync_if_never_synced(self) -> SyncRun | None:
        """Run a scheduled sync only if no sync of this source ever succeeded."""
        if await self._runs.latest_successful(self._source_name) is not None:
            return None
        return await self.sync(SyncTrigger.SCHEDULED)

    async def state(self) -> SyncState:
        """Current state: running flag, last run and last successful run."""
        return SyncState(
            running=self.running,
            last_run=await self._runs.latest(self._source_name),
            last_successful_run=await self._runs.latest_successful(self._source_name),
        )

    async def _run(self, trigger: SyncTrigger) -> SyncRun:
        """Read the window, save it, and record the outcome."""
        started_at = self._clock()
        window_start = await self._window_start(started_at)
        trace_count = 0
        failure: tuple[SyncFailureReason, Exception] | None = None
        try:
            traces = await self._source.list_traces(window_start, started_at)
            await self._traces.save_traces(traces)
            trace_count = len(traces)
        except (TraceSourceError, RepositoryError) as error:
            failure = (_failure_reason(error), error)

        run = SyncRun(
            source=self._source_name,
            trigger=trigger,
            status=SyncStatus.SUCCEEDED if failure is None else SyncStatus.FAILED,
            started_at=started_at,
            finished_at=self._clock(),
            window_start=window_start,
            window_end=started_at,
            trace_count=trace_count,
            failure_reason=None if failure is None else failure[0],
        )
        await self._runs.save(run)
        if failure is not None:
            raise failure[1]
        for hook in self._after_sync:
            await hook(run)
        return run

    async def _window_start(self, now: datetime) -> datetime:
        """Marker minus the overlap, or `initial_lookback` before now on the first run."""
        marker = await self._runs.latest_successful(self._source_name)
        if marker is None:
            return now - self._initial_lookback
        return min(marker.window_end - self._overlap, now)


def _failure_reason(error: Exception) -> SyncFailureReason:
    """Classify a sync error into the reason stored with the run."""
    if isinstance(error, TraceSourceAuthenticationError):
        return SyncFailureReason.TRACE_SOURCE_AUTH_FAILED
    if isinstance(error, TraceSourceError):
        return SyncFailureReason.TRACE_SOURCE_UNAVAILABLE
    return SyncFailureReason.DATABASE_UNAVAILABLE
