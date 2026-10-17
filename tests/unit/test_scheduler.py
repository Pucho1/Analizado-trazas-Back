from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.application.sync_service import SyncService
from app.core.constants import SYNC_MISFIRE_GRACE
from app.core.exceptions import TraceSourceError
from app.domain.models import SyncStatus
from app.infrastructure.scheduler import (
    INITIAL_SYNC_JOB_ID,
    create_sync_scheduler,
    run_initial_sync,
    run_scheduled_sync,
)
from tests.unit.fakes import (
    FailingTraces,
    InMemorySyncRunRepository,
    InMemoryTraceRepository,
    InMemoryTraceSource,
)

MADRID = ZoneInfo("Europe/Madrid")
SCHEDULE = [time(8, 0), time(14, 0), time(20, 0)]


def make_service(
    source: InMemoryTraceSource | FailingTraces | None = None,
    runs: InMemorySyncRunRepository | None = None,
) -> SyncService:
    """Sync service with in-memory collaborators."""
    return SyncService(
        source_name="langfuse",
        source=source or InMemoryTraceSource(),
        traces=InMemoryTraceRepository(),
        runs=runs or InMemorySyncRunRepository(),
        overlap=timedelta(hours=1),
        initial_lookback=timedelta(days=30),
    )


def test_one_daily_job_per_scheduled_time_plus_the_initial_sync() -> None:
    scheduler = create_sync_scheduler(make_service(), SCHEDULE, MADRID)

    jobs = {job.id: job for job in scheduler.get_jobs()}

    assert set(jobs) == {"sync-0800", "sync-1400", "sync-2000", INITIAL_SYNC_JOB_ID}
    assert isinstance(jobs[INITIAL_SYNC_JOB_ID].trigger, DateTrigger)
    assert jobs[INITIAL_SYNC_JOB_ID].func is run_initial_sync
    for job_id in ("sync-0800", "sync-1400", "sync-2000"):
        assert isinstance(jobs[job_id].trigger, CronTrigger)
        assert jobs[job_id].func is run_scheduled_sync


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        # Summer time (UTC+2): 08:00 in Madrid is 06:00 UTC.
        (datetime(2026, 7, 1, 5, 0, tzinfo=UTC), datetime(2026, 7, 1, 6, 0, tzinfo=UTC)),
        # Winter time (UTC+1), after the last scan of the day: next is 08:00 tomorrow.
        (datetime(2026, 12, 1, 19, 30, tzinfo=UTC), datetime(2026, 12, 2, 7, 0, tzinfo=UTC)),
        # Between scans: 14:00 in Madrid.
        (datetime(2026, 12, 1, 9, 0, tzinfo=UTC), datetime(2026, 12, 1, 13, 0, tzinfo=UTC)),
    ],
)
def test_scans_fire_at_local_times_in_the_configured_zone(
    now: datetime, expected: datetime
) -> None:
    scheduler = create_sync_scheduler(make_service(), SCHEDULE, MADRID)
    triggers = [job.trigger for job in scheduler.get_jobs() if job.id != INITIAL_SYNC_JOB_ID]

    next_fire = min(trigger.get_next_fire_time(None, now) for trigger in triggers)

    assert next_fire == expected


@pytest.mark.anyio
async def test_jobs_never_overlap_and_tolerate_sleep() -> None:
    scheduler = create_sync_scheduler(make_service(), SCHEDULE, MADRID)
    # Job defaults are applied when the scheduler starts; paused, nothing runs.
    scheduler.start(paused=True)
    try:
        jobs = scheduler.get_jobs()
    finally:
        scheduler.shutdown(wait=False)

    assert len(jobs) == 4
    for job in jobs:
        assert job.coalesce is True
        assert job.max_instances == 1
        assert job.misfire_grace_time == SYNC_MISFIRE_GRACE.total_seconds()


def test_empty_schedule_keeps_only_the_initial_sync() -> None:
    scheduler = create_sync_scheduler(make_service(), [], MADRID)

    assert [job.id for job in scheduler.get_jobs()] == [INITIAL_SYNC_JOB_ID]


@pytest.mark.anyio
async def test_scheduled_sync_records_a_run() -> None:
    runs = InMemorySyncRunRepository()

    await run_scheduled_sync(make_service(runs=runs))

    assert [run.status for run in runs.runs] == [SyncStatus.SUCCEEDED]


@pytest.mark.anyio
async def test_scheduled_sync_failure_is_logged_not_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runs = InMemorySyncRunRepository()

    await run_scheduled_sync(make_service(FailingTraces(TraceSourceError("down")), runs))

    assert [run.status for run in runs.runs] == [SyncStatus.FAILED]
    assert "Scheduled sync failed" in caplog.text


@pytest.mark.anyio
async def test_initial_sync_runs_once() -> None:
    runs = InMemorySyncRunRepository()
    service = make_service(runs=runs)

    await run_initial_sync(service)
    await run_initial_sync(service)

    assert len(runs.runs) == 1


@pytest.mark.anyio
async def test_initial_sync_failure_is_logged_not_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    await run_initial_sync(make_service(FailingTraces(TraceSourceError("down"))))

    assert "Initial sync failed" in caplog.text
