"""Daily synchronisation scans with APScheduler, running inside the API process."""

import logging
from collections.abc import Sequence
from datetime import time
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.application.sync_service import SyncService
from app.core.constants import SYNC_MISFIRE_GRACE
from app.core.exceptions import ObservaError, SyncAlreadyRunningError
from app.domain.models import SyncTrigger

logger = logging.getLogger(__name__)

INITIAL_SYNC_JOB_ID = "initial-sync"


def create_sync_scheduler(
    service: SyncService, schedule: Sequence[time], timezone: ZoneInfo
) -> AsyncIOScheduler:
    """Scheduler with one daily scan per time in `schedule` plus an initial sync.

    The initial sync runs as soon as the scheduler starts and only does something if
    nothing was ever synced. Missed scans (e.g. machine asleep) still run if it wakes
    up within `SYNC_MISFIRE_GRACE`, and several missed ones collapse into one.
    The scheduler is returned unstarted; start it inside the running event loop.
    """
    scheduler = AsyncIOScheduler(
        timezone=timezone,
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": int(SYNC_MISFIRE_GRACE.total_seconds()),
        },
    )
    for at in schedule:
        scheduler.add_job(
            run_scheduled_sync,
            CronTrigger(hour=at.hour, minute=at.minute, timezone=timezone),
            args=[service],
            id=f"sync-{at:%H%M}",
            name=f"Sync at {at:%H:%M}",
        )
    scheduler.add_job(run_initial_sync, args=[service], id=INITIAL_SYNC_JOB_ID, name="Initial sync")
    return scheduler


async def run_scheduled_sync(service: SyncService) -> None:
    """Run one scheduled scan; failures are logged, never raised into the scheduler."""
    try:
        run = await service.sync(SyncTrigger.SCHEDULED)
    except SyncAlreadyRunningError:
        logger.info("Scheduled sync skipped: another sync is running")
    except ObservaError:
        logger.warning("Scheduled sync failed", exc_info=True)
    else:
        logger.info("Scheduled sync stored %d traces", run.trace_count)


async def run_initial_sync(service: SyncService) -> None:
    """Sync at startup if nothing was ever synced; failures are logged, never raised."""
    try:
        run = await service.sync_if_never_synced()
    except SyncAlreadyRunningError:
        logger.info("Initial sync skipped: another sync is running")
    except ObservaError:
        logger.warning("Initial sync failed", exc_info=True)
    else:
        if run is not None:
            logger.info("Initial sync stored %d traces", run.trace_count)
