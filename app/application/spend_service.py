"""Use cases for spend: totals per profile and comparisons between periods."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.application.trace_service import utc_now
from app.core.exceptions import ProfileNotFoundError
from app.domain.models import Profile, TimeRange
from app.domain.monitoring import day_bounds, local_day
from app.domain.ports import ProfileRepository, SpendRepository
from app.domain.spend import (
    DayRange,
    PeriodComparison,
    SpendTotals,
    comparison_ranges,
    share,
)


@dataclass(frozen=True, slots=True)
class ProfileSpend:
    """What one profile spent and its share of the total cost (None if nothing was spent)."""

    profile: Profile
    totals: SpendTotals
    cost_share: Decimal | None


@dataclass(frozen=True, slots=True)
class SpendSummary:
    """Spend per profile (largest cost first), unclassified spend and the total."""

    start: datetime
    end: datetime
    profiles: list[ProfileSpend]
    unclassified: SpendTotals
    total: SpendTotals


class SpendService:
    """Spend over relative ranges (for the screen) and over local calendar days (reports)."""

    def __init__(
        self,
        spend: SpendRepository,
        profiles: ProfileRepository,
        timezone: ZoneInfo,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        """Aggregate with `spend`; days are local to `timezone`."""
        self._spend = spend
        self._profiles = profiles
        self._timezone = timezone
        self._clock = clock

    def yesterday(self) -> date:
        """The last complete local day."""
        return local_day(self._clock(), self._timezone) - timedelta(days=1)

    async def summary(self, time_range: TimeRange) -> SpendSummary:
        """Spend of every profile (including archived ones) in the window ending now."""
        end = self._clock()
        start = end - time_range.duration
        by_profile = await self._spend.totals_by_profile(start, end)
        total = sum(by_profile.values(), start=SpendTotals.zero())
        items = [
            ProfileSpend(profile, totals, share(totals.cost, total.cost))
            for profile in await self._profiles.list_profiles()
            if (totals := by_profile.get(profile.id)) is not None
        ]
        items.sort(key=lambda item: (-item.totals.cost, item.profile.id))
        return SpendSummary(
            start=start,
            end=end,
            profiles=items,
            unclassified=by_profile.get(None, SpendTotals.zero()),
            total=total,
        )

    async def totals(self, days: DayRange) -> Mapping[int | None, SpendTotals]:
        """Totals per profile id (None = unclassified) over local days."""
        start, _ = day_bounds(days.first, self._timezone)
        _, end = day_bounds(days.last, self._timezone)
        return await self._spend.totals_by_profile(start, end)

    async def comparisons(
        self, day: date | None = None, profile_id: int | None = None
    ) -> tuple[date, list[PeriodComparison]]:
        """Day, week and month up to `day` (default yesterday) vs the previous ones.

        For one profile if `profile_id` is given (`ProfileNotFoundError` if absent),
        otherwise for every trace.
        """
        if profile_id is not None and await self._profiles.get(profile_id) is None:
            raise ProfileNotFoundError(f"Profile {profile_id} does not exist")
        day = day or self.yesterday()
        comparisons = []
        for period, (current_range, previous_range) in comparison_ranges(day).items():
            current = await self._totals_of(current_range, profile_id)
            previous = await self._totals_of(previous_range, profile_id)
            comparisons.append(
                PeriodComparison(period, current_range, previous_range, current, previous)
            )
        return day, comparisons

    async def _totals_of(self, days: DayRange, profile_id: int | None) -> SpendTotals:
        """Totals of one profile, or of everything if `profile_id` is None."""
        by_profile = await self.totals(days)
        if profile_id is None:
            return sum(by_profile.values(), start=SpendTotals.zero())
        return by_profile.get(profile_id, SpendTotals.zero())
