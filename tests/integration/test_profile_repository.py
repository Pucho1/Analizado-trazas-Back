"""`SqlAlchemyProfileRepository` against a real PostgreSQL (see `conftest.py`)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.exceptions import ProfileNameTakenError, ProfileNotFoundError
from app.domain.models import ProfileRule, ProfileStatus, TraceKey
from app.infrastructure.database.profile_repository import SqlAlchemyProfileRepository
from app.infrastructure.database.session import create_session_factory
from app.infrastructure.database.tables import ObservationRow, TraceRow
from app.infrastructure.database.trace_repository import SqlAlchemyTraceRepository
from tests.unit.fakes import make_observation, make_trace

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 17, 14, 0, tzinfo=UTC)
PORTFOLIO = ProfileRule(
    environment="sdk-experiment", trace_name="rag-portafolio-eval", tags=("app:rag-portafolio",)
)


@pytest.fixture
def profiles(engine: AsyncEngine) -> SqlAlchemyProfileRepository:
    """Repository under test on the emptied test database."""
    return SqlAlchemyProfileRepository(create_session_factory(engine))


@pytest.fixture
def traces(engine: AsyncEngine) -> SqlAlchemyTraceRepository:
    """Trace repository on the same database, to store traces to classify."""
    return SqlAlchemyTraceRepository(create_session_factory(engine))


async def store_sample(traces: SqlAlchemyTraceRepository) -> None:
    """Store traces shaped like the user's real data."""
    await traces.save_traces(
        [
            make_trace(
                id="p1",
                timestamp=NOW - timedelta(hours=2),
                environment="sdk-experiment",
                name="rag-portafolio-eval",
                tags=("app:rag-portafolio",),
                cost=Decimal("0.0001617"),
                observations=(make_observation(id="p1-root", start_time=NOW),),
            ),
            make_trace(
                id="p2",
                timestamp=NOW - timedelta(hours=1),
                environment="sdk-experiment",
                name="rag-portafolio-eval",
                tags=("app:rag-portafolio",),
                cost=Decimal("0.0002"),
            ),
            make_trace(
                id="e1",
                timestamp=NOW - timedelta(days=3),
                environment="sdk-experiment",
                name="experiment-item-run",
            ),
        ]
    )


async def count_rows(engine: AsyncEngine, table: type[TraceRow] | type[ObservationRow]) -> int:
    """Number of rows in `table`."""
    async with engine.connect() as connection:
        result = await connection.execute(select(func.count()).select_from(table))
        return result.scalar_one()


async def test_add_get_and_list(profiles: SqlAlchemyProfileRepository) -> None:
    first = await profiles.add("Portafolio", PORTFOLIO, NOW)
    second = await profiles.add("Experimentos", ProfileRule(environment="production"), NOW)

    assert first.id != second.id
    assert (first.name, first.rule, first.status, first.created_at, first.archived_at) == (
        "Portafolio",
        PORTFOLIO,
        ProfileStatus.ACTIVE,
        NOW,
        None,
    )
    assert await profiles.get(first.id) == first
    assert await profiles.get(999_999) is None
    assert await profiles.list_profiles() == [first, second]


async def test_names_are_unique_ignoring_case(profiles: SqlAlchemyProfileRepository) -> None:
    await profiles.add("Asistente de la web", PORTFOLIO, NOW)

    with pytest.raises(ProfileNameTakenError):
        await profiles.add("ASISTENTE DE LA WEB", PORTFOLIO, NOW)


async def test_update_overwrites_and_checks_the_name(
    profiles: SqlAlchemyProfileRepository,
) -> None:
    profile = await profiles.add("Portafolio", PORTFOLIO, NOW)
    await profiles.add("Otro", PORTFOLIO, NOW)
    archived = replace(
        profile,
        name="Asistente",
        rule=ProfileRule(environment="production", tags=("web",)),
        status=ProfileStatus.ARCHIVED,
        archived_at=NOW,
    )

    await profiles.update(archived)

    assert await profiles.get(profile.id) == archived
    with pytest.raises(ProfileNameTakenError):
        await profiles.update(replace(archived, name="otro"))
    with pytest.raises(ProfileNotFoundError):
        await profiles.update(replace(archived, id=999_999))


async def test_assign_count_and_list_keys(
    profiles: SqlAlchemyProfileRepository, traces: SqlAlchemyTraceRepository
) -> None:
    await store_sample(traces)
    profile = await profiles.add("Portafolio", PORTFOLIO, NOW)

    await profiles.assign_profiles({"p1": profile.id, "p2": profile.id})

    assert await profiles.count_traces_by_profile() == {profile.id: 2, None: 1}
    keys = {key.id: key for key in await profiles.list_trace_keys()}
    assert keys["p1"] == TraceKey(
        id="p1",
        environment="sdk-experiment",
        name="rag-portafolio-eval",
        tags=("app:rag-portafolio",),
        profile_id=profile.id,
    )
    assert keys["e1"].profile_id is None


async def test_resyncing_a_trace_keeps_its_profile(
    profiles: SqlAlchemyProfileRepository, traces: SqlAlchemyTraceRepository
) -> None:
    await store_sample(traces)
    profile = await profiles.add("Portafolio", PORTFOLIO, NOW)
    await profiles.assign_profiles({"p1": profile.id})

    await store_sample(traces)

    keys = {key.id: key for key in await profiles.list_trace_keys()}
    assert keys["p1"].profile_id == profile.id


async def test_discover_groups_only_unclassified_traces_largest_first(
    profiles: SqlAlchemyProfileRepository, traces: SqlAlchemyTraceRepository
) -> None:
    await store_sample(traces)

    groups = await profiles.discover_groups()

    assert [(g.trace_name, g.tags, g.trace_count) for g in groups] == [
        ("rag-portafolio-eval", ("app:rag-portafolio",), 2),
        ("experiment-item-run", (), 1),
    ]
    portfolio = groups[0]
    assert portfolio.total_cost == Decimal("0.0003617")
    assert (portfolio.first_seen, portfolio.last_seen) == (
        NOW - timedelta(hours=2),
        NOW - timedelta(hours=1),
    )

    profile = await profiles.add("Portafolio", PORTFOLIO, NOW)
    await profiles.assign_profiles({"p1": profile.id, "p2": profile.id})
    assert [g.trace_name for g in await profiles.discover_groups()] == ["experiment-item-run"]


async def test_delete_cascades_to_traces_and_observations(
    profiles: SqlAlchemyProfileRepository,
    traces: SqlAlchemyTraceRepository,
    engine: AsyncEngine,
) -> None:
    await store_sample(traces)
    profile = await profiles.add("Portafolio", PORTFOLIO, NOW)
    await profiles.assign_profiles({"p1": profile.id, "p2": profile.id})

    assert await profiles.delete(profile.id) is True
    assert await profiles.delete(profile.id) is False

    assert await profiles.get(profile.id) is None
    assert [key.id for key in await profiles.list_trace_keys()] == ["e1"]
    assert await count_rows(engine, ObservationRow) == 0
