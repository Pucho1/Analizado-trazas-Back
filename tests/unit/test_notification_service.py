"""`NotificationService` with fake notifiers and in-memory repositories."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.application.notification_service import NotificationService
from app.application.reports.data import ReportDataBuilder
from app.application.reports.presenters import EmailPresenter, ReportPresenter, TelegramPresenter
from app.application.reports.registry import default_registry
from app.application.reports.service import ReportService
from app.application.spend_service import SpendService
from app.core.constants import PENDING_ALERTS_MAX_AGE
from app.core.exceptions import (
    ChannelNotConfiguredError,
    NotificationError,
    ReportAlreadySentError,
)
from app.domain.models import ProfileRule, SyncRun, SyncStatus, SyncTrigger
from app.domain.monitoring import Alert, AlertKind, AlertMetric, AlertScope, Severity
from app.domain.notifications import (
    DeliveryKind,
    DeliveryStatus,
    NotificationFailure,
    TelegramChat,
)
from app.domain.reporting import Channel, ChannelSettings
from tests.unit.fakes import (
    FakeChatDirectory,
    FakeNotifier,
    InMemoryDeliveryRepository,
    InMemoryEventMappingRepository,
    InMemoryMonitoringRepository,
    InMemoryProfileRepository,
    InMemoryReportSettingsRepository,
    InMemorySpendRepository,
    make_profile,
    make_trace,
)

pytestmark = pytest.mark.anyio

MADRID = ZoneInfo("Europe/Madrid")
# 14:05 in Madrid on Saturday 17: the morning report (08:30) is due.
AFTERNOON = datetime(2026, 10, 17, 12, 5, tzinfo=UTC)
YESTERDAY = date(2026, 10, 16)
REPORT_TIME = time(8, 30)


def alert(id: int, *, profile_id: int = 1, created_at: datetime = AFTERNOON) -> Alert:
    """A stored, not yet notified alert."""
    return Alert(
        id=id,
        profile_id=profile_id,
        kind=AlertKind.ANOMALY,
        scope=AlertScope.TRACE,
        metric=AlertMetric.LATENCY,
        severity=Severity.CRITICAL,
        observed=Decimal(10),
        threshold=Decimal("2.4"),
        baseline_p95=Decimal("1.2"),
        day=None,
        affected_count=1,
        worst_trace_id="t",
        created_at=created_at,
    )


RUN = SyncRun(
    source="langfuse",
    trigger=SyncTrigger.SCHEDULED,
    status=SyncStatus.SUCCEEDED,
    started_at=AFTERNOON,
    finished_at=AFTERNOON,
    window_start=AFTERNOON - timedelta(hours=7),
    window_end=AFTERNOON,
    trace_count=23,
)


class World:
    """Two channels, one profile with a trace yesterday, and the service under test."""

    def __init__(
        self,
        *,
        now: datetime = AFTERNOON,
        email: FakeNotifier | None = None,
        telegram: FakeNotifier | None = None,
        configured: tuple[Channel, ...] = (Channel.EMAIL, Channel.TELEGRAM),
        chats: FakeChatDirectory | None = None,
    ) -> None:
        """Build the service; only the `configured` channels get a notifier."""
        self.now = now
        self.profiles = InMemoryProfileRepository(
            [make_trace(id="y", timestamp=datetime(2026, 10, 16, 10, tzinfo=UTC), name="web")]
        )
        self.profiles.profiles[1] = make_profile(
            id=1, name="Asistente de la web", rule=ProfileRule(environment="production")
        )
        self.profiles.profile_of["y"] = 1
        self.monitoring = InMemoryMonitoringRepository(self.profiles)
        self.settings = InMemoryReportSettingsRepository()
        self.deliveries = InMemoryDeliveryRepository()
        self.email = email or FakeNotifier()
        self.telegram = telegram or FakeNotifier()
        all_notifiers = {Channel.EMAIL: self.email, Channel.TELEGRAM: self.telegram}
        spend = SpendService(
            InMemorySpendRepository(self.profiles), self.profiles, MADRID, self.clock
        )
        presenters: dict[Channel, ReportPresenter] = {
            Channel.EMAIL: EmailPresenter(),
            Channel.TELEGRAM: TelegramPresenter(),
        }
        reports = ReportService(
            registry=default_registry(),
            settings=self.settings,
            data=ReportDataBuilder(
                profiles=self.profiles,
                spend=InMemorySpendRepository(self.profiles),
                monitoring=self.monitoring,
                mappings=InMemoryEventMappingRepository(),
                spend_service=spend,
                timezone=MADRID,
            ),
            presenters=presenters,
            spend=spend,
        )
        self.service = NotificationService(
            notifiers={channel: all_notifiers[channel] for channel in configured},
            presenters=presenters,
            settings=self.settings,
            deliveries=self.deliveries,
            monitoring=self.monitoring,
            profiles=self.profiles,
            reports=reports,
            timezone=MADRID,
            daily_report_time=REPORT_TIME,
            telegram_chats=chats,
            clock=self.clock,
        )

    def clock(self) -> datetime:
        """The fake current time."""
        return self.now

    def kinds(self) -> list[tuple[Channel, DeliveryKind, DeliveryStatus]]:
        """Recorded deliveries, compactly."""
        return [(d.channel, d.kind, d.status) for d in self.deliveries.deliveries]


# --- daily report -------------------------------------------------------------------


async def test_daily_report_is_sent_once_per_channel_and_day() -> None:
    world = World()

    first = await world.service.send_daily_report_if_due()
    second = await world.service.send_daily_report_if_due()

    assert [(d.channel, d.day, d.status) for d in first] == [
        (Channel.EMAIL, YESTERDAY, DeliveryStatus.SENT),
        (Channel.TELEGRAM, YESTERDAY, DeliveryStatus.SENT),
    ]
    assert second == []
    assert world.email.sent[0].subject == "Observa · viernes 16 de octubre de 2026"
    assert world.email.sent[0].content_type.startswith("text/html")
    assert "«Asistente de la web», viernes 16: 1 consulta" in world.telegram.sent[0].body


@pytest.mark.parametrize(
    ("now", "due"),
    [
        (datetime(2026, 10, 17, 6, 29, tzinfo=UTC), False),  # 08:29 in Madrid
        (datetime(2026, 10, 17, 6, 30, tzinfo=UTC), True),  # 08:30 exactly
        (datetime(2026, 10, 17, 21, 59, tzinfo=UTC), True),  # 23:59, still that day
        (datetime(2026, 10, 17, 22, 0, tzinfo=UTC), False),  # 00:00 next day: before 08:30
    ],
)
async def test_daily_report_is_due_from_the_configured_local_time(now: datetime, due: bool) -> None:
    sent = await World(now=now).service.send_daily_report_if_due()

    assert bool(sent) is due


async def test_a_failed_daily_report_is_retried_later() -> None:
    world = World(telegram=FakeNotifier(NotificationFailure.UNAVAILABLE))
    await world.service.send_daily_report_if_due()
    world.telegram.failure = None
    world.now += timedelta(hours=6)

    retried = await world.service.send_daily_report_if_due()

    assert [(d.channel, d.status) for d in retried] == [(Channel.TELEGRAM, DeliveryStatus.SENT)]
    assert world.kinds() == [
        (Channel.EMAIL, DeliveryKind.DAILY_REPORT, DeliveryStatus.SENT),
        (Channel.TELEGRAM, DeliveryKind.DAILY_REPORT, DeliveryStatus.FAILED),
        (Channel.TELEGRAM, DeliveryKind.DAILY_REPORT, DeliveryStatus.SENT),
    ]
    assert world.deliveries.deliveries[1].error_code is NotificationFailure.UNAVAILABLE


async def test_disabled_or_unconfigured_channels_get_no_daily_report() -> None:
    world = World(configured=(Channel.EMAIL,))
    await world.settings.save(ChannelSettings(Channel.EMAIL, False, ("overview",)))

    assert await world.service.send_daily_report_if_due() == []
    assert world.email.sent == world.telegram.sent == []


async def test_manual_daily_report() -> None:
    world = World()

    delivery = await world.service.send_daily_report(Channel.TELEGRAM, date(2026, 10, 5))

    assert (delivery.day, delivery.status) == (date(2026, 10, 5), DeliveryStatus.SENT)
    with pytest.raises(ReportAlreadySentError):
        await world.service.send_daily_report(Channel.TELEGRAM, date(2026, 10, 5))


async def test_manual_daily_report_failures_are_recorded_and_raised() -> None:
    world = World(email=FakeNotifier(NotificationFailure.AUTH_FAILED))

    with pytest.raises(NotificationError) as raised:
        await world.service.send_daily_report(Channel.EMAIL)

    assert raised.value.reason == "auth_failed"
    assert world.kinds() == [(Channel.EMAIL, DeliveryKind.DAILY_REPORT, DeliveryStatus.FAILED)]


async def test_manual_actions_on_an_unconfigured_channel() -> None:
    service = World(configured=()).service

    with pytest.raises(ChannelNotConfiguredError):
        await service.send_daily_report(Channel.EMAIL)
    with pytest.raises(ChannelNotConfiguredError):
        await service.send_test(Channel.TELEGRAM)
    with pytest.raises(ChannelNotConfiguredError):
        await service.telegram_chats()


# --- alerts -------------------------------------------------------------------------


async def test_pending_alerts_go_in_one_message_per_channel_and_are_marked() -> None:
    world = World()
    world.monitoring.alerts = [alert(1), alert(2), alert(3, profile_id=9)]

    sent = await world.service.send_pending_alerts()

    assert [a.id for a in sent] == [1, 2, 3]
    assert len(world.email.sent) == len(world.telegram.sent) == 1
    message = world.telegram.sent[0]
    assert message.subject.startswith("[Requiere atención] Observa · Alertas · sábado 17, 14:05")
    assert "<b>3 Alertas nuevas</b>" in message.body
    assert "<i>Asistente de la web</i>" in message.body
    assert "<i>Perfil eliminado</i>" in message.body
    assert {a.notified_at for a in world.monitoring.alerts} == {AFTERNOON}
    assert await world.service.send_pending_alerts() == []


async def test_alerts_stay_pending_if_every_channel_fails() -> None:
    world = World(
        email=FakeNotifier(NotificationFailure.UNAVAILABLE),
        telegram=FakeNotifier(NotificationFailure.REJECTED),
    )
    world.monitoring.alerts = [alert(1)]

    assert await world.service.send_pending_alerts() == []

    assert world.monitoring.alerts[0].notified_at is None
    assert [d.status for d in world.deliveries.deliveries] == [DeliveryStatus.FAILED] * 2


async def test_one_channel_is_enough_to_mark_alerts_notified() -> None:
    world = World(email=FakeNotifier(NotificationFailure.UNAVAILABLE))
    world.monitoring.alerts = [alert(1)]

    await world.service.send_pending_alerts()

    assert world.monitoring.alerts[0].notified_at == AFTERNOON


async def test_alerts_respect_the_channel_setting() -> None:
    world = World()
    await world.settings.save(
        ChannelSettings(Channel.EMAIL, True, ("overview",), alerts_enabled=False)
    )
    world.monitoring.alerts = [alert(1)]

    await world.service.send_pending_alerts()

    assert (len(world.email.sent), len(world.telegram.sent)) == (0, 1)


async def test_old_pending_alerts_are_dropped() -> None:
    world = World()
    world.monitoring.alerts = [
        alert(1, created_at=AFTERNOON - PENDING_ALERTS_MAX_AGE - timedelta(seconds=1))
    ]

    assert await world.service.send_pending_alerts() == []
    assert world.email.sent == []


async def test_without_alerts_nothing_is_sent() -> None:
    world = World(now=datetime(2026, 10, 17, 6, 0, tzinfo=UTC))  # before the report time

    await world.service.after_sync(RUN)

    assert world.email.sent == world.telegram.sent == []
    assert world.deliveries.deliveries == []


# --- after a scan -------------------------------------------------------------------


async def test_after_sync_sends_alerts_and_the_due_report() -> None:
    world = World()
    world.monitoring.alerts = [alert(1)]

    await world.service.after_sync(RUN)

    assert world.kinds() == [
        (Channel.EMAIL, DeliveryKind.ALERTS, DeliveryStatus.SENT),
        (Channel.TELEGRAM, DeliveryKind.ALERTS, DeliveryStatus.SENT),
        (Channel.EMAIL, DeliveryKind.DAILY_REPORT, DeliveryStatus.SENT),
        (Channel.TELEGRAM, DeliveryKind.DAILY_REPORT, DeliveryStatus.SENT),
    ]


async def test_scan_summary_only_where_enabled() -> None:
    world = World(now=datetime(2026, 10, 17, 6, 0, tzinfo=UTC))
    await world.settings.save(
        ChannelSettings(Channel.TELEGRAM, True, ("overview",), scan_summary_enabled=True)
    )

    await world.service.after_sync(RUN)

    assert world.email.sent == []
    (summary,) = world.telegram.sent
    assert "Consultas revisadas: <b>23</b>" in summary.body
    assert "Alertas: <b>ninguna</b>" in summary.body


async def test_after_sync_never_raises() -> None:
    world = World(
        email=FakeNotifier(NotificationFailure.AUTH_FAILED),
        telegram=FakeNotifier(NotificationFailure.UNAVAILABLE),
    )
    world.monitoring.alerts = [alert(1)]

    await world.service.after_sync(RUN)

    assert {d.status for d in world.deliveries.deliveries} == {DeliveryStatus.FAILED}


# --- test messages, channels and chats ----------------------------------------------


async def test_send_test() -> None:
    world = World()

    delivery = await world.service.send_test(Channel.TELEGRAM)

    assert (delivery.kind, delivery.status) == (DeliveryKind.TEST, DeliveryStatus.SENT)
    assert "ya puede escribirte por este canal" in world.telegram.sent[0].body


async def test_failed_test_raises_with_its_reason() -> None:
    world = World(telegram=FakeNotifier(NotificationFailure.REJECTED))

    with pytest.raises(NotificationError) as raised:
        await world.service.send_test(Channel.TELEGRAM)

    assert raised.value.reason == "rejected"


async def test_channels_and_deliveries() -> None:
    world = World(configured=(Channel.TELEGRAM,))
    await world.service.send_test(Channel.TELEGRAM)

    channels = await world.service.channels()
    deliveries = await world.service.list_deliveries(10)

    assert [(c.channel, c.configured) for c in channels] == [
        (Channel.EMAIL, False),
        (Channel.TELEGRAM, True),
    ]
    assert [d.kind for d in deliveries] == [DeliveryKind.TEST]


async def test_telegram_chats() -> None:
    chat = TelegramChat(id="42", name="Miguel", kind="private")
    world = World(configured=(), chats=FakeChatDirectory([chat]))

    assert await world.service.telegram_chats() == [chat]
