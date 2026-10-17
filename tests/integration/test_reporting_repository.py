"""Spend, event mapping and report settings repositories on a real PostgreSQL."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.constants import DEFAULT_EMAIL_SECTIONS, DEFAULT_TELEGRAM_SECTIONS
from app.domain.events import EventMapping, StepCount
from app.domain.models import ObservationLevel, ProfileRule
from app.domain.reporting import Channel, ChannelSettings
from app.domain.spend import SpendTotals
from app.infrastructure.database.profile_repository import SqlAlchemyProfileRepository
from app.infrastructure.database.reporting_repository import (
    SqlAlchemyEventMappingRepository,
    SqlAlchemyReportSettingsRepository,
    SqlAlchemySpendRepository,
)
from app.infrastructure.database.session import create_engine, create_session_factory
from app.infrastructure.database.trace_repository import SqlAlchemyTraceRepository
from tests.unit.fakes import make_observation, make_trace

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


async def stored_profile(engine: AsyncEngine) -> int:
    """A profile with two traces (one with steps) plus one unclassified trace."""
    sessions = create_session_factory(engine)
    error = ObservationLevel.ERROR
    await SqlAlchemyTraceRepository(sessions).save_traces(
        [
            make_trace(
                id="a",
                timestamp=NOW - timedelta(hours=2),
                cost=Decimal("0.0001617"),
                input_tokens=762,
                output_tokens=79,
                observations=(
                    make_observation(id="a1", name="manipulation-attempt", start_time=NOW),
                    make_observation(id="a2", name="router", level=error, start_time=NOW),
                    make_observation(id="a3", name="router", level=error, start_time=NOW),
                    make_observation(id="a4", name=None, start_time=NOW),
                ),
            ),
            make_trace(
                id="b",
                timestamp=NOW - timedelta(hours=1),
                cost=Decimal("0.0002"),
                input_tokens=100,
                output_tokens=20,
                observations=(make_observation(id="b1", name="router", start_time=NOW),),
            ),
            make_trace(id="loose", timestamp=NOW - timedelta(hours=1), cost=Decimal("0.5")),
            make_trace(id="old", timestamp=NOW - timedelta(days=5), cost=Decimal(1)),
        ]
    )
    profiles = SqlAlchemyProfileRepository(sessions)
    profile = await profiles.add("Web", ProfileRule(environment="production"), NOW)
    await profiles.assign_profiles({"a": profile.id, "b": profile.id, "old": profile.id})
    return profile.id


async def test_totals_by_profile(engine: AsyncEngine) -> None:
    profile_id = await stored_profile(engine)
    spend = SqlAlchemySpendRepository(create_session_factory(engine))

    totals = await spend.totals_by_profile(NOW - timedelta(days=1), NOW)

    assert totals == {
        profile_id: SpendTotals(2, Decimal("0.0003617"), 862, 99, 961),
        None: SpendTotals(1, Decimal("0.5"), 10, 5, 15),
    }
    assert await spend.totals_by_profile(NOW, NOW + timedelta(days=1)) == {}


async def test_step_counts(engine: AsyncEngine) -> None:
    profile_id = await stored_profile(engine)
    spend = SqlAlchemySpendRepository(create_session_factory(engine))

    steps = await spend.step_counts(profile_id, NOW - timedelta(days=1), NOW)

    assert steps == [
        StepCount("manipulation-attempt", ObservationLevel.DEFAULT, 1, frozenset({"a"})),
        StepCount("router", ObservationLevel.DEFAULT, 1, frozenset({"b"})),
        StepCount("router", ObservationLevel.ERROR, 2, frozenset({"a"})),
    ]
    assert await spend.step_counts(profile_id, None, NOW - timedelta(hours=3)) == []
    assert len(await spend.step_counts(profile_id, None, None)) == 3


async def test_event_mappings_are_replaced_as_a_whole(engine: AsyncEngine) -> None:
    profile_id = await stored_profile(engine)
    repository = SqlAlchemyEventMappingRepository(create_session_factory(engine))
    first = [
        EventMapping("manipulation-attempt", "Intentos de manipulación"),
        EventMapping("in-domain-response-fallback", "Respuestas de reserva"),
        EventMapping("inDomain-response-fallback", "Respuestas de reserva"),
    ]

    await repository.replace_mappings(profile_id, first)
    assert await repository.list_mappings(profile_id) == sorted(
        first, key=lambda m: m.observation_name
    )

    await repository.replace_mappings(profile_id, [EventMapping("router", "Desvíos")])
    assert await repository.list_mappings(profile_id) == [EventMapping("router", "Desvíos")]

    await repository.replace_mappings(profile_id, [])
    assert await repository.list_mappings(profile_id) == []


async def test_a_step_name_is_mapped_once_per_profile(engine: AsyncEngine) -> None:
    profile_id = await stored_profile(engine)
    statement = text(
        "INSERT INTO event_mappings (profile_id, observation_name, label) "
        "VALUES (:p, 'x', 'A'), (:p, 'x', 'B')"
    )

    async with engine.connect() as connection:
        with pytest.raises(IntegrityError):
            await connection.execute(statement, {"p": profile_id})


async def test_mappings_are_deleted_with_their_profile(engine: AsyncEngine) -> None:
    profile_id = await stored_profile(engine)
    sessions = create_session_factory(engine)
    repository = SqlAlchemyEventMappingRepository(sessions)
    await repository.replace_mappings(profile_id, [EventMapping("router", "Desvíos")])

    await SqlAlchemyProfileRepository(sessions).delete(profile_id)

    assert await repository.list_mappings(profile_id) == []


async def test_migration_seeds_the_default_report_settings(database_url: str) -> None:
    # The session fixture has just run every migration on an empty schema.
    engine = create_engine(database_url)
    try:
        repository = SqlAlchemyReportSettingsRepository(create_session_factory(engine))
        assert await repository.get(Channel.EMAIL) == ChannelSettings(
            Channel.EMAIL, True, DEFAULT_EMAIL_SECTIONS
        )
        assert await repository.get(Channel.TELEGRAM) == ChannelSettings(
            Channel.TELEGRAM, True, DEFAULT_TELEGRAM_SECTIONS
        )
    finally:
        await engine.dispose()


async def test_report_settings_defaults_and_updates(engine: AsyncEngine) -> None:
    repository = SqlAlchemyReportSettingsRepository(create_session_factory(engine))
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM report_settings"))

    assert await repository.get(Channel.EMAIL) == ChannelSettings(
        Channel.EMAIL, True, DEFAULT_EMAIL_SECTIONS
    )
    assert await repository.get(Channel.TELEGRAM) == ChannelSettings(
        Channel.TELEGRAM, True, DEFAULT_TELEGRAM_SECTIONS
    )

    changed = ChannelSettings(Channel.TELEGRAM, False, ("alerts",))
    await repository.save(changed)
    await repository.save(changed)
    assert await repository.get(Channel.TELEGRAM) == changed
