"""Use cases for notifications: the morning report, alerts after scans and tests.

Automatic flows (after a scan, the scheduled report) never raise: failures are
recorded as deliveries and logged, and the scan or scheduler carries on. Manual
actions (send now, test) raise so the caller learns what went wrong.
"""

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.application.reports.content import RenderedReport, Report
from app.application.reports.messages import (
    alerts_message,
    channel_test_message,
    scan_summary_message,
)
from app.application.reports.presenters import ReportPresenter
from app.application.reports.service import ReportService
from app.application.trace_service import utc_now
from app.core.constants import PENDING_ALERTS_MAX_AGE
from app.core.exceptions import (
    ChannelNotConfiguredError,
    NotificationError,
    ObservaError,
    ReportAlreadySentError,
)
from app.domain.models import SyncRun
from app.domain.monitoring import Alert
from app.domain.notifications import (
    Delivery,
    DeliveryKind,
    DeliveryStatus,
    NotificationFailure,
    OutgoingMessage,
    TelegramChat,
)
from app.domain.ports import (
    DeliveryRepository,
    MonitoringRepository,
    Notifier,
    ProfileRepository,
    ReportSettingsRepository,
    TelegramChatDirectory,
)
from app.domain.reporting import Channel, ChannelSettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ChannelStatus:
    """Whether a channel is configured in `.env`, and what it is set to receive."""

    channel: Channel
    configured: bool
    settings: ChannelSettings


class NotificationService:
    """Sends reports, alerts and tests through the configured channels."""

    def __init__(
        self,
        *,
        notifiers: Mapping[Channel, Notifier],
        presenters: Mapping[Channel, ReportPresenter],
        settings: ReportSettingsRepository,
        deliveries: DeliveryRepository,
        monitoring: MonitoringRepository,
        profiles: ProfileRepository,
        reports: ReportService,
        timezone: ZoneInfo,
        daily_report_time: time,
        telegram_chats: TelegramChatDirectory | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        """`notifiers` holds only the configured channels."""
        self._notifiers = notifiers
        self._presenters = presenters
        self._settings = settings
        self._deliveries = deliveries
        self._monitoring = monitoring
        self._profiles = profiles
        self._reports = reports
        self._timezone = timezone
        self._daily_report_time = daily_report_time
        self._telegram_chats = telegram_chats
        self._clock = clock
        self._daily_lock = asyncio.Lock()

    # --- automatic flows (never raise) -------------------------------------------------

    async def after_sync(self, run: SyncRun) -> None:
        """`SyncService` hook: pending alerts, optional scan summaries, and the morning
        report if it is due and still unsent (this also retries a failed one)."""
        sent: list[Alert] = []
        try:
            sent = await self.send_pending_alerts()
        except ObservaError:
            logger.warning("Sending alerts failed", exc_info=True)
        try:
            await self._send_scan_summaries(run, len(sent))
        except ObservaError:
            logger.warning("Sending scan summaries failed", exc_info=True)
        await self.run_daily_report()

    async def run_daily_report(self) -> None:
        """Scheduler entry point: send the morning report if due; log failures."""
        try:
            await self.send_daily_report_if_due()
        except ObservaError:
            logger.warning("Sending the daily report failed", exc_info=True)

    async def send_pending_alerts(self) -> list[Alert]:
        """Send every alert not yet notified (last 24 h) in one message per channel.

        Alerts are marked as notified once at least one channel got them; if every
        channel fails they stay pending for the next scan. Returns the alerts sent.
        """
        now = self._clock()
        pending = list(await self._monitoring.pending_alerts(now - PENDING_ALERTS_MAX_AGE))
        if not pending:
            return []
        names = {profile.id: profile.name for profile in await self._profiles.list_profiles()}
        report = alerts_message(pending, names, self._local(now))
        delivered = False
        for channel in self._notifiers:
            if (await self._settings.get(channel)).alerts_enabled:
                delivery = await self._deliver(channel, DeliveryKind.ALERTS, report)
                delivered = delivered or delivery.status is DeliveryStatus.SENT
        if not delivered:
            return []
        await self._monitoring.mark_notified([a.id for a in pending if a.id is not None], now)
        return pending

    async def send_daily_report_if_due(self) -> list[Delivery]:
        """Send the report about yesterday to every enabled channel that lacks it.

        Due from `daily_report_time` (local) on; never sent twice for the same day.
        """
        now = self._local(self._clock())
        if now.time() < self._daily_report_time:
            return []
        day = now.date() - timedelta(days=1)
        deliveries = []
        async with self._daily_lock:
            for channel in self._notifiers:
                settings = await self._settings.get(channel)
                if settings.enabled and not await self._deliveries.daily_report_sent(channel, day):
                    deliveries.append(await self._deliver_daily(channel, day))
        return deliveries

    async def _send_scan_summaries(self, run: SyncRun, new_alerts: int) -> None:
        """A short note to the channels that asked for one after every scan."""
        report = scan_summary_message(run, new_alerts, self._local(self._clock()))
        for channel in self._notifiers:
            if (await self._settings.get(channel)).scan_summary_enabled:
                await self._deliver(channel, DeliveryKind.SCAN_SUMMARY, report)

    # --- manual actions (raise) --------------------------------------------------------

    async def send_daily_report(self, channel: Channel, day: date | None = None) -> Delivery:
        """Send the daily report about `day` (default yesterday) through `channel` now.

        Raises `ChannelNotConfiguredError`, `ReportAlreadySentError` if it already
        reached the channel, or `NotificationError` if delivery fails.
        """
        self._require(channel)
        day = day or self._reports.yesterday()
        async with self._daily_lock:
            if await self._deliveries.daily_report_sent(channel, day):
                raise ReportAlreadySentError(f"The report about {day} already went by {channel}")
            delivery = await self._deliver_daily(channel, day)
        _raise_if_failed(delivery)
        return delivery

    async def send_test(self, channel: Channel) -> Delivery:
        """Send a test message; raises like `send_daily_report`."""
        self._require(channel)
        delivery = await self._deliver(
            channel, DeliveryKind.TEST, channel_test_message(self._local(self._clock()))
        )
        _raise_if_failed(delivery)
        return delivery

    async def channels(self) -> list[ChannelStatus]:
        """Every channel: configured or not, and its settings."""
        return [
            ChannelStatus(channel, channel in self._notifiers, await self._settings.get(channel))
            for channel in Channel
        ]

    async def list_deliveries(self, limit: int) -> Sequence[Delivery]:
        """The most recent deliveries, newest first."""
        return await self._deliveries.list_deliveries(limit)

    async def telegram_chats(self) -> Sequence[TelegramChat]:
        """Chats that wrote to the bot, to find TELEGRAM_CHAT_ID (needs only the token)."""
        if self._telegram_chats is None:
            raise ChannelNotConfiguredError("TELEGRAM_BOT_TOKEN is not set")
        return await self._telegram_chats.list_chats()

    # --- delivery ----------------------------------------------------------------------

    async def _deliver_daily(self, channel: Channel, day: date) -> Delivery:
        """Build, send and record the daily report of `day` for `channel`."""
        rendered = await self._reports.daily(channel, day)
        return await self._send_rendered(channel, DeliveryKind.DAILY_REPORT, rendered, day)

    async def _deliver(self, channel: Channel, kind: DeliveryKind, report: Report) -> Delivery:
        """Lay `report` out for `channel`, send it and record the delivery."""
        rendered = self._presenters[channel].render(report)
        return await self._send_rendered(channel, kind, rendered, None)

    async def _send_rendered(
        self, channel: Channel, kind: DeliveryKind, rendered: RenderedReport, day: date | None
    ) -> Delivery:
        """Send through the channel's notifier and record the outcome (never raises it)."""
        message = OutgoingMessage(rendered.subject, rendered.body, rendered.content_type)
        try:
            await self._notifiers[channel].send(message)
        except NotificationError as error:
            logger.warning("Delivery of %s by %s failed: %s", kind, channel, error)
            delivery = Delivery(
                channel=channel,
                kind=kind,
                status=DeliveryStatus.FAILED,
                created_at=self._clock(),
                day=day,
                error_code=NotificationFailure(error.reason),
            )
        else:
            delivery = Delivery(
                channel=channel,
                kind=kind,
                status=DeliveryStatus.SENT,
                created_at=self._clock(),
                day=day,
            )
        await self._deliveries.record(delivery)
        return delivery

    def _require(self, channel: Channel) -> None:
        """Raise `ChannelNotConfiguredError` unless the channel has its `.env` variables."""
        if channel not in self._notifiers:
            raise ChannelNotConfiguredError(f"The {channel} channel is not configured in .env")

    def _local(self, moment: datetime) -> datetime:
        """`moment` in the monitoring time zone."""
        return moment.astimezone(self._timezone)


def _raise_if_failed(delivery: Delivery) -> None:
    """Turn a failed delivery back into `NotificationError` for manual actions."""
    if delivery.error_code is not None:
        raise NotificationError(
            delivery.error_code.value, f"Delivery failed: {delivery.error_code}"
        )
