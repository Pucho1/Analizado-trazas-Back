"""Use cases for reports: build the daily report and manage per-channel settings."""

from collections.abc import Mapping, Sequence
from datetime import date

from app.application.reports.content import RenderedReport, Report
from app.application.reports.data import ReportDataBuilder
from app.application.reports.formatting import long_date
from app.application.reports.presenters import ReportPresenter
from app.application.reports.registry import SectionRegistry
from app.application.reports.sections import ReportSection
from app.application.spend_service import SpendService
from app.core.exceptions import UnknownReportSectionError
from app.domain.ports import ReportSettingsRepository
from app.domain.reporting import Channel, ChannelSettings


class ReportService:
    """Builds reports from the registered sections and lays them out per channel."""

    def __init__(
        self,
        *,
        registry: SectionRegistry,
        settings: ReportSettingsRepository,
        data: ReportDataBuilder,
        presenters: Mapping[Channel, ReportPresenter],
        spend: SpendService,
    ) -> None:
        """Sections from `registry`, data from `data`, one presenter per channel."""
        self._registry = registry
        self._settings = settings
        self._data = data
        self._presenters = presenters
        self._spend = spend

    def yesterday(self) -> date:
        """The last complete local day: what a daily report is about by default."""
        return self._spend.yesterday()

    def sections(self) -> list[ReportSection]:
        """Every registered section."""
        return self._registry.all()

    async def settings(self, channel: Channel) -> ChannelSettings:
        """The channel's settings."""
        return await self._settings.get(channel)

    async def update_settings(
        self, channel: Channel, *, enabled: bool, sections: Sequence[str]
    ) -> ChannelSettings:
        """Replace the channel's settings; every section must be registered."""
        unknown = [key for key in sections if self._registry.get(key) is None]
        if unknown:
            raise UnknownReportSectionError(f"Unknown report sections: {unknown}")
        settings = ChannelSettings(channel, enabled=enabled, sections=tuple(sections))
        await self._settings.save(settings)
        return settings

    async def build(self, day: date, section_keys: Sequence[str]) -> Report:
        """The report of `day` with the given sections in order.

        Sections with nothing to say are left out, and so are keys no longer registered.
        """
        data = await self._data.build(day)
        sections = []
        for key in section_keys:
            section = self._registry.get(key)
            content = section.build(data) if section is not None else None
            if content is not None:
                sections.append(content)
        return Report(title=f"Observa · {long_date(day)}", day=day, sections=tuple(sections))

    async def daily(self, channel: Channel, day: date | None = None) -> RenderedReport:
        """The daily report of `day` (default: yesterday) laid out for `channel`."""
        settings = await self._settings.get(channel)
        report = await self.build(day or self.yesterday(), settings.sections)
        return self._presenters[channel].render(report)
