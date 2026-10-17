"""`TraceRepository` backed by PostgreSQL through SQLAlchemy."""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from itertools import batched
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.constants import DATABASE_SAVE_BATCH_SIZE
from app.domain.models import Observation, ObservationLevel, Trace
from app.infrastructure.database.errors import database_errors_as_repository_errors
from app.infrastructure.database.tables import ObservationRow, TraceRow

# Columns overwritten when a trace that already exists is saved again.
_TRACE_UPDATE_COLUMNS = (
    "name",
    "timestamp",
    "environment",
    "tags",
    "latency",
    "cost",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "synced_at",
)


def _utc_now() -> datetime:
    """Current moment as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


class SqlAlchemyTraceRepository:
    """Stores traces and their observations in the `traces` and `observations` tables."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = _utc_now,
        batch_size: int = DATABASE_SAVE_BATCH_SIZE,
    ) -> None:
        """Use `session_factory` for connections and `clock` to stamp `synced_at`.

        Traces are written `batch_size` at a time to keep statements under the
        driver's bind-parameter limit.
        """
        self._session_factory = session_factory
        self._clock = clock
        self._batch_size = batch_size

    async def save_traces(self, traces: Sequence[Trace]) -> None:
        """Upsert the traces and replace their observations, all in one transaction."""
        if not traces:
            return
        synced_at = self._clock()
        async with (
            database_errors_as_repository_errors(),
            self._session_factory() as session,
            session.begin(),
        ):
            for batch in batched(traces, self._batch_size, strict=False):
                await self._save_batch(session, batch, synced_at)

    async def list_traces(self, start: datetime, end: datetime) -> Sequence[Trace]:
        """Return the traces within [start, end], oldest first, with their observations."""
        statement = (
            select(TraceRow)
            .where(TraceRow.timestamp.between(start, end))
            .options(selectinload(TraceRow.observations))
            .order_by(TraceRow.timestamp, TraceRow.id)
        )
        async with database_errors_as_repository_errors(), self._session_factory() as session:
            rows = await session.scalars(statement)
            return [_to_trace(row) for row in rows]

    async def _save_batch(
        self, session: AsyncSession, traces: Sequence[Trace], synced_at: datetime
    ) -> None:
        """Upsert one batch of traces, then swap their old observations for the new ones."""
        upsert = pg_insert(TraceRow)
        upsert = upsert.on_conflict_do_update(
            index_elements=[TraceRow.id],
            set_={column: upsert.excluded[column] for column in _TRACE_UPDATE_COLUMNS},
        )
        await session.execute(upsert, [_trace_values(trace, synced_at) for trace in traces])

        trace_ids = [trace.id for trace in traces]
        await session.execute(delete(ObservationRow).where(ObservationRow.trace_id.in_(trace_ids)))

        observation_values = [
            _observation_values(trace.id, observation)
            for trace in traces
            for observation in trace.observations
        ]
        for observation_batch in batched(observation_values, self._batch_size, strict=False):
            await session.execute(insert(ObservationRow), list(observation_batch))


def _trace_values(trace: Trace, synced_at: datetime) -> dict[str, Any]:
    """Column values of the `traces` row for one domain trace."""
    return {
        "id": trace.id,
        "name": trace.name,
        "timestamp": trace.timestamp,
        "environment": trace.environment,
        "tags": list(trace.tags),
        "latency": trace.latency,
        "cost": trace.cost,
        "input_tokens": trace.input_tokens,
        "output_tokens": trace.output_tokens,
        "total_tokens": trace.total_tokens,
        "synced_at": synced_at,
    }


def _observation_values(trace_id: str, observation: Observation) -> dict[str, Any]:
    """Column values of the `observations` row for one domain observation."""
    return {
        "id": observation.id,
        "trace_id": trace_id,
        "name": observation.name,
        "type": observation.type,
        "level": observation.level.value,
        "model": observation.model,
        "start_time": observation.start_time,
    }


def _to_trace(row: TraceRow) -> Trace:
    """Rebuild a domain trace (with its observations) from its rows."""
    return Trace(
        id=row.id,
        timestamp=row.timestamp,
        name=row.name,
        environment=row.environment,
        latency=row.latency,
        cost=row.cost,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        total_tokens=row.total_tokens,
        tags=tuple(row.tags),
        observations=tuple(_to_observation(observation) for observation in row.observations),
    )


def _to_observation(row: ObservationRow) -> Observation:
    """Rebuild a domain observation from its row."""
    return Observation(
        id=row.id,
        name=row.name,
        type=row.type,
        level=ObservationLevel(row.level),
        model=row.model,
        start_time=row.start_time,
    )
