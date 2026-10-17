"""Deliveries, pending alerts and channel notification settings on a real PostgreSQL."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.exceptions import RepositoryError
from app.domain.models import ProfileRule
from app.domain.monitoring import Alert, AlertKind, AlertMetric, AlertScope, Severity
from app.domain.notifications import (
    Delivery,
    DeliveryKind,
    DeliveryStatus,
    NotificationFailure,
)
from app.domain.reporting import Channel, ChannelSettings
from app.infrastructure.database.delivery_repository import SqlAlchemyDeliveryRepository
from app.infrastructure.database.monitoring_repository import SqlAlchemyMonitoringRepository
from app.infrastructure.database.profile_repository import SqlAlchemyProfileRepository
from app.infrastructure.database.reporting_repository import SqlAlchemyReportSettingsRepository
from app.infrastructure.database.session import create_session_factory

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
DAY = date(2026, 10, 16)


def daily(channel: Channel, status: DeliveryStatus, created_at: datetime = NOW) -> Delivery:
    """A daily report delivery about `DAY`."""
    failed = status is DeliveryStatus.FAILED
    return Delivery(
        channel=channel,
        kind=DeliveryKind.DAILY_REPORT,
        status=status,
        created_at=created_at,
        day=DAY,
        error_code=NotificationFailure.UNAVAILABLE if failed else None,
    )


async def test_deliveries_round_trip_newest_first(engine: AsyncEngine) -> None:
    repository = SqlAlchemyDeliveryRepository(create_session_factory(engine))
    test = Delivery(Channel.TELEGRAM, DeliveryKind.TEST, DeliveryStatus.SENT, NOW)
    failed = daily(Channel.EMAIL, DeliveryStatus.FAILED, NOW + timedelta(minutes=1))

    await repository.record(test)
    await repository.record(failed)

    listed = await repository.list_deliveries(10)
    assert [replace(d, id=None) for d in listed] == [failed, test]
    assert all(d.id is not None for d in listed)
    assert len(await repository.list_deliveries(1)) == 1


async def test_daily_report_sent_only_counts_successes_of_that_channel_and_day(
    engine: AsyncEngine,
) -> None:
    repository = SqlAlchemyDeliveryRepository(create_session_factory(engine))
    await repository.record(daily(Channel.EMAIL, DeliveryStatus.FAILED))
    assert await repository.daily_report_sent(Channel.EMAIL, DAY) is False

    await repository.record(daily(Channel.EMAIL, DeliveryStatus.SENT))

    assert await repository.daily_report_sent(Channel.EMAIL, DAY) is True
    assert await repository.daily_report_sent(Channel.TELEGRAM, DAY) is False
    assert await repository.daily_report_sent(Channel.EMAIL, DAY + timedelta(days=1)) is False


async def test_the_database_forbids_a_second_successful_daily_report(engine: AsyncEngine) -> None:
    repository = SqlAlchemyDeliveryRepository(create_session_factory(engine))
    await repository.record(daily(Channel.EMAIL, DeliveryStatus.SENT))
    await repository.record(daily(Channel.EMAIL, DeliveryStatus.FAILED))  # failures may repeat
    await repository.record(daily(Channel.TELEGRAM, DeliveryStatus.SENT))  # other channel
    second = daily(Channel.EMAIL, DeliveryStatus.SENT)

    with pytest.raises(RepositoryError):
        await repository.record(second)


async def test_pending_alerts_and_marking_them(engine: AsyncEngine) -> None:
    sessions = create_session_factory(engine)
    profile = await SqlAlchemyProfileRepository(sessions).add(
        "Web", ProfileRule(environment="production"), NOW
    )
    monitoring = SqlAlchemyMonitoringRepository(sessions)
    base = Alert(
        profile_id=profile.id,
        kind=AlertKind.ANOMALY,
        scope=AlertScope.TRACE,
        metric=AlertMetric.LATENCY,
        severity=Severity.WARNING,
        observed=Decimal(3),
        threshold=Decimal(2),
        baseline_p95=Decimal(2),
        day=None,
        affected_count=1,
        worst_trace_id="t",
        created_at=NOW,
    )
    await monitoring.save_alerts(
        [base, replace(base, created_at=NOW - timedelta(days=2)), replace(base, notified_at=NOW)]
    )

    (pending,) = await monitoring.pending_alerts(NOW - timedelta(hours=24))
    assert pending.id is not None
    await monitoring.mark_notified([pending.id], NOW + timedelta(minutes=5))
    await monitoring.mark_notified([], NOW)

    assert await monitoring.pending_alerts(NOW - timedelta(hours=24)) == []
    marked = {a.id: a.notified_at for a in await monitoring.list_alerts(None, None, 10)}
    assert marked[pending.id] == NOW + timedelta(minutes=5)


async def test_channel_notification_settings_persist(engine: AsyncEngine) -> None:
    repository = SqlAlchemyReportSettingsRepository(create_session_factory(engine))
    assert await repository.get(Channel.TELEGRAM) == ChannelSettings(
        Channel.TELEGRAM,
        True,
        ("overview", "alerts", "business_events"),
        alerts_enabled=True,
        scan_summary_enabled=False,
    )

    changed = ChannelSettings(
        Channel.TELEGRAM, False, ("alerts",), alerts_enabled=False, scan_summary_enabled=True
    )
    await repository.save(changed)

    assert await repository.get(Channel.TELEGRAM) == changed
