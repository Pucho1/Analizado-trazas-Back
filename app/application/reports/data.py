"""Everything a daily report can say, computed once before the sections run.

Sections only read `ReportData`; they never query storage. A new section that needs
new data adds a field here and fills it in `ReportDataBuilder`.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.application.spend_service import SpendService
from app.core.constants import ALERTS_MAX_LIMIT, LEARNING_TRACES
from app.domain.events import EventSummary, summarize_events
from app.domain.models import Profile, ProfileStatus
from app.domain.monitoring import Alert, baseline_days, daily_totals, day_bounds
from app.domain.ports import (
    EventMappingRepository,
    MonitoringRepository,
    ProfileRepository,
    SpendRepository,
)
from app.domain.spend import DayRange, PeriodComparison, SpendTotals, typical_value


@dataclass(frozen=True, slots=True)
class TypicalDay:
    """A normal day of a profile (None where there is not enough history)."""

    queries: Decimal | None
    cost: Decimal | None


@dataclass(frozen=True, slots=True)
class ProfileDay:
    """One active profile on the report day."""

    profile: Profile
    totals: SpendTotals
    previous: SpendTotals
    typical: TypicalDay
    trace_count: int
    events: EventSummary
    alerts: tuple[Alert, ...]

    @property
    def learning(self) -> bool:
        """Whether the profile still lacks the traces to know what is normal."""
        return self.trace_count < LEARNING_TRACES


@dataclass(frozen=True, slots=True)
class ReportData:
    """The report day: each active profile, the unclassified traffic and the trends."""

    day: date
    profiles: tuple[ProfileDay, ...]
    unclassified: SpendTotals
    unclassified_previous: SpendTotals
    comparisons: tuple[PeriodComparison, ...]

    @property
    def total(self) -> SpendTotals:
        """Everything spent on the report day (active profiles and unclassified)."""
        return sum((p.totals for p in self.profiles), start=self.unclassified)

    @property
    def previous_total(self) -> SpendTotals:
        """Everything spent the day before."""
        return sum((p.previous for p in self.profiles), start=self.unclassified_previous)


class ReportDataBuilder:
    """Gathers `ReportData` for one local day from the repositories."""

    def __init__(
        self,
        *,
        profiles: ProfileRepository,
        spend: SpendRepository,
        monitoring: MonitoringRepository,
        mappings: EventMappingRepository,
        spend_service: SpendService,
        timezone: ZoneInfo,
    ) -> None:
        """Read from the repositories; days are local to `timezone`."""
        self._profiles = profiles
        self._spend = spend
        self._monitoring = monitoring
        self._mappings = mappings
        self._spend_service = spend_service
        self._timezone = timezone

    async def build(self, day: date) -> ReportData:
        """The data of `day` (archived profiles are left out: they are not monitored)."""
        today = await self._spend_service.totals(DayRange(day, day))
        before = day - timedelta(days=1)
        yesterday = await self._spend_service.totals(DayRange(before, before))
        profiles = [
            await self._profile_day(profile, day, today, yesterday)
            for profile in await self._profiles.list_profiles()
            if profile.status is ProfileStatus.ACTIVE
        ]
        _, comparisons = await self._spend_service.comparisons(day)
        return ReportData(
            day=day,
            profiles=tuple(profiles),
            unclassified=today.get(None, SpendTotals.zero()),
            unclassified_previous=yesterday.get(None, SpendTotals.zero()),
            comparisons=tuple(comparisons),
        )

    async def _profile_day(
        self,
        profile: Profile,
        day: date,
        today: Mapping[int | None, SpendTotals],
        yesterday: Mapping[int | None, SpendTotals],
    ) -> ProfileDay:
        """One profile's figures, normal day, events and alerts for `day`."""
        start, end = day_bounds(day, self._timezone)
        steps = await self._spend.step_counts(profile.id, start, end)
        mappings = await self._mappings.list_mappings(profile.id)
        return ProfileDay(
            profile=profile,
            totals=today.get(profile.id, SpendTotals.zero()),
            previous=yesterday.get(profile.id, SpendTotals.zero()),
            typical=await self._typical_day(profile, start),
            trace_count=await self._monitoring.count_traces(profile.id, profile.baseline_since),
            events=summarize_events(steps, mappings),
            alerts=await self._alerts(profile.id, day, start, end),
        )

    async def _typical_day(self, profile: Profile, day_start: datetime) -> TypicalDay:
        """A normal day from the complete days before the report day."""
        since = profile.baseline_since
        first = await self._monitoring.first_trace_at(profile.id, since)
        days = baseline_days(first, day_start, self._timezone)
        if not days:
            return TypicalDay(queries=None, cost=None)
        range_start, _ = day_bounds(days[0], self._timezone)
        _, range_end = day_bounds(days[-1], self._timezone)
        if since is not None:
            range_start = max(range_start, since)
        samples = await self._monitoring.traces_between(profile.id, range_start, range_end)
        totals = daily_totals(samples, days, self._timezone)
        return TypicalDay(
            queries=typical_value([Decimal(t.query_count) for t in totals]),
            cost=typical_value([t.cost for t in totals]),
        )

    async def _alerts(
        self, profile_id: int, day: date, start: datetime, end: datetime
    ) -> tuple[Alert, ...]:
        """Alerts about the report day: per-day ones for that day, per-trace ones raised then."""
        alerts = await self._monitoring.list_alerts(profile_id, start, ALERTS_MAX_LIMIT)
        about_day = [
            alert
            for alert in alerts
            if alert.day == day or (alert.day is None and alert.created_at < end)
        ]
        return tuple(sorted(about_day, key=lambda alert: (alert.created_at, alert.id or 0)))
