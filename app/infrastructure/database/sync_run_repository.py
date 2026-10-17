"""`SyncRunRepository` backed by the `sync_runs` table."""

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models import SyncFailureReason, SyncRun, SyncStatus, SyncTrigger
from app.infrastructure.database.errors import database_errors_as_repository_errors
from app.infrastructure.database.tables import SyncRunRow


class SqlAlchemySyncRunRepository:
    """Stores every sync run; the marker is the successful run that reached furthest."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Use `session_factory` for connections."""
        self._session_factory = session_factory

    async def save(self, run: SyncRun) -> None:
        """Insert the run."""
        async with (
            database_errors_as_repository_errors(),
            self._session_factory() as session,
            session.begin(),
        ):
            session.add(_to_row(run))

    async def latest(self, source: str) -> SyncRun | None:
        """The most recently started run of `source`."""
        statement = (
            select(SyncRunRow)
            .where(SyncRunRow.source == source)
            .order_by(SyncRunRow.started_at.desc(), SyncRunRow.id.desc())
        )
        return await self._first(statement)

    async def latest_successful(self, source: str) -> SyncRun | None:
        """The successful run of `source` with the latest `window_end`."""
        statement = (
            select(SyncRunRow)
            .where(
                SyncRunRow.source == source,
                SyncRunRow.status == SyncStatus.SUCCEEDED.value,
            )
            .order_by(SyncRunRow.window_end.desc(), SyncRunRow.id.desc())
        )
        return await self._first(statement)

    async def _first(self, statement: Select[SyncRunRow]) -> SyncRun | None:
        """First row of `statement` as a domain run, or None."""
        async with database_errors_as_repository_errors(), self._session_factory() as session:
            row = await session.scalar(statement.limit(1))
            return None if row is None else _to_domain(row)


def _to_row(run: SyncRun) -> SyncRunRow:
    """Row for a domain run."""
    return SyncRunRow(
        source=run.source,
        trigger=run.trigger.value,
        status=run.status.value,
        started_at=run.started_at,
        finished_at=run.finished_at,
        window_start=run.window_start,
        window_end=run.window_end,
        trace_count=run.trace_count,
        error_code=None if run.failure_reason is None else run.failure_reason.value,
    )


def _to_domain(row: SyncRunRow) -> SyncRun:
    """Domain run for a row."""
    return SyncRun(
        source=row.source,
        trigger=SyncTrigger(row.trigger),
        status=SyncStatus(row.status),
        started_at=row.started_at,
        finished_at=row.finished_at,
        window_start=row.window_start,
        window_end=row.window_end,
        trace_count=row.trace_count,
        failure_reason=None if row.error_code is None else SyncFailureReason(row.error_code),
    )
