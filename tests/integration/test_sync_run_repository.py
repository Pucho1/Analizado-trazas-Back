"""`SqlAlchemySyncRunRepository` against a real PostgreSQL (see `conftest.py`)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.domain.models import SyncFailureReason, SyncRun, SyncStatus, SyncTrigger
from app.infrastructure.database.session import create_session_factory
from app.infrastructure.database.sync_run_repository import SqlAlchemySyncRunRepository

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 17, 14, 0, tzinfo=UTC)


@pytest.fixture
def runs(engine: AsyncEngine) -> SqlAlchemySyncRunRepository:
    """Repository under test on the emptied test database."""
    return SqlAlchemySyncRunRepository(create_session_factory(engine))


def make_run(
    *,
    started_at: datetime,
    window_end: datetime | None = None,
    status: SyncStatus = SyncStatus.SUCCEEDED,
    source: str = "langfuse",
) -> SyncRun:
    """A run started at `started_at`; its window ends then unless given."""
    failed = status is SyncStatus.FAILED
    return SyncRun(
        source=source,
        trigger=SyncTrigger.MANUAL if failed else SyncTrigger.SCHEDULED,
        status=status,
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=4),
        window_start=started_at - timedelta(days=1),
        window_end=window_end or started_at,
        trace_count=0 if failed else 12,
        failure_reason=SyncFailureReason.TRACE_SOURCE_AUTH_FAILED if failed else None,
    )


async def test_nothing_recorded(runs: SqlAlchemySyncRunRepository) -> None:
    assert await runs.latest("langfuse") is None
    assert await runs.latest_successful("langfuse") is None


async def test_round_trips_successful_and_failed_runs(
    runs: SqlAlchemySyncRunRepository,
) -> None:
    succeeded = make_run(started_at=NOW - timedelta(hours=6))
    failed = make_run(started_at=NOW, status=SyncStatus.FAILED)

    await runs.save(succeeded)
    await runs.save(failed)

    assert await runs.latest("langfuse") == failed
    assert await runs.latest_successful("langfuse") == succeeded


async def test_marker_is_the_successful_run_that_reached_furthest(
    runs: SqlAlchemySyncRunRepository,
) -> None:
    furthest = make_run(started_at=NOW - timedelta(hours=6), window_end=NOW)
    later_but_shorter = make_run(started_at=NOW, window_end=NOW - timedelta(hours=1))

    await runs.save(furthest)
    await runs.save(later_but_shorter)

    assert await runs.latest_successful("langfuse") == furthest
    assert await runs.latest("langfuse") == later_but_shorter


async def test_runs_are_kept_per_source(runs: SqlAlchemySyncRunRepository) -> None:
    langsmith = make_run(started_at=NOW, source="langsmith")

    await runs.save(langsmith)

    assert await runs.latest("langfuse") is None
    assert await runs.latest_successful("langsmith") == langsmith
