import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from app.application.sync_service import AfterSyncHook, SyncService
from app.core.exceptions import (
    RepositoryError,
    SyncAlreadyRunningError,
    TraceSourceAuthenticationError,
    TraceSourceError,
)
from app.domain.models import SyncFailureReason, SyncRun, SyncStatus, SyncTrigger, Trace
from app.domain.ports import SyncRunRepository, TraceRepository, TraceSource
from tests.unit.fakes import (
    FailingTraces,
    InMemorySyncRunRepository,
    InMemoryTraceRepository,
    InMemoryTraceSource,
    make_trace,
)

pytestmark = pytest.mark.anyio

SOURCE = "langfuse"
NOW = datetime(2026, 10, 17, 14, 0, tzinfo=UTC)
OVERLAP = timedelta(hours=1)
LOOKBACK = timedelta(days=30)


class Clock:
    """Clock that returns `now` and can be moved forward."""

    def __init__(self, now: datetime = NOW) -> None:
        """Start at `now`."""
        self.now = now

    def __call__(self) -> datetime:
        """Current fake time."""
        return self.now


def make_run(
    *,
    window_end: datetime,
    status: SyncStatus = SyncStatus.SUCCEEDED,
    source: str = SOURCE,
) -> SyncRun:
    """A finished run whose window ends at `window_end`."""
    return SyncRun(
        source=source,
        trigger=SyncTrigger.SCHEDULED,
        status=status,
        started_at=window_end,
        finished_at=window_end,
        window_start=window_end - timedelta(hours=6),
        window_end=window_end,
        trace_count=0,
        failure_reason=(
            SyncFailureReason.TRACE_SOURCE_UNAVAILABLE if status is SyncStatus.FAILED else None
        ),
    )


def make_service(
    *,
    source: TraceSource | None = None,
    traces: TraceRepository | None = None,
    runs: SyncRunRepository | None = None,
    clock: Clock | None = None,
    after_sync: Sequence[AfterSyncHook] = (),
) -> SyncService:
    """Service with in-memory collaborators unless given."""
    return SyncService(
        source_name=SOURCE,
        source=source or InMemoryTraceSource(),
        traces=traces or InMemoryTraceRepository(),
        runs=runs or InMemorySyncRunRepository(),
        overlap=OVERLAP,
        initial_lookback=LOOKBACK,
        clock=clock or Clock(),
        after_sync=after_sync,
    )


async def test_first_sync_reads_initial_lookback_until_now() -> None:
    source = InMemoryTraceSource()

    run = await make_service(source=source).sync(SyncTrigger.MANUAL)

    assert source.calls == [(NOW - LOOKBACK, NOW)]
    assert (run.window_start, run.window_end) == (NOW - LOOKBACK, NOW)


async def test_next_sync_starts_at_marker_minus_overlap() -> None:
    marker = NOW - timedelta(hours=6)
    source = InMemoryTraceSource()
    runs = InMemorySyncRunRepository([make_run(window_end=marker)])

    await make_service(source=source, runs=runs).sync(SyncTrigger.SCHEDULED)

    assert source.calls == [(marker - OVERLAP, NOW)]


async def test_marker_is_the_successful_run_that_reached_furthest() -> None:
    source = InMemoryTraceSource()
    runs = InMemorySyncRunRepository(
        [
            make_run(window_end=NOW - timedelta(hours=12)),
            make_run(window_end=NOW - timedelta(hours=6)),
            make_run(window_end=NOW - timedelta(hours=1), status=SyncStatus.FAILED),
            make_run(window_end=NOW - timedelta(minutes=5), source="langsmith"),
        ]
    )

    await make_service(source=source, runs=runs).sync(SyncTrigger.SCHEDULED)

    assert source.calls == [(NOW - timedelta(hours=6) - OVERLAP, NOW)]


async def test_window_never_starts_in_the_future() -> None:
    source = InMemoryTraceSource()
    runs = InMemorySyncRunRepository([make_run(window_end=NOW + timedelta(hours=3))])

    await make_service(source=source, runs=runs).sync(SyncTrigger.MANUAL)

    assert source.calls == [(NOW, NOW)]


async def test_saves_the_traces_read_and_records_a_successful_run() -> None:
    inside = make_trace(id="inside", timestamp=NOW - timedelta(hours=1))
    future = make_trace(id="future", timestamp=NOW + timedelta(hours=1))
    repository = InMemoryTraceRepository()
    runs = InMemorySyncRunRepository()
    clock = Clock()

    class SlowSource(InMemoryTraceSource):
        async def list_traces(self, start: datetime, end: datetime) -> Sequence[Trace]:
            """Take three seconds of fake time to answer."""
            clock.now = NOW + timedelta(seconds=3)
            return await super().list_traces(start, end)

    service = make_service(
        source=SlowSource([inside, future]), traces=repository, runs=runs, clock=clock
    )

    run = await service.sync(SyncTrigger.MANUAL)

    assert repository.saves == [[inside]]
    assert run == SyncRun(
        source=SOURCE,
        trigger=SyncTrigger.MANUAL,
        status=SyncStatus.SUCCEEDED,
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=3),
        window_start=NOW - LOOKBACK,
        window_end=NOW,
        trace_count=1,
    )
    assert runs.runs == [run]


async def test_syncing_overlapping_windows_does_not_duplicate() -> None:
    trace = make_trace(id="a", timestamp=NOW - timedelta(minutes=30))
    repository = InMemoryTraceRepository()
    clock = Clock()
    service = make_service(source=InMemoryTraceSource([trace]), traces=repository, clock=clock)

    await service.sync(SyncTrigger.SCHEDULED)
    clock.now = NOW + timedelta(minutes=10)
    await service.sync(SyncTrigger.SCHEDULED)

    assert repository.traces == [trace]


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (TraceSourceAuthenticationError("bad keys"), SyncFailureReason.TRACE_SOURCE_AUTH_FAILED),
        (TraceSourceError("HTTP 500"), SyncFailureReason.TRACE_SOURCE_UNAVAILABLE),
    ],
)
async def test_source_failure_is_recorded_and_raised(
    error: Exception, reason: SyncFailureReason
) -> None:
    repository = InMemoryTraceRepository()
    runs = InMemorySyncRunRepository()
    service = make_service(source=FailingTraces(error), traces=repository, runs=runs)

    with pytest.raises(type(error)):
        await service.sync(SyncTrigger.MANUAL)

    (run,) = runs.runs
    assert (run.status, run.failure_reason, run.trace_count) == (SyncStatus.FAILED, reason, 0)
    assert repository.saves == []


async def test_save_failure_is_recorded_and_raised() -> None:
    runs = InMemorySyncRunRepository()
    service = make_service(
        source=InMemoryTraceSource([make_trace(timestamp=NOW)]),
        traces=FailingTraces(RepositoryError("disk full")),
        runs=runs,
    )

    with pytest.raises(RepositoryError):
        await service.sync(SyncTrigger.MANUAL)

    (run,) = runs.runs
    assert run.failure_reason is SyncFailureReason.DATABASE_UNAVAILABLE


async def test_failed_run_does_not_move_the_marker() -> None:
    marker = NOW - timedelta(hours=6)
    runs = InMemorySyncRunRepository([make_run(window_end=marker)])
    clock = Clock()
    failing = make_service(source=FailingTraces(TraceSourceError("down")), runs=runs, clock=clock)
    with pytest.raises(TraceSourceError):
        await failing.sync(SyncTrigger.SCHEDULED)
    source = InMemoryTraceSource()
    clock.now = NOW + timedelta(hours=6)

    await make_service(source=source, runs=runs, clock=clock).sync(SyncTrigger.SCHEDULED)

    assert source.calls == [(marker - OVERLAP, NOW + timedelta(hours=6))]


async def test_only_one_sync_runs_at_a_time() -> None:
    release = asyncio.Event()

    class BlockingSource:
        async def list_traces(self, start: datetime, end: datetime) -> Sequence[Trace]:
            """Wait until released."""
            await release.wait()
            return []

    service = make_service(source=BlockingSource())
    first = asyncio.create_task(service.sync(SyncTrigger.SCHEDULED))
    await asyncio.sleep(0)

    assert service.running
    with pytest.raises(SyncAlreadyRunningError):
        await service.sync(SyncTrigger.MANUAL)

    release.set()
    run = await first
    assert run.status is SyncStatus.SUCCEEDED
    assert not service.running


async def test_after_sync_hooks_run_only_after_success() -> None:
    seen: list[SyncRun] = []

    async def hook(run: SyncRun) -> None:
        seen.append(run)

    run = await make_service(after_sync=[hook]).sync(SyncTrigger.MANUAL)
    failing = make_service(source=FailingTraces(TraceSourceError("down")), after_sync=[hook])
    with pytest.raises(TraceSourceError):
        await failing.sync(SyncTrigger.MANUAL)

    assert seen == [run]


async def test_initial_sync_runs_when_never_synced() -> None:
    runs = InMemorySyncRunRepository([make_run(window_end=NOW, status=SyncStatus.FAILED)])

    run = await make_service(runs=runs).sync_if_never_synced()

    assert run is not None
    assert run.trigger is SyncTrigger.SCHEDULED


async def test_initial_sync_is_skipped_after_a_successful_sync() -> None:
    source = InMemoryTraceSource()
    runs = InMemorySyncRunRepository([make_run(window_end=NOW - timedelta(days=2))])

    assert await make_service(source=source, runs=runs).sync_if_never_synced() is None
    assert source.calls == []


async def test_state_reports_last_run_and_last_successful_run() -> None:
    succeeded = make_run(window_end=NOW - timedelta(hours=6))
    failed = make_run(window_end=NOW, status=SyncStatus.FAILED)
    service = make_service(runs=InMemorySyncRunRepository([succeeded, failed]))

    state = await service.state()

    assert (state.running, state.last_run, state.last_successful_run) == (
        False,
        failed,
        succeeded,
    )


async def test_state_when_never_synced() -> None:
    state = await make_service().state()

    assert (state.running, state.last_run, state.last_successful_run) == (False, None, None)
