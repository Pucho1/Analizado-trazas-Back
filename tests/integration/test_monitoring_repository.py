"""`SqlAlchemyMonitoringRepository` (and profile thresholds) on a real PostgreSQL."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.domain.models import ProfileRule, Thresholds
from app.domain.monitoring import (
    Alert,
    AlertKind,
    AlertMetric,
    AlertScope,
    Baseline,
    DayMetric,
    MetricStats,
    Severity,
    TraceMetric,
    TraceSample,
)
from app.infrastructure.database.monitoring_repository import SqlAlchemyMonitoringRepository
from app.infrastructure.database.profile_repository import SqlAlchemyProfileRepository
from app.infrastructure.database.session import create_session_factory
from app.infrastructure.database.trace_repository import SqlAlchemyTraceRepository
from tests.unit.fakes import make_trace

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
D = Decimal


@pytest.fixture
def profiles(engine: AsyncEngine) -> SqlAlchemyProfileRepository:
    """Profile repository on the emptied test database."""
    return SqlAlchemyProfileRepository(create_session_factory(engine))


@pytest.fixture
def monitoring(engine: AsyncEngine) -> SqlAlchemyMonitoringRepository:
    """Repository under test."""
    return SqlAlchemyMonitoringRepository(create_session_factory(engine))


async def profile_with_traces(engine: AsyncEngine, profiles: SqlAlchemyProfileRepository) -> int:
    """A profile owning traces t1 (3 days ago), t2 (2 h ago) and t3 (1 h ago, no latency)."""
    traces = SqlAlchemyTraceRepository(create_session_factory(engine))
    await traces.save_traces(
        [
            make_trace(id="t1", timestamp=NOW - timedelta(days=3), cost=D("0.01"), latency=1.5),
            make_trace(id="t2", timestamp=NOW - timedelta(hours=2), cost=D("0.02"), latency=2.5),
            make_trace(id="t3", timestamp=NOW - timedelta(hours=1), cost=D("0.03"), latency=None),
            make_trace(id="other", timestamp=NOW, cost=D(9)),
        ]
    )
    profile = await profiles.add("Agente", ProfileRule(environment="production"), NOW)
    await profiles.assign_profiles({"t1": profile.id, "t2": profile.id, "t3": profile.id})
    return profile.id


def baseline() -> Baseline:
    """A baseline with exact decimals in both levels."""
    return Baseline(
        computed_at=NOW,
        profile_trace_count=150,
        trace_sample_size=150,
        day_sample_size=8,
        trace_stats={TraceMetric.COST: MetricStats(D("0.000160500001"), D("0.0003"), 150)},
        day_stats={DayMetric.QUERY_COUNT: MetricStats(D("12.5"), D("30.05"), 8)},
    )


def make_alert(profile_id: int, *, created_at: datetime = NOW, day: date | None = None) -> Alert:
    """A stored-shaped alert."""
    return Alert(
        profile_id=profile_id,
        kind=AlertKind.ANOMALY,
        scope=AlertScope.DAY if day else AlertScope.TRACE,
        metric=AlertMetric.LATENCY,
        severity=Severity.WARNING,
        observed=D("3.5"),
        threshold=D("2.5"),
        baseline_p95=D("2.5"),
        day=day,
        affected_count=2,
        worst_trace_id="t2",
        created_at=created_at,
    )


async def test_trace_queries(
    engine: AsyncEngine,
    profiles: SqlAlchemyProfileRepository,
    monitoring: SqlAlchemyMonitoringRepository,
) -> None:
    profile_id = await profile_with_traces(engine, profiles)
    since = NOW - timedelta(days=1)

    assert await monitoring.count_traces(profile_id, None) == 3
    assert await monitoring.count_traces(profile_id, since) == 2
    assert await monitoring.first_trace_at(profile_id, None) == NOW - timedelta(days=3)
    assert await monitoring.first_trace_at(profile_id, NOW) is None
    assert [s.id for s in await monitoring.latest_traces(profile_id, None, 2)] == ["t3", "t2"]
    between = await monitoring.traces_between(
        profile_id, NOW - timedelta(hours=2), NOW - timedelta(hours=1)
    )
    assert between == [
        TraceSample(
            id="t2",
            timestamp=NOW - timedelta(hours=2),
            cost=D("0.02"),
            input_tokens=10,
            output_tokens=5,
            latency=2.5,
        )
    ]  # start inclusive, end exclusive


async def test_baseline_round_trip_is_exact_and_replaceable(
    engine: AsyncEngine,
    profiles: SqlAlchemyProfileRepository,
    monitoring: SqlAlchemyMonitoringRepository,
) -> None:
    profile_id = await profile_with_traces(engine, profiles)

    assert await monitoring.get_baseline(profile_id) is None
    await monitoring.save_baseline(profile_id, baseline())
    assert await monitoring.get_baseline(profile_id) == baseline()

    newer = replace(baseline(), profile_trace_count=151, day_stats={})
    await monitoring.save_baseline(profile_id, newer)
    assert await monitoring.get_baseline(profile_id) == newer

    await monitoring.delete_baseline(profile_id)
    await monitoring.delete_baseline(profile_id)
    assert await monitoring.get_baseline(profile_id) is None


async def test_alerts_round_trip_and_queries(
    engine: AsyncEngine,
    profiles: SqlAlchemyProfileRepository,
    monitoring: SqlAlchemyMonitoringRepository,
) -> None:
    profile_id = await profile_with_traces(engine, profiles)
    old = make_alert(profile_id, created_at=NOW - timedelta(days=2))
    new = make_alert(profile_id, day=date(2026, 10, 17))

    await monitoring.save_alerts([old, new])
    await monitoring.save_alerts([])

    (recent,) = await monitoring.alerts_since(profile_id, NOW - timedelta(hours=24))
    assert recent.id is not None
    assert replace(recent, id=None) == new
    listed = await monitoring.list_alerts(None, None, 10)
    assert [a.created_at for a in listed] == [NOW, NOW - timedelta(days=2)]
    assert len(await monitoring.list_alerts(profile_id, None, 1)) == 1
    assert await monitoring.list_alerts(profile_id + 1, None, 10) == []
    assert len(await monitoring.list_alerts(None, NOW - timedelta(hours=1), 10)) == 1


async def test_deleting_a_profile_deletes_its_baseline_and_alerts(
    engine: AsyncEngine,
    profiles: SqlAlchemyProfileRepository,
    monitoring: SqlAlchemyMonitoringRepository,
) -> None:
    profile_id = await profile_with_traces(engine, profiles)
    await monitoring.save_baseline(profile_id, baseline())
    await monitoring.save_alerts([make_alert(profile_id)])

    await profiles.delete(profile_id)

    assert await monitoring.get_baseline(profile_id) is None
    assert await monitoring.list_alerts(None, None, 10) == []


async def test_profile_thresholds_and_baseline_since_persist(
    profiles: SqlAlchemyProfileRepository,
) -> None:
    profile = await profiles.add("Agente", ProfileRule(environment="production"), NOW)
    assert profile.thresholds == Thresholds()
    tuned = replace(
        profile,
        thresholds=Thresholds(
            warning_factor=D("1.5"),
            critical_factor=D(3),
            daily_budget=D("2.75"),
            min_trace_cost=D(0),
            min_daily_cost=D("0.000001"),
            silence_hours=6,
        ),
        baseline_since=NOW,
    )

    await profiles.update(tuned)

    assert await profiles.get(profile.id) == tuned


@pytest.mark.parametrize(
    "assignment",
    [
        "warning_factor = 3",  # above critical
        "warning_factor = 0",
        "daily_budget = 0",
        "min_trace_cost = -1",
        "silence_hours = 0",
    ],
)
async def test_database_rejects_inconsistent_thresholds(
    engine: AsyncEngine, profiles: SqlAlchemyProfileRepository, assignment: str
) -> None:
    profile = await profiles.add("Agente", ProfileRule(environment="production"), NOW)
    statement = text(f"UPDATE profiles SET {assignment} WHERE id = :id")

    async with engine.connect() as connection:
        with pytest.raises(IntegrityError):
            await connection.execute(statement, {"id": profile.id})
