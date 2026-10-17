"""`SqlAlchemyTraceRepository` against a real PostgreSQL (see `conftest.py`)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import delete, func, make_url, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.exceptions import RepositoryError
from app.domain.models import ObservationLevel, Trace
from app.infrastructure.database.session import create_engine, create_session_factory
from app.infrastructure.database.tables import Base, ObservationRow, TraceRow
from app.infrastructure.database.trace_repository import SqlAlchemyTraceRepository
from tests.unit.fakes import make_observation, make_trace

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
START = NOW - timedelta(days=1)
# Observations named like this get a non-default level in `sample_trace`.
LEVELS_BY_NAME = {"failed": ObservationLevel.ERROR, "warned": ObservationLevel.WARNING}


def fixed_clock() -> datetime:
    """Fixed `synced_at` for assertions."""
    return NOW


@pytest.fixture
def repository(engine: AsyncEngine) -> SqlAlchemyTraceRepository:
    """Repository under test, stamping `synced_at` with `NOW`."""
    return SqlAlchemyTraceRepository(create_session_factory(engine), clock=fixed_clock)


async def count_rows(engine: AsyncEngine, table: type[Base]) -> int:
    """Number of rows in `table`."""
    async with engine.connect() as connection:
        result = await connection.execute(select(func.count()).select_from(table))
        return result.scalar_one()


def sample_trace(
    id: str = "trace-1",
    timestamp: datetime = NOW - timedelta(hours=2),
    cost: Decimal = Decimal("0.000160500001"),
    observation_ids: tuple[str, ...] = ("root", "generation"),
) -> Trace:
    """A trace with every field filled in and one observation per id (ids are prefixed)."""
    return make_trace(
        id=id,
        timestamp=timestamp,
        name="experiment-item-run",
        environment="sdk-experiment",
        latency=5.358,
        cost=cost,
        input_tokens=762,
        output_tokens=77,
        tags=("rag", "web"),
        observations=tuple(
            make_observation(
                id=f"{id}-{observation_id}",
                name=observation_id,
                type="GENERATION" if observation_id == "generation" else "SPAN",
                level=LEVELS_BY_NAME.get(observation_id, ObservationLevel.DEFAULT),
                model="gpt-4o-mini" if observation_id == "generation" else None,
                start_time=timestamp + timedelta(milliseconds=index),
            )
            for index, observation_id in enumerate(observation_ids)
        ),
    )


async def test_round_trips_a_trace_with_its_observations(
    repository: SqlAlchemyTraceRepository,
) -> None:
    trace = sample_trace(observation_ids=("root", "generation", "warned", "failed"))

    await repository.save_traces([trace])

    assert await repository.list_traces(START, NOW) == [trace]


async def test_saving_twice_updates_instead_of_duplicating(
    repository: SqlAlchemyTraceRepository, engine: AsyncEngine
) -> None:
    await repository.save_traces([sample_trace(observation_ids=("root", "old-step"))])
    updated = sample_trace(cost=Decimal("0.5"), observation_ids=("root", "new-step"))

    await repository.save_traces([updated])

    assert await repository.list_traces(START, NOW) == [updated]
    assert await count_rows(engine, TraceRow) == 1
    assert await count_rows(engine, ObservationRow) == 2


async def test_trace_without_observations(repository: SqlAlchemyTraceRepository) -> None:
    trace = sample_trace(observation_ids=())

    await repository.save_traces([trace])

    assert await repository.list_traces(START, NOW) == [trace]


async def test_lists_only_traces_within_range_inclusive_oldest_first(
    repository: SqlAlchemyTraceRepository,
) -> None:
    traces = [
        sample_trace(id="before", timestamp=START - timedelta(microseconds=1)),
        sample_trace(id="at-end", timestamp=NOW),
        sample_trace(id="at-start", timestamp=START),
        sample_trace(id="after", timestamp=NOW + timedelta(microseconds=1)),
    ]

    await repository.save_traces(traces)

    listed = await repository.list_traces(START, NOW)
    assert [trace.id for trace in listed] == ["at-start", "at-end"]


async def test_saving_nothing_is_a_no_op(
    repository: SqlAlchemyTraceRepository, engine: AsyncEngine
) -> None:
    await repository.save_traces([])

    assert await count_rows(engine, TraceRow) == 0


async def test_saves_in_batches(engine: AsyncEngine) -> None:
    repository = SqlAlchemyTraceRepository(
        create_session_factory(engine), clock=fixed_clock, batch_size=2
    )
    traces = [
        sample_trace(id=f"trace-{index}", observation_ids=("a", "b", "c")) for index in range(5)
    ]

    await repository.save_traces(traces)

    assert len(await repository.list_traces(START, NOW)) == 5
    assert await count_rows(engine, ObservationRow) == 15


async def test_stamps_synced_at(repository: SqlAlchemyTraceRepository, engine: AsyncEngine) -> None:
    await repository.save_traces([sample_trace()])

    async with engine.connect() as connection:
        synced_at = (await connection.execute(select(TraceRow.synced_at))).scalar_one()
    assert synced_at == NOW


async def test_keeps_cost_exact(repository: SqlAlchemyTraceRepository) -> None:
    await repository.save_traces([sample_trace(cost=Decimal("0.033028799987"))])

    (trace,) = await repository.list_traces(START, NOW)
    assert trace.cost == Decimal("0.033028799987")


async def test_deleting_a_trace_deletes_its_observations(
    repository: SqlAlchemyTraceRepository, engine: AsyncEngine
) -> None:
    await repository.save_traces([sample_trace()])

    async with engine.begin() as connection:
        await connection.execute(delete(TraceRow))

    assert await count_rows(engine, ObservationRow) == 0


async def test_database_rejects_unknown_levels(
    repository: SqlAlchemyTraceRepository, engine: AsyncEngine
) -> None:
    await repository.save_traces([sample_trace(observation_ids=())])
    statement = text(
        "INSERT INTO observations (id, trace_id, type, level, start_time) "
        "VALUES ('x', 'trace-1', 'SPAN', 'FATAL', now())"
    )

    async with engine.connect() as connection:
        with pytest.raises(IntegrityError):
            await connection.execute(statement)


async def test_unreachable_database_raises_repository_error() -> None:
    engine = create_engine("postgresql+asyncpg://x:y@127.0.0.1:1/none")
    repository = SqlAlchemyTraceRepository(create_session_factory(engine))
    traces = [sample_trace()]

    try:
        with pytest.raises(RepositoryError):
            await repository.list_traces(START, NOW)
        with pytest.raises(RepositoryError):
            await repository.save_traces(traces)
    finally:
        await engine.dispose()


async def test_wrong_password_raises_repository_error(database_url: str) -> None:
    wrong = make_url(database_url).set(password="wrong-password")
    engine = create_engine(wrong)
    repository = SqlAlchemyTraceRepository(create_session_factory(engine))

    try:
        with pytest.raises(RepositoryError):
            await repository.list_traces(START, NOW)
    finally:
        await engine.dispose()


async def test_migrations_match_the_tables(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        differences = await connection.run_sync(
            lambda sync: compare_metadata(MigrationContext.configure(sync), Base.metadata)
        )

    assert differences == []
