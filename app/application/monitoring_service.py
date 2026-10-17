"""Use cases for baselines and alerts: recalculate, evaluate, relearn and tune."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from app.application.trace_service import utc_now
from app.core.constants import BASELINE_MAX_TRACES, MONITORING_MAX_LOOKBACK
from app.core.exceptions import InvalidThresholdsError, ProfileNotFoundError
from app.domain.models import Profile, ProfileStatus, SyncRun
from app.domain.monitoring import (
    Alert,
    Baseline,
    DayTotal,
    apply_silence,
    baseline_days,
    compute_baseline,
    daily_totals,
    day_bounds,
    days_between,
    evaluate_days,
    evaluate_traces,
)
from app.domain.ports import MonitoringRepository, ProfileRepository

# Sentinel for "field not sent" in threshold updates (None means "remove the budget").
UNCHANGED: Any = object()


@dataclass(frozen=True, slots=True)
class BaselineView:
    """A profile's current baseline, as shown to the user."""

    profile: Profile
    baseline: Baseline


class MonitoringService:
    """After every sync, recalculate each active profile's baseline and raise alerts.

    Only traces and days within `MONITORING_MAX_LOOKBACK` of now are judged, so a long
    backfill never raises alerts about old data. Archived profiles are not monitored.
    """

    def __init__(
        self,
        profiles: ProfileRepository,
        monitoring: MonitoringRepository,
        timezone: ZoneInfo,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        """Read profiles and traces from the repositories; days are local to `timezone`."""
        self._profiles = profiles
        self._monitoring = monitoring
        self._timezone = timezone
        self._clock = clock

    async def after_sync(self, run: SyncRun) -> None:
        """`SyncService` hook: evaluate the window the sync has just read."""
        await self.evaluate(run.window_start, run.window_end)

    async def evaluate(self, start: datetime, end: datetime) -> list[Alert]:
        """Recalculate baselines and store the new alerts for traces in [start, end)."""
        now = self._clock()
        start = max(start, now - MONITORING_MAX_LOOKBACK)
        raised: list[Alert] = []
        for profile in await self._profiles.list_profiles():
            if profile.status is ProfileStatus.ACTIVE:
                raised.extend(await self._evaluate_profile(profile, start, end, now))
        return raised

    async def baseline(self, profile_id: int) -> BaselineView:
        """The profile's baseline as of now (computed, not stored)."""
        profile = await self._get(profile_id)
        return BaselineView(profile, await self._compute_baseline(profile, self._clock()))

    async def relearn(self, profile_id: int) -> Profile:
        """Start the baseline again from now: the profile learns from new traces only."""
        profile = await self._get(profile_id)
        relearning = replace(profile, baseline_since=self._clock())
        await self._profiles.update(relearning)
        await self._monitoring.delete_baseline(profile_id)
        return relearning

    async def update_thresholds(
        self,
        profile_id: int,
        *,
        warning_factor: Decimal = UNCHANGED,
        critical_factor: Decimal = UNCHANGED,
        daily_budget: Decimal | None = UNCHANGED,
        min_trace_cost: Decimal = UNCHANGED,
        min_daily_cost: Decimal = UNCHANGED,
        silence_hours: int = UNCHANGED,
    ) -> Profile:
        """Change the thresholds given; raises `InvalidThresholdsError` if inconsistent."""
        profile = await self._get(profile_id)
        changes: dict[str, Any] = {
            name: value
            for name, value in {
                "warning_factor": warning_factor,
                "critical_factor": critical_factor,
                "daily_budget": daily_budget,
                "min_trace_cost": min_trace_cost,
                "min_daily_cost": min_daily_cost,
                "silence_hours": silence_hours,
            }.items()
            if value is not UNCHANGED
        }
        try:
            thresholds = replace(profile.thresholds, **changes)
        except ValueError as error:
            raise InvalidThresholdsError(str(error)) from error
        updated = replace(profile, thresholds=thresholds)
        await self._profiles.update(updated)
        return updated

    async def list_alerts(
        self, profile_id: int | None, since: datetime | None, limit: int
    ) -> list[Alert]:
        """Stored alerts, newest first."""
        return list(await self._monitoring.list_alerts(profile_id, since, limit))

    async def _evaluate_profile(
        self, profile: Profile, start: datetime, end: datetime, now: datetime
    ) -> list[Alert]:
        """Refresh the profile's baseline, then store the alerts that pass the silence."""
        baseline = await self._compute_baseline(profile, now)
        await self._monitoring.save_baseline(profile.id, baseline)
        thresholds = profile.thresholds
        samples = await self._monitoring.traces_between(profile.id, start, end)
        totals = await self._day_totals(profile.id, days_between(start, end, self._timezone), None)
        candidates = evaluate_traces(profile.id, samples, baseline, thresholds, now)
        candidates += evaluate_days(profile.id, totals, baseline, thresholds, now)
        if not candidates:
            return []
        recent = await self._monitoring.alerts_since(profile.id, now - thresholds.silence)
        alerts = apply_silence(candidates, recent, thresholds.silence, now)
        await self._monitoring.save_alerts(alerts)
        return alerts

    async def _compute_baseline(self, profile: Profile, now: datetime) -> Baseline:
        """Baseline from the profile's traces since `baseline_since`."""
        since = profile.baseline_since
        first = await self._monitoring.first_trace_at(profile.id, since)
        return compute_baseline(
            samples=await self._monitoring.latest_traces(profile.id, since, BASELINE_MAX_TRACES),
            days=await self._day_totals(
                profile.id, baseline_days(first, now, self._timezone), since
            ),
            profile_trace_count=await self._monitoring.count_traces(profile.id, since),
            previous=await self._monitoring.get_baseline(profile.id),
            critical_factor=profile.thresholds.critical_factor,
            now=now,
        )

    async def _day_totals(
        self, profile_id: int, days: Sequence[date], since: datetime | None
    ) -> list[DayTotal]:
        """Totals of the profile for each local day in `days` (traces from `since` on)."""
        if not days:
            return []
        start, _ = day_bounds(days[0], self._timezone)
        _, end = day_bounds(days[-1], self._timezone)
        if since is not None:
            start = max(start, since)
        samples = await self._monitoring.traces_between(profile_id, start, end)
        return daily_totals(samples, days, self._timezone)

    async def _get(self, profile_id: int) -> Profile:
        """The profile, or `ProfileNotFoundError`."""
        profile = await self._profiles.get(profile_id)
        if profile is None:
            raise ProfileNotFoundError(f"Profile {profile_id} does not exist")
        return profile
