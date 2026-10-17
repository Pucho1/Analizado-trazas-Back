"""Use cases for profiles: manage them, discover candidates and classify traces."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from app.application.trace_service import utc_now
from app.core.constants import UNNAMED_TRACE_NAME
from app.core.exceptions import ProfileNotFoundError
from app.domain.models import (
    DiscoveredGroup,
    Profile,
    ProfileRule,
    ProfileStatus,
    SyncRun,
    classify,
)
from app.domain.ports import ProfileRepository


@dataclass(frozen=True, slots=True)
class ProfileWithCount:
    """A profile and how many stored traces belong to it."""

    profile: Profile
    trace_count: int


@dataclass(frozen=True, slots=True)
class ProfileOverview:
    """Every profile with its trace count, plus the traces no profile captures."""

    profiles: list[ProfileWithCount]
    unclassified_count: int


class ProfileService:
    """Profiles group traces by rule; whatever no rule captures is "unclassified".

    Stored traces are reclassified whenever profiles change and after every sync, so
    the assignment always reflects the current rules.
    """

    def __init__(
        self, profiles: ProfileRepository, clock: Callable[[], datetime] = utc_now
    ) -> None:
        """Use `profiles` for storage and `clock` to stamp creation and archive dates."""
        self._profiles = profiles
        self._clock = clock

    async def overview(self) -> ProfileOverview:
        """Every profile (oldest first) with its trace count, and the unclassified count."""
        profiles = await self._profiles.list_profiles()
        counts = await self._profiles.count_traces_by_profile()
        return ProfileOverview(
            profiles=[ProfileWithCount(profile, counts.get(profile.id, 0)) for profile in profiles],
            unclassified_count=counts.get(None, 0),
        )

    async def discover(self) -> list[DiscoveredGroup]:
        """Candidate profiles: groups of unclassified traces, largest first.

        Traces without a name (fragments whose root step never arrived) are not
        proposed; they stay unclassified.
        """
        groups = await self._profiles.discover_groups()
        return [group for group in groups if group.trace_name != UNNAMED_TRACE_NAME]

    async def create(self, name: str, rule: ProfileRule) -> Profile:
        """Create an active profile and classify the stored traces with it."""
        profile = await self._profiles.add(name.strip(), rule, self._clock())
        await self.reclassify()
        return profile

    async def update(
        self, profile_id: int, *, name: str | None = None, rule: ProfileRule | None = None
    ) -> Profile:
        """Rename the profile and/or change its rule; reclassify if the rule changed."""
        current = await self._get(profile_id)
        updated = replace(
            current,
            name=current.name if name is None else name.strip(),
            rule=current.rule if rule is None else rule,
        )
        await self._profiles.update(updated)
        if updated.rule != current.rule:
            await self.reclassify()
        return updated

    async def archive(self, profile_id: int) -> Profile:
        """Stop monitoring the profile; it keeps its history. Idempotent."""
        current = await self._get(profile_id)
        if current.status is ProfileStatus.ARCHIVED:
            return current
        archived = replace(current, status=ProfileStatus.ARCHIVED, archived_at=self._clock())
        await self._profiles.update(archived)
        return archived

    async def unarchive(self, profile_id: int) -> Profile:
        """Monitor the profile again. Idempotent."""
        current = await self._get(profile_id)
        if current.status is ProfileStatus.ACTIVE:
            return current
        active = replace(current, status=ProfileStatus.ACTIVE, archived_at=None)
        await self._profiles.update(active)
        return active

    async def delete(self, profile_id: int) -> None:
        """Delete the profile and all its traces; raises `ProfileNotFoundError` if absent."""
        if not await self._profiles.delete(profile_id):
            raise ProfileNotFoundError(f"Profile {profile_id} does not exist")

    async def reclassify(self) -> int:
        """Assign every stored trace to its profile; return how many changed."""
        traces = await self._profiles.list_trace_keys()
        assignments = classify(traces, await self._profiles.list_profiles())
        changes = {
            trace.id: assignments[trace.id]
            for trace in traces
            if assignments[trace.id] != trace.profile_id
        }
        if changes:
            await self._profiles.assign_profiles(changes)
        return len(changes)

    async def reclassify_after_sync(self, run: SyncRun) -> None:
        """`SyncService` hook: classify the traces the sync has just stored."""
        await self.reclassify()

    async def _get(self, profile_id: int) -> Profile:
        """The profile, or `ProfileNotFoundError`."""
        profile = await self._profiles.get(profile_id)
        if profile is None:
            raise ProfileNotFoundError(f"Profile {profile_id} does not exist")
        return profile
