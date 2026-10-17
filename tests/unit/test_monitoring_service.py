"""`MonitoringService` with in-memory repositories: orchestration edge cases."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.application.monitoring_service import MonitoringService
from app.core.constants import LEARNING_TRACES, MONITORING_MAX_LOOKBACK
from app.core.exceptions import InvalidThresholdsError, ProfileNotFoundError
from app.domain.models import (
    ProfileRule,
    ProfileStatus,
    SyncRun,
    SyncStatus,
    SyncTrigger,
    Thresholds,
    Trace,
)
from app.domain.monitoring import AlertKind, AlertMetric, AlertScope, Severity, TraceMetric
from tests.unit.fakes import (
    InMemoryMonitoringRepository,
    InMemoryProfileRepository,
    make_profile,
    make_trace,
)

pytestmark = pytest.mark.anyio

MADRID = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)  # 14:00 in Madrid
WINDOW = (NOW - timedelta(hours=6), NOW)
RULE = ProfileRule(environment="production", trace_name="agent")
PER_DAY = 12
D = Decimal


def agent_trace(id: str, at: datetime, *, latency: float = 1.0, cost: str = "0.02") -> Trace:
    """A trace of the monitored agent."""
    return make_trace(
        id=id, timestamp=at, environment="production", name="agent", latency=latency, cost=D(cost)
    )


def history(days: int = 10, per_day: int = PER_DAY) -> list[Trace]:
    """`per_day` normal traces on each of the `days` days before today (10:00 UTC)."""
    return [
        agent_trace(
            f"h{day}-{i}",
            NOW.replace(hour=10, minute=i) - timedelta(days=day),
            latency=1.0 + (i % 3) / 10,
        )
        for day in range(1, days + 1)
        for i in range(per_day)
    ]


class World:
    """A profile with traces, its repositories and the service under test."""

    def __init__(
        self,
        traces: list[Trace],
        *,
        status: ProfileStatus = ProfileStatus.ACTIVE,
        thresholds: Thresholds | None = None,
    ) -> None:
        """Store `traces` already assigned to profile 1."""
        self.profiles = InMemoryProfileRepository(traces)
        self.profiles.profiles[1] = make_profile(
            id=1,
            name="Agente",
            rule=RULE,
            status=status,
            created_at=NOW - timedelta(days=60),
            thresholds=thresholds,
        )
        self.profiles.profile_of.update(dict.fromkeys(self.profiles.traces, 1))
        self.monitoring = InMemoryMonitoringRepository(self.profiles)
        self.clock_now = NOW
        self.service = MonitoringService(
            self.profiles, self.monitoring, MADRID, clock=lambda: self.clock_now
        )

    def add(self, trace: Trace) -> None:
        """Store a new trace of the profile (as a sync + reclassification would)."""
        self.profiles.traces[trace.id] = trace
        self.profiles.profile_of[trace.id] = 1


async def test_ready_profile_raises_a_latency_alert_for_a_new_spike() -> None:
    world = World(history())
    world.add(agent_trace("spike", NOW - timedelta(hours=1), latency=10.0))

    (alert,) = await world.service.evaluate(*WINDOW)

    assert (alert.kind, alert.scope, alert.metric, alert.severity) == (
        AlertKind.ANOMALY,
        AlertScope.TRACE,
        AlertMetric.LATENCY,
        Severity.CRITICAL,
    )
    assert (alert.worst_trace_id, alert.observed, alert.affected_count) == ("spike", D("10"), 1)
    assert world.monitoring.alerts == [world.monitoring.alerts[0]]
    assert world.monitoring.alerts[0].id == 1


async def test_baseline_is_recalculated_and_stored() -> None:
    world = World(history())

    await world.service.evaluate(*WINDOW)

    baseline = world.monitoring.baselines[1]
    assert baseline.profile_trace_count == 10 * PER_DAY
    assert baseline.learning is False
    assert baseline.days_ready is True
    assert baseline.day_sample_size == 10
    assert baseline.trace_stats[TraceMetric.LATENCY].p95 == D("1.2")


async def test_learning_profile_raises_no_anomalies_but_still_its_budget() -> None:
    traces = history(days=4)[: LEARNING_TRACES // 2]
    world = World(traces, thresholds=Thresholds(daily_budget=D("0.05")))
    for i in range(3):
        world.add(agent_trace(f"new{i}", NOW - timedelta(hours=1), latency=50.0))

    alerts = await world.service.evaluate(*WINDOW)

    assert world.monitoring.baselines[1].learning is True
    assert [(a.kind, a.observed) for a in alerts] == [(AlertKind.BUDGET, D("0.06"))]


async def test_archived_profiles_are_not_monitored() -> None:
    world = World(history(), status=ProfileStatus.ARCHIVED)
    world.add(agent_trace("spike", NOW - timedelta(hours=1), latency=10.0))

    assert await world.service.evaluate(*WINDOW) == []
    assert world.monitoring.baselines == {}


async def test_profile_without_traces_is_learning_and_quiet() -> None:
    world = World([])

    assert await world.service.evaluate(*WINDOW) == []
    baseline = world.monitoring.baselines[1]
    assert (baseline.profile_trace_count, baseline.trace_stats, baseline.day_stats) == (0, {}, {})


async def test_old_data_beyond_the_lookback_is_not_judged() -> None:
    world = World(history())
    old = NOW - MONITORING_MAX_LOOKBACK - timedelta(hours=1)
    world.add(agent_trace("old-spike", old, latency=10.0))

    alerts = await world.service.evaluate(NOW - timedelta(days=30), NOW)

    assert [a.worst_trace_id for a in alerts if a.scope is AlertScope.TRACE] == []


async def test_second_scan_within_the_silence_adds_nothing() -> None:
    world = World(history())
    world.add(agent_trace("spike", NOW - timedelta(hours=1), latency=10.0))
    await world.service.evaluate(*WINDOW)
    world.clock_now = NOW + timedelta(hours=6)

    assert await world.service.evaluate(NOW, world.clock_now) == []
    assert len(world.monitoring.alerts) == 1


async def test_after_silence_the_same_problem_alerts_again() -> None:
    world = World(history())
    world.add(agent_trace("spike", NOW - timedelta(hours=1), latency=10.0))
    await world.service.evaluate(*WINDOW)
    world.clock_now = NOW + timedelta(hours=25)
    world.add(agent_trace("spike2", world.clock_now - timedelta(hours=1), latency=10.0))

    (again,) = await world.service.evaluate(world.clock_now - timedelta(hours=6), world.clock_now)

    assert again.worst_trace_id == "spike2"


async def test_daily_query_count_anomaly() -> None:
    world = World(history())
    for i in range(3 * PER_DAY):
        world.add(agent_trace(f"busy{i}", NOW - timedelta(hours=2, minutes=i)))

    alerts = await world.service.evaluate(*WINDOW)

    # 36 queries (p95 12) and 0.72 $ (p95 0.24 $, above the 0.10 $ minimum): both critical.
    daily = {a.metric: a for a in alerts if a.scope is AlertScope.DAY}
    assert set(daily) == {AlertMetric.QUERY_COUNT, AlertMetric.COST}
    assert (daily[AlertMetric.QUERY_COUNT].observed, daily[AlertMetric.COST].observed) == (
        D(3 * PER_DAY),
        D("0.72"),
    )
    assert {a.severity for a in daily.values()} == {Severity.CRITICAL}
    assert {a.day for a in daily.values()} == {NOW.astimezone(MADRID).date()}


async def test_after_sync_evaluates_the_sync_window() -> None:
    world = World(history())
    world.add(agent_trace("spike", NOW - timedelta(hours=1), latency=10.0))
    run = SyncRun(
        source="langfuse",
        trigger=SyncTrigger.SCHEDULED,
        status=SyncStatus.SUCCEEDED,
        started_at=NOW,
        finished_at=NOW,
        window_start=WINDOW[0],
        window_end=WINDOW[1],
        trace_count=1,
    )

    await world.service.after_sync(run)

    assert len(world.monitoring.alerts) == 1


async def test_relearn_starts_learning_from_now() -> None:
    world = World(history())
    await world.service.evaluate(*WINDOW)

    profile = await world.service.relearn(1)
    view = await world.service.baseline(1)

    assert profile.baseline_since == NOW
    assert world.monitoring.baselines == {}
    assert (view.baseline.profile_trace_count, view.baseline.learning) == (0, True)


async def test_after_relearning_only_new_traces_count() -> None:
    world = World(history())
    await world.service.relearn(1)
    world.clock_now = NOW + timedelta(days=1)
    world.add(agent_trace("after", NOW + timedelta(hours=1)))

    view = await world.service.baseline(1)

    assert view.baseline.profile_trace_count == 1
    assert view.baseline.day_sample_size == 1  # only the (partial) day since relearning


async def test_baseline_view_does_not_store_anything() -> None:
    world = World(history())

    view = await world.service.baseline(1)

    assert view.baseline.learning is False
    assert world.monitoring.baselines == {}


async def test_update_thresholds_changes_only_what_is_sent() -> None:
    world = World(history())

    profile = await world.service.update_thresholds(1, daily_budget=D("1.5"), silence_hours=6)
    cleared = await world.service.update_thresholds(1, daily_budget=None)

    assert profile.thresholds == Thresholds(daily_budget=D("1.5"), silence_hours=6)
    assert cleared.thresholds == Thresholds(silence_hours=6)
    assert world.profiles.profiles[1] == cleared


async def test_inconsistent_thresholds_are_rejected() -> None:
    world = World(history())
    above_critical = D(3)

    with pytest.raises(InvalidThresholdsError):
        await world.service.update_thresholds(1, warning_factor=above_critical)


async def _baseline(service: MonitoringService) -> object:
    """Baseline of profile 99."""
    return await service.baseline(99)


async def _relearn(service: MonitoringService) -> object:
    """Relearn profile 99."""
    return await service.relearn(99)


async def _tune(service: MonitoringService) -> object:
    """Change a threshold of profile 99."""
    return await service.update_thresholds(99, silence_hours=1)


@pytest.mark.parametrize("operation", [_baseline, _relearn, _tune])
async def test_unknown_profile(
    operation: Callable[[MonitoringService], Awaitable[object]],
) -> None:
    service = World([]).service

    with pytest.raises(ProfileNotFoundError):
        await operation(service)


async def test_list_alerts_newest_first_and_filtered() -> None:
    world = World(history())
    world.add(agent_trace("spike", NOW - timedelta(hours=1), latency=10.0))
    await world.service.evaluate(*WINDOW)

    assert len(await world.service.list_alerts(None, None, 10)) == 1
    assert await world.service.list_alerts(2, None, 10) == []
    assert await world.service.list_alerts(None, NOW + timedelta(seconds=1), 10) == []
